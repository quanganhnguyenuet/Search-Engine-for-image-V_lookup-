"""Random seeds for a held-out hops set, drawn from parts of the corpus hops-v1 never touches.

Writing new queries by hand, after weeks of fixing the pipeline on hops-v1, tends to pick
brands and phrasings the pipeline already handles. This script picks the material instead:

  brands      with >= 3 products and >= 2 product types, not named in any hops-v1 query
  categories  not holding any product of those hop brands, for generic multi_intent parts

The query author then writes one query per sampled item without running the pipeline.

  python scripts/sample_hops_holdout.py --seed 2026 > candidates.md
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import unicodedata
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SEARCH = ROOT / "data/processed/products-v1/search"
USED = ROOT / "eval/hops-v1/queries.jsonl"


def fold(text: str) -> str:
    text = unicodedata.normalize("NFD", text.lower().replace("đ", "d"))
    kept = (c if c.isalnum() else " " for c in text if not unicodedata.combining(c))
    return " " + "".join(kept) + " "


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=2026)
    parser.add_argument("--brands", type=int, default=30)
    parser.add_argument("--categories", type=int, default=16)
    args = parser.parse_args()
    graph = json.loads((SEARCH / "graph.json").read_text(encoding="utf-8"))
    cats = json.loads((SEARCH / "entity_categories.json").read_text(encoding="utf-8"))
    info = cats["entities"]
    used = " ".join(
        fold(json.loads(line)["query"]) for line in USED.read_text("utf-8").splitlines() if line
    )
    products = defaultdict(list)
    for entity in graph["entities"]:
        if entity["brand_id"] and entity["id"] in info:
            products[entity["brand_id"]].append(entity["id"])
    brands = {b["id"]: b for b in graph["brands"]}
    eligible = []
    for brand_id, ids in products.items():
        names = [brands[brand_id]["name"], *brands[brand_id]["aliases"]]
        if any(fold(n).strip() and fold(n) in used for n in names):
            continue
        types = {info[i]["product_type"] for i in ids}
        if len(ids) >= 3 and len(types) >= 2:
            eligible.append(brand_id)
    rng = random.Random(args.seed)
    picked = rng.sample(sorted(eligible), min(args.brands, len(eligible)))
    taken = {info[i]["category"] for b in picked for i in products[b]}
    by_category = defaultdict(list)
    for entity_id, row in info.items():
        by_category[row["category"]].append(entity_id)
    free = sorted(c for c, ids in by_category.items() if c not in taken and len(ids) >= 3)
    generic = rng.sample(free, min(args.categories, len(free)))

    out = sys.stdout
    out.write(f"# hops holdout candidates (seed {args.seed}, {len(eligible)} eligible brands)\n\n")
    out.write("## Brands\n\n")
    for brand_id in picked:
        out.write(f"### {brands[brand_id]['name']} ({brand_id})\n")
        for entity_id in sorted(products[brand_id], key=lambda i: info[i]["product_type"]):
            row = info[entity_id]
            out.write(f"- {row['name']} | {row['product_type']} | {row['category']}\n")
        out.write("\n")
    out.write("## Generic categories\n\n")
    for category in generic:
        sample = rng.sample(by_category[category], min(4, len(by_category[category])))
        types = ", ".join(sorted({info[i]["product_type"] for i in sample}))
        out.write(f"- {category} ({cats['category_names'].get(category, '')}): {types}\n")


if __name__ == "__main__":
    main()
