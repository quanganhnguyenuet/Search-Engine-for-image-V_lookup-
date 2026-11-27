"""Entities created by a CSV import: graph.json and entity_categories.json entries."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import admin_release  # noqa: E402
from build_graph_lite import add_admin_entities, seal  # noqa: E402

NAMES = {
    "food.cake_snack": ("Bánh kẹo", "food"),
    "drug.oral": ("Thuốc uống", "drug"),
}


def graph() -> dict:
    return seal(
        {
            "entities": [
                {
                    "id": "e1",
                    "name": "Siro ho Bảo Thanh",
                    "core": "Siro ho Bảo Thanh",
                    "notes": [],
                    "brand_id": "brand:bao-thanh",
                    "codes": [],
                    "model_tail": None,
                    "latin_head": None,
                    "aliases": ["siro ho bao thanh"],
                }
            ],
            "brands": [{"id": "brand:bao-thanh", "name": "Bảo Thanh", "aliases": ["bao thanh"]}],
            "edges": [{"src": "e1", "rel": "MADE_BY", "dst": "brand:bao-thanh"}],
        }
    )


def row(eid: str, name: str, brand: str = "", label: dict | None = None, **enrich) -> dict:
    return {
        "entity_id": eid,
        "name": name,
        "pack": {"entity_id": eid, "name": name, "members": [eid], "snippets": []},
        "label": label,
        "enrich": {"brand": brand, "model": "agy:gemini-3.8-flash-low", **enrich}
        if brand or enrich
        else None,
    }


def test_a_new_entity_gets_aliases_and_an_existing_brand() -> None:
    g = graph()
    added = add_admin_entities(g, [row("n1", "Viên ngậm Bảo Thanh BT-20", brand="bảo thanh")])
    seal(g)

    assert added == ["n1"]
    entity = next(e for e in g["entities"] if e["id"] == "n1")
    assert entity["brand_id"] == "brand:bao-thanh"
    assert "bt 20" in entity["aliases"] and "vien ngam bao thanh bt 20" in entity["aliases"]
    edge = next(e for e in g["edges"] if e["src"] == "n1")
    assert (edge["dst"], edge["source"], edge["reviewed"]) == ("brand:bao-thanh", "gemini", False)


def test_an_unknown_brand_is_created_only_when_the_name_spells_it() -> None:
    g = graph()
    add_admin_entities(
        g,
        [
            row("n1", "Bánh pía Tân Huê Viên", brand="Tân Huê Viên"),
            row("n2", "Bánh pía Sóc Trăng", brand="Kinh Đô"),
        ],
    )

    by_id = {e["id"]: e for e in g["entities"]}
    assert by_id["n1"]["brand_id"] == "brand:tan-hue-vien"
    assert by_id["n2"]["brand_id"] is None
    assert "brand:tan-hue-vien" in {b["id"] for b in g["brands"]}


def test_an_entity_already_in_the_graph_is_left_alone() -> None:
    g = graph()
    before = seal(g)["aliases_sha256"]

    assert add_admin_entities(g, [row("e1", "Siro ho khác")]) == []
    assert seal(g)["aliases_sha256"] == before


def categories() -> dict:
    return {
        "coverage": {"canonical_entities": 1},
        "purposes": {},
        "ingredients": {},
        "models": {},
        "places": {},
        "entities": {},
        "edges": [],
    }


def test_release_labels_imported_entities_and_keeps_them_in_review() -> None:
    g = graph()
    label = {
        "category": "food.cake_snack",
        "product_type": "Bánh pía",
        "market_tier": "not_applicable",
        "basis": "evidence",
        "confidence": "high",
        "model": "agy:gemini-3.8-flash-medium",
    }
    rows = [
        row("n1", "Bánh pía Sóc Trăng", label=label, origin="Sóc Trăng", treats=[]),
        row("n2", "Không nhãn"),
    ]
    add_admin_entities(g, rows)
    doc = categories()

    added = admin_release.add_new_categories(doc, rows, g, NAMES)

    assert added == ["n1"]
    entry = doc["entities"]["n1"]
    assert (entry["category"], entry["source"], entry["imported"]) == (
        "food.cake_snack",
        "gemini",
        True,
    )
    assert entry["needs_review"] is True
    assert entry["origin"] == "Sóc Trăng"
    assert {"src": "n1", "rel": "ORIGIN", "dst": "place:soc-trang", "source": "gemini"} in doc[
        "edges"
    ]
    assert doc["places"] == {"place:soc-trang": "Sóc Trăng"}
    assert doc["coverage"]["canonical_entities"] == 2
