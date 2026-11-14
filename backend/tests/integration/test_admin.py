import os
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select

from app.modules.admin.models import ModerationAction
from app.modules.admin.repository import AdminRepository
from app.modules.auth.models import User

from .test_auth import ORIGIN, register

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(os.getenv("RUN_INTEGRATION") != "1", reason="PostgreSQL required"),
]


def setup_admin(auth, role="admin"):
    client, factory = auth
    body, result = register(auth)
    actor_id = UUID(result.json()["user"]["id"])
    with factory() as db:
        db.get(User, actor_id).role = role
        target = User(
            email=f"{uuid4().hex}@example.com",
            username="target_" + uuid4().hex[:12],
            password_hash="not-used-for-login",
        )
        db.add(target)
        db.commit()
        target_id, username = target.id, target.username
    headers = {**ORIGIN, "X-CSRF-Token": result.json()["csrf_token"]}
    return actor_id, target_id, username, headers


def test_admin_lookup_role_changes_and_audit(auth):
    client, factory = auth
    actor, target, username, headers = setup_admin(auth)
    response = client.get("/api/admin/users", params={"username": " " + username.upper() + " "})
    assert response.status_code == 200
    assert response.json() == [{"id": str(target), "username": username, "role": "user"}]
    assert "no-store" in response.headers["cache-control"]
    assert client.get("/api/admin/users", params={"username": "missing"}).json() == []
    for role in ("moderator", "user"):
        result = client.put(
            f"/api/admin/users/{target}/role",
            json={"role": role, "reason": "Reviewed assignment"},
            headers=headers,
        )
        assert result.status_code == 200
        assert set(result.json()) == {"id", "username", "role"}
        assert result.json()["role"] == role
    with factory() as db:
        rows = db.scalars(
            select(ModerationAction)
            .where(ModerationAction.target_user_id == target)
            .order_by(ModerationAction.created_at)
        ).all()
        assert len(rows) == 2
        assert {(row.previous_value, row.new_value) for row in rows} == {
            ("user", "moderator"),
            ("moderator", "user"),
        }
        assert all(
            row.actor_id == actor and row.action == "set_role" and row.reason for row in rows
        )


@pytest.mark.parametrize("role", ["user", "moderator"])
def test_non_admin_cannot_lookup_or_change_roles(auth, role):
    client, factory = auth
    _, target, username, headers = setup_admin(auth, role)
    assert client.get("/api/admin/users", params={"username": username}).status_code == 403
    assert (
        client.put(
            f"/api/admin/users/{target}/role",
            json={"role": "moderator", "reason": "x"},
            headers=headers,
        ).status_code
        == 403
    )
    with factory() as db:
        assert db.get(User, target).role == "user"
        assert db.scalar(select(ModerationAction)) is None


@pytest.mark.parametrize("new_role", ["admin", "invalid"])
def test_cannot_assign_admin_or_invalid_role(auth, new_role):
    client, factory = auth
    _, target, _, headers = setup_admin(auth)
    assert (
        client.put(
            f"/api/admin/users/{target}/role",
            json={"role": new_role, "reason": "x"},
            headers=headers,
        ).status_code
        == 422
    )
    with factory() as db:
        assert db.get(User, target).role == "user"
        assert db.scalar(select(ModerationAction)) is None


def test_cannot_change_existing_admin(auth):
    client, factory = auth
    actor, _, _, headers = setup_admin(auth)
    assert (
        client.put(
            f"/api/admin/users/{actor}/role", json={"role": "user", "reason": "x"}, headers=headers
        ).status_code
        == 403
    )
    with factory() as db:
        assert db.get(User, actor).role == "admin"
        assert db.scalar(select(ModerationAction)) is None


@pytest.mark.parametrize("failure", ["origin", "missing_origin", "csrf", "missing_csrf", "session"])
def test_role_change_requires_origin_csrf_and_session(auth, failure):
    client, factory = auth
    _, target, _, headers = setup_admin(auth)
    if failure == "origin":
        headers["Origin"] = "https://evil.example"
    elif failure == "missing_origin":
        headers.pop("Origin")
    elif failure == "csrf":
        headers["X-CSRF-Token"] = "wrong"
    elif failure == "missing_csrf":
        headers.pop("X-CSRF-Token")
    else:
        client.cookies.clear()
    assert client.put(
        f"/api/admin/users/{target}/role",
        json={"role": "moderator", "reason": "x"},
        headers=headers,
    ).status_code == (401 if failure == "session" else 403)
    with factory() as db:
        assert db.get(User, target).role == "user"
        assert db.scalar(select(ModerationAction)) is None
    if failure == "session":
        assert client.get("/api/admin/users", params={"username": "anyone"}).status_code == 401


def test_role_and_audit_rollback_together(auth, monkeypatch):
    client, factory = auth
    _, target, _, headers = setup_admin(auth)
    original = AdminRepository.record_role_change

    def fail_after_audit(self, *args):
        original(self, *args)
        self.db.flush()
        raise RuntimeError("Injected audit failure")

    monkeypatch.setattr(AdminRepository, "record_role_change", fail_after_audit)
    with pytest.raises(RuntimeError, match="Injected audit failure"):
        client.put(
            f"/api/admin/users/{target}/role",
            json={"role": "moderator", "reason": "x"},
            headers=headers,
        )
    with factory() as db:
        assert db.get(User, target).role == "user"
        assert db.scalar(select(ModerationAction)) is None


def test_missing_user_and_required_reason(auth):
    client, _ = auth
    _, target, _, headers = setup_admin(auth)
    assert (
        client.put(
            f"/api/admin/users/{uuid4()}/role",
            json={"role": "user", "reason": "x"},
            headers=headers,
        ).status_code
        == 404
    )
    for reason in (None, "", "   "):
        assert (
            client.put(
                f"/api/admin/users/{target}/role",
                json={"role": "moderator", "reason": reason},
                headers=headers,
            ).status_code
            == 422
        )


def test_admin_access_tracks_session_expiry_and_current_role(auth):
    from datetime import UTC, datetime, timedelta

    from app.modules.auth.models import Session
    from app.modules.auth.service import digest

    client, factory = auth
    actor, target, username, headers = setup_admin(auth)
    with factory() as db:
        db.get(User, actor).role = "moderator"
        db.commit()
    assert client.get("/api/admin/users", params={"username": username}).status_code == 403
    assert (
        client.put(
            f"/api/admin/users/{target}/role",
            json={"role": "moderator", "reason": "x"},
            headers=headers,
        ).status_code
        == 403
    )
    with factory() as db:
        db.get(User, actor).role = "admin"
        db.scalar(
            select(Session).where(Session.token_hash == digest(client.cookies["session_id"]))
        ).expires_at = datetime.now(UTC) - timedelta(seconds=1)
        db.commit()
    assert client.get("/api/admin/users", params={"username": username}).status_code == 401
    assert (
        client.put(
            f"/api/admin/users/{target}/role",
            json={"role": "moderator", "reason": "x"},
            headers=headers,
        ).status_code
        == 401
    )
