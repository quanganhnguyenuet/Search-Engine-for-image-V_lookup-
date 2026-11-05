"""Metrics for a human-judged candidate pool; never assume unjudged means irrelevant."""

import math
from collections import defaultdict


def evaluate_pool(samples: list[dict], mode: str, k: int = 10) -> dict:
    groups = defaultdict(list)
    outside = []
    for sample in samples:
        if type(sample["answerable"]) is not bool:
            raise ValueError("Confirm answerable as true/false after reviewing corpus candidates")
        judgments = sample["judgments"]
        ids = sample["rankings"][mode][:k]
        if len(ids) != len(set(ids)):
            raise ValueError("Duplicate result IDs")
        if any(type(value) is not int or value not in (0, 1, 2)
               for value in judgments.values()):
            raise ValueError("Every pooled candidate needs a human grade 0, 1 or 2")
        if any(id_ not in judgments for id_ in ids):
            raise ValueError("Unjudged result; expand and label the pool before evaluation")
        if not sample["answerable"]:
            outside.append({"query_id": sample["query_id"], "returned": len(ids)})
            continue
        if 2 not in judgments.values():
            raise ValueError("An answerable query needs at least one grade-2 relevant image")
        grades = [judgments[id_] for id_ in ids]
        ideal = sorted(judgments.values(), reverse=True)[:k]

        def dcg(values):
            return sum((2 ** grade - 1) / math.log2(rank + 2)
                       for rank, grade in enumerate(values))

        row = {"hit": int(2 in grades), "ndcg": dcg(grades) / dcg(ideal)}
        groups["all"].append(row)
        groups[sample["group"]].append(row)

    def summarize(rows):
        return {"queries": len(rows), "hits": sum(row["hit"] for row in rows),
                f"HitRate@{k}": sum(row["hit"] for row in rows) / len(rows),
                f"nDCG@{k}": sum(row["ndcg"] for row in rows) / len(rows)}

    return {"mode": mode, "metrics": {group: summarize(rows) for group, rows in groups.items()},
            "outside_domain": outside,
            "judgment_scope": "human-judged candidate pool, not exhaustive corpus relevance"}
