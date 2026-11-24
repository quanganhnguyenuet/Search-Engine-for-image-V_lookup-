import importlib.util
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("hops_judge_server", SCRIPTS / "hops_judge_server.py")
server = importlib.util.module_from_spec(spec)
sys.modules["hops_judge_server"] = server  # pydantic resolves postponed annotations here
spec.loader.exec_module(server)

QUERY = {"qid": "h01", "kind": "multi_hop", "intents": None}
POOL = {"qid": "h01", "entities": [{"entity_id": "a"}, {"entity_id": "b"}]}


def judgment(grades: dict) -> object:
    return server.Judgment(qid="h01", grades={k: server.Grade(grade=g) for k, g in grades.items()})


def test_every_product_of_the_pool_needs_a_grade():
    error = server.validate(judgment({"a": 2}), QUERY, POOL)
    assert error == "1 product(s) of the query are not graded"


def test_grades_from_an_earlier_pool_do_not_block_saving():
    # "old" was in the pool before it was rebuilt; its grade stays in the saved line.
    assert server.validate(judgment({"a": 2, "b": 0, "old": 1}), QUERY, POOL) is None
