from types import SimpleNamespace

from visualseek_api.application.graph_view import GraphView

GRAPH = {
    "entities": [{"id": e, "name": e.upper()} for e in ("a", "b", "c", "d")],
    "brands": [{"id": "brand:x", "name": "X"}],
    "edges": [
        {"src": "a", "rel": "MADE_BY", "dst": "brand:x", "source": "jsonld", "confidence": 0.9},
        {"src": "b", "rel": "MADE_BY", "dst": "brand:x", "reviewed": True},
        {"src": "d", "rel": "MADE_BY", "dst": "brand:x"},
    ],
}
CATEGORIES = {
    "category_names": {"tech.mouse": "Mouse"},
    "purposes": {"purpose:p1": "Cure"},
    "entities": {
        "a": {"category": "tech.mouse", "product_type": "Mouse", "source": "gemini"},
        "b": {"category": "tech.mouse"},
        "c": {"category": "tech.mouse"},
    },
    "edges": [
        {"src": "a", "rel": "TREATS", "dst": "purpose:p1", "source": "gemini"},
        {"src": "c", "rel": "TREATS", "dst": "purpose:p1", "source": "gemini"},
        {"src": "a", "rel": "IS_A", "dst": "category:ignored"},
    ],
}
# d is a duplicate record of a: the review merges it into a.
REVIEW = SimpleNamespace(canonical=lambda e: "a" if e == "d" else e)


def view() -> GraphView:
    return GraphView(GRAPH, CATEGORIES, REVIEW)


def test_focus_entity_lists_its_relations_and_merged_records() -> None:
    result = view().subgraph(["a"])
    nodes = {n["id"]: n for n in result["nodes"]}
    assert set(nodes) == {"a", "brand:x", "category:tech.mouse", "purpose:p1"}
    assert nodes["a"]["props"] == {"product_type": "Mouse", "merged_records": 2}
    assert nodes["brand:x"]["degree"] == 2  # a (with d merged in) and b
    assert {e["rel"] for e in result["edges"]} == {"MADE_BY", "IS_A", "TREATS"}
    made_by = next(e for e in result["edges"] if e["rel"] == "MADE_BY")
    assert (made_by["origin"], made_by["confidence"]) == ("jsonld", 0.9)


def test_ids_are_resolved_through_the_review() -> None:
    assert {n["id"] for n in view().subgraph(["d"])["nodes"]} >= {"a"}


def test_siblings_are_capped_per_value_and_marked() -> None:
    result = view().subgraph(["a"], siblings=1)
    siblings = [n for n in result["nodes"] if n["role"] == "sibling"]
    assert {n["id"] for n in siblings} <= {"b", "c"}
    assert len(siblings) == 2  # one per brand / category / purpose, shared ones counted once


def test_shared_only_keeps_brand_and_category_and_values_shared_by_two() -> None:
    result = view().subgraph(["a", "b", "c"], origins=["a"], shared_only=True)
    ids = {n["id"] for n in result["nodes"]}
    assert "purpose:p1" in ids  # shared by a and c
    only_b = view().subgraph(["b", "c"], shared_only=True)
    assert "purpose:p1" not in {n["id"] for n in only_b["nodes"]}
    roles = {n["id"]: n["role"] for n in result["nodes"]}
    assert roles["a"] == "origin" and roles["b"] == "seed"


def test_unknown_ids_are_ignored() -> None:
    assert view().subgraph(["nope"]) == {"nodes": [], "edges": [], "truncated": False}
