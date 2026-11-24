"""S1 entity linking on names people actually type, not catalogue names (eval/s1-names-v1).

build  writes eval/s1-names-v1/queries.jsonl with two kinds of query:
         short      the catalogue name trimmed at its ends (short_aliases.candidate). It
                    measures reachability by short name and is partly circular for the
                    names that received a short alias; the informative rows are the ones
                    whose alias was rejected (shared or not distinctive).
         web_title  the first segment of a source page title (how shops name the product),
                    independent of the alias rules. Kept only when it shares a 4+ letter
                    token with the entity name and differs from it.
       Split dev/test by entity (sha256 of the canonical id).
run    links every query with the production S1/S2 plan (dictionary, then dense seed) for
       three dictionaries: baseline, shared derived aliases suppressed, and suppressed plus
       short aliases; reports exact / wrong / asked back / none.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages" / "retrieval" / "src"))

from visualseek_retrieval.entity_review import EntityReview  # noqa: E402
from visualseek_retrieval.short_aliases import candidate  # noqa: E402
from visualseek_retrieval.text import normalize  # noqa: E402

ARTIFACTS = ROOT / "data/processed/products-v1/search"
CORPUS = ROOT / "data/processed/products-v1"
OUT = ROOT / "eval/s1-names-v1"
REPORT = CORPUS / "eval/runs/s1-names-v1.json"
PACKAGED_REVIEW = (
    ROOT / "services/api/src/visualseek_api/application/products-v1-identity-review.json"
)
TITLE_SPLIT = re.compile(r"\s+[-|–—:]\s+|\s*\|\s*")
MAX_WEB_TITLES = 600
MAX_WORDS = 14


def review_path() -> Path:
    local = ARTIFACTS / "identity_review.json"
    return local if local.exists() else PACKAGED_REVIEW


def bucket(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def split_of(entity_id: str) -> str:
    return "dev" if int(bucket(entity_id)[:8], 16) % 2 == 0 else "test"


def load_graph_review() -> tuple[dict, EntityReview]:
    graph = json.loads((ARTIFACTS / "graph.json").read_text(encoding="utf-8"))
    return graph, EntityReview.load(graph, review_path().read_bytes())


def build(_: argparse.Namespace) -> None:
    graph, review = load_graph_review()
    brand_phrases = {
        tuple(normalize(alias).split())
        for brand in graph["brands"]
        for alias in (*brand["aliases"], brand["name"])
        if normalize(alias)
    }
    rows = []
    shorts: dict[str, set[str]] = defaultdict(set)
    surface_of: dict[str, str] = {}
    names = {}
    for entity in graph["entities"]:
        entity_id = review.canonical(entity["id"])
        names.setdefault(entity_id, entity["name"])
        short = candidate(entity["name"], brand_phrases)
        if short:
            shorts[normalize(short)].add(entity_id)
            surface_of.setdefault(normalize(short), short)
    for key, owners in sorted(shorts.items()):
        # A short name shared by several entities expects S1 to ask back, not to guess.
        anchor = sorted(owners)[0]
        rows.append(
            {
                "kind": "short",
                "query": surface_of[key],
                "expected": sorted(owners),
                "split": split_of(anchor),
            }
        )

    titles: dict[str, set[str]] = defaultdict(set)
    for line in (CORPUS / "assets.jsonl").read_text(encoding="utf-8").splitlines():
        asset = json.loads(line)
        kept = set(review.result_ids(asset["asset_id"], asset["entity_ids"]))
        for source in asset["sources"]:
            entity_id = review.canonical(source["entity_id"])
            page = source.get("page") or {}
            if entity_id in kept and page.get("status") == "ok" and source.get("page_title"):
                titles[entity_id].add(source["page_title"])
    web = []
    for entity_id, options in titles.items():
        name_tokens = {t for t in normalize(names[entity_id]).split() if len(t) >= 4}
        usable = []
        for title in sorted(options):
            head = TITLE_SPLIT.split(title.strip())[0].strip()
            tokens = normalize(head).split()
            if (
                head
                and len(tokens) <= MAX_WORDS
                and name_tokens & set(tokens)
                and normalize(head) != normalize(names[entity_id])
            ):
                usable.append(head)
        if usable:
            pick = min(usable, key=lambda text, e=entity_id: bucket(f"{e}|{text}"))
            web.append(
                {
                    "kind": "web_title",
                    "query": pick,
                    "expected": [entity_id],
                    "split": split_of(entity_id),
                }
            )
    web.sort(key=lambda row: bucket(row["expected"][0]))
    rows += web[:MAX_WEB_TITLES]
    for i, row in enumerate(rows):
        row["qid"] = f"{row['kind']}-{i:04d}"
    OUT.mkdir(parents=True, exist_ok=True)
    body = "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows)
    (OUT / "queries.jsonl").write_text(body, encoding="utf-8", newline="\n")
    manifest = {
        "version": 1,
        "queries_sha256": hashlib.sha256(body.encode("utf-8")).hexdigest(),
        "graph_fingerprint": graph["fingerprint"],
        "identity_review_fingerprint": review.fingerprint,
        "counts": {
            f"{kind}/{split}": n
            for (kind, split), n in sorted(Counter((r["kind"], r["split"]) for r in rows).items())
        },
        "note": "short rows are partly circular with short_aliases; web_title rows are not",
    }
    (OUT / "manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    print(json.dumps(manifest["counts"]))


def outcome(plan, expected: list[str], canonical) -> str:
    if plan.clarification is not None:
        options = {canonical(o["entity_id"]) for c in plan.clarification for o in c["options"]}
        return "asked_right" if set(expected) <= options else "asked_wrong"
    if plan.out_of_scope:
        return "none"
    found = {canonical(e) for e in (plan.origin_ids or plan.seeds or plan.anchors)}
    if not found:
        return "none"
    if len(expected) > 1:
        return "wrong"  # several entities share this short name: guessing one is wrong
    return "exact" if found == set(expected) else "wrong"


def run(args: argparse.Namespace) -> None:
    from visualseek_retrieval.encoder import TextEncoder
    from visualseek_retrieval.pipeline import SearchPipeline

    graph, review = load_graph_review()
    rows = [json.loads(line) for line in (OUT / "queries.jsonl").read_text("utf-8").splitlines()]
    dense = np.load(ARTIFACTS / "entity_dense.npy")
    short_file = json.loads((ARTIFACTS / "short_aliases.json").read_text(encoding="utf-8"))
    if short_file["identity_review_fingerprint"] != review.fingerprint:
        raise SystemExit("short_aliases.json is stale; run scripts/build_short_aliases.py")
    blocked = short_file["suppressed"]["aliases"]
    variants = {
        "baseline": SearchPipeline(graph, dense, None, None, review=review),
        "suppressed": SearchPipeline(
            graph, dense, None, None, review=review, suppressed_aliases=blocked
        ),
        "suppressed+short": SearchPipeline(
            graph,
            dense,
            None,
            None,
            review=review,
            short_aliases=short_file["aliases"],
            suppressed_aliases=blocked,
        ),
    }
    vectors = TextEncoder(device=args.device).encode([row["query"] for row in rows])
    report = {
        "queries_sha256": hashlib.sha256((OUT / "queries.jsonl").read_bytes()).hexdigest(),
        "short_aliases_fingerprint": short_file["fingerprint"],
        "identity_review_fingerprint": review.fingerprint,
        "results": {},
        "changed": [],
    }
    per_variant = {}
    for name, pipeline in variants.items():
        outcomes = []
        for row, vector in zip(rows, vectors, strict=True):
            plan = pipeline.plan(row["query"], pipeline.spotter.spot(row["query"], vector))
            outcomes.append(outcome(plan, row["expected"], review.canonical))
        per_variant[name] = outcomes
        table = {}
        for kind in ("short", "web_title"):
            for split in ("dev", "test"):
                picked = [
                    o
                    for o, r in zip(outcomes, rows, strict=True)
                    if r["kind"] == kind and r["split"] == split
                ]
                counts = Counter(picked)
                table[f"{kind}/{split}"] = {
                    "n": len(picked),
                    **{
                        k: round(counts[k] / len(picked), 3) if picked else 0.0
                        for k in ("exact", "asked_right", "wrong", "asked_wrong", "none")
                    },
                }
        report["results"][name] = table
    for row, before, after in zip(
        rows, per_variant["baseline"], per_variant["suppressed+short"], strict=True
    ):
        if before != after:
            report["changed"].append({**row, "before": before, "after": after})
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    for name, table in report["results"].items():
        print(name)
        for key, value in table.items():
            print(f"  {key:16s} {value}")
    print("changed:", Counter((c["before"], c["after"]) for c in report["changed"]))
    print(f"wrote {REPORT}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("build")
    p_run = sub.add_parser("run")
    p_run.add_argument("--device", default="cpu")
    args = parser.parse_args()
    build(args) if args.cmd == "build" else run(args)


if __name__ == "__main__":
    main()
