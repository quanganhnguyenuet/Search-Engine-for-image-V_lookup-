"""Image-query ground truth and macro recall, independent of the search implementation."""

import random
import unicodedata
from collections import defaultdict

import numpy as np

from visualseek_retrieval.artifact import ArtifactIndex


def prepare_queries(index: ArtifactIndex, *, count=200, seed=42, split="test") -> list[dict]:
    """Balanced category sampling; the same subject label is only a relevance proxy."""
    groups = defaultdict(list)
    for asset in index.assets:
        label = " ".join(unicodedata.normalize("NFC", asset.get("keyword", ""))
                         .replace("_", " ").casefold().split())
        if not asset.get("search_excluded") and label:
            groups[(asset["category"], label)].append(asset)
    candidates = defaultdict(list)
    for (category, label), members in sorted(groups.items()):
        if len(members) < 2:
            continue
        ids = sorted(member["asset_id"] for member in members)
        for member in sorted(members, key=lambda row: row["asset_id"]):
            if split != "all" and member.get("split") != split:
                continue
            candidates[category].append({
                "query_id": member["asset_id"], "asset_id": member["asset_id"],
                "query_category": category, "subject": label,
                "query_split": member.get("split"),
                "ground_truth_source": "keyword_proxy",
                "relevant_asset_ids": [id_ for id_ in ids if id_ != member["asset_id"]],
            })
    if count < 1 or sum(map(len, candidates.values())) < count:
        raise ValueError("Requested query count exceeds eligible queries or is not positive")
    rng = random.Random(seed)
    for category in sorted(candidates):
        rng.shuffle(candidates[category])
    result = []
    while len(result) < count:
        for category in sorted(candidates):
            if candidates[category] and len(result) < count:
                result.append(candidates[category].pop())
    return result


def validate_queries(index: ArtifactIndex, queries: list[dict]) -> None:
    if not queries or len({row["query_id"] for row in queries}) != len(queries):
        raise ValueError("Queries must be nonempty and have unique query_id values")
    assets = {a["asset_id"]: a for a in index.assets if not a.get("search_excluded")}
    for row in queries:
        source = row["asset_id"]
        relevant = row["relevant_asset_ids"]
        if source not in assets or not row.get("ground_truth_source"):
            raise ValueError(
                "Query must reference a searchable asset and declare ground truth source")
        if (not isinstance(relevant, list) or not relevant or source in relevant
                or len(relevant) != len(set(relevant)) or not set(relevant) <= assets.keys()):
            raise ValueError("Relevant IDs must be unique, searchable, nonempty and exclude self")
        category = row.get("category")  # query_category is descriptive, never a search filter.
        if category and any(assets[id_]["category"] != category for id_ in relevant):
            raise ValueError("Ground truth contains positives outside the category filter")


def score_ranking(ranking: list[str], relevant: list[str], ks: list[int]) -> dict[str, float]:
    if not relevant or len(ranking) != len(set(ranking)):
        raise ValueError("Nonempty ground truth and duplicate-free ranking required")
    positives = set(relevant)
    result = {}
    for k in ks:
        if k < 1:
            raise ValueError("k must be positive")
        hits = len(set(ranking[:k]) & positives)
        result[f"Recall@{k}"] = hits / len(positives)
        result[f"HitRate@{k}"] = float(hits > 0)
        result[f"Precision@{k}"] = hits / k
    return result


def percentiles(values: list[float]) -> dict | None:
    if not values:
        return None
    return {f"p{p}": float(np.percentile(values, p, method="linear")) for p in (50, 90, 95, 99)}
