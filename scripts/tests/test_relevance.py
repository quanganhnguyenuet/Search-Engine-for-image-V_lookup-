import pytest
from prepare_text_search_queries import draft_queries

from visualseek_evaluation.relevance import evaluate_pool


def sample(ids, judgments, answerable=True):
    return {"query_id": "q1", "group": "attributes", "rankings": {"contextual": ids},
            "judgments": judgments, "answerable": answerable}


def test_relevance_distinguishes_partial_and_fully_relevant():
    report = evaluate_pool([sample(["a", "b"], {"a": 1, "b": 2})], "contextual", k=1)
    metrics = report["metrics"]["all"]
    assert metrics["HitRate@1"] == 0
    assert metrics["nDCG@1"] == pytest.approx(1 / 3)
    best = evaluate_pool([sample(["b", "a"], {"a": 1, "b": 2})], "contextual", k=1)
    assert best["metrics"]["all"]["nDCG@1"] == 1


def test_never_score_unjudged_candidates_as_irrelevant():
    for row in (sample(["a"], {"a": None}), sample(["b"], {"a": 2})):
        with pytest.raises(ValueError):
            evaluate_pool([row], "contextual")


def test_outside_domain_not_counted_as_hit_rate_denominator():
    report = evaluate_pool([sample([], {"a": 0}, answerable=False),
                            sample(["a"], {"a": 2})], "contextual")
    assert report["metrics"]["all"]["queries"] == 1
    assert len(report["outside_domain"]) == 1


def test_draft_queries_have_fixed_slices_without_intent_leakage():
    from collections import Counter

    rows = draft_queries()
    assert len(rows) == len({row["query_id"] for row in rows}) == 200
    assert Counter(row["split"] for row in rows) == {"development": 100, "held-out": 100}
    assert Counter(row["group"] for row in rows) == {
        "objects": 40, "scenes": 40, "attributes": 40, "spelling": 30, "names": 30, "outside": 20}
    dev = {row["intent_id"] for row in rows if row["split"] == "development"}
    held = {row["intent_id"] for row in rows if row["split"] == "held-out"}
    assert not dev.intersection(held)
    assert all(row["answerable"] is None for row in rows)
