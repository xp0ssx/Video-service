import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app


@pytest.fixture
def settings() -> Settings:
    return Settings(
        _env_file=None,
        postgres_password="unit-test-value-never-used-in-a-running-database",
        s3_access_key="unit-test-access-key",
        s3_secret_key="unit-test-value-never-used-in-a-running-storage",
    )


@pytest.fixture
def client(settings: Settings):
    with TestClient(create_app(settings)) as test_client:
        yield test_client
