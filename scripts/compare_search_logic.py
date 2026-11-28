"""Run the search with condition search (2026-11-28) off and on, over every eval set.

The rule is a switch of one pipeline (Settings.condition_search), so both sides see the same
data. Query vectors are cached: the first run loads bge-m3 and SigLIP2
(on a 4 GB card, stop the API first), later runs need no model and can run beside the API.

  python scripts/compare_search_logic.py

Writes report.md (drug queries side by side) and prints what changed in the older sets: a
query whose top 10 is unchanged scores exactly as before.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages" / "retrieval" / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from evaluate_category import REVIEW, SEARCH, load_env  # noqa: E402
from evaluate_v2 import recall_at_10  # noqa: E402

from visualseek_retrieval.categories import load_descriptions  # noqa: E402
from visualseek_retrieval.entity_review import EntityReview  # noqa: E402
from visualseek_retrieval.fusion import asset_payloads, load_visual  # noqa: E402
from visualseek_retrieval.pipeline import SearchPipeline, Settings  # noqa: E402
from visualseek_retrieval.qdrant_store import COLLECTION, connect  # noqa: E402


class Cached:
    """Encoder that remembers vectors; loads the real model only for a text it has not seen."""

    def __init__(self, name: str, store: dict, make) -> None:
        self.name, self.store, self.make, self.model_ = name, store, make, None

    def encode(self, texts: list[str], **_) -> np.ndarray:
        missing = [t for t in texts if f"{self.name}:{t}" not in self.store]
        if missing:
            if self.model_ is None:
                print(f"loading the {self.name} model", flush=True)
                self.model_ = self.make()
            for text, vector in zip(missing, self.model_.encode(missing), strict=True):
                self.store[f"{self.name}:{text}"] = np.asarray(vector, dtype=np.float32)
        return np.stack([self.store[f"{self.name}:{t}"] for t in texts])


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def ndcg(ids: list[str], grades: dict[str, int]) -> float:
    def dcg(values: list[int]) -> float:
        return sum((2**g - 1) / math.log2(i + 2) for i, g in enumerate(values))

    ideal = dcg(sorted(grades.values(), reverse=True)[:10])
    return dcg([grades.get(a, 0) for a in ids]) / ideal if ideal else 0.0


def top(result: dict) -> list[str]:
    return [h["asset_id"] for h in result["results"]]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out", type=Path, default=ROOT / "data/processed/products-v1/eval/search-logic"
    )
    parser.add_argument("--device", default="cuda")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    load_env()
    cache_path = args.out / "vectors.npz"
    store = dict(np.load(cache_path)) if cache_path.exists() else {}

    def text_model():
        from visualseek_retrieval.encoder import TextEncoder

        return TextEncoder(device=args.device)

    def image_model():
        from visualseek_retrieval.image_text import ImageTextEncoder

        return ImageTextEncoder(device=args.device)

    graph = json.loads((SEARCH / "graph.json").read_text(encoding="utf-8"))
    short = json.loads((SEARCH / "short_aliases.json").read_text(encoding="utf-8"))
    categories = json.loads((ROOT / "graph/entity_categories.json").read_text(encoding="utf-8"))
    live_review = SEARCH / "identity_review.json"
    review = EntityReview.load(
        graph, (live_review if live_review.exists() else REVIEW).read_bytes()
    )
    rows = read_jsonl(SEARCH / "doc_texts.jsonl")
    image_encoder, fusion = load_visual(
        SEARCH,
        [r["asset_id"] for r in rows],
        args.device,
        encoder=Cached("image", store, image_model),
    )
    client = connect(os.environ["QDRANT_URL"], os.environ.get("QDRANT_API_KEY"), timeout=60)
    text_encoder = Cached("text", store, text_model)
    payloads = asset_payloads(rows, review, categories)
    descriptions = load_descriptions(
        ROOT / "graph/taxonomy-v1.json", categories["taxonomy_sha256"]
    )

    def build(**switches) -> SearchPipeline:
        return SearchPipeline(
            graph,
            np.load(SEARCH / "entity_dense.npy"),
            text_encoder,
            client,
            COLLECTION,
            settings=Settings(**switches),
            review=review,
            short_aliases=short["aliases"],
            suppressed_aliases=short["suppressed"]["aliases"],
            categories=categories,
            category_descriptions=descriptions,
            image_encoder=image_encoder,
            fusion=fusion,
            payloads=payloads,
        )

    old, new = build(condition_search=False), build()
    names = {e["id"]: e["name"] for e in graph["entities"]}
    silver = ROOT / "data/processed/products-v1/eval/silver-v1.1"
    sets = {
        "silver-v1.1": read_jsonl(silver / "dev.jsonl") + read_jsonl(silver / "test.jsonl"),
        "category-v1": read_jsonl(ROOT / "eval/category-v1/queries.jsonl"),
        "category-v1-multi": read_jsonl(ROOT / "eval/category-v1/multi.jsonl"),
        "descriptive-v1": read_jsonl(ROOT / "eval/descriptive-v1/queries.jsonl"),
        "hops-v1": read_jsonl(ROOT / "eval/hops-v1/queries.jsonl"),
        "drug-v1": read_jsonl(ROOT / "eval/drug-v1/queries.jsonl"),
    }
    grades = {
        r["qid"]: r["grades"]
        for r in read_jsonl(ROOT / "eval/descriptive-v1/judgments.jsonl")
    }
    summary: dict[str, dict] = {}
    report = ["# drug-v1: before and after", ""]
    try:
        for name, queries in sets.items():
            before, after, changed = [], [], []
            for item in queries:
                a, b = old.search(item["query"]), new.search(item["query"])
                differs = top(a) != top(b)
                if differs:
                    changed.append(item["query"])
                if name == "silver-v1.1":
                    for bucket, result in ((before, a), (after, b)):
                        bucket.append(
                            recall_at_10(item, top(result))
                            if item.get("rel_by_intent")
                            else float(not top(result))
                        )
                elif name == "category-v1" and item.get("categories"):
                    gold = set(item["categories"])
                    for bucket, result in ((before, a), (after, b)):
                        hits = result["results"]
                        bucket.append(
                            sum(bool(gold & set(h.get("categories") or [])) for h in hits) / 10
                        )
                elif name == "descriptive-v1" and item["qid"] in grades:
                    judged = grades[item["qid"]]
                    before.append(ndcg(top(a), judged))
                    after.append(ndcg(top(b), judged))
                    if differs:
                        print(
                            f"  descriptive changed: {item['query']!r} nDCG "
                            f"{before[-1]:.2f} -> {after[-1]:.2f}, judged in new top 10: "
                            f"{sum(x in judged for x in top(b))}/{len(top(b))}"
                        )
                if name == "drug-v1":
                    report.append(f"## {item['query']}")
                    for label, result in (("Trước", a), ("Sau", b)):
                        listed = [
                            " + ".join(names.get(e, e) for e in h["entity_ids"])
                            for h in result["results"]
                        ]
                        shown = "; ".join(
                            f"{n} ×{listed.count(n)}" if listed.count(n) > 1 else n
                            for n in dict.fromkeys(listed)
                        )
                        status = result["trace"]["status"]
                        report.append(f"- **{label}** ({status}, {len(listed)} ảnh): {shown}")
                    report.append("")
            summary[name] = {"queries": len(queries), "changed": len(changed)}
            if before:
                summary[name]["before"] = round(float(np.mean(before)), 4)
                summary[name]["after"] = round(float(np.mean(after)), 4)
            print(name, summary[name], flush=True)
            if name not in ("descriptive-v1", "drug-v1"):
                for query in changed[:15]:
                    print(f"  changed: {query}")
    finally:
        np.savez(cache_path, **store)
    (args.out / "report.md").write_text("\n".join(report), encoding="utf-8")
    (args.out / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    print(f"report: {args.out / 'report.md'}")


if __name__ == "__main__":
    main()
