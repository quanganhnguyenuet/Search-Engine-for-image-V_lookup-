"""How much of a rebuilt hops pool the human judgments cover, and what that does to P@10.

A pool rebuilt after a code change can show products nobody graded. ``evaluate_hops.py`` scores
them as wrong, so its P@10 is a lower bound until they are graded. This prints, for the
``current`` configuration of a pool, aggregates only (a holdout set must not be read query by
query):

  cards        products of the pool without a grade, and the queries they appear in
  slots        share of the top-10 slots that show an ungraded product
  P@10 lower   ungraded counted wrong (what evaluate_hops reports)
  P@10 judged  relevant / graded slots (ungraded slots left out)
  P@10 upper   ungraded counted relevant

  python scripts/hops_coverage.py --set hops-v2-holdout --pool runs/pool_current.jsonl
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
from evaluate_hops import K, read_jsonl

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--set", default="hops-v1")
    parser.add_argument("--pool", type=Path, help="pool file (default: the set's pool)")
    args = parser.parse_args()
    pool_path = args.pool or ROOT / f"data/processed/products-v1/eval/{args.set}/pool.jsonl"
    pool = {p["qid"]: p for p in read_jsonl(pool_path)}
    judged = {r["qid"]: r for r in read_jsonl(ROOT / "eval" / args.set / "judgments.jsonl")}
    queries = {q["qid"]: q for q in read_jsonl(ROOT / "eval" / args.set / "queries.jsonl")}
    by_kind: dict[str, dict[str, list[float]]] = {}
    ungraded_cards = affected = scored = 0
    for qid, row in pool.items():
        if qid not in judged or judged[qid].get("flag") == "bad_query":
            continue
        scored += 1
        grades = judged[qid]["grades"]
        owners: dict[str, list[str]] = {}
        for entity in row["entities"]:
            for asset in entity["assets"]:
                owners.setdefault(asset["asset_id"], []).append(entity["entity_id"])
        new = [e["entity_id"] for e in row["entities"] if e["entity_id"] not in grades]
        ungraded_cards += len(new)
        affected += bool(new)
        relevant = unknown = 0
        for asset in row["runs"]["current"][:K]:
            entities = owners.get(asset, [])
            graded = [grades[e]["grade"] for e in entities if e in grades]
            if not graded:
                unknown += 1
            elif max(graded) == 2:
                relevant += 1
        known = K - unknown
        kind = queries[qid]["kind"]
        stats = by_kind.setdefault(kind, {"lower": [], "judged": [], "upper": [], "slots": []})
        stats["lower"].append(relevant / K)
        stats["upper"].append((relevant + unknown) / K)
        stats["judged"].append(relevant / known if known else float("nan"))
        stats["slots"].append(unknown / K)
    print(
        f"{args.set}: {scored} judged queries; {ungraded_cards} ungraded products in "
        f"{affected} of them"
    )
    for kind, stats in sorted(by_kind.items()):
        mean = {k: float(np.nanmean(v)) for k, v in stats.items()}
        print(
            f"  {kind:13s} n={len(stats['lower']):2d}  ungraded slots {mean['slots']:.3f}  "
            f"P@10 lower {mean['lower']:.3f}  judged-only {mean['judged']:.3f}  "
            f"upper {mean['upper']:.3f}"
        )


if __name__ == "__main__":
    main()
