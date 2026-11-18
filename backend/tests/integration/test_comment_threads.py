"""Threads, text roundtrips and likes against the isolated real PostgreSQL."""

import os
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import UTC, datetime
from threading import Barrier
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete, event, func, select, text
from sqlalchemy.exc import IntegrityError

from app.core.config import get_settings
from app.db.session import Database
from app.modules.auth.models import Channel, User
from app.modules.interactions.models import Comment, CommentLike
from app.modules.interactions.schemas import CreateComment
from app.modules.interactions.service import InteractionService
from app.modules.videos.models import Video

from .test_auth import ORIGIN, register
from .test_feed_interactions import seed
from .test_feed_interactions import social as social
from .test_stabilization import migration_db as migration_db

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(os.getenv("RUN_INTEGRATION") != "1", reason="Isolated PostgreSQL required"),
]


def post(client, video, headers, body="Комментарий", parent=None):
    return client.post(
        f"/api/videos/{video}/comments", json={"body": body, "parent_id": parent}, headers=headers
    )


@pytest.mark.parametrize(
    "body",
    [
        "Обычный русский текст",
        "<b>Текст</b>",
        "<script>alert(1)</script>",
        "& < > \" '",
        "  \nСтрока 1\n\nСтрока 2\t \n",
        "😀 👩🏽‍💻 e\u0301 漢字",
        "&lt; и &amp;",
        r'C:\path\file "quoted"',
        "😀" * 2000,
    ],
)
def test_raw_text_roundtrip_at_all_backend_boundaries(social, body):
    client, factory, headers, _, video = social
    result = post(client, video, headers, body)
    assert result.status_code == 201
    value = result.json()
    assert value["body"] == body
    assert value["parent_id"] is None
    with factory() as db:
        assert db.get(Comment, UUID(value["id"])).body == body
    assert client.get(f"/api/videos/{video}/comments").json()["items"][0]["body"] == body
    reply = post(client, video, headers, body, value["id"])
    assert reply.status_code == 201 and reply.json()["body"] == body
    assert (
        client.get(f"/api/videos/{video}/comments/{value['id']}/replies").json()["items"][0]["body"]
        == body
    )


@pytest.mark.parametrize("body", ["😀" * 2001, " " + "x" * 2000, "\n\t ", "\x00", 123])
def test_raw_text_limits(social, body):
    client, _, headers, _, video = social
    assert post(client, video, headers, body).status_code == 422


def test_thread_normalization_counts_pagination_and_scopes(social):
    client, factory, headers, user, video = social
    root = post(client, video, headers).json()
    other = post(client, video, headers, "Other root").json()
    first = post(client, video, headers, "first reply", root["id"]).json()
    second = post(client, video, headers, "reply to reply", first["id"]).json()
    assert first["parent_id"] == second["parent_id"] == root["id"]
    assert second["author"]["id"] == str(user)
    assert first["id"] != second["id"]
    # Deterministic keyset tie handling, including replies and roots.
    with factory() as db:
        for value in (root, other, first, second):
            db.get(Comment, UUID(value["id"])).created_at = datetime(2026, 1, 1, tzinfo=UTC)
        db.commit()
    path = f"/api/videos/{video}/comments"
    one = client.get(path, params={"limit": 1}).json()
    two = client.get(path, params={"limit": 1, "cursor": one["next_cursor"]}).json()
    roots = one["items"] + two["items"]
    assert [row["id"] for row in roots] == sorted([root["id"], other["id"]])
    assert {row["id"]: row["replies_count"] for row in roots} == {root["id"]: 2, other["id"]: 0}
    reply_path = path + f"/{root['id']}/replies"
    a = client.get(reply_path, params={"limit": 1}).json()
    b = client.get(reply_path, params={"limit": 1, "cursor": a["next_cursor"]}).json()
    assert [row["id"] for row in a["items"] + b["items"]] == sorted([first["id"], second["id"]])
    assert b["next_cursor"] is None and two["next_cursor"] is None
    for destination, cursor in (
        (path, a["next_cursor"]),
        (reply_path, one["next_cursor"]),
        (path + f"/{other['id']}/replies", a["next_cursor"]),
    ):
        assert client.get(destination, params={"cursor": cursor}).status_code == 422
    assert client.get(path + f"/{first['id']}/replies").status_code == 404
    assert client.get(reply_path, params={"limit": 101}).status_code == 422
    assert client.get(reply_path, params={"cursor": "bad"}).status_code == 422


def test_invalid_parent_and_cross_video_cannot_leak_or_write(social):
    client, factory, headers, user, video = social
    other = seed(factory, user, "Other video")
    root = post(client, other, headers).json()["id"]
    for parent in (str(uuid4()), root):
        assert post(client, video, headers, parent=parent).status_code == 404
        assert client.get(f"/api/videos/{video}/comments/{parent}/replies").status_code == 404
        for method in ("PUT", "DELETE"):
            assert (
                client.request(
                    method, f"/api/videos/{video}/comments/{parent}/like", headers=headers
                ).status_code
                == 404
            )
    assert post(client, video, headers, parent="invalid").status_code == 422
    # Same-video invariant is also a PostgreSQL FK constraint.
    with factory() as db, pytest.raises(IntegrityError):
        db.add(Comment(video_id=video, user_id=user, body="invalid", parent_id=UUID(root)))
        db.flush()


@pytest.mark.parametrize("is_reply", [False, True])
def test_comment_like_idempotency_read_counts_and_guest(social, is_reply):
    client, factory, headers, user, video = social
    root = post(client, video, headers).json()["id"]
    comment = post(client, video, headers, parent=root).json()["id"] if is_reply else root
    path = f"/api/videos/{video}/comments/{comment}/like"
    listing = f"/api/videos/{video}/comments" + (f"/{root}/replies" if is_reply else "")
    for _ in range(2):
        assert client.put(path, headers=headers).json() == {"likes_count": 1, "liked_by_me": True}
    item = client.get(listing).json()["items"][0]
    assert item["likes_count"] == 1 and item["liked_by_me"] is True
    with factory() as db:
        assert (
            db.scalar(
                select(func.count())
                .select_from(CommentLike)
                .where(CommentLike.comment_id == UUID(comment))
            )
            == 1
        )
    cookie = client.cookies["session_id"]
    client.cookies.clear()
    guest = client.get(listing).json()["items"][0]
    assert guest["likes_count"] == 1 and guest["liked_by_me"] is False
    for method in ("PUT", "DELETE"):
        assert client.request(method, path, headers=ORIGIN).status_code == 401
    assert post(client, video, ORIGIN, parent=root).status_code == 401
    client.cookies.set("session_id", cookie)
    for _ in range(2):
        assert client.delete(path, headers=headers).json() == {
            "likes_count": 0,
            "liked_by_me": False,
        }


@pytest.mark.parametrize("role", ["user", "moderator", "admin"])
def test_thread_privacy_grant_revoke_hidden_and_no_role_bypass(social, role):
    client, factory, headers, _, video = social
    root = post(client, video, headers).json()["id"]
    reply = post(client, video, headers, parent=root).json()["id"]
    assert (
        client.patch(
            f"/api/videos/{video}", headers=headers, json={"visibility": "private"}
        ).status_code
        == 200
    )
    owner_cookie = client.cookies["session_id"]
    client.cookies.clear()
    account, registered = register((client, factory))
    recipient_cookie = client.cookies["session_id"]
    recipient_headers = {**ORIGIN, "X-CSRF-Token": registered.json()["csrf_token"]}
    with factory() as db:
        db.get(User, UUID(registered.json()["user"]["id"])).role = role
        db.commit()

    def check(expected, current_headers):
        assert client.get(f"/api/videos/{video}/comments/{root}/replies").status_code == expected
        for comment in (root, reply):
            for method in ("PUT", "DELETE"):
                assert (
                    client.request(
                        method,
                        f"/api/videos/{video}/comments/{comment}/like",
                        headers=current_headers,
                    ).status_code
                    == expected
                )
            assert post(client, video, current_headers, parent=comment).status_code == (
                201 if expected == 200 else expected
            )

    check(404, recipient_headers)
    client.cookies.clear()
    assert client.get(f"/api/videos/{video}/comments/{root}/replies").status_code == 404
    client.cookies.set("session_id", owner_cookie)
    check(200, headers)
    assert (
        client.put(f"/api/videos/{video}/access/{account['username']}", headers=headers).status_code
        == 204
    )
    client.cookies.clear()
    client.cookies.set("session_id", recipient_cookie)
    check(200, recipient_headers)
    client.cookies.clear()
    client.cookies.set("session_id", owner_cookie)
    assert (
        client.delete(
            f"/api/videos/{video}/access/{account['username']}", headers=headers
        ).status_code
        == 204
    )
    client.cookies.clear()
    client.cookies.set("session_id", recipient_cookie)
    check(404, recipient_headers)
    with factory() as db:
        db.get(Video, video).moderation_state = "hidden"
        db.commit()
    client.cookies.clear()
    client.cookies.set("session_id", owner_cookie)
    check(404, headers)
    # No distinction between an existing comment and an unknown id on hidden video.
    unknown = client.get(f"/api/videos/{video}/comments/{uuid4()}/replies")
    known = client.get(f"/api/videos/{video}/comments/{root}/replies")
    assert unknown.status_code == known.status_code == 404
    assert unknown.json()["message"] == known.json()["message"]


@pytest.mark.parametrize("failure", ["csrf", "origin", "missing_origin"])
def test_thread_write_origin_and_csrf(social, failure):
    client, _, headers, _, video = social
    root = post(client, video, headers).json()["id"]
    bad = dict(headers)
    if failure == "csrf":
        bad.pop("X-CSRF-Token")
    elif failure == "origin":
        bad["Origin"] = "https://evil.example"
    else:
        bad.pop("Origin")
    assert post(client, video, bad, parent=root).status_code == 403
    for method in ("PUT", "DELETE"):
        assert (
            client.request(
                method, f"/api/videos/{video}/comments/{root}/like", headers=bad
            ).status_code
            == 403
        )


def test_comment_page_aggregates_have_constant_query_count(social):
    client, factory, headers, user, video = social
    root = post(client, video, headers).json()["id"]
    for _ in range(30):
        post(client, video, headers, parent=root)
        post(client, video, headers)
    bind = factory.kw["bind"]
    statements = []

    def count(conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith("SELECT"):
            statements.append(statement)

    client.cookies.clear()
    event.listen(bind, "before_cursor_execute", count)
    try:
        for suffix in ("", f"/{root}/replies"):
            counts = []
            for limit in (1, 100):
                statements.clear()
                result = client.get(
                    f"/api/videos/{video}/comments{suffix}", params={"limit": limit}
                )
                assert result.status_code == 200
                counts.append(len(statements))
            assert counts[0] == counts[1] and counts[1] <= 5
    finally:
        event.remove(bind, "before_cursor_execute", count)


def test_concurrent_comment_likes():
    database = Database(get_settings())
    factory = database.session_factory
    user, channel, video, root = [uuid4() for _ in range(4)]
    try:
        with factory() as db:
            db.add(
                User(
                    id=user,
                    email=f"{user}@example.com",
                    username="thread_" + user.hex[:20],
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
                    title="Concurrency",
                    visibility="public",
                    status="ready",
                )
            )
            db.flush()
            db.add(Comment(id=root, video_id=video, user_id=user, body="Test"))
            db.commit()

        @contextmanager
        def limited():
            with factory() as db:
                db.execute(text("SET LOCAL lock_timeout='5s'"))
                yield db

        service = InteractionService(limited)
        reply = service.add_comment(video, user, CreateComment(body="Reply", parent_id=root))["id"]
        for identifier in (root, reply):
            for enabled in (True, False):
                barrier = Barrier(4)

                def change(barrier=barrier, identifier=identifier, enabled=enabled):
                    barrier.wait(timeout=4)
                    return service.comment_like(video, identifier, user, enabled)

                with ThreadPoolExecutor(max_workers=4) as executor:
                    futures = [executor.submit(change) for _ in range(4)]
                    assert [future.result(timeout=10) for future in futures] == [
                        {"likes_count": int(enabled), "liked_by_me": enabled}
                    ] * 4
    finally:
        with factory() as db:
            # Cascades clean up only this test's comments and comment likes.
            db.execute(delete(Video).where(Video.id == video))
            db.execute(delete(Channel).where(Channel.id == channel))
            db.execute(delete(User).where(User.id == user))
            db.commit()
        database.close()


@pytest.mark.parametrize("new_data", ["reply", "like"])
def test_migration_preserves_existing_comments_likes_privacy_and_moderation(migration_db, new_data):
    engine, migrate = migration_db
    migrate("upgrade", "8e21c4a9d510")
    user, channel, video, comment, recipient = [uuid4() for _ in range(5)]
    body = '  <b>Текст</b> &lt; &amp; \\ "\n😀  '
    with engine.begin() as db:
        db.execute(
            text(
                "INSERT INTO users(id,email,username,password_hash) "
                "VALUES (:u,'test@example.com','test','synthetic')"
            ),
            {"u": user},
        )
        db.execute(
            text("INSERT INTO channels(id,user_id,title) VALUES (:c,:u,'Test')"),
            {"c": channel, "u": user},
        )
        db.execute(
            text(
                "INSERT INTO videos(id,channel_id,title,visibility,status,moderation_state) "
                "VALUES (:v,:c,'Test','private','ready','hidden')"
            ),
            {"v": video, "c": channel},
        )
        db.execute(
            text("INSERT INTO comments(id,video_id,user_id,body) VALUES (:id,:v,:u,:body)"),
            {"id": comment, "v": video, "u": user, "body": body},
        )
        db.execute(
            text("INSERT INTO likes(video_id,user_id) VALUES (:v,:u)"), {"v": video, "u": user}
        )
        db.execute(
            text(
                "INSERT INTO users(id,email,username,password_hash) "
                "VALUES (:u,'recipient@example.com','recipient','synthetic')"
            ),
            {"u": recipient},
        )
        db.execute(
            text("INSERT INTO video_access(video_id,user_id,granted_by) VALUES (:v,:u,:owner)"),
            {"v": video, "u": recipient, "owner": user},
        )
        grants = db.execute(text("SELECT * FROM video_access")).all()
        before = db.execute(text("SELECT id,video_id,user_id,body,created_at FROM comments")).all()
        likes = db.execute(text("SELECT * FROM likes")).all()
    migrate("upgrade", "head")
    migrate("upgrade", "head")
    with engine.begin() as db:
        assert (
            db.execute(text("SELECT id,video_id,user_id,body,created_at FROM comments")).all()
            == before
        )
        assert db.execute(text("SELECT * FROM likes")).all() == likes
        assert db.execute(text("SELECT * FROM video_access")).all() == grants
        assert db.scalar(text("SELECT parent_id FROM comments")) is None
        assert db.execute(text("SELECT visibility,moderation_state FROM videos")).one() == (
            "private",
            "hidden",
        )
        if new_data == "like":
            db.execute(
                text("INSERT INTO comment_likes(comment_id,user_id) VALUES (:c,:u)"),
                {"c": comment, "u": user},
            )
        else:
            db.execute(
                text(
                    "INSERT INTO comments(id,video_id,user_id,body,parent_id) "
                    "VALUES (:id,:v,:u,'Reply',:parent)"
                ),
                {"id": uuid4(), "v": video, "u": user, "parent": comment},
            )
    assert "Downgrade refused" in migrate("downgrade", "8e21c4a9d510", False)
    migrate("upgrade", "head")
    with engine.connect() as db:
        assert db.scalar(text("SELECT count(*) FROM comment_likes")) == int(new_data == "like")
        assert db.scalar(text("SELECT count(*) FROM comments WHERE parent_id IS NOT NULL")) == int(
            new_data == "reply"
        )


@pytest.mark.parametrize("is_reply", [False, True])
def test_comment_likes_count_other_users_without_claiming_their_like(social, is_reply):
    client, _, headers, _, video = social
    root = post(client, video, headers).json()["id"]
    comment = post(client, video, headers, parent=root).json()["id"] if is_reply else root
    path = f"/api/videos/{video}/comments/{comment}/like"
    listing = f"/api/videos/{video}/comments" + (f"/{root}/replies" if is_reply else "")
    assert client.put(path, headers=headers).json()["likes_count"] == 1
    owner_cookie = client.cookies["session_id"]
    client.cookies.clear()
    _, response = register(social[:2])
    other_headers = {**ORIGIN, "X-CSRF-Token": response.json()["csrf_token"]}
    item = client.get(listing).json()["items"][0]
    assert item["likes_count"] == 1 and item["liked_by_me"] is False
    assert client.put(path, headers=other_headers).json() == {"likes_count": 2, "liked_by_me": True}
    client.cookies.clear()
    client.cookies.set("session_id", owner_cookie)
    assert client.delete(path, headers=headers).json() == {"likes_count": 1, "liked_by_me": False}
    item = client.get(listing).json()["items"][0]
    assert item["likes_count"] == 1 and item["liked_by_me"] is False
