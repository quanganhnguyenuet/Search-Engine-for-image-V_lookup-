import importlib.util
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "category_review_server", SCRIPTS / "category_review_server.py"
)
server = importlib.util.module_from_spec(spec)
sys.modules["category_review_server"] = server  # pydantic resolves postponed annotations here
spec.loader.exec_module(server)

LABELS = {"e1": {"category": "drug.oral"}, "e2": {"category": "fashion.jewelry"}}
CATS = {"drug.oral", "fashion.jewelry", "car.parts"}


def item(**kw):
    base = {
        "entity_id": "e1",
        "verdict": "ok",
        "category": "drug.oral",
        "product_type": "Thuốc kháng sinh",
        "market_tier": "not_applicable",
    }
    return server.Item(**{**base, **kw})


def test_validate_accepts_ok_and_fix_items():
    items = [
        item(),
        item(
            entity_id="e2",
            verdict="fix",
            category="fashion.jewelry",
            product_type="Nhẫn kim cương",
            market_tier="luxury",
        ),
    ]
    assert server.validate(items, LABELS, CATS) is None


def test_validate_rejects_bad_items():
    assert "unknown entity" in server.validate([item(entity_id="zz")], LABELS, CATS)
    assert "unknown category" in server.validate([item(category="x.y")], LABELS, CATS)
    assert "empty" in server.validate([item(product_type="  ")], LABELS, CATS)
    assert "fashion" in server.validate([item(market_tier="luxury")], LABELS, CATS)


def test_latest_decision_per_entity_wins():
    rows = [{"entity_id": "e1", "verdict": "ok"}, {"entity_id": "e1", "verdict": "fix"}]
    assert server.latest(rows)["e1"]["verdict"] == "fix"
