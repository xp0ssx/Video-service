"""Real PostgreSQL, controlled server clock, and independent concurrency sessions."""

import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete, select, text

from app.core.config import get_settings
from app.db.session import Database
from app.modules.auth.models import Channel, User
from app.modules.videos import views
from app.modules.videos.deletion import delete_owned
from app.modules.videos.models import (
    Video,
    VideoDeletion,
    VideoViewReceipt,
    VideoViewSession,
)
from app.modules.videos.views import StartView, ViewProgress, ViewService, viewer_key

from .test_auth import ORIGIN, register
from .test_feed_interactions import social as social
from .test_stabilization import migration_db as migration_db

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(os.getenv("RUN_INTEGRATION") != "1", reason="Isolated PostgreSQL required"),
]


class Clock:
    def __init__(self):
        self.now = datetime(2026, 1, 1, tzinfo=UTC)

    def __call__(self):
        return self.now

    def advance(self, ms):
        self.now += timedelta(milliseconds=ms)


@pytest.fixture
def clock(monkeypatch):
    value = Clock()
    monkeypatch.setattr(views, "utcnow", value)
    return value


def start(client, video, headers, position=0, **extra):
    return client.post(
        f"/api/videos/{video}/views",
        headers=headers,
        json={"position_ms": position, **extra},
    )


def progress(client, video, session, headers, position, sequence=1, event="progress", **extra):
    return client.post(
        f"/api/videos/{video}/views/{session}",
        headers=headers,
        json={"position_ms": position, "sequence": sequence, "event": event, **extra},
    )


def total(client, video):
    return client.get(f"/api/videos/{video}").json()["views_count"]


def qualify(client, video, headers, clock):
    session = start(client, video, headers).json()["session_id"]
    clock.advance(1000)
    response = progress(client, video, session, headers, 1000)
    assert response.status_code == 200
    return response.json()


def test_open_watch_and_range_requests_never_count(social):
    client, _, headers, _, video = social
    for _ in range(3):
        assert total(client, video) == 0
        client.get(f"/api/videos/{video}/media", headers={"Range": "bytes=0-10"})
    assert total(client, video) == 0
    assert start(client, video, headers).json()["views_count"] == 0


@pytest.mark.parametrize(
    "duration,threshold", [(2000, 1000), (9001, 4501), (10000, 10000), (20000, 10000)]
)
def test_threshold_and_before_threshold(social, clock, duration, threshold):
    client, factory, headers, _, video = social
    with factory() as db:
        db.get(Video, video).duration_ms = duration
        db.commit()
    state = start(client, video, headers).json()
    assert state["threshold_ms"] == threshold
    session, position, sequence = state["session_id"], 0, 0
    while position < threshold - 1:
        delta = min(1000, threshold - 1 - position)
        clock.advance(delta)
        position += delta
        sequence += 1
        state = progress(client, video, session, headers, position, sequence).json()
        assert state["counted"] is False and state["views_count"] == 0
    clock.advance(1)
    state = progress(client, video, session, headers, threshold, sequence + 1).json()
    assert state["counted"] is True and state["completed"] is True
    assert total(client, video) == 1


def test_no_client_only_time_seek_pause_idle_and_sequence_replay(social, clock):
    client, _, headers, _, video = social
    session = start(client, video, headers).json()["session_id"]
    # Client time cannot substitute for server time.
    state = progress(client, video, session, headers, 900).json()
    assert state["played_ms"] == 0
    clock.advance(100)
    state = progress(client, video, session, headers, 2000, 2).json()
    assert state["played_ms"] == 0  # Unreported seek also rejected.
    progress(client, video, session, headers, 0, 3, "seek")
    progress(client, video, session, headers, 0, 4, "resume")
    clock.advance(400)
    state = progress(client, video, session, headers, 400, 5, "pause").json()
    assert state["played_ms"] == 400
    clock.advance(60_000)
    state = progress(client, video, session, headers, 1500, 6).json()
    assert state["played_ms"] == 400
    # Old/out-of-order reports cannot rewind the baseline or add progress.
    state = progress(client, video, session, headers, 0, 4).json()
    assert state["played_ms"] == 400 and state["views_count"] == 0
    clock.advance(9000)
    state = progress(client, video, session, headers, 2000, 7).json()
    assert state["played_ms"] == 400


def test_explicit_seek_does_not_credit_skipped_interval(social, clock):
    client, _, headers, _, video = social
    session = start(client, video, headers).json()["session_id"]
    clock.advance(2000)
    state = progress(client, video, session, headers, 2000, 1, "seek").json()
    assert state["views_count"] == state["played_ms"] == 0


def test_speed_change_uses_previous_rate_and_server_time(social, clock):
    client, _, headers, _, video = social
    session = start(client, video, headers, rate=2).json()["session_id"]
    clock.advance(250)
    state = progress(client, video, session, headers, 500, 1, "rate", rate=0.5).json()
    assert state["played_ms"] == 500
    clock.advance(1000)
    assert progress(client, video, session, headers, 1000, 2, rate=0.5).json()["counted"]


def test_repeat_confirmation_reload_window_and_expiry(social, clock):
    client, _, headers, _, video = social
    first = qualify(client, video, headers, clock)
    assert first["counted"]
    for sequence in (1, 2, 3):
        assert not progress(client, video, first["session_id"], headers, 1000, sequence).json()[
            "counted"
        ]
    reloaded = start(client, video, headers).json()
    assert reloaded["completed"] and reloaded["views_count"] == 1
    assert progress(client, video, first["session_id"], headers, 1000).status_code == 404
    clock.advance(24 * 3600_000)
    assert qualify(client, video, headers, clock)["counted"]
    assert total(client, video) == 2


def test_expired_session_and_new_session_before_threshold(social, clock):
    client, _, headers, _, video = social
    first = start(client, video, headers).json()["session_id"]
    clock.advance(400)
    assert progress(client, video, first, headers, 400).json()["played_ms"] == 400
    second = start(client, video, headers, position=400).json()
    assert second["played_ms"] == 0
    clock.advance(3600_000)
    assert progress(client, video, second["session_id"], headers, 1500).status_code == 404
    assert total(client, video) == 0


def test_anonymous_cookie_dedup_and_distinct_viewers(social, clock):
    client, factory, headers, user, video = social
    with factory() as db:
        users_before = set(db.scalars(select(User.id)))
    assert qualify(client, video, headers, clock)["counted"]
    client.cookies.clear()
    response = start(client, video, ORIGIN)
    cookie = response.headers["set-cookie"]
    assert "HttpOnly" in cookie and "SameSite=lax" in cookie and "Max-Age=2592000" in cookie
    session = response.json()["session_id"]
    clock.advance(1000)
    assert progress(client, video, session, ORIGIN, 1000).json()["counted"]
    assert start(client, video, ORIGIN).json()["completed"]
    with factory() as db:
        rows = db.scalars(select(VideoViewReceipt).where(VideoViewReceipt.video_id == video)).all()
        assert len(rows) == 2
        assert all(len(row.viewer_key) == 64 and str(user) not in row.viewer_key for row in rows)
        assert set(db.scalars(select(User.id))) == users_before  # No guest account.
    client.cookies.clear()
    assert progress(client, video, session, ORIGIN, 1000).status_code == 404
    assert qualify(client, video, ORIGIN, clock)["counted"]
    assert total(client, video) == 3


@pytest.mark.parametrize("guest", [False, True])
@pytest.mark.parametrize("failure", ["origin", "missing_origin", "csrf"])
def test_origin_and_applicable_csrf(social, guest, failure):
    client, _, headers, _, video = social
    if guest:
        client.cookies.clear()
        headers = dict(ORIGIN)
    session = start(client, video, headers).json()["session_id"]
    bad = dict(headers)
    if failure == "origin":
        bad["Origin"] = "https://evil.example"
    elif failure == "missing_origin":
        bad.pop("Origin")
    else:
        bad.pop("X-CSRF-Token", None)
    expected = 200 if guest and failure == "csrf" else 403
    assert start(client, video, bad).status_code == expected
    # Starting again replaces a session, so fetch its current id for the guest case.
    if expected == 200:
        session = start(client, video, headers).json()["session_id"]
    assert progress(client, video, session, bad, 1000).status_code == expected


def test_no_counter_patch_identity_spoof_or_foreign_session(social, clock):
    client, _, headers, _, video = social
    assert (
        client.patch(f"/api/videos/{video}", headers=headers, json={"views_count": 99}).status_code
        == 422
    )
    assert start(client, video, headers, user_id=str(uuid4())).status_code == 422
    session = start(client, video, headers).json()["session_id"]
    assert progress(client, video, session, headers, 1000, played_ms=1000).status_code == 422
    assert progress(client, uuid4(), session, headers, 1000).status_code == 404
    client.cookies.clear()
    start(client, video, ORIGIN)
    clock.advance(1000)
    assert progress(client, video, session, ORIGIN, 1000).status_code == 404


@pytest.mark.parametrize("role", ["user", "moderator", "admin"])
def test_private_owner_grant_revoke_and_hidden(social, clock, role):
    client, factory, headers, _, video = social
    assert (
        client.patch(
            f"/api/videos/{video}", headers=headers, json={"visibility": "private"}
        ).status_code
        == 200
    )
    assert qualify(client, video, headers, clock)["counted"]  # Owner included.
    owner_cookie = client.cookies["session_id"]
    client.cookies.clear()
    assert start(client, video, ORIGIN).status_code == 404
    account, response = register((client, factory))
    recipient_cookie = client.cookies["session_id"]
    recipient_headers = {**ORIGIN, "X-CSRF-Token": response.json()["csrf_token"]}
    with factory() as db:
        db.get(User, UUID(response.json()["user"]["id"])).role = role
        db.commit()
    assert start(client, video, recipient_headers).status_code == 404
    client.cookies.clear()
    client.cookies.set("session_id", owner_cookie)
    path = f"/api/videos/{video}/access/{account['username']}"
    assert client.put(path, headers=headers).status_code == 204
    client.cookies.clear()
    client.cookies.set("session_id", recipient_cookie)
    session = start(client, video, recipient_headers).json()["session_id"]
    clock.advance(1000)
    assert progress(client, video, session, recipient_headers, 1000).json()["counted"]
    # A new, unqualified session is rejected after grant revocation as well.
    clock.advance(24 * 3600_000)
    session = start(client, video, recipient_headers).json()["session_id"]
    client.cookies.clear()
    client.cookies.set("session_id", owner_cookie)
    assert client.delete(path, headers=headers).status_code == 204
    client.cookies.clear()
    client.cookies.set("session_id", recipient_cookie)
    clock.advance(1000)
    assert progress(client, video, session, recipient_headers, 1000).status_code == 404
    with factory() as db:
        db.get(Video, video).moderation_state = "hidden"
        db.commit()
    client.cookies.clear()
    client.cookies.set("session_id", owner_cookie)
    assert start(client, video, headers).status_code == 404
    assert progress(client, video, session, headers, 1000).status_code == 404


def test_deleted_video_cleans_events_and_rejects_old_session(social, clock):
    client, factory, headers, user, video = social
    value = qualify(client, video, headers, clock)
    delete_owned(factory, user, video)
    assert start(client, video, headers).status_code == 404
    assert progress(client, video, value["session_id"], headers, 1000).status_code == 404
    with factory() as db:
        for model in (VideoViewSession, VideoViewReceipt):
            assert db.scalar(select(model).where(model.video_id == video)) is None


def test_views_in_all_existing_read_models(social, clock):
    client, _, headers, _, video = social
    qualify(client, video, headers, clock)
    name = client.get("/api/auth/session").json()["user"]["username"]
    for path in (f"/videos/{video}", f"/me/videos/{video}"):
        assert client.get("/api" + path).json()["views_count"] == 1
    for path in ("/videos", "/videos?q=Проверка", f"/channels/{name}/videos", "/me/videos"):
        item = next(
            row for row in client.get("/api" + path).json()["items"] if row["id"] == str(video)
        )
        assert item["views_count"] == 1
    playlist = client.post("/api/playlists", headers=headers, json={"title": "Test"}).json()["id"]
    assert (
        client.put(f"/api/playlists/{playlist}/items/{video}", headers=headers).status_code == 204
    )
    for prefix in ("/api/playlists", "/api/me/playlists"):
        assert client.get(f"{prefix}/{playlist}").json()["items"][0]["views_count"] == 1


@pytest.fixture
def durable():
    database = Database(get_settings())
    factory = database.session_factory
    user, channel, video = [uuid4() for _ in range(3)]
    with factory() as db:
        db.add(
            User(
                id=user,
                email=f"{user}@example.com",
                username="v_" + user.hex[:20],
                password_hash="synthetic",
            )
        )
        db.flush()
        db.add(Channel(id=channel, user_id=user, title="Synthetic"))
        db.flush()
        db.add(
            Video(
                id=video,
                channel_id=channel,
                title="Synthetic",
                visibility="public",
                status="ready",
                duration_ms=2000,
            )
        )
        db.commit()
    try:
        yield factory, user, video
    finally:
        with factory() as db:
            for model in (VideoViewSession, VideoViewReceipt, VideoDeletion):
                db.execute(delete(model).where(model.video_id == video))
            db.execute(delete(Video).where(Video.id == video))
            db.execute(delete(Channel).where(Channel.id == channel))
            db.execute(delete(User).where(User.id == user))
            db.commit()
        database.close()


@pytest.mark.parametrize("same_viewer", [True, False])
def test_parallel_confirmations_atomic_and_no_lost_increments(durable, same_viewer):
    factory, user, video = durable
    clock = Clock()
    service = ViewService(factory, clock)
    keys = [viewer_key(user, None), viewer_key(None, "synthetic-guest")]
    one = service.start(user, keys[0], video, StartView(position_ms=0))
    two = one if same_viewer else service.start(None, keys[1], video, StartView(position_ms=0))
    barrier = Barrier(2)
    clock.advance(1000)

    def confirm(index):
        barrier.wait(timeout=10)
        return service.progress(
            user if same_viewer or index == 0 else None,
            keys[0] if same_viewer else keys[index],
            video,
            (one, two)[index]["session_id"],
            ViewProgress(position_ms=1000, sequence=1),
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        values = list(pool.map(confirm, (0, 1)))
    assert sum(value["counted"] for value in values) == (1 if same_viewer else 2)
    with factory() as db:
        assert db.get(Video, video).views_count == (1 if same_viewer else 2)


def test_restart_and_cleanup_preserve_historical_total(durable):
    factory, user, video = durable
    clock = Clock()
    key = viewer_key(user, None)
    service = ViewService(factory, clock)
    started = service.start(user, key, video, StartView(position_ms=0))
    clock.advance(1000)
    # Another service instance can finish persisted playback.
    assert ViewService(factory, clock).progress(
        user, key, video, started["session_id"], ViewProgress(position_ms=1000, sequence=1)
    )["counted"]
    # A fresh interpreter observes persisted totals and dedup receipts.
    probe = subprocess.run(
        [
            sys.executable,
            "-c",
            "\n".join(
                [
                    "from uuid import UUID",
                    "from app.core.config import get_settings",
                    "from app.db.session import Database",
                    "from app.modules.videos.models import Video, VideoViewReceipt",
                    "d = Database(get_settings())",
                    "with d.session_factory() as db:",
                    f" assert db.get(Video, UUID('{video}')).views_count == 1",
                    f" assert db.get(VideoViewReceipt, (UUID('{video}'), '{key}')) is not None",
                    "d.close()",
                ]
            ),
        ],
        capture_output=True,
    )
    assert probe.returncode == 0
    clock.advance(24 * 3600_000)
    service.cleanup()
    with factory() as db:
        assert db.get(Video, video).views_count == 1
        assert db.get(VideoViewReceipt, (video, key)) is None
        assert db.get(VideoViewSession, (video, key)) is None
    started = service.start(user, key, video, StartView(position_ms=0))
    clock.advance(1000)
    assert (
        service.progress(
            user, key, video, started["session_id"], ViewProgress(position_ms=1000, sequence=1)
        )["views_count"]
        == 2
    )


def test_upgrade_existing_comments_tombstones_and_totals(migration_db):
    engine, migrate = migration_db
    migrate("upgrade", "d4a6c2e91f08")
    user, channel, video, tombstone, comment = [uuid4() for _ in range(5)]
    with engine.begin() as db:
        db.execute(
            text(
                "INSERT INTO users(id,email,username,password_hash) "
                "VALUES (:u,'views@example.com','views_migration','synthetic')"
            ),
            {"u": user},
        )
        db.execute(
            text("INSERT INTO channels(id,user_id,title) VALUES (:c,:u,'Synthetic')"),
            {"c": channel, "u": user},
        )
        for identifier in (video, tombstone):
            db.execute(
                text("INSERT INTO videos(id,channel_id,title) VALUES (:v,:c,'Synthetic')"),
                {"v": identifier, "c": channel},
            )
        db.execute(text("UPDATE videos SET deleted_at=now() WHERE id=:v"), {"v": tombstone})
        db.execute(text("INSERT INTO video_deletions(video_id) VALUES (:v)"), {"v": tombstone})
        db.execute(
            text("INSERT INTO comments(id,video_id,user_id,body) VALUES (:id,:v,:u,'  text\n')"),
            {"id": comment, "v": video, "u": user},
        )
        before = db.execute(text("SELECT to_jsonb(v) FROM videos v ORDER BY id")).scalars().all()
    migrate("upgrade", "head")
    with engine.begin() as db:
        after = db.execute(text("SELECT to_jsonb(v) FROM videos v ORDER BY id")).scalars().all()
        assert all(row.pop("views_count") == 0 for row in after)
        assert after == before
        assert (
            db.scalar(text("SELECT body FROM comments WHERE id=:id"), {"id": comment}) == "  text\n"
        )
        assert db.scalar(text("SELECT count(*) FROM video_deletions")) == 1
        db.execute(text("UPDATE videos SET views_count=1 WHERE id=:v"), {"v": video})
    migrate("downgrade", "d4a6c2e91f08", success=False)
    with engine.connect() as db:
        assert db.scalar(text("SELECT views_count FROM videos WHERE id=:v"), {"v": video}) == 1
