"""Deletion tests use only generated videos and isolated PostgreSQL/SeaweedFS."""

import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from queue import Queue
from threading import Barrier, Event
from uuid import UUID, uuid4

import pytest
from botocore.exceptions import ClientError
from fastapi import HTTPException
from sqlalchemy import delete, func, select, text

from app.core.config import get_settings
from app.db.session import Database
from app.infrastructure.storage import Storage
from app.infrastructure.transcoder import LostLease
from app.modules.admin.models import ModerationAction
from app.modules.auth.models import Channel, User
from app.modules.interactions.models import Comment, CommentLike, Like
from app.modules.moderation.models import Report
from app.modules.videos.deletion import DELETED_TEXT, delete_owned, storage_writer
from app.modules.videos.models import Video, VideoAccess, VideoAsset, VideoDeletion, VideoJob
from app.modules.videos.schemas import UpdateVideo
from app.modules.videos.service import VideoService
from app.worker.deletions import Deletions
from app.worker.jobs import Jobs

from .test_auth import ORIGIN, register
from .test_stabilization import migration_db as migration_db
from .test_stabilization import seed_history
from .test_videos import clip as clip
from .test_videos import send, setup, worker

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("RUN_INTEGRATION") != "1", reason="Isolated PostgreSQL/S3 required"
    ),
]


def owner(client):
    return UUID(client.get("/api/auth/session").json()["user"]["id"])


def due(factory, video_id):
    with factory() as db:
        db.get(VideoDeletion, UUID(str(video_id))).run_after = datetime.now(UTC) - timedelta(
            seconds=1
        )
        db.commit()


def no_video(client, identifier):
    for path in (
        f"/videos/{identifier}",
        f"/me/videos/{identifier}",
        f"/videos/{identifier}/access",
        f"/videos/{identifier}/comments",
    ):
        assert client.get("/api" + path).status_code == 404, path
    for kind in ("media", "thumbnail"):
        for method in (client.get, client.head):
            assert method(f"/api/videos/{identifier}/{kind}").status_code == 404
    assert identifier not in {
        row["id"] for row in client.get("/api/me/videos?limit=100").json()["items"]
    }


@pytest.mark.parametrize("visibility", ["public", "private"])
def test_ready_video_all_links_and_dependent_data(auth, clip, visibility):
    client, factory, headers, identifier = setup(auth, visibility)
    video_id, uid = UUID(identifier), owner(client)
    name = client.get("/api/auth/session").json()["user"]["username"]
    assert send(client, headers, identifier, clip).status_code == 202
    worker(client, factory).run_once()
    state = client.get(f"/api/me/videos/{identifier}").json()
    assert state["status"] == "ready", (state["status"], state["error_stage"], state["error_code"])
    root = client.post(
        f"/api/videos/{identifier}/comments", headers=headers, json={"body": "root"}
    ).json()["id"]
    reply = client.post(
        f"/api/videos/{identifier}/comments",
        headers=headers,
        json={"body": "reply", "parent_id": root},
    ).json()["id"]
    assert (
        client.put(f"/api/videos/{identifier}/comments/{reply}/like", headers=headers).status_code
        == 200
    )
    assert client.put(f"/api/videos/{identifier}/like", headers=headers).status_code == 200
    playlists = []
    for _ in range(2):
        playlist = client.post(
            "/api/playlists", headers=headers, json={"title": "Synthetic"}
        ).json()
        playlists.append(playlist["id"])
        assert (
            client.put(
                f"/api/playlists/{playlist['id']}/items/{identifier}", headers=headers
            ).status_code
            == 204
        )
    with factory() as db:
        db.add(VideoAccess(video_id=video_id, user_id=uid, granted_by=uid))
        keys = list(
            db.scalars(select(VideoAsset.object_key).where(VideoAsset.video_id == video_id))
        )
        db.commit()
    assert client.delete(f"/api/videos/{identifier}", headers=headers).status_code == 204
    no_video(client, identifier)
    for path in ("/api/videos", "/api/videos?q=Test", f"/api/channels/{name}/videos"):
        assert identifier not in {row["id"] for row in client.get(path).json()["items"]}
    for pid in playlists:
        for prefix in ("/api/playlists", "/api/me/playlists"):
            assert client.get(f"{prefix}/{pid}").json()["items"] == []
        assert (
            client.put(f"/api/playlists/{pid}/items/{identifier}", headers=headers).status_code
            == 404
        )
    for path, data in (
        ("/comments", {"body": "late"}),
        ("/reports", {"reason": "spam", "body": "late"}),
    ):
        assert (
            client.post(f"/api/videos/{identifier}{path}", headers=headers, json=data).status_code
            == 404
        )
    assert client.get(f"/api/videos/{identifier}/comments/{root}/replies").status_code == 404
    assert (
        client.put(f"/api/videos/{identifier}/comments/{reply}/like", headers=headers).status_code
        == 404
    )
    assert client.put(f"/api/videos/{identifier}/like", headers=headers).status_code == 404
    assert (
        client.patch(
            f"/api/videos/{identifier}", headers=headers, json={"title": "resurrect"}
        ).status_code
        == 404
    )
    assert send(client, headers, identifier, clip).status_code == 404
    with factory() as db:
        for model in (VideoAccess, Like, Comment):
            assert db.scalar(select(model).where(model.video_id == video_id)) is None
        assert db.get(CommentLike, (UUID(reply), uid)) is None
        tombstone = db.get(Video, video_id)
        assert (
            tombstone.deleted_at and tombstone.title == DELETED_TEXT and tombstone.description == ""
        )
    # Repeated DELETE never adds a second cleanup job or resets the existing one.
    Deletions(factory, client.app.state.storage).run()
    with factory() as db:
        cleanup = db.get(VideoDeletion, video_id)
        assert cleanup.status == "complete" and cleanup.cleaned_at
        attempts, scheduled = cleanup.attempts, cleanup.run_after
        assert db.scalar(select(VideoAsset).where(VideoAsset.video_id == video_id)) is None
        assert db.scalar(select(VideoJob).where(VideoJob.video_id == video_id)) is None
    assert client.delete(f"/api/videos/{identifier}", headers=headers).status_code == 204
    with factory() as db:
        cleanup = db.get(VideoDeletion, video_id)
        assert (cleanup.attempts, cleanup.run_after) == (attempts, scheduled)
    for key in keys:
        with pytest.raises(ClientError):
            client.app.state.storage.head(key)


@pytest.mark.parametrize("role", ["user", "moderator", "admin"])
def test_only_owner_even_with_private_grant(auth, clip, role):
    client, factory, headers, identifier = setup(auth, "private")
    owner_cookie = client.cookies["session_id"]
    assert send(client, headers, identifier, clip).status_code == 202
    worker(client, factory).run_once()
    client.cookies.clear()
    _, response = register(auth)
    grantee_cookie = client.cookies["session_id"]
    grantee = response.json()
    other_headers = {**ORIGIN, "X-CSRF-Token": grantee["csrf_token"]}
    with factory() as db:
        uid = UUID(grantee["user"]["id"])
        db.get(User, uid).role = role
        db.add(VideoAccess(video_id=UUID(identifier), user_id=uid, granted_by=uid))
        db.commit()
    assert client.get(f"/api/videos/{identifier}").status_code == 200
    assert client.delete(f"/api/videos/{identifier}", headers=other_headers).status_code == 404
    client.cookies.clear()
    client.cookies.set("session_id", owner_cookie)
    assert client.delete(f"/api/videos/{identifier}", headers=headers).status_code == 204
    client.cookies.clear()
    client.cookies.set("session_id", grantee_cookie)
    assert client.get(f"/api/videos/{identifier}").status_code == 404
    assert client.head(f"/api/videos/{identifier}/media").status_code == 404
    assert client.delete(f"/api/videos/{identifier}", headers=other_headers).status_code == 404


def test_delete_requires_session_origin_csrf(auth):
    client, factory, headers, identifier = setup(auth)
    path = f"/api/videos/{identifier}"
    for invalid in (
        {},
        ORIGIN,
        {"X-CSRF-Token": headers["X-CSRF-Token"]},
        {**headers, "Origin": "https://evil.example"},
        {**ORIGIN, "X-CSRF-Token": "invalid"},
    ):
        assert client.delete(path, headers=invalid).status_code == 403
    client.cookies.clear()
    assert client.delete(path, headers=headers).status_code == 401
    with factory() as db:
        assert db.get(Video, UUID(identifier)).deleted_at is None


@pytest.mark.parametrize(
    "status", ["awaiting_upload", "uploading", "queued", "processing", "ready", "failed"]
)
def test_every_lifecycle_state_and_old_claim(auth, status):
    client, factory, headers, identifier = setup(auth)
    uid, vid, token = owner(client), UUID(identifier), uuid4()
    with factory() as db:
        video = db.get(Video, vid)
        video.status = status
        video.upload_token, video.upload_expires_at = (
            token,
            datetime.now(UTC) + timedelta(minutes=10),
        )
        job = VideoJob(
            video_id=vid,
            status="running" if status == "processing" else "queued",
            lease_token=token,
            lease_expires_at=datetime.now(UTC) + timedelta(minutes=5),
        )
        db.add(job)
        db.flush()
        claim = {"id": job.id, "video_id": vid, "token": token}
        db.commit()
    assert client.delete(f"/api/videos/{identifier}", headers=headers).status_code == 204
    jobs = Jobs(factory)
    for operation in (
        lambda: jobs.publish(claim, {}, []),
        lambda: jobs.fail(claim, "late"),
        lambda: jobs.heartbeat(claim["id"], token),
    ):
        with pytest.raises(LostLease):
            operation()
    with pytest.raises(HTTPException) as rejected:
        VideoService(factory).accepted(uid, vid, token, "not-written", 1, "0" * 64)
    assert rejected.value.status_code == 404
    assert jobs.claim() is None
    with factory() as db:
        db.get(VideoJob, claim["id"]).lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
        db.commit()
    jobs.recover()
    no_video(client, identifier)


def test_reports_and_audit_survive_without_deleted_content(auth):
    client, factory, headers, identifier = setup(auth)
    uid, vid = owner(client), UUID(identifier)
    with factory() as db:
        db.get(User, uid).role = "moderator"
        db.get(Video, vid).status = "ready"
        action = ModerationAction(
            actor_id=uid,
            video_id=vid,
            action="hide",
            previous_value="visible",
            new_value="hidden",
            reason="Sensitive quote",
        )
        db.add(action)
        db.flush()
        report = Report(
            video_id=vid,
            reporter_id=uid,
            reason="spam",
            body="Sensitive quote",
            status="actioned",
            reviewed_by=uid,
            reviewed_at=datetime.now(UTC),
            review_note="Sensitive quote",
            resolved_action_id=action.id,
        )
        db.add(report)
        db.flush()
        rid, aid = report.id, action.id
        db.commit()
    assert client.delete(f"/api/videos/{identifier}", headers=headers).status_code == 204
    assert not client.get("/api/moderation/reports?status=all").json()["items"]
    history = client.get("/api/moderation/actions").json()["items"]
    assert history[0]["id"] == str(aid) and history[0]["reason"] == DELETED_TEXT
    assert (
        client.put(
            f"/api/moderation/videos/{identifier}/state",
            headers=headers,
            json={"state": "visible", "reason": "restore"},
        ).status_code
        == 404
    )
    with factory() as db:
        assert db.get(Report, rid).resolved_action_id == aid
        assert db.get(Report, rid).body == DELETED_TEXT
        assert db.get(Report, rid).review_note == DELETED_TEXT


def test_real_ffmpeg_finishing_after_delete_never_publishes(auth, clip, monkeypatch):
    from app.worker import main

    client, factory, headers, identifier = setup(auth)
    assert send(client, headers, identifier, clip).status_code == 202
    original = main.transcode

    def finish_after_delete(*args):
        result = original(*args)
        assert client.delete(f"/api/videos/{identifier}", headers=headers).status_code == 204
        return result

    monkeypatch.setattr(main, "transcode", finish_after_delete)
    worker(client, factory).run_once()
    no_video(client, identifier)
    with factory() as db:
        assert db.get(Video, UUID(identifier)).published_at is None
        assert (
            db.scalar(
                select(VideoAsset).where(
                    VideoAsset.video_id == UUID(identifier), VideoAsset.kind == "playback"
                )
            )
            is None
        )
    Deletions(factory, client.app.state.storage).run()
    with factory() as db:
        assert db.get(VideoDeletion, UUID(identifier)).status == "complete"


def test_storage_failure_restart_and_late_object_reconciliation(auth, clip, monkeypatch, caplog):
    client, factory, headers, identifier = setup(auth)
    assert send(client, headers, identifier, clip).status_code == 202
    worker(client, factory).run_once()
    storage = client.app.state.storage
    other_key = f"processed/{uuid4()}/{uuid4()}/playback.mp4"
    storage.client.put_object(Bucket=storage.bucket, Key=other_key, Body=b"other synthetic video")
    assert client.delete(f"/api/videos/{identifier}", headers=headers).status_code == 204
    original = storage.client.delete_object

    def offline(**kwargs):
        raise OSError("secret-must-not-be-logged")

    monkeypatch.setattr(storage.client, "delete_object", offline)
    Deletions(factory, storage).run()
    no_video(client, identifier)
    with factory() as db:
        assert db.get(VideoDeletion, UUID(identifier)).status == "retry"
        assert db.get(VideoDeletion, UUID(identifier)).last_error_code == "storage_unavailable"
    assert "secret-must-not-be-logged" not in caplog.text
    monkeypatch.setattr(storage.client, "delete_object", original)
    due(factory, identifier)
    # New storage and worker objects have no shared in-memory retry state.
    restarted = Storage(client.app.state.settings)
    try:
        Deletions(factory, restarted).run()
        with factory() as db:
            assert db.get(VideoDeletion, UUID(identifier)).status == "complete"
        late = f"sources/{identifier}/{uuid4()}/original"
        restarted.client.put_object(
            Bucket=restarted.bucket, Key=late, Body=b"late synthetic upload"
        )
        due(factory, identifier)
        Deletions(factory, restarted).run()
        with pytest.raises(ClientError):
            restarted.head(late)
        assert restarted.head(other_key)["ContentLength"] > 0
    finally:
        restarted.client.delete_object(Bucket=restarted.bucket, Key=other_key)
        restarted.close()


@pytest.fixture
def race_case():
    settings = get_settings()
    assert settings.postgres_host == "test-db" and settings.postgres_db == "video_service_test"
    database, storage = Database(settings), Storage(settings)
    uid, cid, vid = uuid4(), uuid4(), uuid4()
    factory = database.session_factory
    with factory() as db:
        db.add(
            User(
                id=uid,
                email=f"{uid}@example.com",
                username="delete_" + uid.hex[:20],
                password_hash="synthetic",
            )
        )
        db.flush()
        db.add(Channel(id=cid, user_id=uid, title="Synthetic deletion race"))
        db.flush()
        db.add(Video(id=vid, channel_id=cid, title="Synthetic deletion race"))
        db.commit()
    try:
        yield factory, storage, uid, vid
    finally:
        delete_owned(factory, uid, vid)
        due(factory, vid)
        Deletions(factory, storage).run()
        with factory() as db:
            db.execute(delete(VideoDeletion).where(VideoDeletion.video_id == vid))
            db.execute(delete(VideoJob).where(VideoJob.video_id == vid))
            db.execute(delete(VideoAsset).where(VideoAsset.video_id == vid))
            db.execute(delete(Video).where(Video.id == vid))
            db.execute(delete(Channel).where(Channel.id == cid))
            db.execute(delete(User).where(User.id == uid))
            db.commit()
        storage.close()
        database.close()


def test_concurrent_delete_and_patch_independent_connections(race_case):
    factory, _, uid, vid = race_case
    start = Barrier(3)

    def remove():
        start.wait(timeout=5)
        delete_owned(factory, uid, vid)

    def patch():
        start.wait(timeout=5)
        try:
            VideoService(factory).patch(uid, vid, UpdateVideo(title="Late edit"))
        except HTTPException as error:
            assert error.status_code == 404

    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = [pool.submit(remove), pool.submit(remove), pool.submit(patch)]
        for future in futures:
            future.result(timeout=10)
    with factory() as db:
        assert db.get(Video, vid).title == DELETED_TEXT
        assert (
            db.scalar(
                select(func.count()).select_from(VideoDeletion).where(VideoDeletion.video_id == vid)
            )
            == 1
        )


def test_delete_while_source_is_being_written(race_case, tmp_path, monkeypatch):
    factory, storage, uid, vid = race_case
    service = VideoService(factory)
    token = service.reserve(uid, vid)
    path = tmp_path / "source"
    path.write_bytes(b"synthetic upload")
    writing, release = Event(), Event()
    original = storage.upload

    def slow_upload(*args):
        writing.set()
        assert release.wait(10)
        return original(*args)

    monkeypatch.setattr(storage, "upload", slow_upload)
    with ThreadPoolExecutor(max_workers=1) as pool:
        upload = pool.submit(
            service.store_source, storage, path, uid, vid, token, path.stat().st_size, "0" * 64
        )
        try:
            assert writing.wait(5)
            delete_owned(factory, uid, vid)
            Deletions(factory, storage).run()
            with factory() as db:
                assert db.get(VideoDeletion, vid).last_error_code == "writer_active"
        finally:
            release.set()
        with pytest.raises(HTTPException) as rejected:
            upload.result(timeout=10)
        assert rejected.value.status_code == 404
    with pytest.raises(LostLease), storage_writer(factory, vid):
        pass
    due(factory, vid)
    Deletions(factory, storage).run()
    with pytest.raises(ClientError):
        storage.head(f"sources/{vid}/{token}/original")


def test_migration_preserves_existing_rows_and_refuses_unsafe_downgrade(migration_db):
    engine, migrate = migration_db
    migrate("upgrade", "b7d2e8f91a60")
    seed_history(engine)
    with engine.connect() as db:
        before = db.execute(text("SELECT id,title,description,status FROM videos")).all()
    migrate("upgrade", "d4a6c2e91f08")
    with engine.begin() as db:
        assert db.execute(text("SELECT id,title,description,status FROM videos")).all() == before
        assert db.scalar(text("SELECT count(*) FROM videos WHERE deleted_at IS NOT NULL")) == 0
        db.execute(text("UPDATE videos SET deleted_at=now()"))
        db.execute(text("INSERT INTO video_deletions(video_id) SELECT id FROM videos"))
    migrate("downgrade", "b7d2e8f91a60", success=False)
    with engine.connect() as db:
        assert db.scalar(text("SELECT version_num FROM alembic_version")) == "d4a6c2e91f08"
        assert db.scalar(text("SELECT count(*) FROM video_deletions")) == 1


def test_crash_after_s3_delete_before_db_commit(auth, clip, monkeypatch):
    client, factory, headers, identifier = setup(auth)
    assert send(client, headers, identifier, clip).status_code == 202
    vid = UUID(identifier)
    with factory() as db:
        key = db.scalar(select(VideoAsset.object_key).where(VideoAsset.video_id == vid))
    assert client.delete(f"/api/videos/{identifier}", headers=headers).status_code == 204
    cleaner = Deletions(factory, client.app.state.storage)
    original = cleaner.clean

    def crash(*args):
        original(*args)
        raise SystemExit("Synthetic process crash before commit")

    monkeypatch.setattr(cleaner, "clean", crash)
    with pytest.raises(SystemExit):
        cleaner.run()
    with factory() as db:
        assert db.get(VideoDeletion, vid).status == "pending"
        assert db.scalar(select(VideoAsset.id).where(VideoAsset.video_id == vid))
    with pytest.raises(ClientError):
        client.app.state.storage.head(key)
    Deletions(factory, client.app.state.storage).run()
    with factory() as db:
        assert db.get(VideoDeletion, vid).status == "complete"


def test_asset_outside_video_scope_is_never_deleted(auth):
    client, factory, headers, identifier = setup(auth)
    vid = UUID(identifier)
    storage = client.app.state.storage
    other = f"processed/{uuid4()}/{uuid4()}/playback.mp4"
    storage.client.put_object(Bucket=storage.bucket, Key=other, Body=b"synthetic protected object")
    try:
        with factory() as db:
            db.add(
                VideoAsset(
                    video_id=vid,
                    kind="playback",
                    object_key=other,
                    content_type="video/mp4",
                    size_bytes=26,
                    sha256="0" * 64,
                )
            )
            db.commit()
        assert client.delete(f"/api/videos/{identifier}", headers=headers).status_code == 204
        Deletions(factory, storage).run()
        with factory() as db:
            assert db.get(VideoDeletion, vid).status == "retry"
            assert db.get(VideoDeletion, vid).last_error_code == "object_scope_invalid"
        assert storage.head(other)["ContentLength"] > 0
    finally:
        storage.client.delete_object(Bucket=storage.bucket, Key=other)


def test_delete_during_body_upload(auth):
    import anyio

    from app.modules.videos.upload import receive

    client, factory, _, identifier = setup(auth)
    vid, uid = UUID(identifier), owner(client)

    class UploadRequest:
        app = client.app
        headers = {"content-type": "application/octet-stream"}

        async def stream(self):
            yield b"synthetic first chunk"
            delete_owned(factory, uid, vid)
            yield b"synthetic last chunk"

    with pytest.raises(HTTPException) as rejected:
        anyio.run(receive, UploadRequest(), VideoService(factory), uid, vid)
    assert rejected.value.status_code == 404
    no_video(client, identifier)
    with factory() as db:
        assert db.scalar(select(VideoAsset.id).where(VideoAsset.video_id == vid)) is None


def test_worker_output_written_after_tombstone_is_cleaned(race_case, clip, tmp_path, monkeypatch):
    from app.infrastructure.transcoder import checksum
    from app.worker.main import Worker

    factory, storage, uid, vid = race_case
    path = tmp_path / "source"
    path.write_bytes(clip)
    service = VideoService(factory)
    token = service.reserve(uid, vid)
    service.store_source(storage, path, uid, vid, token, len(clip), checksum(path))
    original = storage.upload
    written = []

    def late_upload(path, key, mime):
        if key.startswith("processed/") and not written:
            delete_owned(factory, uid, vid)
        original(path, key, mime)
        written.append(key)

    monkeypatch.setattr(storage, "upload", late_upload)
    Worker(get_settings(), factory, storage).run_once()
    assert written
    with factory() as db:
        assert db.get(Video, vid).deleted_at is not None
        assert db.get(Video, vid).published_at is None
    Deletions(factory, storage).run()
    for key in written:
        with pytest.raises(ClientError):
            storage.head(key)


def test_publication_waiting_on_delete_lock_is_rejected(race_case):
    factory, _, uid, vid = race_case
    token = uuid4()
    with factory() as db:
        db.get(Video, vid).status = "processing"
        job = VideoJob(
            video_id=vid,
            status="running",
            attempts=1,
            lease_token=token,
            lease_expires_at=datetime.now(UTC) + timedelta(minutes=5),
        )
        db.add(job)
        db.flush()
        claim = {"id": job.id, "video_id": vid, "token": token}
        db.commit()
    pids = Queue()

    @contextmanager
    def contender():
        with factory() as db:
            db.execute(text("SET LOCAL lock_timeout='8s'"))
            pids.put(db.scalar(select(func.pg_backend_pid())))
            yield db

    blocker = factory()
    with ThreadPoolExecutor(max_workers=1) as pool:
        try:
            blocker_pid = blocker.scalar(select(func.pg_backend_pid()))
            blocker.scalar(select(Video).where(Video.id == vid).with_for_update())
            publication = pool.submit(
                Jobs(contender).publish,
                claim,
                {"duration_ms": 2000, "width": 320, "height": 180},
                [],
            )
            contender_pid = pids.get(timeout=3)
            with factory() as observer:
                deadline = time.monotonic() + 3
                while not observer.scalar(
                    text("SELECT :b = ANY(pg_blocking_pids(:c))"),
                    {"b": blocker_pid, "c": contender_pid},
                ):
                    assert time.monotonic() < deadline, "Publication must wait for video lock"
                    time.sleep(0.01)
            assert not publication.done()
            # DELETE holds Video only: it never waits for the publisher's Job lock.
            delete_owned(lambda: blocker, uid, vid)
            with pytest.raises(LostLease):
                publication.result(timeout=3)
        finally:
            blocker.rollback()
            blocker.close()


def test_cleanup_in_new_process_including_multipart(race_case):
    factory, storage, uid, vid = race_case
    key = f"sources/{vid}/{uuid4()}/original"
    storage.client.put_object(Bucket=storage.bucket, Key=key, Body=b"synthetic source")
    part_key = f"sources/{vid}/{uuid4()}/original"
    upload = storage.client.create_multipart_upload(Bucket=storage.bucket, Key=part_key)
    storage.client.upload_part(
        Bucket=storage.bucket,
        Key=part_key,
        UploadId=upload["UploadId"],
        PartNumber=1,
        Body=b"synthetic incomplete upload",
    )
    delete_owned(factory, uid, vid)
    program = (
        "from app.core.config import get_settings; from app.db.session import Database; "
        "from app.infrastructure.storage import Storage; "
        "from app.worker.deletions import Deletions; "
        "s=get_settings(); d=Database(s); storage=Storage(s); "
        "Deletions(d.session_factory,storage).run(); storage.close(); d.close()"
    )
    result = subprocess.run([sys.executable, "-c", program], capture_output=True, timeout=20)
    assert result.returncode == 0, "New worker process must recover persisted cleanup"
    with factory() as db:
        assert db.get(VideoDeletion, vid).status == "complete"
    with pytest.raises(ClientError):
        storage.head(key)
    remaining = storage.client.list_multipart_uploads(
        Bucket=storage.bucket, Prefix=f"sources/{vid}/"
    )
    assert not remaining.get("Uploads", [])


@pytest.fixture
def studio_videos(auth):
    client, factory, headers, identifier = setup(auth)
    identifiers = [identifier]
    for index in range(4):
        response = client.post("/api/videos", headers=headers, json={"title": f"Studio {index}"})
        assert response.status_code == 201
        identifiers.append(response.json()["id"])
    return client, factory, headers, identifiers


@pytest.mark.parametrize("deleted_anchor", [False, True])
@pytest.mark.parametrize("tied_timestamps", [False, True])
def test_studio_cursor_survives_deletion(studio_videos, deleted_anchor, tied_timestamps):
    client, factory, headers, identifiers = studio_videos
    with factory() as db:
        for index, identifier in enumerate(identifiers):
            db.get(Video, UUID(identifier)).created_at = datetime(2026, 1, 1, tzinfo=UTC) + (
                timedelta(0) if tied_timestamps else timedelta(seconds=index)
            )
        db.commit()
    before = client.get("/api/me/videos?limit=100").json()["items"]
    expected = [row["id"] for row in before]
    expected_order = sorted(identifiers, reverse=True) if tied_timestamps else identifiers[::-1]
    assert expected == expected_order
    first = client.get("/api/me/videos?limit=1").json()
    anchor = first["next_cursor"]
    assert anchor == expected[0]
    # Also delete an item after the anchor: subsequent pages must filter tombstones.
    deleted = [expected[1]] + ([anchor] if deleted_anchor else [])
    for identifier in deleted:
        assert client.delete(f"/api/videos/{identifier}", headers=headers).status_code == 204
        no_video(client, identifier)
    seen = []
    cursor = anchor
    while cursor:
        response = client.get("/api/me/videos", params={"limit": 1, "cursor": cursor})
        assert response.status_code == 200
        page = response.json()
        seen.extend(row["id"] for row in page["items"])
        cursor = page["next_cursor"]
        assert len(seen) <= len(expected)
    assert seen == expected[2:]
    assert len(seen) == len(set(seen))


@pytest.mark.parametrize("deleted_anchor", [False, True])
def test_studio_rejects_foreign_cursor(studio_videos, auth, deleted_anchor):
    client, _, _, _ = studio_videos
    cookie = client.cookies["session_id"]
    client.cookies.clear()
    _, response = register(auth)
    headers = {**ORIGIN, "X-CSRF-Token": response.json()["csrf_token"]}
    foreign = client.post("/api/videos", headers=headers, json={"title": "Foreign"}).json()["id"]
    if deleted_anchor:
        assert client.delete(f"/api/videos/{foreign}", headers=headers).status_code == 204
    client.cookies.clear()
    client.cookies.set("session_id", cookie)
    result = client.get("/api/me/videos", params={"cursor": foreign})
    assert result.status_code == 404
    assert result.json()["code"] == "http_404"
    assert result.json()["message"] == "Видео не найдено"
    assert client.get(f"/api/me/videos/{foreign}").status_code == 404


@pytest.mark.parametrize("cursor,status", [(str(uuid4()), 404), ("invalid", 422)])
def test_studio_rejects_invalid_cursor(studio_videos, cursor, status):
    client, _, _, _ = studio_videos
    assert client.get("/api/me/videos", params={"cursor": cursor}).status_code == status


@pytest.mark.parametrize("limit", [1, 2])
def test_studio_unchanged_pagination(studio_videos, limit):
    client, _, _, identifiers = studio_videos
    expected = [row["id"] for row in client.get("/api/me/videos?limit=100").json()["items"]]
    seen, cursor = [], None
    while True:
        response = client.get(
            "/api/me/videos", params={"limit": limit, **({"cursor": cursor} if cursor else {})}
        )
        assert response.status_code == 200
        page = response.json()
        seen.extend(row["id"] for row in page["items"])
        cursor = page["next_cursor"]
        assert len(seen) <= len(identifiers)
        if cursor is None:
            break
    assert seen == expected
    assert len(set(seen)) == len(identifiers)
