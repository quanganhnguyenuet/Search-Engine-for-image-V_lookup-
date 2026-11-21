from types import SimpleNamespace

import pytest

from visualseek_evaluation.image import (
    percentiles,
    prepare_queries,
    score_ranking,
    validate_queries,
)


def test_recall_uses_all_positives_instead_of_k_or_hit_rate():
    metrics = score_ranking(["a", "x", "b"], ["a", "b", "c", "d"], [1, 3, 10])
    assert metrics["Recall@3"] == 0.5
    assert metrics["Precision@3"] == pytest.approx(2 / 3)
    assert metrics["HitRate@3"] == 1
    assert metrics["Recall@10"] == 0.5
    assert metrics["Precision@10"] == 0.2
    assert score_ranking([], ["a"], [5])["Recall@5"] == 0
    with pytest.raises(ValueError):
        score_ranking(["a", "a"], ["a"], [5])
    with pytest.raises(ValueError):
        score_ranking(["a"], [], [5])


def test_proxy_queries_are_balanced_reproducible_and_do_not_filter_by_query_category():
    index = SimpleNamespace(assets=[
        {"asset_id": f"{c}{i}", "keyword": "Áo dài", "category": c, "split": "test"}
        for c in ("a", "b") for i in range(5)
    ] + [{"asset_id": "excluded", "keyword": "Áo dài", "category": "a",
          "split": "test", "search_excluded": True}])
    queries = prepare_queries(index, count=6, seed=42)
    assert queries == prepare_queries(index, count=6, seed=42)
    assert [row["query_category"] for row in queries] == ["a", "b"] * 3
    assert all("category" not in row and row["ground_truth_source"] == "keyword_proxy"
               for row in queries)
    assert all(len(row["relevant_asset_ids"]) == 4 for row in queries)
    validate_queries(index, queries)
    for relevant in ([], ["excluded"], [queries[0]["asset_id"]], ["unknown"], ["a1", "a1"]):
        with pytest.raises(ValueError):
            validate_queries(index, [{**queries[0], "relevant_asset_ids": relevant}])
    with pytest.raises(ValueError):
        validate_queries(index, [queries[0], queries[0]])
    with pytest.raises(ValueError):
        prepare_queries(index, count=11)


def test_ground_truth_cannot_include_positives_outside_search_filter():
    index = SimpleNamespace(assets=[
        {"asset_id": "query", "category": "a"}, {"asset_id": "positive", "category": "b"}])
    query = {"query_id": "q", "asset_id": "query", "relevant_asset_ids": ["positive"],
             "ground_truth_source": "human", "category": "a"}
    with pytest.raises(ValueError, match="category filter"):
        validate_queries(index, [query])
    del query["category"]
    validate_queries(index, [query])


def test_p95_is_linear_percentile_and_empty_samples_are_not_zero_latency():
    assert percentiles(list(range(1, 101)))["p95"] == pytest.approx(95.05)
    assert percentiles([]) is None
