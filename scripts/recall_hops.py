"""Pool-relative Recall@10 of a hops set (PLAN_PRODUCTS_V1.md section 5.2, on judged pools).

The hops sets are judged per product on a pool: the top 10 of every configuration of
scripts/build_eval_hops.py. A relevant image that no configuration found was never shown to
the judge, so ``Rel`` here is the relevant images of the pool, not of the catalogue. The
recall is an upper bound of the real one and is only fair between systems of the same pool.

  Rel          images with grade 2 (multi_intent: grade 2 and the part they answer)
  multi_hop    |R10 & Rel| / min(|Rel|, 10)
  multi_intent (1/k) * sum_i min(|R10 & Rel_i|, s) / min(|Rel_i|, s),  s = 10 // k

A query without any relevant image in the pool has no Recall and is left out (an intent
without one is left out of its query's mean); the report says how many. R10 is the top 10 of
the configuration, as stored in the pool. Only aggregates are printed: a holdout set must
not be read query by query.

  python scripts/recall_hops.py [--set hops-v2-holdout]
  python scripts/recall_hops.py --before-pool <older pool.jsonl>   # same Rel for both runs
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from pathlib import Path

import numpy as np
from evaluate_hops import K, bootstrap, image_grades, read_jsonl

ROOT = Path(__file__).resolve().parents[1]


def relevant(pool_rows: list[dict], grades: dict, parts: int | None) -> list[set[str]]:
    """Relevant asset ids, one set per intent (one set for a multi_hop query).

    Several pools (an older run's) share the grades: an image takes the best grade of its
    products in any of them."""
    best: dict[str, tuple[int, set[int]]] = {}
    for row in pool_rows:
        for asset, (grade, answered) in image_grades(row, grades).items():
            old = best.get(asset, (0, set()))
            best[asset] = (max(old[0], grade), old[1] | answered)
    if parts is None:
        return [{a for a, (g, _) in best.items() if g == 2}]
    return [
        {a for a, (g, answered) in best.items() if g == 2 and i in answered} for i in range(parts)
    ]


def recall(ranked: list[str], rel: list[set[str]], k_intents: int | None) -> float | None:
    got = set(ranked[:K])
    if k_intents is None:
        return len(got & rel[0]) / min(len(rel[0]), K) if rel[0] else None
    share = max(1, K // k_intents)
    parts = [min(len(got & r), share) / min(len(r), share) for r in rel if r]
    return float(np.mean(parts)) if parts else None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--set", default="hops-v1")
    parser.add_argument("--before-pool", type=Path, help="an older pool of the same set")
    args = parser.parse_args()
    queries = {q["qid"]: q for q in read_jsonl(ROOT / "eval" / args.set / "queries.jsonl")}
    pool = {
        p["qid"]: p
        for p in read_jsonl(ROOT / f"data/processed/products-v1/eval/{args.set}/pool.jsonl")
    }
    judged = {r["qid"]: r for r in read_jsonl(ROOT / "eval" / args.set / "judgments.jsonl")}
    older = {p["qid"]: p for p in read_jsonl(args.before_pool)} if args.before_pool else {}
    usable = [
        q for q in queries if q in judged and judged[q].get("flag") != "bad_query" and q in pool
    ]

    scores: dict[str, dict[str, dict[str, float]]] = defaultdict(lambda: defaultdict(dict))
    left_out: dict[str, int] = defaultdict(int)
    for qid in usable:
        query = queries[qid]
        parts = len(query["intents"]) if query["kind"] == "multi_intent" else None
        rows = [pool[qid], *([older[qid]] if qid in older else [])]
        rel = relevant(rows, judged[qid]["grades"], parts)
        runs = dict(pool[qid]["runs"])
        if qid in older:
            runs["before_checkpoint"] = older[qid]["runs"]["current"]
        found = {name: recall(ranked, rel, parts) for name, ranked in runs.items()}
        if found["current"] is None:
            left_out[query["kind"]] += 1
            continue
        for name, value in found.items():
            scores[query["kind"]][name][qid] = value
    for kind, by_config in scores.items():
        base = by_config["current"]
        n_all = sum(queries[q]["kind"] == kind for q in usable)
        print(
            f"== {kind}: Recall@10 on {len(base)} of {n_all} queries "
            f"({left_out[kind]} without a relevant image in the pool)"
        )
        for name, values in by_config.items():
            shared = [q for q in base if q in values]
            mean = float(np.mean([values[q] for q in shared]))
            line = f"  {name:18} {mean:.3f}"
            if name != "current":
                deltas = [values[q] - base[q] for q in shared]
                line += (
                    f"   delta vs current {float(np.mean(deltas)):+.3f}  CI95 {bootstrap(deltas)}"
                )
            else:
                line += f"   CI95 of the mean {bootstrap(list(base.values()))}"
            print(line)


if __name__ == "__main__":
    main()
