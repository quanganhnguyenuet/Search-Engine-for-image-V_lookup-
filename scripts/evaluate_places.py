"""Place-query flow ("đặc sản Bến Tre"): the ORIGIN path against content search.

For every Vietnamese place with at least ``--min-food`` food entities whose ORIGIN edge points
at it, the query is ``đặc sản <place>`` and the relevant images are the images of those entities
(``Rel``). The graph path and the content-only path (the place shortcut switched off) are run
on the same pipeline. Metrics follow PLAN_PRODUCTS_V1.md 5.2:

  Recall@10     |R10 & Rel| / min(|Rel|, 10)
  Precision     share of the returned images that are in Rel (fewer than 10 images is not wrong)
  returned      images in the answer (the graph path shows no more than the place has)

Rel comes from the graph's own ORIGIN edges, so this checks that the flow does what the graph
says, not that the edges are right (that is a data review). It is not a human-judged set.

  python scripts/evaluate_places.py [--min-food 3] [--out runs/places.json]
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import os
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "packages" / "retrieval" / "src"))

from evaluate_category import build_pipeline, load_env  # noqa: E402

from visualseek_retrieval.encoder import TextEncoder  # noqa: E402
from visualseek_retrieval.enrichment import is_vietnamese_place  # noqa: E402
from visualseek_retrieval.pipeline import Settings  # noqa: E402
from visualseek_retrieval.qdrant_store import connect  # noqa: E402

K = 10


def relevant_assets(pipeline, entity_ids: set[str]) -> set[str]:
    review = pipeline.review
    return {
        asset
        for asset, payload in pipeline.payloads.items()
        if any(review.canonical(e) in entity_ids for e in payload["entity_ids"])
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--min-food", type=int, default=3)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    load_env()
    encoder = TextEncoder(device="cpu")
    client = connect(os.environ["QDRANT_URL"], os.environ.get("QDRANT_API_KEY"), timeout=60)
    graph_path = build_pipeline(Settings(), encoder, client)
    content_only = build_pipeline(dataclasses.replace(Settings()), encoder, client)
    content_only._expand_place = lambda plan, query: False  # the shortcut off, all else equal

    index = graph_path.enrichment
    rows = []
    for node_id, node in sorted(index.nodes.items()):
        if node["kind"] != "Place" or not is_vietnamese_place(node["label"]):
            continue
        origin = index.by_node.get(("ORIGIN", node_id), ())
        entities = {graph_path.review.canonical(e) for e in origin}
        food = {e for e in entities if graph_path._entity_l1(e) == "food"}
        if len(food) < args.min_food:
            continue
        rel = relevant_assets(graph_path, food)
        query = f"đặc sản {node['label']}"
        row = {
            "place": node["label"],
            "query": query,
            "entities": len(food),
            "rel_images": len(rel),
        }
        for name, pipeline in (("graph", graph_path), ("content", content_only)):
            response = pipeline.search(query)
            top = [hit["asset_id"] for hit in response["results"][:K]]
            got = set(top)
            row[name] = {
                "recall10": len(got & rel) / min(len(rel), K),
                "precision": len(got & rel) / len(top) if top else 0.0,
                "returned": len(top),
                "status": response["trace"]["status"],
            }
        rows.append(row)
        g, c = row["graph"], row["content"]
        print(
            f"{row['place']:12s} food entities {row['entities']:2d} rel images "
            f"{row['rel_images']:3d} | graph R@10 {g['recall10']:.2f} P {g['precision']:.2f} "
            f"({g['returned']} ảnh) | content R@10 {c['recall10']:.2f} P {c['precision']:.2f}",
            flush=True,
        )
    summary = {
        name: {
            "recall10": round(float(np.mean([r[name]["recall10"] for r in rows])), 3),
            "precision": round(float(np.mean([r[name]["precision"] for r in rows])), 3),
        }
        for name in ("graph", "content")
    }
    print(json.dumps({"places": len(rows), **summary}, ensure_ascii=False))
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(
            json.dumps({"rows": rows, "summary": summary}, ensure_ascii=False, indent=1),
            encoding="utf-8",
        )


if __name__ == "__main__":
    main()
