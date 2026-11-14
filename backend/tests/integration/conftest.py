from types import SimpleNamespace

import pytest
from fastapi import Depends
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from app.core.config import get_settings
from app.db.session import Database
from app.main import create_app
from app.modules.auth.router import require_roles


@pytest.fixture
def auth():
    settings = get_settings()
    database = Database(settings)
    with database.engine.connect() as connection:
        transaction = connection.begin()
        factory = sessionmaker(
            bind=connection, expire_on_commit=False, join_transaction_mode="create_savepoint"
        )
        app = create_app(settings)

        gate = Depends(require_roles("admin"))

        @app.get("/test-admin")
        def admin(user=gate):
            return {"role": user.role}

        with TestClient(app) as client:
            app.state.database = SimpleNamespace(session_factory=factory)
            yield client, factory
        transaction.rollback()
    database.close()
