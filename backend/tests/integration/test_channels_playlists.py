import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete, func, select

from app.core.config import get_settings
from app.db.session import Database
from app.modules.access.policy import can_watch, watch_listing
from app.modules.auth.models import Channel, User
from app.modules.channels.schemas import Order
from app.modules.channels.service import ChannelService
from app.modules.playlists.models import Playlist, PlaylistItem, Subscription
from app.modules.playlists.service import PlaylistService
from app.modules.videos.models import Video

from .test_auth import ORIGIN, register

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(os.getenv("RUN_INTEGRATION") != "1", reason="Isolated PostgreSQL required"),
]


@pytest.fixture
def case(auth):
    client, factory = auth
    owner, response = register(auth)
    uid = UUID(response.json()["user"]["id"])
    headers = {**ORIGIN, "X-CSRF-Token": response.json()["csrf_token"]}
    cookie = client.cookies["session_id"]
    with factory() as db:
        channel = db.scalar(select(Channel).where(Channel.user_id == uid))
        videos = [
            Video(
                channel_id=channel.id,
                title="Video " + str(n),
                status="ready",
                visibility="public",
                published_at=datetime.now(UTC),
            )
            for n in range(3)
        ]
        db.add_all(videos)
        db.commit()
        ids = [str(v.id) for v in videos]
    result = client.post("/api/playlists", json={"title": "Playlist"}, headers=headers)
    assert result.status_code == 201
    pid = result.json()["id"]
    for vid in ids:
        assert client.put(f"/api/playlists/{pid}/items/{vid}", headers=headers).status_code == 204
    return client, factory, owner, uid, headers, cookie, ids, pid


def test_channel_edit_and_subscription(case):
    c, f, owner, uid, h, cookie, ids, pid = case
    name = owner["username"]
    assert c.get("/api/channels/" + name.upper()).json()["owned_by_me"]
    assert (
        c.patch(
            "/api/me/channel",
            headers=h,
            json={"title": " New channel ", "description": "Description"},
        ).json()["title"]
        == "New channel"
    )
    assert len(c.get(f"/api/channels/{name}/videos").json()["items"]) == 3
    assert c.put(f"/api/channels/{name}/subscription", headers=h).status_code == 422
    c.cookies.clear()
    other, response = register((c, f))
    oh = {**ORIGIN, "X-CSRF-Token": response.json()["csrf_token"]}
    for _ in range(2):
        assert (
            c.put(f"/api/channels/{name}/subscription", headers=oh).json()["subscribers_count"] == 1
        )
    assert c.get("/api/me/subscriptions").json()["items"][0]["username"] == name
    assert c.get("/api/channels/" + name).json()["subscribed_by_me"]
    assert c.patch("/api/me/channel", headers=oh, json={"title": "Other"}).status_code == 200
    assert c.get("/api/channels/" + name).json()["title"] == "New channel"
    assert (
        c.patch(
            "/api/me/channel", headers=oh, json={"title": "hack", "user_id": str(uid)}
        ).status_code
        == 422
    )
    for _ in range(2):
        assert (
            c.delete(f"/api/channels/{name}/subscription", headers=oh).json()["subscribers_count"]
            == 0
        )
    c.cookies.clear()
    assert c.get("/api/me/subscriptions").status_code == 401
    assert c.put(f"/api/channels/{name}/subscription", headers=ORIGIN).status_code == 401


def test_playlist_ownership_order_delete(case):
    c, f, owner, uid, h, cookie, ids, pid = case
    url = "/api/playlists/" + pid
    assert (
        c.patch(url, headers=h, json={"title": "Changed", "description": "text"}).status_code == 204
    )
    assert c.get(url).json()["title"] == "Changed"
    assert c.put(url + "/items/" + ids[0], headers=h).status_code == 204
    assert len(c.get(url).json()["items"]) == 3
    assert c.put(url + "/order", headers=h, json={"video_ids": ids[::-1]}).status_code == 204
    assert [v["id"] for v in c.get(url).json()["items"]] == ids[::-1]
    for invalid in (ids[:1], [ids[0]] * 3, [str(uuid4())] + ids[1:]):
        assert c.put(url + "/order", headers=h, json={"video_ids": invalid}).status_code == 422
        assert [v["id"] for v in c.get(url).json()["items"]] == ids[::-1]
    first = c.get(url, params={"limit": 1}).json()
    second = c.get(url, params={"limit": 2, "cursor": first["next_cursor"]}).json()
    assert [v["id"] for v in first["items"] + second["items"]] == ids[::-1]
    c.cookies.clear()
    _, response = register((c, f))
    oh = {**ORIGIN, "X-CSRF-Token": response.json()["csrf_token"]}
    for method, path, body in [
        ("PATCH", url, {"title": "hack"}),
        ("DELETE", url, None),
        ("PUT", url + "/items/" + ids[0], None),
        ("DELETE", url + "/items/" + ids[0], None),
        ("PUT", url + "/order", {"video_ids": ids}),
    ]:
        assert (
            c.request(method, path, headers=oh, **({"json": body} if body else {})).status_code
            == 404
        )
    assert c.get("/api/me/playlists/" + pid).status_code == 404
    own = c.post("/api/playlists", json={"title": "Own"}, headers=oh).json()["id"]
    assert c.put(f"/api/playlists/{own}/items/{ids[0]}", headers=oh).status_code == 404
    c.cookies.clear()
    c.cookies.set("session_id", cookie)
    for _ in range(2):
        assert c.delete(url + "/items/" + ids[0], headers=h).status_code == 204
    assert c.delete(url, headers=h).status_code == 204
    assert c.get(url).status_code == 404
    with f() as db:
        assert (
            db.scalar(
                select(func.count())
                .select_from(PlaylistItem)
                .where(PlaylistItem.playlist_id == UUID(pid))
            )
            == 0
        )
        assert all(db.get(Video, UUID(vid)) is not None for vid in ids)


@pytest.mark.parametrize("role", ["user", "moderator", "admin"])
def test_private_filter_and_revocation(case, role):
    c, f, owner, uid, h, cookie, ids, pid = case
    with f() as db:
        db.get(Video, UUID(ids[0])).visibility = "private"
        db.get(Video, UUID(ids[1])).moderation_state = "hidden"
        db.commit()
    c.cookies.clear()
    other, response = register((c, f))
    other_id = UUID(response.json()["user"]["id"])
    oh = {**ORIGIN, "X-CSRF-Token": response.json()["csrf_token"]}
    with f() as db:
        db.get(User, other_id).role = role
        db.commit()
    url = "/api/playlists/" + pid

    def visible(expected):
        data = c.get(url, params={"limit": 1}).json()
        assert [v["id"] for v in data["items"]] == expected
        assert "total" not in data and "position" not in str(data)

    visible([ids[2]])
    assert (
        c.put("/api/channels/" + owner["username"] + "/subscription", headers=oh).status_code == 200
    )
    visible([ids[2]])
    c.cookies.clear()
    c.cookies.set("session_id", cookie)
    assert c.put(f"/api/videos/{ids[0]}/access/{other['username']}", headers=h).status_code == 204
    grant_cookie = response.cookies["session_id"]
    c.cookies.clear()
    c.cookies.set("session_id", grant_cookie)
    visible([ids[0]])
    assert (
        c.delete("/api/channels/" + owner["username"] + "/subscription", headers=oh).status_code
        == 200
    )
    visible([ids[0]])
    c.cookies.clear()
    c.cookies.set("session_id", cookie)
    assert (
        c.delete(f"/api/videos/{ids[0]}/access/{other['username']}", headers=h).status_code == 204
    )
    c.cookies.clear()
    c.cookies.set("session_id", grant_cookie)
    visible([ids[2]])
    assert c.get(f"/api/videos/{ids[0]}").status_code == 404
    with f() as db:
        allowed = set(db.scalars(select(Video.id).where(watch_listing(other_id))))
        for vid in ids:
            assert (UUID(vid) in allowed) == can_watch(db, other_id, db.get(Video, UUID(vid)))


@pytest.mark.parametrize("failure", ["csrf", "origin", "missing_origin"])
def test_mutation_security(case, failure):
    c, f, owner, uid, h, cookie, ids, pid = case
    h = dict(h)
    if failure == "csrf":
        h.pop("X-CSRF-Token")
    elif failure == "origin":
        h["Origin"] = "https://evil.example"
    else:
        h.pop("Origin")
    for method, path, data in [
        ("PATCH", "/api/me/channel", {"title": "x"}),
        ("PUT", f"/api/channels/{owner['username']}/subscription", None),
        ("DELETE", f"/api/channels/{owner['username']}/subscription", None),
        ("POST", "/api/playlists", {"title": "x"}),
        ("PATCH", f"/api/playlists/{pid}", {"title": "x"}),
        ("DELETE", f"/api/playlists/{pid}", None),
        ("PUT", f"/api/playlists/{pid}/order", {"video_ids": ids}),
        ("PUT", f"/api/playlists/{pid}/items/{ids[0]}", None),
        ("DELETE", f"/api/playlists/{pid}/items/{ids[0]}", None),
    ]:
        assert (
            c.request(method, path, headers=h, **({"json": data} if data else {})).status_code
            == 403
        )


def test_concurrent_subscriptions_and_playlist_orders():
    database = Database(get_settings())
    factory = database.session_factory
    assert get_settings().postgres_host == "test-db"
    user, other, channel, video, second, pid = [uuid4() for _ in range(6)]
    name = "concurrent_" + channel.hex[:16]
    try:
        with factory() as db:
            db.add_all(
                [
                    User(
                        id=user,
                        username=name,
                        email=name + "@example.com",
                        password_hash="synthetic",
                    ),
                    User(
                        id=other,
                        username="other_" + other.hex[:16],
                        email=str(other) + "@example.com",
                        password_hash="synthetic",
                    ),
                ]
            )
            db.flush()
            db.add(Channel(id=channel, user_id=user, title="Channel"))
            db.flush()
            db.add_all(
                [
                    Video(
                        id=v, channel_id=channel, title="Video", status="ready", visibility="public"
                    )
                    for v in [video, second]
                ]
            )
            db.add(Playlist(id=pid, channel_id=channel, title="Playlist"))
            db.commit()
        channels = ChannelService(factory)
        playlists = PlaylistService(factory)
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(lambda _: channels.subscribe(name, other, True), range(4)))
            list(pool.map(lambda _: playlists.mutate(pid, user, "add", video_id=video), range(4)))
        playlists.mutate(pid, user, "add", video_id=second)
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(
                pool.map(
                    lambda n: playlists.mutate(
                        pid,
                        user,
                        "order",
                        Order(video_ids=[video, second] if n % 2 else [second, video]),
                    ),
                    range(8),
                )
            )
        with factory() as db:
            assert (
                db.scalar(
                    select(func.count())
                    .select_from(Subscription)
                    .where(Subscription.channel_id == channel)
                )
                == 1
            )
            assert sorted(
                db.scalars(select(PlaylistItem.position).where(PlaylistItem.playlist_id == pid))
            ) == [0, 1]
    finally:
        with factory() as db:
            db.execute(delete(Playlist).where(Playlist.id == pid))
            db.execute(delete(Subscription).where(Subscription.channel_id == channel))
            db.execute(delete(Video).where(Video.channel_id == channel))
            db.execute(delete(Channel).where(Channel.id == channel))
            db.execute(delete(User).where(User.id.in_([user, other])))
            db.commit()
        database.close()


def test_channel_lists_cursor_scopes_and_visibility(case):
    c, f, owner, uid, h, cookie, ids, pid = case
    url = "/api/channels/" + owner["username"]
    for n in range(2):
        assert c.post("/api/playlists", headers=h, json={"title": str(n)}).status_code == 201
    first = c.get(url + "/playlists", params={"limit": 1}).json()
    rest = c.get(url + "/playlists", params={"cursor": first["next_cursor"], "limit": 100}).json()
    assert len({item["id"] for item in first["items"] + rest["items"]}) == 3
    assert c.get(url + "/videos", params={"cursor": first["next_cursor"]}).status_code == 422
    with f() as db:
        db.get(Video, UUID(ids[0])).visibility = "private"
        db.get(Video, UUID(ids[1])).status = "processing"
        db.commit()
    c.cookies.clear()
    assert [item["id"] for item in c.get(url + "/videos").json()["items"]] == [ids[2]]
    assert c.get("/api/playlists/" + pid).json()["next_cursor"] is None
    assert c.get("/api/me/playlists/" + pid).status_code == 401


def test_inaccessible_anchor_cannot_probe_membership(case):
    from app.core.pagination import encode_cursor

    c, f, owner, uid, h, cookie, ids, pid = case
    with f() as db:
        db.get(Video, UUID(ids[0])).visibility = "private"
        db.commit()
    c.cookies.clear()
    for identifier in (UUID(ids[0]), uuid4()):
        cursor = encode_cursor(datetime(1970, 1, 1, tzinfo=UTC), identifier, "playlist:" + pid)
        result = c.get("/api/playlists/" + pid, params={"cursor": cursor})
        assert result.status_code == 422
    with f() as db:
        for status, moderation in [
            ("processing", "visible"),
            ("ready", "hidden"),
            ("ready", "visible"),
        ]:
            video = db.get(Video, UUID(ids[0]))
            video.status = status
            video.moderation_state = moderation
            db.flush()
            for viewer in (None, uid, uuid4()):
                sql = bool(
                    db.scalar(select(Video.id).where(Video.id == video.id, watch_listing(viewer)))
                )
                assert sql == can_watch(db, viewer, video)
