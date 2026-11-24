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
