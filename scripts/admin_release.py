"""Release the admin's graph edits (graph/admin/changes.jsonl) to the search.

The edits are applied to a copy of the live artifacts, never in place:

  0. entities created by a CSV import (graph/admin/new_entities.jsonl) that the live graph does
     not have yet -> graph.json entities (+ MADE_BY) and entity_categories.json entries;
  1. brand edits -> MADE_BY edges of graph.json (on every merged member). If the graph changed,
     the doc texts are rebuilt reusing the old vectors (build_doc_texts.py --reuse, only changed
     cards are embedded), the changed Qdrant points are synced (qdrant_sync_changes.py --write)
     and short_aliases.json is rebuilt for the new graph fingerprint;
  2. label, attribute and relation edits -> entity_categories.json, then the category payload of
     the affected Qdrant points;
  3. the copy replaces data/processed/products-v1/search (the old one is kept as
     search-prev-<time>), graph/entity_categories.json is updated, and the release is recorded in
     graph/admin/releases.jsonl.

The API runs release() in-process from POST /api/v1/admin/release, lending its loaded bge-m3, and
reloads afterwards. Run by hand, restart the API afterwards (and stop it first if the GPU cannot
hold a second bge-m3). Dry run unless --write.

  python scripts/admin_release.py
  python scripts/admin_release.py --write --actor admin@example.com
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import shutil
import subprocess
import sys
import time
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages" / "retrieval" / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from build_graph_lite import add_admin_entities, canonical_members, seal  # noqa: E402
from graph_apply import (  # noqa: E402
    category_entry,
    clean_enrichment,
    expected_payloads,
    imported_label,
    slug_id,
    source_of,
)

from visualseek_retrieval.admin_overlay import (  # noqa: E402
    ENRICH_FIELDS,
    LABEL_FIELDS,
    LIST_FIELDS,
    ActorDirectory,
    apply_made_by,
    enrich_override,
    fold,
    label_override,
    last_release,
    load_new_entities,
    pending_fields,
    read_jsonl,
)

CORPUS = ROOT / "data/processed/products-v1"
SEARCH = CORPUS / "search"
ADMIN_DIR = ROOT / "graph/admin"
UPLOADS = ROOT / "data/admin/uploads.jsonl"
CATEGORIES = ROOT / "graph/entity_categories.json"
TAXONOMY = ROOT / "graph/taxonomy-v1.json"
RELATION_TABLES = {
    "TREATS": ("purposes", "purpose"),
    "HAS_INGREDIENT": ("ingredients", "ingredient"),
    "FITS": ("models", "model"),
    "ORIGIN": ("places", "place"),
}
FIELD_REL = {**LIST_FIELDS, "origin": "ORIGIN"}
EDGE_ORDER = {"IS_A": 0, "TREATS": 1, "HAS_INGREDIENT": 2, "FITS": 3, "ORIGIN": 4}


# Where the progress lines go; the API points it at the release log file.
OUT = sys.stdout


def log(message: str, **fields) -> None:
    line = json.dumps({"at": now(), "step": message, **fields}, ensure_ascii=False)
    print(line, file=OUT, flush=True)


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def category_names() -> dict[str, tuple[str, str]]:
    taxonomy = json.loads(TAXONOMY.read_text(encoding="utf-8"))
    return {c["id"]: (c["name"], l1["id"]) for l1 in taxonomy["categories"] for c in l1["children"]}


def coverage(entities: dict, canonical: int) -> dict:
    """The same counts as graph_apply.merge."""

    def count(key: str) -> int:
        return sum(bool(e.get(key)) for e in entities.values())

    return {
        "canonical_entities": canonical,
        "labelled": len(entities),
        "by_source": dict(Counter(e["source"] for e in entities.values())),
        "needs_review": sum(bool(e.get("needs_review")) for e in entities.values()),
        "fashion": sum(e["category_l1"] == "fashion" for e in entities.values()),
        **{
            f"with_{key}": count(key)
            for key in (
                "treats",
                "ingredients",
                "fits",
                "origin",
                "material",
                "form_factor",
                "target_audience",
            )
        },
    }


def apply_to_categories(
    document: dict,
    state: dict[str, dict],
    graph: dict,
    members: dict[str, list[str]],
    names: dict[str, tuple[str, str]],
) -> list[str]:
    """Apply the admin state to entity_categories.json in place; returns the changed entities.

    Mirrors what graph_apply.merge produces for an entity with these edits, without re-reading
    the LLM caches (a merge could also pick up label changes nobody reviewed here)."""
    brand_of = {e["src"]: e["dst"] for e in graph["edges"] if e["rel"] == "MADE_BY"}
    graph_names = {e["id"]: e["name"] for e in graph["entities"]}
    by_src: dict[str, list[dict]] = defaultdict(list)
    for edge in document["edges"]:
        by_src[edge["src"]].append(edge)
    changed = []
    for entity_id, fields in sorted(state.items()):
        label = label_override(fields)
        old = document["entities"].get(entity_id)
        if old is None and "category" not in label:
            continue  # an unlabelled entity needs a category before it can enter the file
        entry = (
            copy.deepcopy(old)
            if old
            else {
                "name": graph_names.get(entity_id, entity_id),
                "source": "human",
                "model": None,
                "basis": None,
                "confidence": None,
            }
        )
        old_edges = by_src.get(entity_id, [])
        old_source = {e["rel"]: e.get("source") for e in old_edges}
        for key in LABEL_FIELDS:
            if key in label:
                entry[key] = label[key]
        if label.get("source"):
            entry["source"] = label["source"]
        entry["category_name"], l1 = names[entry["category"]]
        entry["category_l1"] = l1
        if l1 != "fashion":
            entry["market_tier"] = "not_applicable"
        entry["needs_review"] = entry["source"] != "human" and bool(entry.get("needs_review"))
        entry["brand_id"] = next(
            (brand_of[m] for m in members.get(entity_id) or [entity_id] if m in brand_of), None
        )
        edited = enrich_override(fields)
        raw = {key: entry.get(key) for key in ENRICH_FIELDS} | edited
        clean = clean_enrichment({**raw, "admin_fields": sorted(edited)}, l1)
        for key in ENRICH_FIELDS:
            if clean[key]:
                entry[key] = clean[key]
            else:
                entry.pop(key, None)
        edges = [{"src": entity_id, "rel": "IS_A", "dst": f"category:{entry['category']}"}]
        for key, rel in FIELD_REL.items():
            values = clean[key] if isinstance(clean[key], list) else [clean[key]]
            source = "admin" if key in edited else old_source.get(rel) or "admin"
            table, prefix = RELATION_TABLES[rel]
            for value in values:
                if value and (nid := slug_id(prefix, value)):
                    document[table].setdefault(nid, value)
                    edges.append({"src": entity_id, "rel": rel, "dst": nid, "source": source})
        if entry != old or edges != old_edges:
            document["entities"][entity_id] = entry
            by_src[entity_id] = edges
            changed.append(entity_id)
    document["edges"] = [
        edge
        for src in sorted(by_src)
        for edge in sorted(by_src[src], key=lambda e: EDGE_ORDER.get(e["rel"], 9))
    ]
    used = {edge["dst"] for edge in document["edges"]}
    for table, _ in RELATION_TABLES.values():
        document[table] = {k: v for k, v in sorted(document[table].items()) if k in used}
    document["coverage"] = coverage(
        document["entities"], document["coverage"].get("canonical_entities", 0)
    )
    return changed


PREFIX_TABLE = {prefix: table for table, prefix in RELATION_TABLES.values()}


def add_new_categories(
    document: dict, rows: list[dict], graph: dict, names: dict[str, tuple[str, str]]
) -> list[str]:
    """Category entries of imported entities, as graph_apply.merge builds them; returns their
    ids. Entities whose classification failed stay unlabelled (an admin sets the category)."""
    brand_of = {e["src"]: e["dst"] for e in graph["edges"] if e["rel"] == "MADE_BY"}
    added = []
    for row in rows:
        eid, label = row["entity_id"], row.get("label")
        if eid in document["entities"] or not label or label.get("category") not in names:
            continue
        enrich = row.get("enrich") or {}
        if enrich:
            enrich = {**enrich, "enrich_source": source_of(enrich.get("model", ""))}
        entry, edges, nodes = category_entry(
            row["name"], imported_label(row), enrich, brand_of.get(eid), names, eid
        )
        document["entities"][eid] = entry
        document["edges"] += edges
        for prefix, nid, value in nodes:
            document[PREFIX_TABLE[prefix]].setdefault(nid, value)
        added.append(eid)
    if added:
        document["coverage"]["canonical_entities"] = document["coverage"].get(
            "canonical_entities", 0
        ) + len(added)
    return added


def run(args: list[str]) -> None:
    log("run", command=" ".join(args))
    started = time.perf_counter()
    result = subprocess.run([sys.executable, *args], cwd=ROOT, capture_output=True, text=True)
    for line in (result.stdout + result.stderr).splitlines()[-20:]:
        print(f"  | {line}", file=OUT, flush=True)
    if result.returncode != 0:
        raise SystemExit(f"{args[0]} failed with exit code {result.returncode}")
    log("done", command=args[0], seconds=round(time.perf_counter() - started, 1))


def active_uploads() -> int:
    rows = read_jsonl(UPLOADS)
    state: dict[str, str] = {}
    for row in rows:
        state[row["asset_id"]] = row["op"]
    return sum(op == "add" for op in state.values())


def sync_category_payload(old_path: Path, new_path: Path, artifacts: Path, write: bool) -> int:
    from qdrant_client import models

    from visualseek_retrieval.qdrant_store import COLLECTION, connect

    _, before, _ = expected_payloads(old_path, artifacts)
    _, after, _ = expected_payloads(new_path, artifacts)
    grouped: dict[str, list[str]] = defaultdict(list)
    for pid, payload in after.items():
        if before.get(pid) != payload:
            grouped[json.dumps(payload, ensure_ascii=False, sort_keys=True)].append(pid)
    points = sum(len(ids) for ids in grouped.values())
    if not write or not points:
        return points
    client = connect(os.environ["QDRANT_URL"], os.environ.get("QDRANT_API_KEY"), timeout=60)
    ops = [
        models.SetPayloadOperation(set_payload=models.SetPayload(payload=json.loads(p), points=ids))
        for p, ids in grouped.items()
    ]
    for start in range(0, len(ops), 50):
        client.batch_update_points(COLLECTION, ops[start : start + 50], wait=True)
    return points


def load_env() -> None:
    path = ROOT / ".env"
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        key, sep, value = line.partition("=")
        if sep and key.strip() and not key.startswith("#"):
            os.environ.setdefault(key.strip(), value.strip().strip('"'))


def release(write: bool, actor: str = "", device: str = "cuda", encoder=None) -> dict | None:
    """Apply the pending admin edits; returns the release record (None on a dry run).

    ``encoder``: a loaded bge-m3 with ``encode(texts, batch_size)`` at document length. The API
    passes its own, since a second copy does not fit beside it on a 4 GB GPU; from the command
    line build_doc_texts loads one (stop the API first on such a GPU)."""
    changes = read_jsonl(ADMIN_DIR / "changes.jsonl")
    releases = read_jsonl(ADMIN_DIR / "releases.jsonl")
    state = fold(changes)
    previous = last_release(releases)
    pending = pending_fields(state, previous["state"])
    through = max((row["seq"] for row in changes), default=0)
    live_graph = json.loads((SEARCH / "graph.json").read_text(encoding="utf-8"))
    live_ids = {entity["id"] for entity in live_graph["entities"]}
    new_rows = [r for r in load_new_entities(ADMIN_DIR).values() if r["entity_id"] not in live_ids]
    if not pending and not new_rows:
        log("nothing_to_release", through_seq=through)
        return None

    live_categories = json.loads(CATEGORIES.read_text(encoding="utf-8"))
    members = canonical_members(CORPUS)
    graph = copy.deepcopy(live_graph)
    new_entities = add_admin_entities(graph, new_rows)
    brand_changed = apply_made_by(graph, state, members)
    seal(graph)
    graph_changed = (graph["fingerprint"], graph["aliases_sha256"]) != (
        live_graph["fingerprint"],
        live_graph["aliases_sha256"],
    )
    categories = copy.deepcopy(live_categories)
    names = category_names()
    new_labelled = add_new_categories(categories, new_rows, graph, names)
    category_changed = apply_to_categories(categories, state, graph, members, names)
    log(
        "plan",
        pending_entities=len(pending),
        pending_fields=sum(len(f) for f in pending.values()),
        through_seq=through,
        new_entities=len(new_entities),
        new_entities_labelled=len(new_labelled),
        made_by_changed=len(brand_changed),
        graph_changed=graph_changed,
        categories_changed=len(category_changed),
        uploads=active_uploads(),
    )
    if not write:
        log("dry_run", detail="pass --write to apply")
        return None

    load_env()
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    stage = CORPUS / f"search-release-{stamp}"
    previous_dir = CORPUS / f"search-prev-{stamp}"
    # Fail before touching Qdrant if the live folder cannot be swapped (a file open on Windows).
    probe = CORPUS / f"search-probe-{stamp}"
    SEARCH.rename(probe)
    probe.rename(SEARCH)
    log("stage", dir=str(stage.relative_to(ROOT)))
    shutil.copytree(SEARCH, stage)

    if graph_changed:
        (stage / "graph.json").write_text(
            json.dumps(graph, ensure_ascii=False, indent=1), encoding="utf-8"
        )
        import build_doc_texts

        log("run", command="build_doc_texts.build --reuse (changed texts only)")
        started = time.perf_counter()
        build_doc_texts.build(CORPUS, stage, device, reuse=SEARCH, encoder=encoder)
        log("done", command="build_doc_texts", seconds=round(time.perf_counter() - started, 1))
        run(
            [
                "scripts/qdrant_sync_changes.py",
                "--old",
                str(SEARCH),
                "--new",
                str(stage),
                "--write",
                "--extra-points",
                str(active_uploads()),
            ]
        )
        run(["scripts/build_short_aliases.py", "--artifacts", str(stage)])

    text = json.dumps(categories, ensure_ascii=False, indent=1, sort_keys=True) + "\n"
    (stage / "entity_categories.json").write_text(text, encoding="utf-8", newline="\n")
    points = sync_category_payload(CATEGORIES, stage / "entity_categories.json", stage, True)
    log("category_payload", points=points)

    SEARCH.rename(previous_dir)
    stage.rename(SEARCH)
    CATEGORIES.write_text(text, encoding="utf-8", newline="\n")
    record = {
        "at": now(),
        "actor": actor,
        "through_seq": through,
        "state": state,
        "new_entities": new_entities,
        "made_by_changed": len(brand_changed),
        "categories_changed": len(category_changed),
        "category_payload_points": points,
        "graph_fingerprint": graph["fingerprint"],
        "previous_dir": str(previous_dir.relative_to(ROOT)),
    }
    ADMIN_DIR.mkdir(parents=True, exist_ok=True)
    with (ADMIN_DIR / "releases.jsonl").open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    log(
        "released",
        **{k: v for k, v in record.items() if k not in ("state", "new_entities")},
        new_entities=len(new_entities),
    )
    return record


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true")
    parser.add_argument(
        "--actor",
        default="",
        help="who started the release; releases.jsonl is in git, an e-mail is stored as an alias",
    )
    parser.add_argument("--device", default="cuda", help="for re-embedding changed doc texts")
    args = parser.parse_args()
    actor = args.actor
    if "@" in actor:
        actor = ActorDirectory(UPLOADS.parent).alias(actor, actor)
    release(args.write, actor, args.device)


if __name__ == "__main__":
    main()
