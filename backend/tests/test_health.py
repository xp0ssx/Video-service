import pytest


class Dependency:
    def __init__(self, healthy: bool = True):
        self.healthy = healthy
        self.calls = 0

    def check_ready(self) -> None:
        self.calls += 1
        if not self.healthy:
            raise RuntimeError("private-driver-detail-must-not-be-exposed")


def test_liveness_ignores_unavailable_dependencies(client):
    database = Dependency(False)
    storage = Dependency(False)
    client.app.state.database = database
    client.app.state.storage = storage
    response = client.get("/health/live")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert database.calls == storage.calls == 0


@pytest.mark.parametrize("database_ok,storage_ok", [(True, True), (False, True), (True, False)])
def test_readiness_reports_real_dependency_status(client, caplog, database_ok, storage_ok):
    client.app.state.database = Dependency(database_ok)
    client.app.state.storage = Dependency(storage_ok)
    response = client.get("/health/ready")
    assert response.status_code == (200 if database_ok and storage_ok else 503)
    assert response.json()["checks"] == {
        "database": "ok" if database_ok else "error",
        "storage": "ok" if storage_ok else "error",
    }
    assert "private-driver-detail" not in response.text
    assert "private-driver-detail" not in caplog.text


def test_openapi_and_swagger_are_available(client):
    schema = client.get("/openapi.json")
    assert schema.status_code == 200
    assert schema.json()["info"]["title"] == "Video-service API"
    assert "/health/live" in schema.json()["paths"]
    docs = client.get("/docs")
    assert docs.status_code == 200
    assert "SwaggerUIBundle" in docs.text


def test_unimplemented_api_is_not_a_fake_success(client):
    assert client.get("/api/not-implemented").status_code == 404
