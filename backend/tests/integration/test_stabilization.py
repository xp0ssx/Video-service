import os
import subprocess
from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

from app.core.config import get_settings
from app.modules.auth.models import User

from .test_auth import ORIGIN
from .test_channels_playlists import case as case
from .test_feed_interactions import social as social

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(os.getenv("RUN_INTEGRATION") != "1", reason="Isolated PostgreSQL required"),
]


@pytest.mark.parametrize("name", ["%00", "abc%00def", "%2500", "ab", "a" * 33, "abc!", "a%0Ab"])
def test_invalid_username_endpoints(case, name):
    c, f, owner, uid, h, cookie, ids, pid = case
    with f() as db:
        db.get(User, uid).role = "admin"
        db.commit()
    requests = [
        ("GET", f"/api/channels/{name}"),
        ("GET", f"/api/channels/{name}/videos"),
        ("GET", f"/api/channels/{name}/playlists"),
        ("PUT", f"/api/channels/{name}/subscription"),
        ("DELETE", f"/api/channels/{name}/subscription"),
        ("GET", "/api/admin/users?username=" + name),
        ("PUT", f"/api/videos/{ids[0]}/access/{name}"),
        ("DELETE", f"/api/videos/{ids[0]}/access/{name}"),
    ]
    for method, url in requests:
        response = c.request(method, url, headers=h)
        assert response.status_code == 422
        assert not any(word in response.text.lower() for word in ("sql", "traceback", "psycopg"))


@pytest.mark.parametrize("length", [3, 32])
def test_lookup_username_boundaries_and_normalization(auth, length):
    c, f = auth
    name = ("u" + uuid4().hex)[:length]
    body = {"username": name, "email": uuid4().hex + "@example.com", "password": uuid4().hex}
    result = c.post("/api/auth/register", headers=ORIGIN, json=body)
    assert result.status_code == 201
    uid = UUID(result.json()["user"]["id"])
    with f() as db:
        db.get(User, uid).role = "admin"
        db.commit()
    normalized = "  " + name.upper() + "  "
    assert c.get("/api/channels/" + normalized).json()["username"] == name
    assert c.get("/api/admin/users", params={"username": normalized}).json()[0]["username"] == name
    assert c.get("/api/channels/" + "z" * length).status_code == 404


@pytest.mark.parametrize(
    "status,note,reviewer,reviewed,action,valid",
    [
        ("open", None, False, False, False, True),
        ("dismissed", "Reviewed", True, True, False, True),
        ("actioned", "Reviewed", True, True, True, True),
        ("dismissed", None, True, True, False, False),
        ("actioned", None, True, True, True, False),
        ("dismissed", "", True, True, False, False),
        ("dismissed", "x", False, True, False, False),
        ("dismissed", "x", True, False, False, False),
        ("open", "x", False, False, False, False),
        ("actioned", "x", True, True, False, False),
        ("dismissed", "x", True, True, True, False),
    ],
)
def test_report_resolution_direct_sql(social, status, note, reviewer, reviewed, action, valid):
    c, f, h, user, video = social
    with f() as db:
        aid = uuid4()
        db.execute(
            text(
                "INSERT INTO moderation_actions "
                "(id,actor_id,video_id,action,"
                "previous_value,new_value,reason) "
                "VALUES (:id,:u,:v,'hide','visible','hidden','Test')"
            ),
            {"id": aid, "u": user, "v": video},
        )
        params = {
            "id": uuid4(),
            "v": video,
            "u": user,
            "status": status,
            "note": note,
            "reviewer": user if reviewer else None,
            "action": aid if action else None,
        }
        statement = text(
            "INSERT INTO reports (id,video_id,reporter_id,reason,body,status,"
            "review_note,reviewed_by,reviewed_at,resolved_action_id) VALUES "
            "(:id,:v,:u,'spam','Test',:status,:note,:reviewer,"
            + ("now()" if reviewed else "NULL")
            + ",:action)"
        )
        if valid:
            db.execute(statement, params)
        else:
            with pytest.raises(IntegrityError), db.begin_nested():
                db.execute(statement, params)
        # Check UPDATE too: start with a valid open report, then attempt the same state.
        db.execute(text("DELETE FROM reports WHERE video_id=:v"), {"v": video})
        db.execute(
            text(
                "INSERT INTO reports (id,video_id,reporter_id,reason,body) "
                "VALUES (:id,:v,:u,'spam','Test')"
            ),
            params,
        )
        update = text(
            "UPDATE reports SET status=:status,review_note=:note,reviewed_by=:reviewer,"
            "reviewed_at="
            + ("now()" if reviewed else "NULL")
            + ",resolved_action_id=:action WHERE id=:id"
        )
        if valid:
            db.execute(update, params)
        else:
            with pytest.raises(IntegrityError), db.begin_nested():
                db.execute(update, params)


@pytest.fixture
def migration_db():
    # Dedicated database created only in test-db, never the working database.
    settings = get_settings()
    assert settings.postgres_host == "test-db"
    admin = create_engine(settings.database_url, isolation_level="AUTOCOMMIT")
    name = "stabilization_" + uuid4().hex
    with admin.connect() as connection:
        connection.execute(text("CREATE DATABASE " + name))
    engine = create_engine(settings.database_url.set(database=name))

    def migrate(direction, target, success=True):
        result = subprocess.run(
            ["alembic", direction, target],
            env={**os.environ, "POSTGRES_DB": name},
            capture_output=True,
            text=True,
        )
        assert (result.returncode == 0) == success, result.stderr
        return result.stderr

    try:
        yield engine, migrate
    finally:
        engine.dispose()
        with admin.connect() as connection:
            connection.execute(text("DROP DATABASE " + name))
        admin.dispose()


def snapshot(engine):
    with engine.connect() as c:
        return (
            c.execute(text("SELECT * FROM alembic_version")).all(),
            c.execute(
                text(
                    "SELECT table_name,column_name,is_nullable FROM information_schema.columns "
                    "WHERE table_schema='public' ORDER BY 1,2"
                )
            ).all(),
            c.execute(
                text(
                    "SELECT conname,pg_get_constraintdef(oid) FROM pg_constraint "
                    "WHERE connamespace='public'::regnamespace ORDER BY 1"
                )
            ).all(),
            c.execute(
                text("SELECT row_to_json(a)::text FROM moderation_actions a ORDER BY id")
            ).all(),
            c.execute(text("SELECT row_to_json(r)::text FROM reports r ORDER BY id")).all(),
        )


def test_migrations_clean_roundtrip(migration_db):
    engine, migrate = migration_db
    migrate("upgrade", "head")
    migrate("downgrade", "base")
    migrate("upgrade", "head")
    result = subprocess.run(
        ["alembic", "check"],
        env={**os.environ, "POSTGRES_DB": engine.url.database},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def seed_history(engine, action="hide", invalid=False):
    uid, cid, vid, aid = [uuid4() for _ in range(4)]
    with engine.begin() as c:
        c.execute(
            text(
                "INSERT INTO users(id,email,username,password_hash) "
                "VALUES (:u,'x@example.com','test','unused')"
            ),
            {"u": uid},
        )
        c.execute(
            text("INSERT INTO channels(id,user_id,title) VALUES (:c,:u,'Test')"),
            {"c": cid, "u": uid},
        )
        c.execute(
            text("INSERT INTO videos(id,channel_id,title) VALUES (:v,:c,'Test')"),
            {"v": vid, "c": cid},
        )
        c.execute(
            text(
                "INSERT INTO moderation_actions(id,actor_id,video_id,action,"
                "previous_value,new_value,reason) "
                "VALUES (:a,:u,:v,:action,'visible','hidden','Test')"
            ),
            {"a": aid, "u": uid, "v": vid, "action": action},
        )
        if invalid:
            c.execute(
                text(
                    "INSERT INTO reports(id,video_id,reporter_id,reason,body,status,"
                    "reviewed_by,reviewed_at) "
                    "VALUES (:id,:v,:u,'spam','Original complaint','dismissed',:u,now())"
                ),
                {"id": uuid4(), "v": vid, "u": uid},
            )


@pytest.mark.parametrize("action", ["hide", "restore"])
def test_downgrade_preserves_audit_atomically(migration_db, action):
    engine, migrate = migration_db
    migrate("upgrade", "head")
    seed_history(engine, action)
    before = snapshot(engine)
    assert "Downgrade refused" in migrate("downgrade", "cc64acf89e63", False)
    assert snapshot(engine) == before
    migrate("upgrade", "head")
    assert snapshot(engine) == before


def test_upgrade_refuses_incompatible_reports_without_changes(migration_db):
    engine, migrate = migration_db
    migrate("upgrade", "3f32713066d6")
    seed_history(engine, invalid=True)
    before = snapshot(engine)
    assert "Upgrade refused: 1 reports" in migrate("upgrade", "head", False)
    assert snapshot(engine) == before


def test_upgrade_preserves_compatible_reports_and_refuses_their_downgrade(migration_db):
    engine, migrate = migration_db
    migrate("upgrade", "3f32713066d6")
    seed_history(engine, invalid=True)
    with engine.begin() as c:
        c.execute(text("UPDATE reports SET review_note='Recorded decision'"))
        c.execute(
            text(
                "UPDATE moderation_actions SET action='set_role', "
                "target_user_id=actor_id,video_id=NULL"
            )
        )
    before = snapshot(engine)[3:]
    migrate("upgrade", "head")
    assert snapshot(engine)[3:] == before
    stable = snapshot(engine)
    assert "Downgrade refused" in migrate("downgrade", "cc64acf89e63", False)
    assert snapshot(engine) == stable
