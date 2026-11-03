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

