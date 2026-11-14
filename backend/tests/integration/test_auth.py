import os
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.core.config import get_settings
from app.modules.auth.models import Channel, Session, User
from app.modules.auth.service import digest, hasher

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(os.getenv("RUN_INTEGRATION") != "1", reason="PostgreSQL required"),
]
ORIGIN = {"Origin": "http://localhost:8080"}
PASSWORD = "test-password-12345"


def data():
    name = "test_" + uuid4().hex[:15]
    return {"username": name, "email": name + "@example.com", "password": PASSWORD}


def register(auth):
    client, factory = auth
    body = data()
    result = client.post("/api/auth/register", json=body, headers=ORIGIN)
    assert result.status_code == 201, result.text
    return body, result


def test_register_channel_hash_cookie_and_restore(auth):
    client, factory = auth
    body, result = register(auth)
    assert result.json()["user"]["role"] == "user"
    assert "email" not in result.json()["user"]
    header = result.headers["set-cookie"]
    assert "HttpOnly" in header and "SameSite=lax" in header and "Secure" not in header
    with factory() as db:
        user = db.scalar(select(User).where(User.email == body["email"]))
        assert user.password_hash.startswith("$argon2id$")
        assert hasher.verify(user.password_hash, PASSWORD)
        assert db.scalar(select(Channel).where(Channel.user_id == user.id)) is not None
        session = db.scalar(select(Session).where(Session.user_id == user.id))
        assert session.token_hash == digest(client.cookies["session_id"])
        assert session.token_hash != client.cookies["session_id"]
    assert client.get("/api/auth/session").json() == result.json()
    assert "no-store" in result.headers["cache-control"]


@pytest.mark.parametrize("field", ["email", "username"])
def test_normalized_duplicate(auth, field):
    client, _ = auth
    body, _ = register(auth)
    second = data()
    second[field] = " " + body[field].upper() + " "
    assert client.post("/api/auth/register", json=second, headers=ORIGIN).status_code == 409


def test_login_logout_and_old_token(auth):
    client, _ = auth
    body, first = register(auth)
    old = client.cookies["session_id"]
    login = client.post(
        "/api/auth/login",
        json={"email": body["email"].upper(), "password": PASSWORD},
        headers=ORIGIN,
    )
    assert login.status_code == 200
    assert client.cookies["session_id"] != old
    new = client.cookies["session_id"]
    response = client.post(
        "/api/auth/logout", headers={**ORIGIN, "X-CSRF-Token": login.json()["csrf_token"]}
    )
    assert response.status_code == 204
    assert client.get("/api/auth/session").status_code == 401
    client.cookies.set("session_id", new)
    assert client.get("/api/auth/session").status_code == 401
    client.cookies.set("session_id", old)
    assert client.get("/api/auth/session").status_code == 401


def test_wrong_password_is_indistinguishable(auth):
    client, _ = auth
    body, _ = register(auth)
    a = client.post(
        "/api/auth/login",
        json={"email": body["email"], "password": "wrong-password-123"},
        headers=ORIGIN,
    )
    b = client.post(
        "/api/auth/login",
        json={"email": "unknown@example.com", "password": "wrong-password-123"},
        headers=ORIGIN,
    )
    assert a.status_code == b.status_code == 401
    assert a.json()["message"] == b.json()["message"]
    assert a.json()["code"] == b.json()["code"]


@pytest.mark.parametrize("headers", [{}, {"Origin": "https://evil.example"}])
def test_origin_registration(auth, headers):
    assert auth[0].post("/api/auth/register", json=data(), headers=headers).status_code == 403


@pytest.mark.parametrize(
    "headers",
    [
        ORIGIN,
        {**ORIGIN, "X-CSRF-Token": "bad"},
        {"Origin": "https://evil.example", "X-CSRF-Token": "bad"},
    ],
)
def test_csrf_logout_preserves_session(auth, headers):
    client, _ = auth
    register(auth)
    assert client.post("/api/auth/logout", headers=headers).status_code == 403
    assert client.get("/api/auth/session").status_code == 200


@pytest.mark.parametrize("role", ["admin", "moderator"])
def test_role_injection_rejected(auth, role):
    body = data()
    body["role"] = role
    assert auth[0].post("/api/auth/register", json=body, headers=ORIGIN).status_code == 422


def test_expiry_and_current_database_role(auth):
    client, factory = auth
    _, response = register(auth)
    assert client.get("/test-admin").status_code == 403
    with factory() as db:
        user = db.get(User, __import__("uuid").UUID(response.json()["user"]["id"]))
        user.role = "admin"
        db.commit()
    assert client.get("/test-admin").status_code == 200
    with factory() as db:
        user = db.get(User, __import__("uuid").UUID(response.json()["user"]["id"]))
        user.role = "moderator"
        db.commit()
    assert client.get("/test-admin").status_code == 403
    assert client.get("/api/auth/session").json()["user"]["role"] == "moderator"
    with factory() as db:
        session = db.scalar(
            select(Session).where(Session.token_hash == digest(client.cookies["session_id"]))
        )
        session.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        db.commit()
    assert client.get("/api/auth/session").status_code == 401
    assert client.get("/test-admin").status_code == 401


def test_bootstrap_admin_cli_does_not_overwrite(auth, monkeypatch):
    from fastapi import HTTPException

    from app.cli import create_admin

    _, factory = auth
    body = data()
    monkeypatch.setattr(
        "sys.argv", ["create_admin", "--email", body["email"], "--username", body["username"]]
    )
    monkeypatch.setattr(create_admin, "getpass", lambda prompt: PASSWORD)
    monkeypatch.setattr(
        create_admin,
        "Database",
        lambda settings: SimpleNamespace(session_factory=factory, close=lambda: None),
    )
    create_admin.main()
    with factory() as db:
        user = db.scalar(select(User).where(User.email == body["email"]))
        assert user.role == "admin"
        original = user.password_hash
    with pytest.raises(HTTPException):
        create_admin.main()
    with factory() as db:
        assert db.scalar(select(User).where(User.email == body["email"])).password_hash == original


def test_https_cookie_and_origin(auth):
    client, _ = auth
    client.app.state.settings = get_settings().model_copy(
        update={"app_origin": "https://testserver"}
    )
    client.base_url = "https://testserver"
    response = client.post(
        "/api/auth/register", json=data(), headers={"Origin": "https://testserver"}
    )
    assert response.status_code == 201
    assert "Secure" in response.headers["set-cookie"]
    assert "HttpOnly" in response.headers["set-cookie"]
    assert client.get("/api/auth/session").status_code == 200
