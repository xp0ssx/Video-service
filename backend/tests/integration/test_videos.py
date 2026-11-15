import os
import subprocess
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from sqlalchemy import func, select

from app.infrastructure.transcoder import LostLease
from app.modules.auth.models import User
from app.modules.videos.models import Video, VideoAsset, VideoJob
from app.worker.jobs import Jobs
from app.worker.main import Worker

from .test_auth import ORIGIN, register

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(os.getenv("RUN_INTEGRATION") != "1", reason="Real PostgreSQL/S3 required"),
]


@pytest.fixture(scope="session")
def clip(tmp_path_factory):
    path = tmp_path_factory.mktemp("synthetic-media") / "input.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc=size=320x180:rate=15",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=44100",
            "-t",
            "2",
            "-c:v",
            "libx264",
            "-threads",
            "2",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-movflags",
            "+faststart",
            str(path),
        ],
        check=True,
        env={"PATH": "/usr/bin:/bin"},
    )
    return path.read_bytes()


def setup(auth, visibility="public"):
    client, factory = auth
    _, response = register(auth)
    headers = {**ORIGIN, "X-CSRF-Token": response.json()["csrf_token"]}
    result = client.post(
        "/api/videos",
        json={
            "title": " Test video ",
            "description": "<script>not HTML</script>",
            "visibility": visibility,
        },
        headers=headers,
    )
    assert result.status_code == 201
    return client, factory, headers, result.json()["id"]


def send(client, headers, video_id, body):
    return client.put(
        f"/api/videos/{video_id}/source",
        content=body,
        headers={**headers, "Content-Type": "application/octet-stream"},
    )


def worker(client, factory):
    return Worker(client.app.state.settings, factory, client.app.state.storage)


def test_upload_process_media_and_ranges(auth, clip):
    client, factory, headers, video_id = setup(auth)
    assert client.get(f"/api/videos/{video_id}").status_code == 404
    assert send(client, headers, video_id, clip).status_code == 202
    with factory() as db:
        job = db.scalar(select(VideoJob).where(VideoJob.video_id == UUID(video_id)))
        assert job.status == "queued" and job.attempts == 0
        assert db.get(Video, UUID(video_id)).status == "queued"
    assert send(client, headers, video_id, clip).status_code == 409
    assert worker(client, factory).run_once()
    assert client.get(f"/api/me/videos/{video_id}").json()["status"] == "ready"
    assert client.get("/api/me/videos").json()["items"][0]["id"] == video_id
    client.cookies.clear()
    metadata = client.get(f"/api/videos/{video_id}")
    assert metadata.status_code == 200 and metadata.json()["title"] == "Test video"
    assert not {"object_key", "password_hash", "email"} & set(metadata.json())
    for kind, mime in [("media", "video/mp4"), ("thumbnail", "image/jpeg")]:
        url = f"/api/videos/{video_id}/{kind}"
        full = client.get(url)
        assert full.status_code == 200 and full.headers["content-type"] == mime
        assert "no-store" in full.headers["cache-control"]
        assert len(full.content) == int(full.headers["content-length"])
        head = client.head(url, headers={"Range": "bytes=9999999-"})
        assert head.status_code == 200 and head.content == b""
        assert head.headers["content-length"] == full.headers["content-length"]
        for value, expected in [
            ("bytes=0-15", full.content[:16]),
            ("bytes=-7", full.content[-7:]),
            ("bytes=10-", full.content[10:]),
        ]:
            partial = client.get(url, headers={"Range": value})
            assert partial.status_code == 206 and partial.content == expected
            assert partial.headers["content-range"].startswith("bytes ")
        for invalid in ["bytes=999999999-", "bytes=0-1,4-5", "bytes=-0", "bytes=no", "other=0-1"]:
            result = client.get(url, headers={"Range": invalid})
            assert result.status_code == 416
            assert result.headers["content-range"] == f"bytes */{len(full.content)}"
    with factory() as db:
        assert (
            db.scalar(
                select(func.count())
                .select_from(VideoAsset)
                .where(VideoAsset.video_id == UUID(video_id))
            )
            == 3
        )
        assert (
            db.scalar(select(VideoJob).where(VideoJob.video_id == UUID(video_id))).status
            == "succeeded"
        )
    assert not worker(client, factory).run_once()


@pytest.mark.parametrize(
    "body,limit,expected,code",
    [(b"", 100, 422, "empty_file"), (b"x" * 101, 100, 413, "file_too_large")],
)
def test_upload_rejection_and_retry(auth, body, limit, expected, code, clip):
    client, factory, headers, video_id = setup(auth)
    client.app.state.settings = client.app.state.settings.model_copy(
        update={"max_upload_bytes": limit}
    )
    assert send(client, headers, video_id, body).status_code == expected
    status = client.get(f"/api/me/videos/{video_id}").json()
    assert (
        status["status"] == "failed"
        and status["error_code"] == code
        and not status["source_accepted"]
    )
    client.app.state.settings = client.app.state.settings.model_copy(
        update={"max_upload_bytes": 500 * 1024 * 1024}
    )
    assert send(client, headers, video_id, clip).status_code == 202


@pytest.mark.parametrize(
    "body",
    [
        b"corrupt video contents",
        b"#EXTM3U\nhttp://example.com/forbidden.ts",
        b"RIFF" + b"\x00" * 100,
    ],
)
def test_corrupt_or_unsupported_input(auth, body):
    client, factory, headers, video_id = setup(auth)
    assert send(client, headers, video_id, body).status_code == 202
    worker(client, factory).run_once()
    assert client.get(f"/api/me/videos/{video_id}").json()["status"] == "failed"
    with factory() as db:
        assert db.scalar(select(VideoJob).where(VideoJob.video_id == UUID(video_id))).attempts == 1
        assert (
            db.scalar(
                select(func.count())
                .select_from(VideoAsset)
                .where(VideoAsset.video_id == UUID(video_id))
            )
            == 1
        )
    assert client.get(f"/api/videos/{video_id}/media").status_code == 404


@pytest.mark.parametrize("role", ["user", "moderator", "admin"])
def test_private_grants_revocation_and_roles(auth, clip, role):
    client, factory, headers, video_id = setup(auth, "private")
    owner_cookie = client.cookies["session_id"]
    assert send(client, headers, video_id, clip).status_code == 202
    worker(client, factory).run_once()
    client.cookies.clear()
    for suffix in ["", "/media", "/thumbnail"]:
        assert client.get(f"/api/videos/{video_id}{suffix}").status_code == 404
    other, response = register(auth)
    other_cookie = client.cookies["session_id"]
    other_headers = {**ORIGIN, "X-CSRF-Token": response.json()["csrf_token"]}
    with factory() as db:
        db.get(User, UUID(response.json()["user"]["id"])).role = role
        db.commit()
    for suffix in ["", "/media", "/thumbnail", "/access"]:
        assert client.get(f"/api/videos/{video_id}{suffix}").status_code == 404
    assert send(client, other_headers, video_id, clip).status_code == 404
    assert (
        client.patch(
            f"/api/videos/{video_id}", json={"visibility": "public"}, headers=other_headers
        ).status_code
        == 404
    )
    assert (
        client.put(
            f"/api/videos/{video_id}/access/{other['username']}", headers=other_headers
        ).status_code
        == 404
    )
    client.cookies.clear()
    client.cookies.set("session_id", owner_cookie)
    for _ in range(2):
        assert (
            client.put(
                f"/api/videos/{video_id}/access/{other['username']}", headers=headers
            ).status_code
            == 204
        )
    assert client.get(f"/api/videos/{video_id}/access").json() == [{"username": other["username"]}]
    client.cookies.clear()
    client.cookies.set("session_id", other_cookie)
    assert (
        client.get(f"/api/videos/{video_id}/media", headers={"Range": "bytes=0-15"}).status_code
        == 206
    )
    client.cookies.clear()
    client.cookies.set("session_id", owner_cookie)
    assert (
        client.delete(
            f"/api/videos/{video_id}/access/{other['username']}", headers=headers
        ).status_code
        == 204
    )
    client.cookies.clear()
    client.cookies.set("session_id", other_cookie)
    assert client.head(f"/api/videos/{video_id}/media").status_code == 404
    assert client.get(f"/api/videos/{video_id}/thumbnail").status_code == 404
    client.cookies.clear()
    client.cookies.set("session_id", owner_cookie)
    assert (
        client.patch(
            f"/api/videos/{video_id}", json={"visibility": "public"}, headers=headers
        ).status_code
        == 200
    )
    client.cookies.clear()
    assert client.get(f"/api/videos/{video_id}/media").status_code == 200
    client.cookies.set("session_id", owner_cookie)
    assert (
        client.patch(
            f"/api/videos/{video_id}", json={"visibility": "private"}, headers=headers
        ).status_code
        == 200
    )
    with factory() as db:
        db.get(Video, UUID(video_id)).moderation_state = "hidden"
        db.commit()
    assert client.get(f"/api/videos/{video_id}/media").status_code == 404


def test_worker_restart_expired_lease_and_fencing(auth, clip):
    client, factory, headers, video_id = setup(auth)
    assert send(client, headers, video_id, clip).status_code == 202
    jobs = Jobs(factory)
    old = jobs.claim()
    assert client.get(f"/api/me/videos/{video_id}").json()["status"] == "processing"
    with factory() as db:
        db.get(VideoJob, old["id"]).lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
        db.commit()
    # A fresh worker instance represents a new process with no in-memory job state.
    assert worker(client, factory).run_once()
    with pytest.raises(LostLease):
        jobs.publish(old, {}, [])
    with factory() as db:
        job = db.get(VideoJob, old["id"])
        assert job.status == "succeeded" and job.attempts == 2
        assert (
            db.scalar(
                select(func.count())
                .select_from(VideoAsset)
                .where(VideoAsset.video_id == UUID(video_id))
            )
            == 3
        )
    assert jobs.claim() is None


def test_retry_storage_failure_and_preserve_visibility(auth, clip, monkeypatch):
    client, factory, headers, video_id = setup(auth, "private")
    assert send(client, headers, video_id, clip).status_code == 202
    storage = client.app.state.storage
    original = storage.download
    monkeypatch.setattr(
        storage, "download", lambda *args: (_ for _ in ()).throw(OSError("synthetic failure"))
    )
    worker(client, factory).run_once()
    with factory() as db:
        job = db.scalar(select(VideoJob).where(VideoJob.video_id == UUID(video_id)))
        assert job.status == "queued" and job.attempts == 1
        job.run_after = datetime.now(UTC)
        db.get(Video, UUID(video_id)).moderation_state = "hidden"
        db.commit()
    monkeypatch.setattr(storage, "download", original)
    worker(client, factory).run_once()
    state = client.get(f"/api/me/videos/{video_id}").json()
    assert (
        state["status"] == "ready"
        and state["visibility"] == "private"
        and state["moderation_state"] == "hidden"
    )
    assert client.get(f"/api/videos/{video_id}").status_code == 404


def test_video_csrf_origin_and_role_injection(auth):
    client, _, headers, video_id = setup(auth)
    assert client.post("/api/videos", json={"title": "x"}, headers=ORIGIN).status_code == 403
    assert (
        client.patch(
            f"/api/videos/{video_id}",
            json={"title": "x"},
            headers={"Origin": "https://evil.example", "X-CSRF-Token": headers["X-CSRF-Token"]},
        ).status_code
        == 403
    )
    assert (
        client.post(
            "/api/videos", json={"title": "x", "channel_id": video_id}, headers=headers
        ).status_code
        == 422
    )
    assert (
        client.patch(f"/api/videos/{video_id}", json={"title": None}, headers=headers).status_code
        == 422
    )


@pytest.mark.parametrize("declared", [None, "1"])
def test_actual_size_with_missing_or_lying_length(auth, declared):
    client, _, headers, video_id = setup(auth)
    client.app.state.settings = client.app.state.settings.model_copy(
        update={"max_upload_bytes": 20}
    )
    extra = {"Content-Type": "application/octet-stream", **headers}
    if declared:
        extra["Content-Length"] = declared
    response = client.put(
        f"/api/videos/{video_id}/source", content=iter([b"x" * 10] * 3), headers=extra
    )
    assert response.status_code == 413
    assert client.get(f"/api/me/videos/{video_id}").json()["error_code"] == "file_too_large"


def test_upload_reservation_limits_expiry_and_stale_token(auth):
    from fastapi import HTTPException

    from app.modules.videos.service import VideoService

    client, factory, headers, video_id = setup(auth)
    user_id = UUID(client.get("/api/auth/session").json()["user"]["id"])
    videos = VideoService(factory)
    first = videos.reserve(user_id, UUID(video_id))
    with pytest.raises(HTTPException) as conflict:
        videos.reserve(user_id, UUID(video_id))
    assert conflict.value.status_code == 409
    second_id = client.post("/api/videos", json={"title": "second"}, headers=headers).json()["id"]
    with pytest.raises(HTTPException) as busy:
        videos.reserve(user_id, UUID(second_id))
    assert busy.value.status_code == 429
    with factory() as db:
        db.get(Video, UUID(video_id)).upload_expires_at = datetime.now(UTC) - timedelta(seconds=1)
        db.commit()
    renewed = videos.reserve(user_id, UUID(video_id))
    assert renewed != first
    with pytest.raises(HTTPException) as stale:
        videos.accepted(user_id, UUID(video_id), first, "not-written", 1, "0" * 64)
    assert stale.value.status_code == 409


def test_upload_storage_and_transaction_failures(auth, clip, monkeypatch):
    from app.modules.videos import service

    client, factory, headers, video_id = setup(auth)
    storage = client.app.state.storage
    original = storage.upload

    def broken(*args):
        raise OSError("Synthetic storage failure")

    monkeypatch.setattr(storage, "upload", broken)
    assert send(client, headers, video_id, clip).status_code == 503
    monkeypatch.setattr(storage, "upload", original)
    original_job = service.VideoJob
    monkeypatch.setattr(service, "VideoJob", broken)
    assert send(client, headers, video_id, clip).status_code == 503
    with factory() as db:
        assert db.scalar(select(VideoAsset).where(VideoAsset.video_id == UUID(video_id))) is None
        assert db.scalar(select(VideoJob).where(VideoJob.video_id == UUID(video_id))) is None
    monkeypatch.setattr(service, "VideoJob", original_job)
    assert send(client, headers, video_id, clip).status_code == 202


def test_ffmpeg_failure_and_attempt_exhaustion(auth, clip, monkeypatch):
    from app.infrastructure.transcoder import ProcessingError
    from app.worker import main

    client, factory, headers, video_id = setup(auth)
    assert send(client, headers, video_id, clip).status_code == 202

    def broken(*args):
        raise ProcessingError("ffmpeg_failed")

    monkeypatch.setattr(main, "transcode", broken)
    worker(client, factory).run_once()
    assert client.get(f"/api/me/videos/{video_id}").json()["error_code"] == "ffmpeg_failed"
    with factory() as db:
        job = db.scalar(select(VideoJob).where(VideoJob.video_id == UUID(video_id)))
        job.status, job.attempts, job.lease_expires_at = (
            "running",
            3,
            datetime.now(UTC) - timedelta(seconds=1),
        )
        db.get(Video, UUID(video_id)).status = "processing"
        db.commit()
    Jobs(factory).recover()
    with factory() as db:
        assert db.get(VideoJob, job.id).status == "failed"
        assert db.get(Video, UUID(video_id)).status == "failed"


@pytest.mark.parametrize(
    "extension,codec",
    [("mp4", "libx264"), ("mov", "libx264"), ("mkv", "libx264"), ("webm", "libvpx-vp9")],
)
def test_worker_without_audio(auth, tmp_path, extension, codec):
    path = tmp_path / ("silent." + extension)
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=blue:s=160x90:r=10",
            "-t",
            "2",
            "-c:v",
            codec,
            "-threads",
            "2",
            str(path),
        ],
        check=True,
        env={"PATH": "/usr/bin:/bin"},
    )
    client, factory, headers, video_id = setup(auth)
    assert send(client, headers, video_id, path.read_bytes()).status_code == 202
    worker(client, factory).run_once()
    assert client.get(f"/api/me/videos/{video_id}").json()["status"] == "ready"


def test_cleanup_preserves_assets_and_active_uploads(auth, clip, tmp_path):
    from uuid import uuid4

    from app.worker.cleanup import Cleanup

    client, factory, headers, video_id = setup(auth)
    assert send(client, headers, video_id, clip).status_code == 202
    worker(client, factory).run_once()
    settings = client.app.state.settings.model_copy(update={"scratch_dir": str(tmp_path)})
    cleanup = Cleanup(factory, settings, client.app.state.storage)
    with factory() as db:
        keys = list(
            db.scalars(select(VideoAsset.object_key).where(VideoAsset.video_id == UUID(video_id)))
        )
        token = uuid4()
        video = db.get(Video, UUID(video_id))
        video.upload_token, video.upload_expires_at = (
            token,
            datetime.now(UTC) + timedelta(minutes=1),
        )
        db.commit()
    assert all(not cleanup.unreferenced(key) for key in keys)
    stale = tmp_path / ("upload-" + str(uuid4()) + "-stale")
    active = tmp_path / ("upload-" + str(token) + "-active")
    for path in (stale, active):
        path.mkdir()
        os.utime(path, (0, 0))
    cleanup.run()
    assert not stale.exists() and active.exists()
    assert all(client.app.state.storage.head(key)["ContentLength"] > 0 for key in keys)
    unknown = tmp_path / ("upload-" + str(uuid4()) + "-uncertain")
    unknown.mkdir()
    os.utime(unknown, (0, 0))

    def unavailable():
        raise OSError("Synthetic database outage")

    with pytest.raises(OSError):
        Cleanup(unavailable, settings, client.app.state.storage).scratch()
    assert unknown.exists()
