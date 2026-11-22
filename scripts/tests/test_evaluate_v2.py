import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "evaluate_v2", Path(__file__).resolve().parents[1] / "evaluate_v2.py"
)
ev = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ev)


def test_recall_examples_from_plan_section_5_2():
    four = {"type": "single_named", "rel_by_intent": [["a", "b", "c", "d"]], "k": 1}
    assert ev.recall_at_10(four, ["a", "b", "c", "x"]) == 0.75
    thirty = {"type": "single_named", "rel_by_intent": [[f"r{i}" for i in range(30)]], "k": 1}
    assert abs(ev.recall_at_10(thirty, [f"r{i}" for i in range(7)] + ["x", "y", "z"]) - 0.7) < 1e-9
    multi = {"type": "multi_intent", "k": 2,
             "rel_by_intent": [[f"a{i}" for i in range(12)], ["b0", "b1", "b2"]]}
    r10 = [f"a{i}" for i in range(8)] + ["b0", "x"]
    assert abs(ev.recall_at_10(multi, r10) - (1 + 1 / 3) / 2) < 1e-9


def test_ndcg_and_percentiles():
    row = {"type": "single_named", "rel_by_intent": [["a"]], "k": 1}
    assert ev.ndcg_at_10(row, ["a"]) == 1.0
    assert ev.ndcg_at_10(row, ["x", "a"]) < 1.0
    stats = ev.percentiles([1.0, 2.0, 3.0, 4.0])
    assert stats["n"] == 4 and stats["max"] == 4.0
