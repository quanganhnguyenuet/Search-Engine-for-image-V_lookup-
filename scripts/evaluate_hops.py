"""Score the eval/hops-v1 runs stored in the pool against the human judgments.

Every configuration of scripts/build_eval_hops.py is scored on the same judged queries; an
image takes the best grade of its products. Paired differences against ``current`` come with
a bootstrap 95 % interval.

  multi_hop     P@10 (grade 2), nDCG@10 (gain 2^grade - 1)
  multi_intent  P@10 (any part answered), all_parts@10 (every part has an image),
                balanced@10 (each part's share of the top 10, capped at 10 // parts)

  python scripts/evaluate_hops.py
"""

from __future__ import annotations

import json
import math
from collections import defaultdict
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
QUERIES = ROOT / "eval/hops-v1/queries.jsonl"
POOL = ROOT / "data/processed/products-v1/eval/hops-v1/pool.jsonl"
JUDGMENTS = ROOT / "eval/hops-v1/judgments.jsonl"
K = 10
SEED = 7


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def image_grades(pool_row: dict, grades: dict) -> dict[str, tuple[int, set[int]]]:
    """asset id -> (best grade, parts answered) over the products shown on the image."""
    out: dict[str, tuple[int, set[int]]] = {}
    for entity in pool_row["entities"]:
        grade = grades.get(entity["entity_id"], {"grade": 0, "intent": None})
        for asset in entity["assets"]:
            best, parts = out.get(asset["asset_id"], (0, set()))
            if grade["grade"] == 2 and grade.get("intent") is not None:
                parts = parts | {grade["intent"]}
            out[asset["asset_id"]] = (max(best, grade["grade"]), parts)
    return out


def score(query: dict, ranked: list[str], graded: dict) -> dict:
    top = [graded.get(a, (0, set())) for a in ranked[:K]]
    precision = sum(g == 2 for g, _ in top) / K
    if query["kind"] == "multi_hop":
        dcg = sum((2**g - 1) / math.log2(i + 2) for i, (g, _) in enumerate(top))
        ideal = sorted((g for g, _ in graded.values()), reverse=True)[:K]
        idcg = sum((2**g - 1) / math.log2(i + 2) for i, g in enumerate(ideal))
        return {"p10": precision, "ndcg10": dcg / idcg if idcg else 0.0}
    parts = len(query["intents"])
    share = max(1, K // parts)
    counts = [sum(k in p for _, p in top) for k in range(parts)]
    return {
        "p10": precision,
        "all_parts10": float(all(counts)),
        "balanced10": float(np.mean([min(c, share) / share for c in counts])),
    }


def bootstrap(deltas: list[float]) -> list[float]:
    if not deltas:
        return [float("nan")] * 2
    data = np.asarray(deltas)
    rng = np.random.default_rng(SEED)
    means = data[rng.integers(0, len(data), (10_000, len(data)))].mean(axis=1)
    return [round(float(np.percentile(means, 2.5)), 3), round(float(np.percentile(means, 97.5)), 3)]


def main() -> None:
    queries = {q["qid"]: q for q in read_jsonl(QUERIES)}
    pool = {p["qid"]: p for p in read_jsonl(POOL)}
    judged = {row["qid"]: row for row in read_jsonl(JUDGMENTS)}
    usable = [q for q in judged if judged[q].get("flag") != "bad_query" and q in pool]
    print(f"judged {len(judged)}/{len(queries)} queries, scored {len(usable)} (bad_query skipped)")
    if not usable:
        return
    per: dict[str, dict[str, dict[str, list[float]]]] = defaultdict(lambda: defaultdict(dict))
    for qid in usable:
        query, row = queries[qid], pool[qid]
        graded = image_grades(row, judged[qid]["grades"])
        for config, ranked in row["runs"].items():
            for metric, value in score(query, ranked, graded).items():
                per[query["kind"]][metric].setdefault(config, []).append(value)
    report = {}
    for kind, metrics in per.items():
        report[kind] = {}
        for metric, by_config in metrics.items():
            base = by_config["current"]
            report[kind][metric] = {
                config: {
                    "mean": round(float(np.mean(values)), 3),
                    **(
                        {}
                        if config == "current"
                        else {
                            "delta_vs_current": round(float(np.mean(values) - np.mean(base)), 3),
                            "ci95": bootstrap([v - b for v, b in zip(values, base, strict=True)]),
                        }
                    ),
                }
                for config, values in by_config.items()
            }
            report[kind]["n"] = len(base)
    print(json.dumps(report, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
