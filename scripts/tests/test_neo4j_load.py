import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import neo4j_load_graph as loader  # noqa: E402

from visualseek_retrieval.entity_review import EntityReview  # noqa: E402

GRAPH = {
    "entities": [
        {"id": "moto", "name": "SYM Galaxy", "core": "SYM Galaxy", "aliases": ["sym galaxy"]},
        {"id": "moto-dup", "name": "SYM Galaxy 115", "aliases": []},
        {"id": "rice", "name": "Rượu thóc", "aliases": []},
    ],
    "brands": [{"id": "brand:sym", "name": "SYM", "aliases": ["sym"]}],
    "edges": [
        {
            "src": "moto",
            "rel": "MADE_BY",
            "dst": "brand:sym",
            "source": "review",
            "confidence": 1.0,
            "reviewed": True,
            "reviewer": "human-1",
        }
    ],
}
CATEGORIES = {
    "category_names": {"moto.vehicle": "Xe máy & mô tô"},
    "entities": {"moto": {"category": "moto.vehicle", "product_type": "Xe máy"}},
}


def test_rows_follow_the_snapshot_and_the_identity_review():
    review = EntityReview(canonical_ids={"moto-dup": "moto"})
    out = loader.rows(GRAPH, CATEGORIES, review)
    assert out["categories"] == [{"id": "moto.vehicle", "name": "Xe máy & mô tô", "l1": "moto"}]
    assert out["made_by"] == [
        {
            "src": "moto",
            "dst": "brand:sym",
            "props": {
                "source": "review",
                "confidence": 1.0,
                "reviewed": True,
                "reviewer": "human-1",
            },
        }
    ]
    assert out["is_a"] == [{"src": "moto", "dst": "moto.vehicle"}]
    assert out["same_as"] == [{"src": "moto-dup", "dst": "moto"}]
    entities = {row["id"]: row["props"] for row in out["entities"]}
    # The merged duplicate reads its type from the canonical entity; empty fields are left out.
    assert entities["moto-dup"]["product_type"] == "Xe máy"
    assert entities["rice"] == {"name": "Rượu thóc"}


def test_rows_include_enriched_nodes_edges_and_entity_attributes():
    categories = {
        "category_names": {"drug.oral": "Tân dược dạng uống"},
        "purposes": {"purpose:da-day": "dạ dày"},
        "ingredients": {"ingredient:famotidin": "Famotidin"},
        "models": {"model:honda-air-blade": "Honda Air Blade"},
        "places": {"place:ben-tre": "Bến Tre"},
        "entities": {
            "moto": {
                "category": "drug.oral",
                "product_type": "Thuốc dạ dày",
                "form_factor": "dạng viên",
                "target_audience": "người lớn",
                "material": "đồng",
                "origin": "Bến Tre",
            }
        },
        "edges": [
            {"src": "moto", "rel": "TREATS", "dst": "purpose:da-day", "source": "gemini"},
            {
                "src": "moto",
                "rel": "HAS_INGREDIENT",
                "dst": "ingredient:famotidin",
                "source": "gemini",
            },
            {"src": "moto", "rel": "FITS", "dst": "model:honda-air-blade", "source": "free"},
            {"src": "moto", "rel": "ORIGIN", "dst": "place:ben-tre", "source": "free"},
        ],
    }
    out = loader.rows(GRAPH, categories, EntityReview(canonical_ids={"moto-dup": "moto"}))
    assert out["purposes"] == [{"id": "purpose:da-day", "name": "dạ dày"}]
    assert out["ingredients"] == [{"id": "ingredient:famotidin", "name": "Famotidin"}]
    assert out["models"] == [{"id": "model:honda-air-blade", "name": "Honda Air Blade"}]
    assert out["places"] == [{"id": "place:ben-tre", "name": "Bến Tre"}]
    assert out["treats"] == [
        {"src": "moto", "dst": "purpose:da-day", "props": {"source": "gemini"}}
    ]
    assert out["has_ingredient"] == [
        {"src": "moto", "dst": "ingredient:famotidin", "props": {"source": "gemini"}}
    ]
    assert out["fits"] == [
        {"src": "moto", "dst": "model:honda-air-blade", "props": {"source": "free"}}
    ]
    assert out["origin"] == [{"src": "moto", "dst": "place:ben-tre", "props": {"source": "free"}}]
    entities = {row["id"]: row["props"] for row in out["entities"]}
    assert entities["moto"]["form_factor"] == "dạng viên"
    assert entities["moto"]["material"] == "đồng"
    assert entities["moto"]["origin"] == "Bến Tre"


def test_snapshot_id_changes_when_only_the_categories_change():
    # A re-merge drops edges without touching graph.json; the reload deletes whatever carries
    # another snapshot id, so the id has to move with entity_categories.json.
    graph = {"fingerprint": "abc"}
    first = loader.snapshot_id(graph, b'{"edges": [1, 2]}')
    assert first == loader.snapshot_id(graph, b'{"edges": [1, 2]}')
    assert first != loader.snapshot_id(graph, b'{"edges": [1]}')
    assert first != loader.snapshot_id({"fingerprint": "xyz"}, b'{"edges": [1, 2]}')
