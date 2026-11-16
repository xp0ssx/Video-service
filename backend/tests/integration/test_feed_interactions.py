import os
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from threading import Barrier
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete, event, func, select, text
from sqlalchemy.exc import IntegrityError

from app.core.config import get_settings
from app.db.session import Database
from app.modules.auth.models import Channel, User
from app.modules.interactions.models import Comment, Like
from app.modules.interactions.service import InteractionService
from app.modules.videos.models import Video

from .test_auth import ORIGIN, register

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(os.getenv("RUN_INTEGRATION") != "1", reason="Isolated PostgreSQL required"),
]


@pytest.fixture
def social(auth):
    client, factory = auth
    _, response = register(auth)
    user = UUID(response.json()["user"]["id"])
    headers = {**ORIGIN, "X-CSRF-Token": response.json()["csrf_token"]}
    with factory() as db:
        channel = db.scalar(select(Channel).where(Channel.user_id == user))
        video = Video(
            channel_id=channel.id,
            title="Проверка видео " + uuid4().hex,
            visibility="public",
            status="ready",
            duration_ms=2000,
            width=320,
            height=180,
            published_at=datetime.now(UTC),
        )
        db.add(video)
        db.commit()
        identifier = video.id
    return client, factory, headers, user, identifier


def seed(factory, user, title, **values):
    with factory() as db:
        channel = db.scalar(select(Channel).where(Channel.user_id == user))
        video = Video(
            channel_id=channel.id,
            title=title,
            visibility="public",
            status="ready",
            duration_ms=2000,
            published_at=datetime.now(UTC),
            **values,
        )
        db.add(video)
        db.commit()
        return video.id


def test_feed_filters_sorting_keyset_and_no_n_plus_one(social):
    client, factory, _, user, original = social
    prefix = "feed_" + uuid4().hex
    allowed = [seed(factory, user, prefix + " " + str(index)) for index in range(5)]
    forbidden = []
    for changes in (
        {"visibility": "private"},
        {"moderation_state": "hidden"},
        {"status": "queued"},
        {"status": "failed"},
        {"status": "awaiting_upload"},
        {"status": "processing"},
        {"status": "uploading"},
    ):
        identifier = seed(factory, user, prefix + " forbidden")
        with factory() as db:
            for key, value in changes.items():
                setattr(db.get(Video, identifier), key, value)
            db.commit()
        forbidden.append(identifier)
    timestamp = datetime.now(UTC)
    with factory() as db:
        for identifier in allowed:
            db.get(Video, identifier).published_at = timestamp
        db.commit()
    cursor = None
    found = []
    for _ in range(4):
        response = client.get(
            "/api/videos",
            params={"q": prefix, "limit": 2, **({"cursor": cursor} if cursor else {})},
        )
        assert response.status_code == 200
        page = response.json()
        found.extend(UUID(item["id"]) for item in page["items"])
        for item in page["items"]:
            assert item["thumbnail_url"] == f"/api/videos/{item['id']}/thumbnail"
            assert {"title", "channel", "duration_ms", "published_at"} <= item.keys()
            assert not {"email", "password_hash", "object_key", "role"} & item.keys()
        cursor = page["next_cursor"]
        if not cursor:
            break
    assert found == sorted(allowed, reverse=True) and len(set(found)) == 5
    assert not set(forbidden) & set(found)
    assert (
        client.get("/api/videos", params={"q": "  ", "limit": 100}).json()
        == client.get("/api/videos", params={"limit": 100}).json()
    )
    queries = []

    def count(conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith("SELECT"):
            queries.append(statement)

    bind = factory.kw["bind"]
    event.listen(bind, "before_cursor_execute", count)
    try:
        assert client.get("/api/videos", params={"q": prefix, "limit": 100}).status_code == 200
        assert len(queries) == 1
    finally:
        event.remove(bind, "before_cursor_execute", count)


@pytest.mark.parametrize(
    "needle,title",
    [
        ("ёжик", "Заметки: ЁЖИК в тумане"),
        ("%", "Рост 100%"),
        ("_", "имя_ролика"),
        ("!", "Знак!"),
        ("\\", "Путь\\файл"),
    ],
)
def test_literal_search_and_cyrillic(social, needle, title):
    client, factory, _, user, _ = social
    marker = uuid4().hex
    match = seed(factory, user, marker + " " + title)
    seed(factory, user, marker + " plain text")
    for state in ("private", "hidden"):
        identifier = seed(factory, user, marker + " " + title)
        with factory() as db:
            row = db.get(Video, identifier)
            if state == "private":
                row.visibility = "private"
            else:
                row.moderation_state = "hidden"
            db.commit()
    # A marker plus the substring exercises literal wildcards without relying on
    # other committed fixtures being absent from the test database.
    result = client.get("/api/videos", params={"q": needle, "limit": 100})
    ids = {UUID(item["id"]) for item in result.json()["items"] if marker in item["title"]}
    assert ids == {match}


def test_cursor_validation_and_query_binding(social):
    client, factory, _, user, _ = social
    marker = uuid4().hex
    for _ in range(3):
        seed(factory, user, marker)
    cursor = client.get("/api/videos", params={"q": marker, "limit": 1}).json()["next_cursor"]
    assert cursor
    for params in (
        {"cursor": "not-valid"},
        {"cursor": cursor, "q": "other"},
        {"limit": 0},
        {"limit": 101},
        {"q": "\x00"},
    ):
        assert client.get("/api/videos", params=params).status_code == 422


def test_likes_idempotency_counts_restore_and_guest(social):
    client, factory, headers, user, video = social
    path = f"/api/videos/{video}/like"
    for _ in range(2):
        assert client.put(path, headers=headers).json() == {"likes_count": 1, "liked_by_me": True}
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(Like).where(Like.video_id == video)) == 1
    assert client.get("/api/auth/session").status_code == 200
    assert client.get(f"/api/videos/{video}").json()["liked_by_me"] is True
    cookie = client.cookies["session_id"]
    client.cookies.clear()
    assert client.get(f"/api/videos/{video}").json()["likes_count"] == 1
    assert client.get(f"/api/videos/{video}").json()["liked_by_me"] is False
    assert client.put(path, headers=ORIGIN).status_code == 401
    assert client.delete(path, headers=ORIGIN).status_code == 401
    client.cookies.set("session_id", cookie)
    for _ in range(2):
        assert client.delete(path, headers=headers).json() == {
            "likes_count": 0,
            "liked_by_me": False,
        }


def test_comments_order_pagination_authors_and_html(social):
    client, factory, headers, user, video = social
    path = f"/api/videos/{video}/comments"
    posted = []
    for body in ("first", "<script>window.pwned=1</script>", "third"):
        response = client.post(path, json={"body": body}, headers=headers)
        assert response.status_code == 201
        result = response.json()
        posted.append(result)
        assert result["body"] == body and result["author"]["id"] == str(user)
        assert set(result["author"]) == {"id", "username"}
    timestamp = datetime.now(UTC)
    with factory() as db:
        for index, row in enumerate(posted):
            db.get(Comment, UUID(row["id"])).created_at = timestamp + timedelta(seconds=index)
        db.commit()
    client.cookies.clear()
    first = client.get(path, params={"limit": 2}).json()
    second = client.get(path, params={"limit": 2, "cursor": first["next_cursor"]}).json()
    assert [row["body"] for row in first["items"] + second["items"]] == [
        "first",
        "<script>window.pwned=1</script>",
        "third",
    ]
    assert second["next_cursor"] is None
    assert client.post(path, json={"body": "guest"}, headers=ORIGIN).status_code == 401


@pytest.mark.parametrize("body", ["", "   ", "\n\t", "x" * 2001, "\x00"])
def test_comment_validation(social, body):
    client, _, headers, _, video = social
    assert (
        client.post(
            f"/api/videos/{video}/comments", json={"body": body}, headers=headers
        ).status_code
        == 422
    )


def test_comment_constraints_postgresql(social):
    _, factory, _, user, video = social
    for body in ("", "   ", "\n\t", "x" * 2001):
        with factory() as db:
            db.add(Comment(video_id=video, user_id=user, body=body))
            with pytest.raises(IntegrityError):
                db.flush()
            db.rollback()


@pytest.mark.parametrize("role", ["user", "moderator", "admin"])
def test_private_access_grants_revoke_and_interactions(social, role):
    client, factory, headers, _, video = social
    assert (
        client.post(
            f"/api/videos/{video}/comments", json={"body": "private content"}, headers=headers
        ).status_code
        == 201
    )
    assert (
        client.patch(
            f"/api/videos/{video}", json={"visibility": "private"}, headers=headers
        ).status_code
        == 200
    )
    owner_cookie = client.cookies["session_id"]
    client.cookies.clear()
    account, response = register((client, factory))
    other_cookie = client.cookies["session_id"]
    other_headers = {**ORIGIN, "X-CSRF-Token": response.json()["csrf_token"]}
    with factory() as db:
        db.get(User, UUID(response.json()["user"]["id"])).role = role
        db.commit()

    def denied():
        assert client.get(f"/api/videos/{video}").status_code == 404
        assert client.get(f"/api/videos/{video}/comments").status_code == 404
        assert client.put(f"/api/videos/{video}/like", headers=other_headers).status_code == 404
        assert client.delete(f"/api/videos/{video}/like", headers=other_headers).status_code == 404
        assert (
            client.post(
                f"/api/videos/{video}/comments", json={"body": "denied"}, headers=other_headers
            ).status_code
            == 404
        )

    denied()
    client.cookies.clear()
    client.cookies.set("session_id", owner_cookie)
    assert (
        client.put(f"/api/videos/{video}/access/{account['username']}", headers=headers).status_code
        == 204
    )
    client.cookies.clear()
    client.cookies.set("session_id", other_cookie)
    assert (
        client.get(f"/api/videos/{video}/comments").json()["items"][0]["body"] == "private content"
    )
    assert client.put(f"/api/videos/{video}/like", headers=other_headers).status_code == 200
    assert (
        client.post(
            f"/api/videos/{video}/comments", json={"body": "granted"}, headers=other_headers
        ).status_code
        == 201
    )
    client.cookies.clear()
    client.cookies.set("session_id", owner_cookie)
    assert (
        client.delete(
            f"/api/videos/{video}/access/{account['username']}", headers=headers
        ).status_code
        == 204
    )
    client.cookies.clear()
    client.cookies.set("session_id", other_cookie)
    denied()
    with factory() as db:
        db.get(Video, video).moderation_state = "hidden"
        db.commit()
    client.cookies.clear()
    client.cookies.set("session_id", owner_cookie)
    assert client.get(f"/api/videos/{video}/comments").status_code == 404
    assert client.put(f"/api/videos/{video}/like", headers=headers).status_code == 404


@pytest.mark.parametrize(
    "method,suffix", [("PUT", "like"), ("DELETE", "like"), ("POST", "comments")]
)
@pytest.mark.parametrize("failure", ["csrf", "origin", "missing_origin"])
def test_interaction_origin_and_csrf(social, method, suffix, failure):
    client, _, headers, _, video = social
    headers = dict(headers)
    if failure == "csrf":
        headers.pop("X-CSRF-Token")
    elif failure == "origin":
        headers["Origin"] = "https://evil.example"
    else:
        headers.pop("Origin")
    kwargs = {"json": {"body": "x"}} if method == "POST" else {}
    assert (
        client.request(
            method, f"/api/videos/{video}/{suffix}", headers=headers, **kwargs
        ).status_code
        == 403
    )


def test_concurrent_like_put_and_delete_are_idempotent():
    settings = get_settings()
    assert settings.postgres_host == "test-db" and settings.postgres_db == "video_service_test"
    database = Database(settings)
    factory = database.session_factory
    user, channel, video = uuid4(), uuid4(), uuid4()
    try:
        with factory() as db:
            db.add(
                User(
                    id=user,
                    email=f"{user}@example.com",
                    username="like_" + user.hex[:20],
                    password_hash="synthetic-not-for-login",
                )
            )
            db.flush()
            db.add(Channel(id=channel, user_id=user, title="Test"))
            db.flush()
            db.add(
                Video(
                    id=video,
                    channel_id=channel,
                    title="Concurrency test",
                    visibility="public",
                    status="ready",
                    published_at=datetime.now(UTC),
                )
            )
            db.commit()

        @contextmanager
        def limited():
            with factory() as db:
                db.execute(text("SET LOCAL lock_timeout='5s'"))
                yield db

        service = InteractionService(limited)
        for enabled in (True, False):
            barrier = Barrier(2)

            def change(barrier=barrier, enabled=enabled):
                barrier.wait(timeout=3)
                return service.like(video, user, enabled)

            with ThreadPoolExecutor(max_workers=2) as executor:
                futures = [executor.submit(change) for _ in range(2)]
                assert [future.result(timeout=8) for future in futures] == [
                    {"likes_count": int(enabled), "liked_by_me": enabled}
                ] * 2
    finally:
        with factory() as db:
            db.execute(delete(Like).where(Like.video_id == video))
            db.execute(delete(Video).where(Video.id == video))
            db.execute(delete(Channel).where(Channel.id == channel))
            db.execute(delete(User).where(User.id == user))
            db.commit()
        database.close()


def test_feed_cursor_survives_privacy_change_of_anchor(social):
    client, factory, _, user, _ = social
    marker = uuid4().hex
    for _ in range(3):
        seed(factory, user, marker)
    first = client.get("/api/videos", params={"q": marker, "limit": 1}).json()
    anchor = UUID(first["items"][0]["id"])
    with factory() as db:
        db.get(Video, anchor).visibility = "private"
        db.commit()
    rest = client.get(
        "/api/videos", params={"q": marker, "limit": 100, "cursor": first["next_cursor"]}
    )
    assert rest.status_code == 200
    assert len(rest.json()["items"]) == 2
    assert str(anchor) not in {item["id"] for item in rest.json()["items"]}


def test_comment_keyset_ties_and_scope(social):
    client, factory, headers, user, video = social
    stamp = datetime.now(UTC)
    identifiers = []
    with factory() as db:
        for index in range(3):
            row = Comment(video_id=video, user_id=user, body=str(index), created_at=stamp)
            db.add(row)
            db.flush()
            identifiers.append(row.id)
        db.commit()
    path = f"/api/videos/{video}/comments"
    first = client.get(path, params={"limit": 1}).json()
    second = client.get(path, params={"cursor": first["next_cursor"], "limit": 2}).json()
    assert [UUID(item["id"]) for item in first["items"] + second["items"]] == sorted(identifiers)
    other = seed(factory, user, "another video")
    assert (
        client.get(
            f"/api/videos/{other}/comments", params={"cursor": first["next_cursor"]}
        ).status_code
        == 422
    )


@pytest.mark.parametrize("field", ["user_id", "author"])
def test_comment_max_length_and_author_injection(social, field):
    client, _, headers, user, video = social
    path = f"/api/videos/{video}/comments"
    result = client.post(path, json={"body": "x" * 2000}, headers=headers)
    assert result.status_code == 201 and result.json()["author"]["id"] == str(user)
    assert len(result.json()["body"]) == 2000
    assert (
        client.post(path, json={"body": "spoof", field: str(uuid4())}, headers=headers).status_code
        == 422
    )
