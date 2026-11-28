import copy
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import admin_release  # noqa: E402

NAMES = {
    "drug.oral": ("Thuốc uống", "drug"),
    "food.drink": ("Đồ uống", "food"),
    "fashion.bags": ("Túi xách", "fashion"),
}


def graph() -> dict:
    return {
        "entities": [
            {"id": "e1", "name": "Siro ho"},
            {"id": "e2", "name": "Nước dừa"},
            {"id": "e3", "name": "Túi"},
        ],
        "brands": [{"id": "brand:b", "name": "B", "aliases": ["b"]}],
        "edges": [{"src": "e3", "rel": "MADE_BY", "dst": "brand:b"}],
    }


def categories() -> dict:
    return {
        "coverage": {"canonical_entities": 3},
        "purposes": {"purpose:ho": "ho"},
        "ingredients": {},
        "models": {},
        "places": {"place:ben-tre": "Bến Tre"},
        "entities": {
            "e1": {
                "name": "Siro ho",
                "category": "drug.oral",
                "category_name": "Thuốc uống",
                "category_l1": "drug",
                "product_type": "Siro",
                "market_tier": "not_applicable",
                "brand_id": None,
                "source": "gemini",
                "needs_review": True,
                "treats": ["ho"],
            },
            "e2": {
                "name": "Nước dừa",
                "category": "food.drink",
                "category_name": "Đồ uống",
                "category_l1": "food",
                "product_type": "Nước dừa",
                "market_tier": "not_applicable",
                "brand_id": None,
                "source": "gemini",
                "needs_review": False,
                "origin": "Bến Tre",
            },
        },
        "edges": [
            {"src": "e1", "rel": "IS_A", "dst": "category:drug.oral"},
            {"src": "e1", "rel": "TREATS", "dst": "purpose:ho", "source": "gemini"},
            {"src": "e2", "rel": "IS_A", "dst": "category:food.drink"},
            {"src": "e2", "rel": "ORIGIN", "dst": "place:ben-tre", "source": "gemini"},
        ],
    }


def apply(state: dict, doc: dict | None = None) -> tuple[dict, list[str]]:
    doc = doc or categories()
    changed = admin_release.apply_to_categories(doc, state, graph(), {}, NAMES)
    return doc, changed


def test_an_empty_state_changes_nothing() -> None:
    before = categories()
    doc, changed = apply({})

    assert changed == []
    assert doc["edges"] == before["edges"]
    assert doc["entities"] == before["entities"]


def test_an_added_relation_gets_an_admin_edge_and_a_value_node() -> None:
    doc, changed = apply({"e1": {"treats": ["ho", "đau họng"]}})

    assert changed == ["e1"]
    assert doc["entities"]["e1"]["treats"][:2] == ["ho", "đau họng"]
    treats = [e for e in doc["edges"] if e["src"] == "e1" and e["rel"] == "TREATS"]
    assert {e["dst"] for e in treats} >= {"purpose:ho", "purpose:dau-hong"}
    assert {e["source"] for e in treats} == {"admin"}
    assert doc["purposes"]["purpose:dau-hong"] == "đau họng"


def test_a_removed_origin_drops_the_edge_and_the_unused_place() -> None:
    doc, changed = apply({"e2": {"origin": ""}})

    assert changed == ["e2"]
    assert "origin" not in doc["entities"]["e2"]
    assert [e["rel"] for e in doc["edges"] if e["src"] == "e2"] == ["IS_A"]
    assert doc["places"] == {}


def test_an_admin_origin_is_kept_where_the_category_gate_would_drop_it() -> None:
    # A province is not an origin for a drug when an LLM proposes it; an admin decides.
    doc, _ = apply({"e1": {"origin": "Bến Tre"}})

    assert doc["entities"]["e1"]["origin"] == "Bến Tre"


def test_a_category_edit_moves_the_is_a_edge_and_clears_the_review_flag() -> None:
    doc, _ = apply({"e1": {"category": "food.drink"}})

    entry = doc["entities"]["e1"]
    assert (entry["category"], entry["category_l1"], entry["category_name"]) == (
        "food.drink",
        "food",
        "Đồ uống",
    )
    assert (entry["source"], entry["needs_review"]) == ("human", False)
    assert {"src": "e1", "rel": "IS_A", "dst": "category:food.drink"} in doc["edges"]
    assert doc["coverage"]["needs_review"] == 0


def test_checking_an_entity_only_clears_its_review_flag() -> None:
    doc, changed = apply({"e1": {"reviewed": True}})

    assert changed == ["e1"]
    assert doc["entities"]["e1"]["needs_review"] is False
    assert doc["entities"]["e1"]["treats"] == ["ho"]


def test_an_unlabelled_entity_enters_only_with_a_category() -> None:
    doc, changed = apply({"e3": {"treats": ["x"]}})
    assert changed == [] and "e3" not in doc["entities"]

    doc, changed = apply({"e3": {"category": "fashion.bags", "market_tier": "luxury"}})
    entry = doc["entities"]["e3"]
    assert (entry["name"], entry["brand_id"], entry["market_tier"]) == ("Túi", "brand:b", "luxury")


def test_release_is_idempotent() -> None:
    state = {"e1": {"treats": ["ho", "sốt"], "category": "food.drink"}, "e2": {"origin": ""}}
    once, _ = apply(state)
    twice, changed = apply(state, copy.deepcopy(once))

    assert changed == []
    assert twice == once


def test_active_uploads_counts_refreshed_rows_and_skips_deleted(tmp_path, monkeypatch) -> None:
    ops = [("a", "add"), ("b", "add"), ("c", "add"), ("a", "update"), ("c", "delete")]
    path = tmp_path / "uploads.jsonl"
    path.write_text(
        "".join(f'{{"asset_id": "{asset}", "op": "{op}"}}\n' for asset, op in ops),
        encoding="utf-8",
    )
    monkeypatch.setattr(admin_release, "UPLOADS", path)

    assert admin_release.active_uploads() == 2
