import os
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import delete, func, select

from app.core.config import get_settings
from app.db.session import Database
from app.modules.admin.models import ModerationAction
from app.modules.auth.models import Channel, User
from app.modules.moderation.models import Report
from app.modules.moderation.repository import ModerationRepository
from app.modules.moderation.schemas import CreateReport, Decision, StateChange
from app.modules.moderation.service import ModerationService
from app.modules.playlists.models import Playlist, PlaylistItem
from app.modules.videos.models import Video, VideoAccess
from app.modules.videos.schemas import UpdateVideo
from app.modules.videos.service import VideoService
from app.worker.jobs import Jobs

from .test_auth import ORIGIN, register
from .test_feed_interactions import social as social
from .test_worker_leases import lease_case as lease_case

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(os.getenv("RUN_INTEGRATION") != "1", reason="Isolated PostgreSQL required"),
]


def submit(social):
    c, f, h, user, video = social
    response = c.post(
        f"/api/videos/{video}/reports", json={"reason": "spam", "body": "Complaint"}, headers=h
    )
    assert response.status_code == 200
    return response.json()["id"]


def role(factory, user, value):
    with factory() as db:
        db.get(User, user).role = value
        db.commit()


def test_report_idempotency_access_and_fields(social):
    c, f, h, user, video = social
    identifier = submit(social)
    assert submit(social) == identifier
    with f() as db:
        assert db.scalar(select(func.count()).select_from(Report)) == 1
    assert c.get("/api/moderation/reports").status_code == 403
    for method, path in [
        ("GET", "/api/moderation/actions"),
        ("PATCH", f"/api/moderation/reports/{identifier}"),
        ("PUT", f"/api/moderation/videos/{video}/state"),
    ]:
        body = (
            {"status": "dismissed", "reason": "x"}
            if method == "PATCH"
            else {"state": "hidden", "reason": "x"}
        )
        assert (
            c.request(
                method, path, headers=h, **({"json": body} if method != "GET" else {})
            ).status_code
            == 403
        )
    c.cookies.clear()
    assert (
        c.post(
            f"/api/videos/{video}/reports", json={"reason": "spam", "body": "x"}, headers=ORIGIN
        ).status_code
        == 401
    )


@pytest.mark.parametrize(
    "data",
    [
        {"reason": "invalid", "body": "x"},
        {"reason": "spam", "body": " "},
        {"reason": "spam", "body": "x", "status": "actioned"},
        {"reason": "spam", "body": "x", "reviewed_by": "fake"},
    ],
)
def test_report_invalid_payload(social, data):
    c, f, h, user, video = social
    assert c.post(f"/api/videos/{video}/reports", json=data, headers=h).status_code == 422


@pytest.mark.parametrize("moderator_role", ["moderator", "admin"])
def test_hide_restore_filters_and_audit(social, moderator_role):
    c, f, h, user, video = social
    identifier = submit(social)
    role(f, user, moderator_role)
    with f() as db:
        title = db.get(Video, video).title
        published = db.get(Video, video).published_at
        channel = db.get(Video, video).channel_id
        username = db.get(User, user).username
        playlist = Playlist(channel_id=channel, title="Test")
        db.add(playlist)
        db.flush()
        pid = playlist.id
        db.add(PlaylistItem(playlist_id=pid, video_id=video, position=0))
        db.commit()
    result = c.patch(
        f"/api/moderation/reports/{identifier}",
        headers=h,
        json={"status": "actioned", "reason": "Reviewed hide"},
    )
    assert result.status_code == 200 and result.json()["resolved_action_id"]
    for path in [
        f"/api/videos/{video}",
        f"/api/videos/{video}/comments",
        f"/api/videos/{video}/media",
        f"/api/videos/{video}/thumbnail",
    ]:
        assert c.get(path).status_code == 404
    for suffix in ("media", "thumbnail"):
        assert c.head(f"/api/videos/{video}/{suffix}").status_code == 404
    assert c.put(f"/api/videos/{video}/like", headers=h).status_code == 404
    assert c.post(f"/api/videos/{video}/comments", headers=h, json={"body": "x"}).status_code == 404
    for path in (
        "/api/videos?q=" + title,
        "/api/channels/" + username + "/videos",
        f"/api/playlists/{pid}",
    ):
        assert not c.get(path).json()["items"]
    assert (
        c.patch(
            f"/api/moderation/reports/{identifier}",
            headers=h,
            json={"status": "dismissed", "reason": "Again"},
        ).status_code
        == 409
    )
    assert (
        c.put(
            f"/api/moderation/videos/{video}/state",
            headers=h,
            json={"state": "hidden", "reason": "Again"},
        ).json()["action_id"]
        is None
    )
    assert len(c.get("/api/moderation/actions").json()["items"]) == 1
    assert (
        c.put(
            f"/api/moderation/videos/{video}/state",
            headers=h,
            json={"state": "visible", "reason": "Restore"},
        ).status_code
        == 200
    )
    assert c.get(f"/api/videos/{video}").status_code == 200
    with f() as db:
        assert db.get(Video, video).published_at == published
    assert len(c.get("/api/moderation/actions").json()["items"]) == 2
    assert c.get("/api/moderation/reports", params={"status": "open"}).json()["items"] == []
    assert len(c.get("/api/moderation/reports", params={"status": "actioned"}).json()["items"]) == 1


def test_private_moderation_has_no_metadata_or_grant(social):
    c, f, h, user, video = social
    identifier = submit(social)
    assert (
        c.patch(f"/api/videos/{video}", headers=h, json={"visibility": "private"}).status_code
        == 200
    )
    c.cookies.clear()
    _, response = register((c, f))
    moderator = UUID(response.json()["user"]["id"])
    mh = {**ORIGIN, "X-CSRF-Token": response.json()["csrf_token"]}
    role(f, moderator, "moderator")
    for state in ("hidden", "visible"):
        queue = c.get("/api/moderation/reports").json()["items"][0]
        assert queue["id"] == identifier and queue["owner_id"] == str(user)
        assert (
            not {"title", "description", "thumbnail_url", "media_url", "visibility", "duration_ms"}
            & queue.keys()
        )
        assert (
            c.put(
                f"/api/moderation/videos/{video}/state",
                headers=mh,
                json={"state": state, "reason": "Service decision"},
            ).status_code
            == 200
        )
        assert c.get(f"/api/videos/{video}").status_code == 404
        assert c.get(f"/api/videos/{video}/media").status_code == 404
        assert (
            c.post(
                f"/api/videos/{video}/reports", headers=mh, json={"reason": "other", "body": "x"}
            ).status_code
            == 404
        )
    assert (
        c.patch(
            f"/api/moderation/reports/{identifier}",
            headers=mh,
            json={"status": "dismissed", "reason": "Insufficient evidence"},
        ).status_code
        == 200
    )
    with f() as db:
        assert db.get(Video, video).visibility == "private"
    role(f, moderator, "user")
    assert c.get("/api/moderation/reports").status_code == 403
    assert c.get("/api/moderation/actions").status_code == 403
    assert (
        c.put(
            f"/api/moderation/videos/{video}/state",
            headers=mh,
            json={"state": "hidden", "reason": "Denied"},
        ).status_code
        == 403
    )


def test_moderation_audit_rollback(social, monkeypatch):
    c, f, h, user, video = social
    identifier = submit(social)
    role(f, user, "moderator")
    original = ModerationRepository.audit

    def fail(self, *args):
        original(self, *args)
        self.db.flush()
        raise RuntimeError("Injected audit failure")

    monkeypatch.setattr(ModerationRepository, "audit", fail)
    with pytest.raises(RuntimeError, match="Injected audit failure"):
        c.patch(
            f"/api/moderation/reports/{identifier}",
            headers=h,
            json={"status": "actioned", "reason": "Fail"},
        )
    with f() as db:
        assert db.get(Video, video).moderation_state == "visible"
        assert db.get(Report, UUID(identifier)).status == "open"
        assert db.scalar(select(func.count()).select_from(ModerationAction)) == 0


@pytest.mark.parametrize("failure", ["csrf", "origin", "missing_origin"])
def test_moderation_security(social, failure):
    c, f, h, user, video = social
    identifier = submit(social)
    role(f, user, "moderator")
    h = dict(h)
    if failure == "csrf":
        h.pop("X-CSRF-Token")
    elif failure == "origin":
        h["Origin"] = "https://evil.example"
    else:
        h.pop("Origin")
    for method, path, body in [
        ("POST", f"/api/videos/{video}/reports", {"reason": "spam", "body": "x"}),
        ("PATCH", f"/api/moderation/reports/{identifier}", {"status": "dismissed", "reason": "x"}),
        ("PUT", f"/api/moderation/videos/{video}/state", {"state": "hidden", "reason": "x"}),
    ]:
        assert c.request(method, path, headers=h, json=body).status_code == 403


def test_hide_competes_with_worker_publication(lease_case):
    case = lease_case
    with case.factory() as db:
        actor = db.get(Channel, db.get(Video, case.video_id).channel_id).user_id
    role(case.factory, actor, "moderator")
    service = ModerationService(case.factory)
    barrier = Barrier(2)

    def hide():
        barrier.wait()
        return service.state(actor, case.video_id, StateChange(state="hidden", reason="Review"))

    def publish():
        barrier.wait()
        Jobs(case.factory).publish(case.claim, case.info, case.assets)

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(hide), pool.submit(publish)]
            for future in futures:
                future.result(timeout=10)
        with case.factory() as db:
            video = db.get(Video, case.video_id)
            assert video.status == "ready" and video.moderation_state == "hidden"
    finally:
        with case.factory() as db:
            db.execute(delete(ModerationAction).where(ModerationAction.video_id == case.video_id))
            db.commit()


def test_concurrent_reports_decisions_and_visibility():
    database = Database(get_settings())
    factory = database.session_factory
    assert get_settings().postgres_host == "test-db"
    actor, channel, video, recipient = [uuid4() for _ in range(4)]
    try:
        with factory() as db:
            db.add(
                User(
                    id=actor,
                    username="mod_" + actor.hex[:16],
                    email=str(actor) + "@example.com",
                    role="moderator",
                    password_hash="synthetic",
                )
            )
            db.flush()
            db.add(
                User(
                    id=recipient,
                    username="recipient_" + recipient.hex[:16],
                    email=str(recipient) + "@example.com",
                    password_hash="synthetic",
                )
            )
            db.add(Channel(id=channel, user_id=actor, title="Test"))
            db.flush()
            db.add(
                Video(
                    id=video, channel_id=channel, title="Test", status="ready", visibility="public"
                )
            )
            db.commit()
        service = ModerationService(factory)
        with ThreadPoolExecutor(max_workers=4) as pool:
            reports = list(
                pool.map(
                    lambda _: service.create(
                        video, actor, CreateReport(reason="spam", body="Concurrent")
                    ),
                    range(4),
                )
            )
        assert len({row["id"] for row in reports}) == 1
        barrier = Barrier(2)

        def decide(status):
            barrier.wait()
            try:
                service.resolve(actor, reports[0]["id"], Decision(status=status, reason="Review"))
                return 200
            except HTTPException as exc:
                return exc.status_code

        with ThreadPoolExecutor(max_workers=2) as pool:
            assert sorted(pool.map(decide, ["actioned", "dismissed"])) == [200, 409]
        with factory() as db:
            report = db.get(Report, reports[0]["id"])
            count = db.scalar(
                select(func.count())
                .select_from(ModerationAction)
                .where(ModerationAction.video_id == video)
            )
            assert count == int(report.status == "actioned")
        service.state(actor, video, StateChange(state="visible", reason="Reset"))
        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = [
                pool.submit(
                    VideoService(factory).grants, actor, video, "recipient_" + recipient.hex[:16]
                ),
                pool.submit(
                    service.state, actor, video, StateChange(state="hidden", reason="Concurrent")
                ),
                pool.submit(
                    VideoService(factory).patch, actor, video, UpdateVideo(visibility="private")
                ),
                pool.submit(
                    service.create, video, actor, CreateReport(reason="spam", body="Again")
                ),
            ]
            for future in futures:
                try:
                    future.result(timeout=10)
                except HTTPException as exc:
                    assert exc.status_code == 404
        with factory() as db:
            row = db.get(Video, video)
            assert row.visibility == "private" and row.moderation_state == "hidden"
            assert db.get(VideoAccess, (video, recipient)) is not None
    finally:
        with factory() as db:
            db.execute(delete(Report).where(Report.video_id == video))
            db.execute(delete(ModerationAction).where(ModerationAction.video_id == video))
            db.execute(delete(VideoAccess).where(VideoAccess.video_id == video))
            db.execute(delete(Video).where(Video.id == video))
            db.execute(delete(Channel).where(Channel.id == channel))
            db.execute(delete(User).where(User.id.in_([actor, recipient])))
            db.commit()
        database.close()


def test_hidden_denies_existing_private_grant(social):
    from app.modules.videos.models import VideoAccess

    c, f, h, owner, video = social
    role(f, owner, "moderator")
    c.cookies.clear()
    _, response = register((c, f))
    recipient = UUID(response.json()["user"]["id"])
    headers = {**ORIGIN, "X-CSRF-Token": response.json()["csrf_token"]}
    with f() as db:
        db.get(Video, video).visibility = "private"
        db.add(VideoAccess(video_id=video, user_id=recipient, granted_by=owner))
        db.commit()
    assert c.get(f"/api/videos/{video}").status_code == 200
    service = ModerationService(f)
    service.state(owner, video, StateChange(state="hidden", reason="Review"))
    for suffix in ("", "/comments", "/media", "/thumbnail"):
        assert c.get(f"/api/videos/{video}" + suffix).status_code == 404
    for suffix in ("media", "thumbnail"):
        assert c.head(f"/api/videos/{video}/{suffix}").status_code == 404
    assert c.put(f"/api/videos/{video}/like", headers=headers).status_code == 404
    assert (
        c.post(
            f"/api/videos/{video}/comments", headers=headers, json={"body": "denied"}
        ).status_code
        == 404
    )
    service.state(owner, video, StateChange(state="visible", reason="Restore"))
    assert c.get(f"/api/videos/{video}").status_code == 200
    with f() as db:
        assert db.get(VideoAccess, (video, recipient)) is not None
        assert db.get(Video, video).visibility == "private"


def test_moderation_queue_pagination_scope(social):
    c, f, h, user, video = social
    first = submit(social)
    with f() as db:
        other = Video(
            channel_id=db.get(Video, video).channel_id,
            title="Another",
            status="ready",
            visibility="public",
        )
        db.add(other)
        db.commit()
        identifier = other.id
    second = c.post(
        f"/api/videos/{identifier}/reports", headers=h, json={"reason": "other", "body": "Second"}
    ).json()["id"]
    role(f, user, "moderator")
    page = c.get("/api/moderation/reports", params={"limit": 1}).json()
    rest = c.get(
        "/api/moderation/reports", params={"limit": 1, "cursor": page["next_cursor"]}
    ).json()
    assert {row["id"] for row in page["items"] + rest["items"]} == {first, second}
    assert rest["next_cursor"] is None
    assert (
        c.get(
            "/api/moderation/reports", params={"status": "all", "cursor": page["next_cursor"]}
        ).status_code
        == 422
    )
