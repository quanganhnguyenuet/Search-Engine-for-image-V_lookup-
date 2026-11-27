import json
import time
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi.testclient import TestClient

from visualseek_api.application.admin import AdminService
from visualseek_api.application.graph_view import GraphView
from visualseek_api.core import auth
from visualseek_api.core.config import Settings, get_settings
from visualseek_api.main import app
from visualseek_retrieval.entity_review import EntityReview

SUPABASE_URL = "https://project.supabase.co"
KEY = ec.generate_private_key(ec.SECP256R1())
JWKS = {
    "keys": [
        json.loads(jwt.algorithms.ECAlgorithm.to_jwk(KEY.public_key()))
        | {"kid": "k", "alg": "ES256", "use": "sig"}
    ]
}
A1, A2, A3 = "a" * 64, "b" * 64, "c" * 64


def token(role: str = "admin") -> dict[str, str]:
    claims = {
        "sub": "admin-1",
        "email": "admin@example.com",
        "aud": "authenticated",
        "iss": f"{SUPABASE_URL}/auth/v1",
        "exp": int(time.time()) + 900,
        "user_role": role,
    }
    value = jwt.encode(claims, KEY, algorithm="ES256", headers={"kid": "k"})
    return {"Authorization": f"Bearer {value}"}


def fake_service() -> SimpleNamespace:
    graph = {
        "entities": [
            {"id": "e1", "name": "Siro ho Bảo Thanh", "aliases": ["bao thanh"], "notes": []},
            {"id": "e2", "name": "Nước dừa", "aliases": [], "notes": []},
        ],
        "brands": [{"id": "brand:bt", "name": "Bảo Thanh", "aliases": ["bao thanh"]}],
        "edges": [{"src": "e1", "rel": "MADE_BY", "dst": "brand:bt"}],
        "fingerprint": "f",
    }
    categories = {
        "taxonomy_sha256": "t",
        "category_names": {"drug.oral": "Thuốc uống", "food.drink": "Đồ uống"},
        "purposes": {"purpose:ho": "ho"},
        "entities": {
            "e1": {
                "name": "Siro ho Bảo Thanh",
                "category": "drug.oral",
                "category_name": "Thuốc uống",
                "category_l1": "drug",
                "product_type": "Siro ho",
                "market_tier": "not_applicable",
                "source": "gemini",
                "needs_review": True,
                "treats": ["ho"],
            }
        },
        "edges": [{"src": "e1", "rel": "TREATS", "dst": "purpose:ho", "source": "gemini"}],
    }
    review = EntityReview(blocked_assets=[A3])
    pipeline = SimpleNamespace(
        graph=graph, review=review, payloads={}, fusion=None, image_encoder=None
    )
    return SimpleNamespace(
        pipeline=pipeline,
        graph_view=GraphView(graph, categories, review),
        categories=categories,
        asset_entities={A1: ["e1"], A2: ["e1"], A3: ["e2"]},
        image_paths={A1: "x.jpg", A2: "y.jpg", A3: "z.jpg"},
        details={},
        base_blocked=[A3],
        upload_paths={},
        info={"graph_fingerprint": "f", "doc_texts_sha256": "d"},
        executor=ThreadPoolExecutor(max_workers=1),
    )


@pytest.fixture
def admin(tmp_path) -> tuple[AdminService, SimpleNamespace]:
    settings = Settings(
        _env_file=None, admin_dir=str(tmp_path / "admin"), admin_data_dir=str(tmp_path / "data")
    )
    service = fake_service()
    admin = AdminService(settings)
    admin.prepare(service)
    return admin, service


@pytest.fixture
def client(admin, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setattr(jwt.PyJWKClient, "fetch_data", lambda self: JWKS)
    monkeypatch.setattr(app.state, "admin", admin[0], raising=False)
    monkeypatch.setattr(app.state, "search", admin[1], raising=False)
    app.dependency_overrides[get_settings] = lambda: Settings(
        _env_file=None, supabase_url=SUPABASE_URL
    )
    auth._verifier.cache_clear()
    yield TestClient(app)
    app.dependency_overrides.clear()
    auth._verifier.cache_clear()


def user() -> auth.AuthUser:
    return auth.AuthUser(id="admin-1", email="admin@example.com", role="admin")


def test_an_edit_is_pending_and_logged_with_who_and_before(admin) -> None:
    service_admin, service = admin

    detail = service_admin.edit(service, "e1", {"treats": ["ho", "đau họng"]}, "", user())

    assert detail["pending"] == ["treats"]
    assert detail["draft"]["treats"] == ["ho", "đau họng"]
    assert detail["live"]["treats"] == ["ho"]
    row = detail["history"][0]
    assert (row["field"], row["before"], row["after"]) == ("treats", ["ho"], ["ho", "đau họng"])
    assert row["actor"]["email"] == "admin@example.com"  # resolved for the admin pages
    stored = service_admin.store.changes()[0]["actor"]
    assert stored == {"id": row["actor"]["id"]} and stored["id"].startswith("admin-")


def test_the_logs_in_git_hold_an_alias_and_never_who_it_is(admin) -> None:
    service_admin, service = admin

    service_admin.edit(service, "e1", {"origin": "Huế"}, "", user())
    service_admin.set_hidden(service, A1, True, "", user())

    store = service_admin.store
    written = store.changes_path.read_text("utf-8") + store.images_path.read_text("utf-8")
    assert "admin@example.com" not in written and '"admin-1"' not in written
    aliases = {r["actor"]["id"] for r in [*store.changes(), *store.image_events()]}
    assert len(aliases) == 1  # one account, one alias
    (alias,) = aliases
    assert store.actors.name(alias) == "admin@example.com"
    assert store.actors.public({"id": alias}) == {"id": alias}  # never aliased twice
    assert store.actors.path.parent == store.data_dir  # outside the tracked admin dir
    store.actors.path.unlink()
    assert store.actors.name(alias) == alias


def test_an_unchanged_value_writes_no_row(admin) -> None:
    service_admin, service = admin

    service_admin.edit(service, "e1", {"treats": ["ho"], "product_type": " Siro  ho "}, "", user())

    assert service_admin.store.changes() == []


def test_discard_returns_the_field_to_its_released_value(admin) -> None:
    service_admin, service = admin
    service_admin.edit(service, "e1", {"origin": "Huế"}, "", user())

    detail = service_admin.discard(service, "e1", ["origin"], user())

    assert detail["pending"] == []
    assert detail["draft"]["origin"] == ""
    assert len(detail["history"]) == 2


@pytest.mark.parametrize(
    ("changes", "code"),
    [
        ({"category": "nope"}, "invalid_admin_request"),
        ({"brand": "brand:unknown"}, "invalid_admin_request"),
        ({"brand": "new:   "}, "invalid_admin_request"),
        ({"market_tier": "cheap"}, "invalid_admin_request"),
        ({"treats": "ho"}, "invalid_admin_request"),
        ({"treats": ["x"] * 13 + [str(i) for i in range(13)]}, "invalid_admin_request"),
        ({"product_type": ""}, "invalid_admin_request"),
        ({"color": "red"}, "invalid_admin_request"),
    ],
)
def test_invalid_edits_are_refused(admin, changes: dict, code: str) -> None:
    from visualseek_api.core.errors import ApplicationError

    service_admin, service = admin
    with pytest.raises(ApplicationError) as error:
        service_admin.edit(service, "e1", changes, "", user())
    assert error.value.code == code
    assert service_admin.store.changes() == []


def test_an_unlabelled_entity_needs_a_category_first(admin) -> None:
    from visualseek_api.core.errors import ApplicationError

    service_admin, service = admin
    with pytest.raises(ApplicationError) as error:
        service_admin.edit(service, "e2", {"treats": ["giải khát"]}, "", user())
    assert error.value.code == "category_required"

    detail = service_admin.edit(
        service, "e2", {"category": "food.drink", "treats": ["giải khát"]}, "", user()
    )
    assert detail["pending"] == ["category", "treats"]
    # A brand needs no category.
    service_admin.edit(service, "e2", {"brand": "new:Cocoxim"}, "", user())


def test_a_new_brand_with_an_existing_name_uses_that_brand(admin) -> None:
    service_admin, service = admin

    detail = service_admin.edit(service, "e2", {"brand": "new:bảo thanh"}, "", user())

    assert detail["draft"]["brand"] == {"id": "brand:bt", "name": "Bảo Thanh", "new": False}


def test_hiding_an_image_blocks_it_at_once_and_showing_it_unblocks(admin) -> None:
    service_admin, service = admin

    service_admin.set_hidden(service, A1, True, "wrong product", user())
    assert service.pipeline.review.blocked_assets == sorted([A1, A3])

    service_admin.set_hidden(service, A1, False, "", user())
    # The identity review's own block stays.
    assert service.pipeline.review.blocked_assets == [A3]
    assert [r["hidden"] for r in service_admin.store.image_events()] == [True, False]


def test_hidden_images_stay_hidden_after_a_reload(admin) -> None:
    service_admin, service = admin
    service_admin.set_hidden(service, A2, True, "", user())

    fresh = fake_service()
    service_admin.prepare(fresh)

    assert A2 in fresh.pipeline.review.blocked_assets


def test_entity_filters(admin) -> None:
    service_admin, service = admin
    service_admin.set_hidden(service, A1, True, "", user())
    service_admin.edit(service, "e1", {"origin": "Huế"}, "", user())

    def ids(flt: str, q: str = "") -> list[str]:
        return [r["id"] for r in service_admin.list_entities(service, q, flt, 0, 10)["items"]]

    assert ids("all") == ["e2", "e1"]  # by name: "Nước dừa" < "Siro ho..."
    assert ids("needs_review") == ["e1"]
    assert ids("no_brand") == ["e2"]
    assert ids("no_category") == ["e2"]
    assert ids("pending") == ["e1"]
    assert ids("hidden_images") == ["e1"]
    assert ids("all", "bao thanh") == ["e1"]
    assert ids("all", "dừa") == ["e2"]


def test_detail_lists_relations_and_images(admin) -> None:
    service_admin, service = admin

    detail = service_admin.entity(service, "e1")

    assert {(e["rel"], e["target"]) for e in detail["edges"]} == {
        ("MADE_BY", "brand:bt"),
        ("IS_A", "category:drug.oral"),
        ("TREATS", "purpose:ho"),
    }
    assert [i["asset_id"] for i in detail["images"]] == [A1, A2]
    assert detail["label"]["needs_review"] is True


def test_release_lists_pending_fields_and_refuses_when_empty(admin) -> None:
    from visualseek_api.core.errors import ApplicationError

    service_admin, service = admin
    with pytest.raises(ApplicationError) as error:
        service_admin.start_release(service, user())
    assert error.value.code == "nothing_to_release"

    service_admin.edit(service, "e1", {"category": "food.drink"}, "", user())
    [item] = service_admin.release_status(service)["pending"]
    assert (item["entity_id"], item["field"]) == ("e1", "category")
    assert (item["before"]["id"], item["after"]["id"]) == ("drug.oral", "food.drink")
    assert item["after"]["l1"] == "food"


def test_routes_need_the_admin_role(client: TestClient) -> None:
    assert client.get("/api/v1/admin/overview", headers=token("user")).status_code == 403
    assert client.get("/api/v1/admin/overview").status_code == 401

    overview = client.get("/api/v1/admin/overview", headers=token()).json()
    assert overview["entities"] == 2
    assert overview["needs_review"] == 1


def test_routes_edit_and_hide(client: TestClient) -> None:
    edited = client.post(
        "/api/v1/admin/entities/e1/changes",
        json={"changes": {"reviewed": True}},
        headers=token(),
    )
    hidden = client.post(
        f"/api/v1/admin/images/{A2}/visibility", json={"hidden": True}, headers=token()
    )
    plain_user = client.post(
        f"/api/v1/admin/images/{A1}/visibility", json={"hidden": True}, headers=token("user")
    )

    assert edited.status_code == 200 and edited.json()["pending"] == ["reviewed"]
    assert hidden.status_code == 200
    assert plain_user.status_code == 403
    entity = client.get("/api/v1/admin/entities/e1", headers=token()).json()
    assert [i["hidden"] for i in entity["images"]] == [False, True]


def test_preview_shows_saved_and_unsaved_edits_without_writing(admin) -> None:
    service_admin, service = admin
    service_admin.edit(service, "e1", {"origin": "Huế"}, "", user())
    before = json.dumps(service.categories, sort_keys=True)

    result = service_admin.preview(service, "e1", {"treats": ["đau họng"]}, 3)

    rows = {(r["rel"], r["label"]): r["change"] for r in result["table"]}
    assert rows["ORIGIN", "Huế"] == "added"  # saved draft
    assert rows["TREATS", "đau họng"] == "added"  # unsaved form value
    assert rows["TREATS", "ho"] == "removed"
    assert rows["MADE_BY", "Bảo Thanh"] is None
    assert (result["added"], result["removed"]) == (2, 1)
    assert {"field": "origin", "before": "", "after": "Huế"} in result["attributes"]
    assert json.dumps(service.categories, sort_keys=True) == before
    assert len(service_admin.store.changes()) == 1


def test_preview_without_edits_is_the_live_graph_and_a_brand_edit_moves_the_edge(admin) -> None:
    service_admin, service = admin

    same = service_admin.preview(service, "e1", {}, 0)
    assert (same["added"], same["removed"], same["attributes"]) == (0, 0, [])

    moved = service_admin.preview(service, "e1", {"brand": "new:Hãng Mới"}, 0)
    rows = {(r["rel"], r["label"]): r["change"] for r in moved["table"]}
    assert rows["MADE_BY", "Hãng Mới"] == "added"
    assert rows["MADE_BY", "Bảo Thanh"] == "removed"

    unlabelled = service_admin.preview(service, "e2", {"treats": ["giải khát"]}, 0)
    assert unlabelled["blocked"] == "category_required"


def test_vocabulary(client: TestClient) -> None:
    brands = client.get("/api/v1/admin/vocab/brand?q=bao", headers=token()).json()
    purposes = client.get("/api/v1/admin/vocab/purpose", headers=token()).json()

    assert brands == [{"id": "brand:bt", "label": "Bảo Thanh", "count": 1}]
    assert purposes == [{"id": "purpose:ho", "label": "ho", "count": 1}]
