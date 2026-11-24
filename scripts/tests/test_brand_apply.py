import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import build_graph_lite as lite  # noqa: E402


def graph():
    entities = [
        {"id": i, "name": i, "brand_id": b, "aliases": [i]}
        for i, b in [("tab", "brand:samsung"), ("moto", "brand:samsung"), ("rice", "brand:cai")]
    ]
    edges = [
        {
            "src": e["id"],
            "rel": "MADE_BY",
            "dst": e["brand_id"],
            "source": "name",
            "confidence": 0.7,
        }
        for e in entities
    ]
    brands = [
        {"id": "brand:samsung", "name": "Samsung", "aliases": ["samsung"]},
        {"id": "brand:cai", "name": "Cai", "aliases": ["cai"]},
    ]
    return lite.seal({"entities": entities, "brands": brands, "edges": edges})


def row(g, entity_id, verdict, to_brand=None, brand="brand:samsung"):
    return {
        "entity_id": entity_id,
        "from_brand": brand,
        "verdict": verdict,
        "to_brand": to_brand,
        "reviewer": "human-1",
        "graph_fingerprint": g["fingerprint"],
    }


PLAN = {
    "not_a_brand": {"new:cai lậy": "ORIGIN"},
    "new_brands": {"new:xe máy Sym": {"id": "brand:sym", "name": "SYM", "aliases": ["sym"]}},
}


def test_review_confirms_moves_and_removes_edges():
    g = graph()
    base = g["fingerprint"]
    decisions = [
        row(g, "tab", "ok"),
        row(g, "moto", "reassign", "new:xe máy Sym"),
        row(g, "rice", "reassign", "new:cai lậy", brand="brand:cai"),
    ]
    out = lite.apply_brand_review(g, decisions, PLAN)
    edges = {e["src"]: e for e in out["edges"]}
    assert edges["tab"]["reviewed"] and edges["tab"]["dst"] == "brand:samsung"
    assert edges["moto"]["dst"] == "brand:sym" and edges["moto"]["source"] == "review"
    assert "rice" not in edges  # a place is not a manufacturer
    assert {e["id"]: e["brand_id"] for e in out["entities"]}["rice"] is None
    assert [b["id"] for b in out["brands"]] == ["brand:samsung", "brand:sym"]  # Cai has no product
    assert out["fingerprint"] != base
    assert out["made_by_review"] == {
        "base_fingerprint": base,
        "confirmed": 1,
        "moved": 1,
        "removed": 1,
        "brands_dropped": 1,
    }


def test_decisions_made_on_another_graph_are_refused():
    g = graph()
    stale = {**row(g, "tab", "ok"), "graph_fingerprint": "other"}
    with pytest.raises(SystemExit, match="another graph"):
        lite.apply_brand_review(g, [stale], PLAN)


def test_a_new_brand_must_be_named_in_the_plan():
    g = graph()
    with pytest.raises(SystemExit, match="new_brands"):
        lite.apply_brand_review(g, [row(g, "moto", "reassign", "new:unknown")], PLAN)
