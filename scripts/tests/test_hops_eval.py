import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from evaluate_hops import image_grades, score  # noqa: E402
from hops_judge_server import Grade, Judgment, validate  # noqa: E402

POOL = {
    "entities": [
        {"entity_id": "a", "assets": [{"asset_id": "1"}, {"asset_id": "2"}]},
        {"entity_id": "b", "assets": [{"asset_id": "2"}, {"asset_id": "3"}]},
    ]
}


def test_image_takes_the_best_grade_of_its_products():
    graded = image_grades(POOL, {"a": {"grade": 2, "intent": 0}, "b": {"grade": 2, "intent": 1}})
    assert graded["2"] == (2, {0, 1}) and graded["1"] == (2, {0}) and graded["3"] == (2, {1})


def test_multi_intent_counts_parts_and_balance():
    query = {"kind": "multi_intent", "intents": ["x", "y"]}
    graded = image_grades(POOL, {"a": {"grade": 2, "intent": 0}, "b": {"grade": 0}})
    result = score(query, ["1", "2", "3"], graded)
    assert result["all_parts10"] == 0.0 and result["p10"] == 0.2
    assert result["balanced10"] == (2 / 5 + 0) / 2


def test_multi_hop_ndcg_is_one_for_the_ideal_order():
    query = {"kind": "multi_hop", "intents": None}
    graded = image_grades(POOL, {"a": {"grade": 2, "intent": None}, "b": {"grade": 0}})
    assert score(query, ["1", "2", "3"], graded)["ndcg10"] == 1.0


def test_validate_requires_every_product_and_a_part_for_relevant_intents():
    query = {"kind": "multi_intent", "intents": ["x", "y"]}
    full = {"a": Grade(grade=2, intent=1), "b": Grade(grade=0)}
    assert validate(Judgment(qid="m01", grades=full), query, POOL) is None
    missing = Judgment(qid="m01", grades={"a": Grade(grade=0)})
    assert "every product" in validate(missing, query, POOL)
    no_part = Judgment(qid="m01", grades={"a": Grade(grade=2), "b": Grade(grade=0)})
    assert "part" in validate(no_part, query, POOL)
