"""Category-query eval for B2: does the category/tier cue put the right objects on top?

Runs the search pipeline in this process (CPU encoder) over eval/category-v1/queries.jsonl.
A hit is relevant when its payload ``categories`` contain a gold category (tier queries:
and ``market_tiers`` a gold tier). Metrics: precision@10 and "any relevant in top 10".
The silver-v1.1 regression and latency run through the API (scripts/evaluate_v2.py).

  python scripts/evaluate_category.py                       # default weights vs off
  python scripts/evaluate_category.py --grid 0.005 0.01 0.02 0.05 0.2
  python scripts/evaluate_category.py --multi --show          # "A và B" queries
"""

from __future__ import annotations

import argparse
import dataclasses
import functools
import json
import os
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages" / "retrieval" / "src"))

from visualseek_retrieval.categories import load_descriptions  # noqa: E402
from visualseek_retrieval.encoder import TextEncoder  # noqa: E402
from visualseek_retrieval.entity_review import EntityReview  # noqa: E402
from visualseek_retrieval.fusion import asset_payloads, load_visual  # noqa: E402
from visualseek_retrieval.pipeline import SearchPipeline, Settings  # noqa: E402
from visualseek_retrieval.qdrant_store import COLLECTION, connect  # noqa: E402

SEARCH = ROOT / "data/processed/products-v1/search"
REVIEW = ROOT / "services/api/src/visualseek_api/application/products-v1-identity-review.json"
EVAL = ROOT / "eval/category-v1/queries.jsonl"
MULTI = ROOT / "eval/category-v1/multi.jsonl"
K = 10


def load_env() -> None:
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        key, sep, value = line.partition("=")
        if sep and key.strip() and not key.startswith("#"):
            os.environ.setdefault(key.strip(), value.strip().strip('"'))


@functools.cache
def visual_parts(device: str) -> tuple:
    """(image encoder, fusion, payloads) as the API builds them; loaded once per process."""
    graph = json.loads((SEARCH / "graph.json").read_text(encoding="utf-8"))
    categories = json.loads((SEARCH / "entity_categories.json").read_text(encoding="utf-8"))
    review = EntityReview.load(graph, REVIEW.read_bytes())
    rows = [
        json.loads(line)
        for line in (SEARCH / "doc_texts.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    image_encoder, fusion = load_visual(SEARCH, [row["asset_id"] for row in rows], device)
    return image_encoder, fusion, asset_payloads(rows, review, categories)


def build_pipeline(
    settings: Settings,
    encoder,
    client,
    visual: bool = True,
    device: str = "cpu",
    descriptions: bool = True,
) -> SearchPipeline:
    """The pipeline the API serves. ``visual=False`` leaves out the SigLIP2 fusion channel
    (P6) entirely; ``Settings(visual_fusion=False)`` keeps it loaded but unused."""
    graph = json.loads((SEARCH / "graph.json").read_text(encoding="utf-8"))
    short = json.loads((SEARCH / "short_aliases.json").read_text(encoding="utf-8"))
    categories = json.loads((SEARCH / "entity_categories.json").read_text(encoding="utf-8"))
    image_encoder, fusion, payloads = visual_parts(device) if visual else (None, None, None)
    return SearchPipeline(
        graph,
        np.load(SEARCH / "entity_dense.npy"),
        encoder,
        client,
        COLLECTION,
        settings=settings,
        review=EntityReview.load(graph, REVIEW.read_bytes()),
        short_aliases=short["aliases"],
        suppressed_aliases=short["suppressed"]["aliases"],
        categories=categories,
        category_descriptions=(
            load_descriptions(ROOT / "graph/taxonomy-v1.json", categories["taxonomy_sha256"])
            if descriptions
            else None
        ),
        image_encoder=image_encoder,
        fusion=fusion,
        payloads=payloads,
    )


def run(pipeline: SearchPipeline, queries: list[dict]) -> list[dict]:
    rows = []
    for query in queries:
        response = pipeline.search(query["query"])
        hits = response["results"][:K]
        if "intents" in query:  # "A và B": every intent should get its share of the top 10
            per_intent = [
                sum(bool(set(hit["categories"] or []) & set(intent)) for hit in hits)
                for intent in query["intents"]
            ]
            share = K // len(query["intents"])
            groups = (response["trace"].get("category") or {}).get("groups", [])
            rows.append(
                {
                    "id": query["id"],
                    "query": query["query"],
                    "kind": query["kind"],
                    "all_intents": all(n > 0 for n in per_intent),
                    "balanced": float(np.mean([min(n, share) / share for n in per_intent])),
                    "per_intent": per_intent,
                    "groups": groups,
                }
            )
            continue
        gold_categories, gold_tiers = set(query["categories"]), set(query["tiers"] or [])
        relevant = [
            bool(set(hit["categories"] or []) & gold_categories)
            and (not gold_tiers or bool(set(hit["market_tiers"] or []) & gold_tiers))
            for hit in hits
        ]
        trace = response["trace"].get("category") or {}
        rows.append(
            {
                "id": query["id"],
                "query": query["query"],
                "kind": query["kind"],
                "p10": sum(relevant) / K,
                "any": any(relevant),
                "detected": [c["id"] for c in trace.get("categories", [])],
                "source": trace.get("source"),
                "tiers": trace.get("tiers", []),
                "status": response["trace"]["status"],
                "out_of_scope": response["out_of_scope"],
            }
        )
    return rows


def summary(rows: list[dict]) -> dict:
    def mean(subset: list[dict], key: str) -> float:
        return round(float(np.mean([r[key] for r in subset])), 3) if subset else float("nan")

    if rows and "balanced" in rows[0]:
        return {
            "all_intents@10": mean(rows, "all_intents"),
            "balanced@10": mean(rows, "balanced"),
            "grouped": sum(bool(r["groups"]) for r in rows),
            "queries": len(rows),
        }
    tier = [r for r in rows if r["kind"] == "tier"]
    return {
        "p@10": mean(rows, "p10"),
        "any@10": mean(rows, "any"),
        "p@10 category": mean([r for r in rows if r["kind"] == "category"], "p10"),
        "p@10 tier": mean(tier, "p10"),
        "detected": sum(bool(r["detected"]) for r in rows),
        "out_of_scope": sum(r["out_of_scope"] for r in rows),
        "queries": len(rows),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--grid", type=float, nargs="*", default=[])
    parser.add_argument("--no-knn", action="store_true")
    parser.add_argument("--prefetch", action="store_true", help="add the category dense channel")
    parser.add_argument("--lex-share", type=float, default=Settings().lexicon_min_share)
    parser.add_argument("--multi", action="store_true", help="eval/category-v1/multi.jsonl")
    parser.add_argument(
        "--no-descriptions", action="store_true", help="lexicon without taxonomy descriptions (A/B)"
    )
    parser.add_argument("--out", type=Path)
    parser.add_argument("--show", action="store_true", help="print every query of the last run")
    args = parser.parse_args()
    load_env()
    path = MULTI if args.multi else EVAL
    queries = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
    encoder = TextEncoder(device="cpu")
    client = connect(os.environ["QDRANT_URL"], os.environ.get("QDRANT_API_KEY"), timeout=60)
    base = dataclasses.replace(
        Settings(), lexicon_min_share=args.lex_share, category_prefetch=args.prefetch
    )
    if args.no_knn:
        base = dataclasses.replace(base, knn_min_share=2.0)
    configs = {
        "off": dataclasses.replace(base, w_category=0.0, w_tier=0.0, segment_categories=False)
    }
    for weight in args.grid or [base.w_category]:
        configs[f"w={weight} whole-query"] = dataclasses.replace(
            base, w_category=weight, w_tier=weight, segment_categories=False
        )
        configs[f"w={weight} segments"] = dataclasses.replace(
            base, w_category=weight, w_tier=weight, segment_categories=True
        )
    results = {}
    for name, settings in configs.items():
        pipeline = build_pipeline(
            settings, encoder, client, descriptions=not args.no_descriptions
        )
        results[name] = run(pipeline, queries)
        print(name, json.dumps(summary(results[name])))
    last = list(results)[-1]
    if args.show:
        for off, on in zip(results["off"], results[last], strict=True):
            if "balanced" in on:
                print(f"{on['query'][:48]:48s} {off['per_intent']} -> {on['per_intent']}")
                continue
            print(
                f"{on['query'][:36]:36s} off {off['p10']:.1f} -> {on['p10']:.1f} "
                f"{on['detected']} {on['source']} {on['tiers']} {on['status']}"
            )
    if args.out:
        args.out.write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
