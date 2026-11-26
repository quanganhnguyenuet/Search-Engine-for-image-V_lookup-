"""Judging pool for eval/hops-v1 (40 hand-written multi-hop and multi-intent queries).

Runs every query through several pipeline configurations in this process and keeps each
configuration's top 10, so the judgments can later score all of them (scripts/evaluate_hops.py):

  current            the API defaults
  no_type_filter     without the requested-product-type filter of brand queries (P1)
  type_expand        the type filter extending hits to their categories
  fill_any           filling the top 10 with any image, not only the targets' categories
  no_graph_prefetch  without the dense channel restricted to the graph targets
  no_graph_boost     without the seed/hop/anchor score adjustment
  content_only       neither, and no category boost or category grouping

The pool groups the union of those images by canonical entity: a person grades a product
once (with a few of its images), not every image.

  python scripts/build_eval_hops.py [--set hops-v2-holdout]
  python scripts/hops_judge_server.py [--set hops-v2-holdout]       # http://127.0.0.1:8013
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "packages" / "retrieval" / "src"))

from evaluate_category import build_pipeline, load_env  # noqa: E402

from visualseek_retrieval.encoder import TextEncoder  # noqa: E402
from visualseek_retrieval.pipeline import Settings  # noqa: E402
from visualseek_retrieval.qdrant_store import connect  # noqa: E402

DEFAULT_SET = "hops-v1"
K = 10
NO_BOOST = {"w_seed": 0.0, "w_dense_seed": 0.0, "w_hop": 0.0, "w_anchor": 0.0}
CONFIGS = {
    "current": {},
    "no_type_filter": {"type_filter": False},
    "type_expand": {"type_expand": True},
    "fill_any": {"fill_related_only": False},
    "no_graph_prefetch": {"graph_prefetch": False},
    "no_graph_boost": NO_BOOST,
    "content_only": {
        **NO_BOOST,
        "graph_prefetch": False,
        "w_category": 0.0,
        "w_tier": 0.0,
        "segment_categories": False,
    },
}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--set", default=DEFAULT_SET, help="query set under eval/")
    parser.add_argument(
        "--configs",
        nargs="*",
        choices=sorted(CONFIGS),
        help="only these configurations (default all); pair a partial pool with --out",
    )
    parser.add_argument("--out", type=Path, help="pool file to write (default: the set's pool)")
    args = parser.parse_args()
    source = ROOT / "eval" / args.set / "queries.jsonl"
    out = args.out or ROOT / "data/processed/products-v1/eval" / args.set / "pool.jsonl"
    if args.configs and "current" not in args.configs:
        parser.error("--configs must include current")
    if args.configs and args.out is None:
        parser.error("a partial --configs pool needs --out, it would replace the full pool")
    load_env()
    queries = [json.loads(line) for line in source.read_text(encoding="utf-8").splitlines() if line]
    encoder = TextEncoder(device="cpu")
    client = connect(os.environ["QDRANT_URL"], os.environ.get("QDRANT_API_KEY"), timeout=60)
    pipelines = {
        name: build_pipeline(dataclasses.replace(Settings(), **change), encoder, client)
        for name, change in CONFIGS.items()
        if not args.configs or name in args.configs
    }
    review = pipelines["current"].review
    names = {e["id"]: e["name"] for e in pipelines["current"].graph["entities"]}
    rows = []
    for query in queries:
        runs, traces, by_entity = {}, {}, {}
        for name, pipeline in pipelines.items():
            response = pipeline.search(query["query"])
            hits = response["results"][:K]
            runs[name] = [hit["asset_id"] for hit in hits]
            trace = response["trace"]
            traces[name] = {
                "status": trace["status"],
                "origins": [n["label"] for n in trace["origins"]],
                "excluded": [n["label"] for n in trace["excluded"]],
                "targets": len(trace["targets"]),
                "negation": trace["negation"],
                "category": [
                    c["label"] for c in (trace.get("category") or {}).get("categories", [])
                ],
            }
            for rank, hit in enumerate(hits, 1):
                for entity_id in hit["entity_ids"]:
                    canonical = review.canonical(entity_id)
                    entry = by_entity.setdefault(
                        canonical,
                        {
                            "entity_id": canonical,
                            "name": names.get(canonical, canonical),
                            "assets": {},
                            "found_by": {},
                        },
                    )
                    entry["assets"].setdefault(hit["asset_id"], hit["image_path"])
                    best = entry["found_by"].get(name)
                    entry["found_by"][name] = rank if best is None else min(best, rank)
        entities = sorted(
            by_entity.values(),
            key=lambda e: (min(e["found_by"].values()), e["name"]),
        )
        for entity in entities:
            entity["assets"] = [
                {"asset_id": a, "image_path": p} for a, p in entity["assets"].items()
            ]
        rows.append({"qid": query["qid"], "runs": runs, "traces": traces, "entities": entities})
        print(query["qid"], len(entities), "entities", flush=True)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), "utf-8")
    total = sum(len(r["entities"]) for r in rows)
    print(f"{len(rows)} queries, {total} entity cards to grade -> {out}")


if __name__ == "__main__":
    main()
