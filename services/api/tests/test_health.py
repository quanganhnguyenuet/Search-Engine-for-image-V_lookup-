from fastapi.testclient import TestClient

from visualseek_api.main import app


def test_health_check() -> None:
    with TestClient(app) as client:
        response = client.get("/api/v1/health")

    assert response.status_code == 200
    assert response.json() == {
        "service": "visualseek-api",
        "status": "ok",
        "version": "0.1.0",
    }
    assert response.headers["x-request-id"]



def test_docs_are_off_in_production(monkeypatch) -> None:
    from visualseek_api.core.config import get_settings
    from visualseek_api.main import create_app

    monkeypatch.setenv("APP_ENV", "production")
    get_settings.cache_clear()
    try:
        with TestClient(create_app()) as client:
            assert client.get("/docs").status_code == 404
            assert client.get("/openapi.json").status_code == 404
            assert client.get("/api/v1/health").status_code == 200
    finally:
        get_settings.cache_clear()
