"""B2 apply: merge category labels into the graph artifacts and the Qdrant payload.

merge   One category per canonical entity from every label source, in priority order:
        human decision > Gemini > other models. Writes graph/entity_categories.json
        (entity attributes + IS_A edges) and proposes a tier per fashion brand in
        graph/brand_tiers.json (never overwrites an entry a person has reviewed).
qdrant  Sets category payload on every point of products_v1_images with set_payload,
        so the vectors and the graph fingerprint stay untouched.

  python scripts/graph_apply.py merge
  python scripts/graph_apply.py qdrant            # dry run: prints what would change
  python scripts/graph_apply.py qdrant --write
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages" / "retrieval" / "src"))

CORPUS = ROOT / "data/processed/products-v1"
GRAPH_DIR = CORPUS / "graph"
ARTIFACTS = CORPUS / "search"
TAXONOMY = ROOT / "graph/taxonomy-v1.json"
CATEGORIES = ROOT / "graph/entity_categories.json"
BRAND_TIERS = ROOT / "graph/brand_tiers.json"
DECISIONS = ROOT / "eval/graph-review/category_decisions.jsonl"
ENRICH_DECISIONS = ROOT / "eval/graph-review/enrich_decisions.jsonl"
ENRICH_PROMPT_VERSION = "b4-enrich-v1"  # scripts/graph_enrich.py PROMPT_VERSION
SOURCE_RANK = {"human": 0, "gemini": 1, "claude": 2, "other": 3, "free": 4}


def slug_id(prefix: str, label: str) -> str:
    from visualseek_retrieval.text import normalize

    slug = "-".join(normalize(label).split())
    return f"{prefix}:{slug}" if slug else ""


def load_enrichment() -> dict[str, dict]:
    """Enrichment per canonical entity: human > enrich_cache (LLM) > enrich-free.jsonl.

    An LLM row replaces the free row of its entity instead of being unioned with it: the LLM
    read the same snippets, and an empty field there means "not applicable". The free rules
    match the whole description and are only a fallback for entities the LLM has not labelled
    (on the pilot they added "kẽm" for every "kem" and "chống thấm" for bricks).
    """
    merged: dict[str, dict] = {}
    free_path = (
        ROOT / "graph/enrich-free.jsonl"
        if (ROOT / "graph/enrich-free.jsonl").exists()
        else GRAPH_DIR / "enrich-free.jsonl"
    )
    for row in read_jsonl(free_path):
        merged[row["entity_id"]] = {**row, "enrich_source": "free"}
    rank = {"gemini": 0, "claude": 1, "other": 2}
    for cache_dir in (GRAPH_DIR / "enrich_cache", ROOT / "graph/enrich_cache"):
        if not cache_dir.exists():
            continue
        for path in sorted(cache_dir.glob("*.json")):
            row = json.loads(path.read_text(encoding="utf-8"))
            if row.get("prompt_version") != ENRICH_PROMPT_VERSION:
                continue
            eid = row["entity_id"]
            src = source_of(row.get("model", ""))
            old = merged.get(eid, {}).get("enrich_source", "free")
            if old != "free" and rank[src] >= rank[old]:
                continue
            merged[eid] = {**row, "enrich_source": src}
    for row in read_jsonl(ENRICH_DECISIONS):
        eid = row["entity_id"]
        merged[eid] = {**merged.get(eid, {}), **row, "enrich_source": "human"}
    from visualseek_retrieval.admin_overlay import enrich_override, load_state

    # Admin edits replace single fields; the other fields keep their source and their checks.
    for eid, fields in load_state(ROOT / "graph/admin").items():
        if edited := enrich_override(fields):
            base = merged.get(eid, {"enrich_source": "free"})
            merged[eid] = {**base, **edited, "admin_fields": sorted(edited)}
    return merged


def clean_enrichment(enrich: dict, l1: str = "") -> dict:
    """Fold wording variants so the same value is one graph node: ingredient spellings
    ('Famotidine' = 'Famotidin'), canonical purpose buckets next to the LLM's own phrases, and
    canonical materials (the query-time filter compares them with '=='). ``l1`` is the entity's
    top-level category: a province is not an origin for a drug or a machine.

    Fields named in ``enrich["admin_fields"]`` were typed by an admin: they are folded the same
    way but not capped, not gated by category, and kept when no canonical form is known."""
    from visualseek_retrieval.enrichment import (
        canonical_fits_model,
        canonical_material,
        normalize_ingredient,
        origin_allowed,
        purpose_buckets,
    )

    def strings(key: str) -> list[str]:
        return [v.strip() for v in (enrich.get(key) or []) if isinstance(v, str) and v.strip()]

    trusted = set(enrich.get("admin_fields") or ())

    def cap(key: str, values: list, size: int) -> list:
        return values if key in trusted else values[:size]

    treats = strings("treats")
    treats = list(dict.fromkeys([*treats, *purpose_buckets(treats)]))
    by_slug: dict[str, str] = {}
    for item in strings("ingredients"):
        # Only a spelling fold that changes the node id replaces the label: "Đẳng sâm" and
        # "AHA" keep their tones and case, "Famotidine" becomes "Famotidin".
        folded = normalize_ingredient(item)
        changed = slug_id("ingredient", folded) != slug_id("ingredient", item)
        canon = folded if changed else item
        by_slug.setdefault(slug_id("ingredient", canon), canon)
    fits = list(
        dict.fromkeys(
            cm
            for f in strings("fits")
            if (cm := canonical_fits_model(f) or (f if "fits" in trusted else None))
        )
    )
    origin = (enrich.get("origin") or "").strip()
    return {
        "treats": cap("treats", treats, 6),
        "ingredients": cap("ingredients", [v for k, v in by_slug.items() if k], 4),
        "fits": cap("fits", fits, 4),
        "origin": origin if "origin" in trusted or origin_allowed(origin, l1) else "",
        "form_factor": (enrich.get("form_factor") or "").strip(),
        "target_audience": (enrich.get("target_audience") or "").strip(),
        "material": canonical_material((enrich.get("material") or "").strip())
        or ((enrich.get("material") or "").strip() if "material" in trusted else ""),
    }


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def source_of(model: str) -> str:
    if "gemini" in model:
        return "gemini"
    return "claude" if "claude" in model else "other"


def load_labels() -> dict[str, dict]:
    """Best label per canonical entity, with how it was produced."""
    best: dict[str, dict] = {}
    for path in (GRAPH_DIR / "llm_cache").glob("*.json"):
        row = json.loads(path.read_text(encoding="utf-8"))
        source = source_of(row["model"])
        old = best.get(row["entity_id"])
        if old is None or SOURCE_RANK[source] < SOURCE_RANK[old["source"]]:
            best[row["entity_id"]] = {**row, "source": source}
    latest = {row["entity_id"]: row for row in read_jsonl(DECISIONS)}
    for entity_id, row in latest.items():
        base = best.get(entity_id, {})
        best[entity_id] = {
            **base,
            "entity_id": entity_id,
            "category": row["category"],
            "product_type": row["product_type"],
            "market_tier": row["market_tier"],
            "source": "human",
        }
    from visualseek_retrieval.admin_overlay import label_override, load_state

    for entity_id, fields in load_state(ROOT / "graph/admin").items():
        edited = label_override(fields)
        base = best.get(entity_id)
        if edited and (base or "category" in edited):
            best[entity_id] = {**(base or {}), "entity_id": entity_id, **edited}
    return best


def imported_label(row: dict) -> dict:
    """The classify result of an entity created by a CSV import, as a load_labels() row."""
    label = row["label"]
    return {
        **label,
        "entity_id": row["entity_id"],
        "source": source_of(label.get("model", "")),
        "imported": True,
    }


def category_entry(
    name: str, label: dict, enrich: dict, brand: str | None, names: dict, entity_id: str
) -> tuple[dict, list[dict], list[tuple[str, str, str]]]:
    """(entity_categories entry, its IS_A and relation edges, (prefix, node id, label) of the
    relation nodes) for one canonical entity."""
    category = label["category"]
    l2_name, l1_id = names[category]
    clean = clean_enrichment(enrich, l1_id)
    treats, ing_list, fits = clean["treats"], clean["ingredients"], clean["fits"]
    origin, material = clean["origin"], clean["material"]
    form_factor, target_audience = clean["form_factor"], clean["target_audience"]
    enrich_src = enrich.get("enrich_source", "free")
    admin_fields = set(enrich.get("admin_fields") or ())

    def src_of(field: str) -> str:
        return "admin" if field in admin_fields else enrich_src

    entry = {
        "name": name,
        "category": category,
        "category_name": l2_name,
        "category_l1": l1_id,
        "product_type": label["product_type"],
        "market_tier": label["market_tier"] if l1_id == "fashion" else "not_applicable",
        "brand_id": brand,
        "source": label["source"],
        "model": label.get("model"),
        "basis": label.get("basis"),
        "confidence": label.get("confidence"),
        # An imported entity stays in the review queue until a person checks its label.
        "needs_review": label["source"] != "human"
        and (
            label.get("basis") == "knowledge"
            or label["source"] != "gemini"
            or bool(label.get("imported"))
        ),
    }
    if label.get("imported"):
        entry["imported"] = True
    for key, value in (
        ("treats", treats),
        ("ingredients", ing_list),
        ("fits", fits),
        ("origin", origin),
        ("form_factor", form_factor),
        ("target_audience", target_audience),
        ("material", material),
    ):
        if value:
            entry[key] = value
    edges = [{"src": entity_id, "rel": "IS_A", "dst": f"category:{category}"}]
    nodes: list[tuple[str, str, str]] = []
    for field, rel, prefix, values in (
        ("treats", "TREATS", "purpose", treats),
        ("ingredients", "HAS_INGREDIENT", "ingredient", ing_list),
        ("fits", "FITS", "model", fits),
        ("origin", "ORIGIN", "place", [origin] if origin else []),
    ):
        for item in values:
            if nid := slug_id(prefix, item):
                nodes.append((prefix, nid, item))
                edges.append({"src": entity_id, "rel": rel, "dst": nid, "source": src_of(field)})
    return entry, edges, nodes


def merge(args: argparse.Namespace) -> None:
    taxonomy = json.loads(TAXONOMY.read_text(encoding="utf-8"))
    names = {
        c["id"]: (c["name"], l1["id"]) for l1 in taxonomy["categories"] for c in l1["children"]
    }
    packs = {p["entity_id"]: p for p in read_jsonl(GRAPH_DIR / "evidence.jsonl")}
    graph = json.loads((ARTIFACTS / "graph.json").read_text(encoding="utf-8"))
    brand_of: dict[str, str] = {}
    for edge in graph["edges"]:
        if edge["rel"] == "MADE_BY":
            brand_of.setdefault(edge["src"], edge["dst"])
    brand_names = {b["id"]: b["name"] for b in graph["brands"]}
    labels = load_labels()
    enrichment = load_enrichment()

    entities, edges = {}, []
    purposes: dict[str, str] = {}
    ingredients: dict[str, str] = {}
    models: dict[str, str] = {}
    places: dict[str, str] = {}
    from visualseek_retrieval.admin_overlay import label_override, load_new_entities, load_state

    # Entities created by a CSV import in /admin: their own evidence pack and LLM results, under
    # any human label or admin edit made after their release.
    state = load_state(ROOT / "graph/admin")
    for row in load_new_entities(ROOT / "graph/admin").values():
        eid = row["entity_id"]
        if eid in packs:
            continue
        packs[eid] = row["pack"]
        if row.get("label") and labels.get(eid, {}).get("source") != "human":
            labels[eid] = {
                **imported_label(row),
                **label_override(state.get(eid, {})),
            }
        base = enrichment.get(eid) or {}
        if row.get("enrich") and base.get("enrich_source", "free") == "free":
            admin_fields = list(base.get("admin_fields") or ())
            enrichment[eid] = {
                **row["enrich"],
                "enrich_source": source_of(row["enrich"].get("model", "")),
                **{field: base[field] for field in admin_fields},
                "admin_fields": admin_fields,
            }

    tables = {"purpose": purposes, "ingredient": ingredients, "model": models, "place": places}
    for entity_id, label in sorted(labels.items()):
        if entity_id not in packs:
            continue
        brand = next((brand_of[m] for m in packs[entity_id]["members"] if m in brand_of), None)
        entry, entity_edges, nodes = category_entry(
            packs[entity_id]["name"],
            label,
            enrichment.get(entity_id) or {},
            brand,
            names,
            entity_id,
        )
        entities[entity_id] = entry
        edges += entity_edges
        for prefix, nid, value in nodes:
            tables[prefix].setdefault(nid, value)

    # Brand tiers: a proposal from the entity-level votes; a reviewed entry is kept as is.
    existing = (
        json.loads(BRAND_TIERS.read_text(encoding="utf-8"))["brands"]
        if BRAND_TIERS.exists()
        else {}
    )
    votes: dict[str, Counter] = defaultdict(Counter)
    members: dict[str, list[str]] = defaultdict(list)
    for entity_id, e in entities.items():
        if e["category_l1"] == "fashion" and e["brand_id"]:
            votes[e["brand_id"]][e["market_tier"]] += 1
            members[e["brand_id"]].append(entity_id)
    brands = dict(existing)
    for brand_id, counter in votes.items():
        if brand_id in existing and existing[brand_id]["status"] == "reviewed":
            continue
        known = {t: n for t, n in counter.items() if t != "unknown"}
        top = max(known, key=known.get) if known else "unknown"
        tied = sum(n == known.get(top) for n in known.values()) > 1
        brands[brand_id] = {
            "name": brand_names.get(brand_id, brand_id),
            "tier": "unknown" if tied else top,
            "votes": dict(counter),
            "entities": len(members[brand_id]),
            "status": "proposed",
        }
    for brand_id, brand in brands.items():
        if brand["status"] == "reviewed":
            for entity_id in members.get(brand_id, []):
                entities[entity_id]["market_tier"] = brand["tier"]
                entities[entity_id]["tier_source"] = "brand"

    coverage = {
        "canonical_entities": len(packs),
        "labelled": len(entities),
        "by_source": dict(Counter(e["source"] for e in entities.values())),
        "needs_review": sum(e["needs_review"] for e in entities.values()),
        "fashion": sum(e["category_l1"] == "fashion" for e in entities.values()),
        "with_treats": sum(bool(e.get("treats")) for e in entities.values()),
        "with_ingredients": sum(bool(e.get("ingredients")) for e in entities.values()),
        "with_fits": sum(bool(e.get("fits")) for e in entities.values()),
        "with_origin": sum(bool(e.get("origin")) for e in entities.values()),
        "with_material": sum(bool(e.get("material")) for e in entities.values()),
        "with_form_factor": sum(bool(e.get("form_factor")) for e in entities.values()),
        "with_target_audience": sum(bool(e.get("target_audience")) for e in entities.values()),
    }
    document = {
        "version": "categories-v1",
        "taxonomy_sha256": taxonomy["taxonomy_sha256"],
        "corpus_fingerprint": graph["corpus_fingerprint"],
        "coverage": coverage,
        "category_names": {c: n[0] for c, n in names.items()},
        "purposes": dict(sorted(purposes.items())),
        "ingredients": dict(sorted(ingredients.items())),
        "models": dict(sorted(models.items())),
        "places": dict(sorted(places.items())),
        "entities": entities,
        "edges": edges,
    }
    text = json.dumps(document, ensure_ascii=False, indent=1, sort_keys=True) + "\n"
    # The API reads the copy next to graph.json; graph/ keeps the source of record.
    CATEGORIES.write_text(text, encoding="utf-8", newline="\n")
    try:
        (ARTIFACTS / "entity_categories.json").write_text(text, encoding="utf-8", newline="\n")
    except PermissionError:
        pass
    BRAND_TIERS.write_text(
        json.dumps(
            {"version": "brand-tiers-v1", "brands": dict(sorted(brands.items()))},
            ensure_ascii=False,
            indent=1,
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(json.dumps(coverage, ensure_ascii=False))
    print("brand tiers:", dict(Counter(b["status"] + ":" + b["tier"] for b in brands.values())))


def load_env() -> None:
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        key, sep, value = line.partition("=")
        if sep and key.strip() and not key.startswith("#"):
            os.environ.setdefault(key.strip(), value.strip().strip('"'))


def expected_payloads(
    categories_path: Path = CATEGORIES, artifacts: Path = ARTIFACTS
) -> tuple[dict, dict[str, dict], list[dict]]:
    """point id -> payload that Qdrant must hold for the current entity_categories.json."""
    from visualseek_retrieval.qdrant_store import point_id

    categories = json.loads(categories_path.read_text(encoding="utf-8"))
    packs = {p["entity_id"]: p for p in read_jsonl(GRAPH_DIR / "evidence.jsonl")}
    by_member = {
        member: entity
        for entity_id, entity in categories["entities"].items()
        # Entities created by a CSV import have no corpus evidence pack: they are their own member.
        for member in (packs.get(entity_id) or {}).get("members") or [entity_id]
    }
    rows = read_jsonl(artifacts / "doc_texts.jsonl")
    out = {}
    for row in rows:
        found = [by_member[m] for m in row["entity_ids"] if m in by_member]
        out[point_id(row["asset_id"])] = {
            "categories": sorted({e["category"] for e in found}),
            "category_l1s": sorted({e["category_l1"] for e in found}),
            "market_tiers": sorted(
                {e["market_tier"] for e in found if e["category_l1"] == "fashion"}
            ),
            "taxonomy_sha256": categories["taxonomy_sha256"],
        }
    return categories, out, rows


def qdrant(args: argparse.Namespace) -> None:
    from qdrant_client import models

    from visualseek_retrieval.qdrant_store import COLLECTION, connect

    load_env()
    categories, expected, rows = expected_payloads()
    grouped: dict[str, list[str]] = defaultdict(list)  # payload json -> point ids
    for pid, payload in expected.items():
        grouped[json.dumps(payload, ensure_ascii=False, sort_keys=True)].append(pid)
    covered = sum(bool(p["categories"]) for p in expected.values())
    print(f"images {len(rows)}, with a category {covered}, payload groups {len(grouped)}")
    if not args.write:
        print("dry run; pass --write to update Qdrant")
        return
    client = connect(os.environ["QDRANT_URL"], os.environ.get("QDRANT_API_KEY"), timeout=60)
    ops = [
        models.SetPayloadOperation(
            set_payload=models.SetPayload(payload=json.loads(payload), points=ids)
        )
        for payload, ids in grouped.items()
    ]
    for start in range(0, len(ops), 50):
        client.batch_update_points(COLLECTION, ops[start : start + 50], wait=True)
    for key in ("categories", "category_l1s", "market_tiers"):
        client.create_payload_index(COLLECTION, key, field_schema=models.PayloadSchemaType.KEYWORD)
    print("updated", sum(len(v) for v in grouped.values()), "points")
    digest = hashlib.sha256(CATEGORIES.read_bytes()).hexdigest()
    (ARTIFACTS / "categories_applied.json").write_text(
        json.dumps({"categories_sha256": digest, "images_with_category": covered}, indent=1),
        encoding="utf-8",
    )


def check(args: argparse.Namespace) -> None:
    """Read every point back from Qdrant and compare with the file; print a random sample."""
    import random

    from visualseek_retrieval.qdrant_store import COLLECTION, connect, point_id

    load_env()
    categories, expected, rows = expected_payloads()
    client = connect(os.environ["QDRANT_URL"], os.environ.get("QDRANT_API_KEY"), timeout=60)
    keys = ("categories", "category_l1s", "market_tiers", "taxonomy_sha256")
    ids = list(expected)
    wrong, missing = [], 0
    for start in range(0, len(ids), 500):
        chunk = ids[start : start + 500]
        got = {str(p.id): p.payload for p in client.retrieve(COLLECTION, chunk, with_payload=True)}
        for pid in chunk:
            payload = got.get(pid)
            if payload is None:
                missing += 1
            elif {k: payload.get(k) for k in keys} != expected[pid]:
                wrong.append(pid)
    print(f"points compared {len(ids)}; missing {missing}; different from file {len(wrong)}")
    entities = categories["entities"]
    print(f"entities in file {len(entities)}; coverage {categories['coverage']}")
    image_of: dict[str, dict] = {}
    for row in rows:
        for member in row["entity_ids"]:
            image_of.setdefault(member, row)
    packs = {p["entity_id"]: p for p in read_jsonl(GRAPH_DIR / "evidence.jsonl")}
    rng = random.Random(args.seed)
    sample = rng.sample(sorted(entities), min(args.sample, len(entities)))
    print(f"\nrandom sample (seed {args.seed}): entity | file | Qdrant payload of one image")
    for entity_id in sample:
        e = entities[entity_id]
        row = next((image_of[m] for m in packs[entity_id]["members"] if m in image_of), None)
        live = (
            client.retrieve(COLLECTION, [point_id(row["asset_id"])], with_payload=True)[0].payload
            if row
            else {}
        )
        print(
            f"- {e['name'][:50]}\n    file  : {e['category']} | {e['product_type']}"
            f" | {e['market_tier']} | {e['source']}"
        )
        print(
            f"    qdrant: {live.get('categories')} {live.get('market_tiers')}"
            f"  (asset {row['asset_id'][:12] if row else None})"
        )
    if wrong or missing:
        raise SystemExit(1)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("merge")
    p = sub.add_parser("qdrant")
    p.add_argument("--write", action="store_true")
    c = sub.add_parser("check")
    c.add_argument("--sample", type=int, default=8)
    c.add_argument("--seed", type=int, default=1)
    args = parser.parse_args()
    {"merge": merge, "qdrant": qdrant, "check": check}[args.cmd](args)


if __name__ == "__main__":
    main()
