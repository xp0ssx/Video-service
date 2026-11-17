"""Real PostgreSQL regression for the seed's filesystem/transaction crash boundary."""

import os
from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.cli.seed_demo import Demo, read_private
from app.modules.auth.models import Channel, User
from app.modules.auth.service import hasher

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(os.getenv("RUN_INTEGRATION") != "1", reason="Isolated PostgreSQL required"),
]


@pytest.mark.parametrize("phase", ["prepared", "committed"])
@pytest.mark.parametrize("key", ["author", "moderator"])
def test_seed_crash_boundary_no_duplicates_and_preserves_edits(auth, tmp_path, phase, key):
    _, factory = auth
    # The auth fixture's outer transaction keeps this regression inside test-db.
    connection = factory.kw["bind"]

    @contextmanager
    def outer():
        with connection.begin_nested():
            yield connection

    database = SimpleNamespace(session_factory=factory, engine=SimpleNamespace(begin=outer))
    demo = Demo(database, tmp_path, "http://unused", fail_after=phase + ":user-" + key)
    if key == "moderator":
        demo.user("admin")
    credentials = demo.credentials_path.read_bytes()
    with pytest.raises(RuntimeError, match="Injected"):
        # Demo opens its own outer transaction; here use a nested transaction as the
        # fixture owns the test's outer rollback.
        demo.user(key)
    retry = Demo(database, tmp_path, "http://unused")
    identifier = retry.user(key)
    with factory() as db:
        user = db.get(User, identifier)
        original_password = user.password_hash
        changed_role = "user" if key == "moderator" else "moderator"
        user.role = changed_role
        user.password_hash = hasher.hash("Synthetic changed password")
        changed_password = user.password_hash
        channel = db.scalar(select(Channel).where(Channel.user_id == identifier))
        channel.title = "User changed channel"
        db.commit()
    assert retry.user(key) == identifier
    with factory() as db:
        rows = db.scalars(
            select(User).where(User.username == retry.accounts[key]["username"])
        ).all()
        assert len(rows) == 1
        assert rows[0].role == changed_role
        assert rows[0].password_hash == changed_password != original_password
        assert (
            db.scalar(select(Channel.title).where(Channel.user_id == identifier))
            == "User changed channel"
        )
    assert demo.credentials_path.read_bytes() == credentials
    assert demo.credentials_path.stat().st_mode & 0o777 == 0o600
    assert read_private(demo.state_path)["resources"]["user-" + key]["done"]
