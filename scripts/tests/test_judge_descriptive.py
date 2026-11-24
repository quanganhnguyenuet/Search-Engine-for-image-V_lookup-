import importlib.util
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]


def load(name: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    # Registered so pydantic can resolve the module's postponed annotations.
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


pool_builder = load("build_eval_descriptive")
judge = load("judge_server")


def test_merge_pool_unions_systems_and_keeps_ranks():
    runs = {"text": ["a", "b", "c"], "img": ["c", "d"], "rerank": ["d", "a"]}
    pool = pool_builder.merge_pool("q1", runs)
    assert sorted(entry["asset_id"] for entry in pool) == ["a", "b", "c", "d"]
    by_id = {entry["asset_id"]: entry["sources"] for entry in pool}
    assert by_id["a"] == {"text": 1, "rerank": 2}
    assert by_id["d"] == {"img": 2, "rerank": 1}


def test_merge_pool_order_is_deterministic_and_not_a_system_ranking():
    runs = {"text": [f"t{i}" for i in range(10)], "img": [f"i{i}" for i in range(10)]}
    first = [entry["asset_id"] for entry in pool_builder.merge_pool("q1", runs)]
    again = [entry["asset_id"] for entry in pool_builder.merge_pool("q1", runs)]
    assert first == again
    assert first[:10] != runs["text"] and first[:10] != runs["img"]
    other_query = [entry["asset_id"] for entry in pool_builder.merge_pool("q2", runs)]
    assert other_query != first


def test_validate_judgment_requires_every_pool_image_unless_flagged():
    pool = ["a", "b"]
    assert judge.validate_judgment(pool, {"grades": {"a": 2, "b": 0}, "flag": None}) is None
    assert "not graded" in judge.validate_judgment(pool, {"grades": {"a": 2}, "flag": None})
    assert judge.validate_judgment(pool, {"grades": {}, "flag": "bad_query"}) is None
    assert "not in the pool" in judge.validate_judgment(pool, {"grades": {"z": 1}, "flag": None})
    assert "0, 1 or 2" in judge.validate_judgment(pool, {"grades": {"a": 3, "b": 0}})
    assert judge.validate_judgment(None, {"grades": {}}) == "unknown qid"


def test_latest_judgment_per_query_wins():
    rows = [
        {"qid": "q1", "grades": {"a": 0}},
        {"qid": "q2", "grades": {"b": 2}},
        {"qid": "q1", "grades": {"a": 2}},
    ]
    latest = judge.latest_judgments(rows)
    assert latest["q1"]["grades"] == {"a": 2} and latest["q2"]["grades"] == {"b": 2}


def test_save_endpoint_reads_the_json_body(tmp_path, monkeypatch):
    """Regression: the body model must be a JSON body, not a query parameter."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    pool_ids = {"q1": ["a", "b"]}
    saved: list[dict] = []
    app = FastAPI()

    @app.post("/api/judgments")
    def save(judgment: judge.Judgment) -> dict:
        payload = judgment.model_dump()
        error = judge.validate_judgment(pool_ids.get(judgment.qid), payload)
        saved.append(payload)
        return {"error": error}

    response = TestClient(app).post(
        "/api/judgments", json={"qid": "q1", "grades": {"a": 2, "b": 1}, "flag": None}
    )
    assert response.status_code == 200 and response.json() == {"error": None}
    assert saved[0]["grades"] == {"a": 2, "b": 1}
