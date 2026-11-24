import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402
from recall_hops import recall, relevant  # noqa: E402


def ids(prefix: str, n: int) -> set[str]:
    return {f"{prefix}{i}" for i in range(n)}


def test_single_recall_divides_by_the_smaller_of_rel_and_ten():
    # PLAN_PRODUCTS_V1.md 5.2: |Rel| = 4 and 3 hits -> 0.75; |Rel| = 30 and 7 hits -> 0.70.
    assert recall(["r0", "r1", "r2", "x"], [ids("r", 4)], None) == pytest.approx(0.75)
    ranked = [f"r{i}" for i in range(7)] + ["x0", "x1", "x2"]
    assert recall(ranked, [ids("r", 30)], None) == pytest.approx(0.7)


def test_only_the_first_ten_count_and_an_empty_rel_has_no_recall():
    ranked = [f"x{i}" for i in range(10)] + ["r0"]
    assert recall(ranked, [{"r0"}], None) == 0.0
    assert recall(["r0"], [set()], None) is None


def test_multi_intent_caps_each_part_so_one_cannot_pay_for_another():
    # k = 2, s = 5: part 1 has 12 relevant and 8 hits -> 5/5; part 2 has 3 and 1 hit -> 1/3.
    ranked = [f"a{i}" for i in range(8)] + ["b0", "x0"]
    assert recall(ranked, [ids("a", 12), ids("b", 3)], 2) == pytest.approx((1 + 1 / 3) / 2)
    # A part without any relevant image in the pool is left out of the mean.
    assert recall(ranked, [ids("a", 12), set()], 2) == pytest.approx(1.0)


def test_relevant_takes_the_best_grade_over_the_pools_and_the_part_answered():
    def pool(asset, entity):
        return {"entities": [{"entity_id": entity, "assets": [{"asset_id": asset}]}]}

    grades = {"e1": {"grade": 2, "intent": 1}, "e2": {"grade": 1, "intent": None}}
    rows = [pool("img1", "e1"), pool("img2", "e2"), pool("img1", "e2")]
    assert relevant(rows, grades, 2) == [set(), {"img1"}]
    assert relevant(rows, grades, None) == [{"img1"}]
