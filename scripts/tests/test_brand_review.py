import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import brand_review_server as server  # noqa: E402

BRANDS = {
    "brand:samsung": {"name": "Samsung", "aliases": ["samsung"]},
    "brand:sym": {"name": "SYM", "aliases": ["sym"]},
}
MEMBERS = {"brand:samsung": ["tab", "galaxy-moto"]}


def decision(*items):
    return server.PageDecision(
        brand_id="brand:samsung", items=[server.Item(**item) for item in items]
    )


def test_a_brand_is_resolved_by_id_name_or_alias_and_new_names_are_marked():
    assert server.resolve_brand("brand:sym", BRANDS) == "brand:sym"
    assert server.resolve_brand("Sym", BRANDS) == "brand:sym"
    assert server.resolve_brand("samsung", BRANDS) == "brand:samsung"
    assert server.resolve_brand("Galaxy Bike", BRANDS) == "new:Galaxy Bike"
    assert server.resolve_brand("  ", BRANDS) is None


def test_items_must_belong_to_the_page_and_a_move_names_its_brand():
    ok = decision({"entity_id": "galaxy-moto", "verdict": "reassign", "to_brand": "SYM"})
    assert server.validate(ok, MEMBERS) is None
    other = decision({"entity_id": "kindle", "verdict": "ok"})
    assert "is not a product" in server.validate(other, MEMBERS)
    empty = decision({"entity_id": "galaxy-moto", "verdict": "reassign", "to_brand": " "})
    assert "name the brand" in server.validate(empty, MEMBERS)
