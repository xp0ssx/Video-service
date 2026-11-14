import os
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.core.config import get_settings
from app.db.session import Database
from app.main import create_app
from app.modules.auth.models import Channel, User

from .test_auth import ORIGIN, data

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(os.getenv("RUN_INTEGRATION") != "1", reason="PostgreSQL required"),
]


@pytest.mark.parametrize("field", ["email", "username"])
def test_concurrent_normalized_registration(field):
    settings = get_settings()
    first, second = data(), data()
    second[field] = " " + first[field].upper() + " "
    barrier = Barrier(2)

    def attempt(body):
        with TestClient(create_app(settings)) as client:
            barrier.wait(timeout=10)
            return client.post("/api/auth/register", json=body, headers=ORIGIN).status_code

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(attempt, [first, second]))
    assert sorted(results) == [201, 409]
    database = Database(settings)
    try:
        with database.session_factory() as db:
            match = getattr(User, field) == first[field]
            assert db.scalar(select(func.count()).select_from(User).where(match)) == 1
            assert db.scalar(select(func.count()).select_from(Channel).join(User).where(match)) == 1
    finally:
        database.close()


def test_registration_rolls_back_when_channel_creation_fails():
    settings, body = get_settings(), data()
    with TestClient(create_app(settings), raise_server_exceptions=False) as client:
        with patch(
            "app.modules.auth.service.Channel", side_effect=RuntimeError("Injected failure")
        ):
            response = client.post("/api/auth/register", json=body, headers=ORIGIN)
        assert response.status_code == 500
    database = Database(settings)
    try:
        with database.session_factory() as db:
            assert db.scalar(select(User).where(User.email == body["email"])) is None
    finally:
        database.close()
