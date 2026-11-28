import json
import time
from collections.abc import Iterator
from types import SimpleNamespace

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi.testclient import TestClient

from visualseek_api.core import auth
from visualseek_api.core.config import Settings, get_settings
from visualseek_api.main import app

SUPABASE_URL = "https://project.supabase.co"
KEY_ID = "test-key"
SIGNING_KEY = ec.generate_private_key(ec.SECP256R1())
OTHER_KEY = ec.generate_private_key(ec.SECP256R1())
JWKS = {
    "keys": [
        json.loads(jwt.algorithms.ECAlgorithm.to_jwk(SIGNING_KEY.public_key()))
        | {"kid": KEY_ID, "alg": "ES256", "use": "sig"}
    ]
}
GRAPH_URL = "/api/v1/graph/subgraph?id=a"
PROTECTED = [("get", GRAPH_URL), ("get", "/api/v1/search/info"), ("post", "/api/v1/search/text")]


def claims(**changes: object) -> dict:
    base = {
        "sub": "user-1",
        "email": "user@example.com",
        "aud": "authenticated",
        "iss": f"{SUPABASE_URL}/auth/v1",
        "exp": int(time.time()) + 900,
    }
    merged = base | changes
    return {name: value for name, value in merged.items() if value is not None}


def token(key: object = SIGNING_KEY, kid: str = KEY_ID, algorithm: str = "ES256", **changes) -> str:
    return jwt.encode(claims(**changes), key, algorithm=algorithm, headers={"kid": kid})


def bearer(value: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {value}"}


def settings(**changes: object) -> Settings:
    values = {"supabase_url": SUPABASE_URL, "require_login": True} | changes
    return Settings(_env_file=None, **values)


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setattr(jwt.PyJWKClient, "fetch_data", lambda self: JWKS)
    # Enough of the search service for the graph and info routes to answer 200.
    service = SimpleNamespace(
        info={"collection": "test"},
        graph_view=SimpleNamespace(
            subgraph=lambda *args: {"nodes": [], "edges": [], "truncated": False}
        ),
    )
    monkeypatch.setattr(app.state, "search", service, raising=False)
    # FastAPI reads the override's signature, so it must not take parameters.
    app.dependency_overrides[get_settings] = lambda: settings()
    auth._verifier.cache_clear()
    yield TestClient(app)
    app.dependency_overrides.clear()
    auth._verifier.cache_clear()


def error_code(response) -> str:
    return response.json()["error"]["code"]


@pytest.mark.parametrize(("method", "url"), PROTECTED)
def test_routes_reject_a_request_without_a_token(client: TestClient, method: str, url: str) -> None:
    response = client.request(method, url, json={"query": "x"} if method == "post" else None)

    assert response.status_code == 401
    assert error_code(response) == "not_authenticated"


@pytest.mark.parametrize("url", [GRAPH_URL, "/api/v1/search/info"])
def test_a_valid_token_is_accepted(client: TestClient, url: str) -> None:
    assert client.get(url, headers=bearer(token())).status_code == 200


def test_small_clock_differences_are_tolerated(client: TestClient) -> None:
    just_expired = token(exp=int(time.time()) - 5)

    assert client.get(GRAPH_URL, headers=bearer(just_expired)).status_code == 200


@pytest.mark.parametrize(
    "bad_token",
    [
        pytest.param(lambda: token(exp=int(time.time()) - 120), id="expired"),
        pytest.param(lambda: token(aud="anon"), id="wrong-audience"),
        pytest.param(lambda: token(iss="https://evil.example/auth/v1"), id="wrong-issuer"),
        pytest.param(lambda: token(exp=None), id="no-expiry"),
        pytest.param(lambda: token(sub=None), id="no-subject"),
        pytest.param(lambda: token(is_anonymous=True), id="anonymous"),
        pytest.param(lambda: token(key=OTHER_KEY), id="forged-signature"),
        pytest.param(lambda: token(kid="unknown"), id="unknown-key"),
        pytest.param(
            lambda: token(key="a-shared-secret-that-is-long-enough-for-hs256", algorithm="HS256"),
            id="symmetric-algorithm",
        ),
        pytest.param(lambda: token(key=None, algorithm="none"), id="unsigned"),
        pytest.param(lambda: "not-a-token", id="garbage"),
    ],
)
def test_untrusted_tokens_are_rejected(client: TestClient, bad_token) -> None:
    response = client.get(GRAPH_URL, headers=bearer(bad_token()))

    assert response.status_code == 401
    assert error_code(response) == "invalid_token"


def test_unreachable_key_server_is_not_reported_as_a_bad_token(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def offline(self):
        raise jwt.PyJWKClientConnectionError("offline")

    monkeypatch.setattr(jwt.PyJWKClient, "fetch_data", offline)
    response = client.get(GRAPH_URL, headers=bearer(token()))

    assert response.status_code == 503
    assert error_code(response) == "auth_unavailable"


def test_missing_supabase_url_keeps_the_routes_closed(client: TestClient) -> None:
    app.dependency_overrides[get_settings] = lambda: settings(supabase_url="")

    for headers in ({}, bearer(token())):
        response = client.get(GRAPH_URL, headers=headers)
        assert response.status_code == 503
        assert error_code(response) == "auth_unconfigured"


def test_login_can_be_switched_off_for_local_tools(client: TestClient) -> None:
    app.dependency_overrides[get_settings] = lambda: settings(require_login=False)

    assert client.get(GRAPH_URL).status_code == 200


def test_login_is_required_unless_configured_otherwise(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("REQUIRE_LOGIN", raising=False)

    assert Settings(_env_file=None).require_login is True


def test_health_and_images_stay_public(client: TestClient) -> None:
    app.state.search.image_file = lambda asset_id: None

    assert client.get("/api/v1/health").status_code == 200
    # 404 from the image lookup itself: the request got past any login check.
    assert client.get(f"/api/v1/images/{'0' * 64}").status_code == 404


def test_only_health_and_images_are_declared_public() -> None:
    """A route added later must be protected, or be listed here on purpose."""
    public = {
        path
        for path, operations in app.openapi()["paths"].items()
        if any("security" not in operation for operation in operations.values())
    }

    assert public == {"/api/v1/health", "/api/v1/images/{asset_id}"}


def test_browser_can_read_a_rejection_across_origins(client: TestClient) -> None:
    origin = {"Origin": "http://localhost:3000"}
    preflight = client.options(
        GRAPH_URL,
        headers=origin
        | {
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "authorization",
        },
    )
    rejected = client.get(GRAPH_URL, headers=origin)

    assert preflight.status_code == 200
    assert rejected.status_code == 401
    assert rejected.headers["access-control-allow-origin"] == "http://localhost:3000"


ADMIN_URL = "/api/v1/admin/me"


def test_admin_routes_need_a_token(client: TestClient) -> None:
    response = client.get(ADMIN_URL)

    assert response.status_code == 401
    assert error_code(response) == "not_authenticated"


@pytest.mark.parametrize("role", [None, "user", "Admin", ["admin"]])
def test_admin_routes_refuse_other_roles(client: TestClient, role) -> None:
    response = client.get(ADMIN_URL, headers=bearer(token(user_role=role)))

    assert response.status_code == 403
    assert error_code(response) == "forbidden"


def test_admin_routes_accept_an_admin(client: TestClient) -> None:
    response = client.get(ADMIN_URL, headers=bearer(token(user_role="admin")))

    assert response.status_code == 200
    assert response.json() == {"id": "user-1", "email": "user@example.com", "role": "admin"}


def test_a_forged_admin_claim_is_rejected(client: TestClient) -> None:
    response = client.get(ADMIN_URL, headers=bearer(token(key=OTHER_KEY, user_role="admin")))

    assert response.status_code == 401


def test_switching_login_off_does_not_open_admin(client: TestClient) -> None:
    app.dependency_overrides[get_settings] = lambda: settings(require_login=False)

    assert client.get(ADMIN_URL).status_code == 401
    assert client.get(ADMIN_URL, headers=bearer(token(user_role="user"))).status_code == 403
    assert client.get(ADMIN_URL, headers=bearer(token(user_role="admin"))).status_code == 200


def test_a_plain_user_still_searches(client: TestClient) -> None:
    assert client.get(GRAPH_URL, headers=bearer(token(user_role="user"))).status_code == 200


def test_key_refresh_failure_keeps_the_cached_keys(monkeypatch) -> None:
    verifier = auth.TokenVerifier("https://project.supabase.co")
    monkeypatch.setattr(jwt.PyJWKClient, "fetch_data", lambda self: JWKS)
    assert verifier.refresh_keys() is True
    cached = verifier._keys.jwk_set_cache.get()

    def offline(self):
        raise jwt.PyJWKClientConnectionError("offline")

    monkeypatch.setattr(jwt.PyJWKClient, "fetch_data", offline)

    assert verifier.refresh_keys() is False
    assert verifier._keys.jwk_set_cache.get() is cached

