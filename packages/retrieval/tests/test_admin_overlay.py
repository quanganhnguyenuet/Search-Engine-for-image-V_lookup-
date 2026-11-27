import pytest

from visualseek_retrieval.admin_overlay import (
    apply_made_by,
    brand_target,
    fold,
    label_override,
    pending_fields,
)


def change(seq: int, entity: str, field: str, after) -> dict:
    return {"seq": seq, "entity_id": entity, "field": field, "before": None, "after": after}


def graph() -> dict:
    return {
        "entities": [
            {"id": "a", "name": "A", "brand_id": "brand:x"},
            {"id": "a2", "name": "A copy", "brand_id": "brand:x"},
            {"id": "b", "name": "B", "brand_id": "brand:y"},
        ],
        "brands": [
            {"id": "brand:x", "name": "X", "aliases": ["x"]},
            {"id": "brand:y", "name": "Y", "aliases": ["y"]},
        ],
        "edges": [
            {"src": "a", "rel": "MADE_BY", "dst": "brand:x"},
            {"src": "a2", "rel": "MADE_BY", "dst": "brand:x"},
            {"src": "b", "rel": "MADE_BY", "dst": "brand:y"},
        ],
    }


def test_the_last_change_of_a_field_wins_in_sequence_order() -> None:
    rows = [change(2, "a", "treats", ["ho"]), change(1, "a", "treats", ["sốt"])]

    assert fold(rows) == {"a": {"treats": ["ho"]}}


def test_pending_compares_with_the_last_release_then_with_the_live_value() -> None:
    state = {"a": {"treats": ["ho"], "origin": "Huế"}, "b": {"origin": "Hà Nội"}}
    released = {"a": {"treats": ["ho"]}}
    live = {"a": {"origin": ""}, "b": {"origin": "Hà Nội"}}

    assert pending_fields(state, released, lambda e: live[e]) == {"a": {"origin": "Huế"}}
    # Without live values every unreleased field counts.
    assert pending_fields(state, released) == {
        "a": {"origin": "Huế"},
        "b": {"origin": "Hà Nội"},
    }


def test_a_brand_is_moved_on_every_merged_member() -> None:
    g = graph()

    changed = apply_made_by(g, {"a": {"brand": "brand:y"}}, {"a": ["a", "a2"]})

    assert sorted(changed) == ["a", "a2"]
    made_by = {e["src"]: e["dst"] for e in g["edges"] if e["rel"] == "MADE_BY"}
    assert made_by == {"a": "brand:y", "a2": "brand:y", "b": "brand:y"}
    assert {e["id"]: e["brand_id"] for e in g["entities"]} == {
        "a": "brand:y",
        "a2": "brand:y",
        "b": "brand:y",
    }
    # brand:x has no product left.
    assert [b["id"] for b in g["brands"]] == ["brand:y"]


def test_a_new_brand_is_created_and_removing_a_brand_drops_the_edge() -> None:
    g = graph()

    apply_made_by(g, {"a": {"brand": "new:Hãng Mới"}, "b": {"brand": None}}, {})

    made_by = {e["src"]: e["dst"] for e in g["edges"] if e["rel"] == "MADE_BY"}
    assert made_by == {"a": "brand:hang-moi", "a2": "brand:x"}
    assert {b["id"]: b["aliases"] for b in g["brands"]}["brand:hang-moi"] == ["hang moi"]


def test_applying_the_same_state_twice_changes_nothing() -> None:
    g = graph()
    state = {"a": {"brand": "brand:y"}}
    apply_made_by(g, state, {})

    assert apply_made_by(g, state, {}) == []


def test_a_new_brand_that_already_exists_reuses_it() -> None:
    brands = {b["id"]: b for b in graph()["brands"]}

    assert brand_target("new:  X ", brands) == ("brand:x", None)
    with pytest.raises(ValueError):
        brand_target("brand:unknown", brands)


def test_checking_an_entity_makes_its_label_human() -> None:
    assert label_override({"reviewed": True}) == {"source": "human"}
    assert label_override({"treats": ["ho"]}) == {}
    assert label_override({"category": "drug.oral"}) == {
        "category": "drug.oral",
        "source": "human",
    }
