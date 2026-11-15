"""Lease fencing against real PostgreSQL row locks on independent connections."""

import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import timedelta
from queue import Queue
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import delete, func, select, text
from sqlalchemy.dialects.postgresql import insert

from app.core.config import get_settings
from app.db.session import Database
from app.infrastructure.transcoder import LostLease
from app.modules.auth.models import Channel, User
from app.modules.videos.models import Video, VideoAsset, VideoJob, WorkerHeartbeat
from app.worker.jobs import Jobs

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(os.getenv("RUN_INTEGRATION") != "1", reason="Isolated PostgreSQL required"),
]


@pytest.fixture
def lease_case():
    settings = get_settings()
    assert settings.postgres_host == "test-db" and settings.postgres_db == "video_service_test"
    database = Database(settings)
    factory = database.session_factory
    user_id, channel_id, video_id, job_id, token = (uuid4() for _ in range(5))
    try:
        with factory() as db:
            now = db.scalar(select(func.clock_timestamp()))
            db.add(
                User(
                    id=user_id,
                    email=f"{user_id.hex}@example.com",
                    username="lease_" + user_id.hex[:20],
                    password_hash="synthetic-not-for-login",
                )
            )
            db.flush()
            db.add(Channel(id=channel_id, user_id=user_id, title="Lease regression"))
            db.flush()
            db.add(
                Video(
                    id=video_id,
                    channel_id=channel_id,
                    title="Lease regression",
                    status="processing",
                    visibility="private",
                )
            )
            db.flush()
            db.add(
                VideoJob(
                    id=job_id,
                    video_id=video_id,
                    status="running",
                    attempts=1,
                    lease_token=token,
                    lease_expires_at=now + timedelta(minutes=5),
                    heartbeat_at=now,
                )
            )
            db.execute(
                insert(WorkerHeartbeat)
                .values(worker_id="main", last_seen_at=now)
                .on_conflict_do_nothing()
            )
            db.commit()
        case = SimpleNamespace(
            database=database, factory=factory, video_id=video_id, job_id=job_id, token=token
        )
        case.claim = {"id": job_id, "video_id": video_id, "token": token}
        case.assets = [
            dict(
                kind=kind,
                object_key=f"processed/{video_id}/{token}/{filename}",
                content_type=mime,
                size_bytes=32,
                sha256="0" * 64,
            )
            for kind, filename, mime in [
                ("playback", "playback.mp4", "video/mp4"),
                ("thumbnail", "thumbnail.jpg", "image/jpeg"),
            ]
        ]
        case.info = {"duration_ms": 2000, "width": 320, "height": 180}
        yield case
    finally:
        # Only UUIDs generated above in the isolated test DB; no production objects.
        with factory() as db:
            db.execute(delete(VideoAsset).where(VideoAsset.video_id == video_id))
            db.execute(delete(VideoJob).where(VideoJob.id == job_id))
            db.execute(delete(Video).where(Video.id == video_id))
            db.execute(delete(Channel).where(Channel.id == channel_id))
            db.execute(delete(User).where(User.id == user_id))
            db.commit()
        database.close()


def wait_for(predicate, message, timeout=6):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    pytest.fail(message)


def invoke(jobs, case, operation):
    if operation == "publish":
        return jobs.publish(case.claim, case.info, case.assets)
    if operation == "fail":
        return jobs.fail(case.claim, "synthetic_failure")
    return jobs.heartbeat(case.job_id, case.token)


@contextmanager
def blocked_operation(case, operation, target, expires=False):
    """The controller owns a lock; pg_blocking_pids proves the contender waits."""
    pid_queue = Queue()
    blocker = case.factory()
    observer = case.database.engine.connect()
    executor = ThreadPoolExecutor(max_workers=1)
    try:
        blocker.execute(text("SET LOCAL lock_timeout='8s'"))
        blocker_pid = blocker.scalar(select(func.pg_backend_pid()))
        target_row = {"video": Video, "job": VideoJob, "heartbeat": WorkerHeartbeat}[target]
        condition = (
            target_row.worker_id == "main"
            if target == "heartbeat"
            else target_row.id == (case.video_id if target == "video" else case.job_id)
        )
        if expires:
            with case.factory() as db:
                job = db.get(VideoJob, case.job_id)
                job.lease_expires_at = db.scalar(select(func.clock_timestamp())) + timedelta(
                    seconds=4
                )
                deadline = job.lease_expires_at
                db.commit()
        else:
            deadline = None

        blocker.scalar(select(target_row).where(condition).with_for_update())

        @contextmanager
        def contender_factory():
            with case.factory() as db:
                db.execute(text("SET LOCAL lock_timeout='8s'"))
                db.execute(text("SET LOCAL statement_timeout='10s'"))
                pid_queue.put(db.scalar(select(func.pg_backend_pid())))
                yield db

        future = executor.submit(invoke, Jobs(contender_factory), case, operation)
        contender_pid = pid_queue.get(timeout=3)
        wait_for(
            lambda: observer.scalar(
                text("SELECT :blocker = ANY(pg_blocking_pids(:contender))"),
                {"blocker": blocker_pid, "contender": contender_pid},
            ),
            "Contender never waited on the controlled PostgreSQL lock",
            timeout=3,
        )
        assert not future.done()
        if expires:
            assert observer.scalar(select(func.clock_timestamp())) < deadline, (
                "Lease expired before the observed lock wait"
            )
            wait_for(
                lambda: observer.scalar(select(func.clock_timestamp())) >= deadline,
                "Database clock did not reach lease expiry",
            )
            # A transaction-start timestamp would still authorize this expired lease.
            assert observer.scalar(select(func.now())) < deadline
        yield future, observer
    finally:
        # Always release the lock before joining, including on assertion failure.
        blocker.rollback()
        blocker.close()
        executor.shutdown(wait=True, cancel_futures=True)
        observer.close()


def assert_not_published(case):
    with case.factory() as db:
        job, video = db.get(VideoJob, case.job_id), db.get(Video, case.video_id)
        assert job.status == "running" and job.lease_token == case.token
        assert video.status == "processing" and video.published_at is None
        assert (
            db.scalar(
                select(func.count())
                .select_from(VideoAsset)
                .where(VideoAsset.video_id == case.video_id)
            )
            == 0
        )


@pytest.mark.parametrize("operation", ["publish", "fail"])
def test_expiry_while_publish_or_fail_waits_for_video(lease_case, operation):
    case = lease_case
    with blocked_operation(case, operation, "video", expires=True) as (future, _):
        pass
    with pytest.raises(LostLease):
        future.result(timeout=2)
    assert_not_published(case)


@pytest.mark.parametrize("target", ["heartbeat", "job"])
def test_expiry_while_heartbeat_waits_for_lock(lease_case, target):
    case = lease_case
    with case.factory() as db:
        heartbeat_before = db.get(WorkerHeartbeat, "main").last_seen_at
        job_heartbeat_before = db.get(VideoJob, case.job_id).heartbeat_at
    with blocked_operation(case, "heartbeat", target, expires=True) as (future, _):
        pass
    with pytest.raises(LostLease):
        future.result(timeout=2)
    assert_not_published(case)
    with case.factory() as db:
        job = db.get(VideoJob, case.job_id)
        assert job.lease_expires_at <= db.scalar(select(func.clock_timestamp()))
        assert job.heartbeat_at == job_heartbeat_before
        assert db.get(WorkerHeartbeat, "main").last_seen_at == heartbeat_before


@pytest.mark.parametrize(
    "operation,target", [("publish", "video"), ("heartbeat", "heartbeat"), ("heartbeat", "job")]
)
def test_valid_lease_survives_controlled_lock_wait(lease_case, operation, target):
    case = lease_case
    with blocked_operation(case, operation, target) as (future, observer):
        released_after = observer.scalar(select(func.clock_timestamp()))
    assert future.result(timeout=2) is None
    with case.factory() as db:
        job, video = db.get(VideoJob, case.job_id), db.get(Video, case.video_id)
        if operation == "publish":
            assert job.status == "succeeded" and video.status == "ready"
            assert video.published_at >= released_after
            assert video.visibility == "private"
            assert set(
                db.scalars(select(VideoAsset.kind).where(VideoAsset.video_id == case.video_id))
            ) == {"playback", "thumbnail"}
        else:
            assert job.status == "running" and video.status == "processing"
            assert job.heartbeat_at >= released_after
            assert job.lease_expires_at - job.heartbeat_at == timedelta(seconds=60)
            assert db.get(WorkerHeartbeat, "main").last_seen_at == job.heartbeat_at


def test_valid_heartbeat_then_atomic_publication(lease_case):
    case = lease_case
    jobs = Jobs(case.factory)
    jobs.heartbeat(case.job_id, case.token)
    jobs.publish(case.claim, case.info, case.assets)
    with case.factory() as db:
        assert db.get(VideoJob, case.job_id).status == "succeeded"
        assert db.get(Video, case.video_id).status == "ready"
        assert (
            db.scalar(
                select(func.count())
                .select_from(VideoAsset)
                .where(VideoAsset.video_id == case.video_id)
            )
            == 2
        )


@pytest.mark.parametrize("operation", ["publish", "heartbeat"])
def test_previous_token_rejected_during_fresh_lease(lease_case, operation):
    case = lease_case
    new_token = uuid4()
    with case.factory() as db:
        job = db.get(VideoJob, case.job_id)
        job.lease_token = new_token
        job.attempts = 2
        job.lease_expires_at = db.scalar(select(func.clock_timestamp())) + timedelta(minutes=1)
        db.commit()
    with pytest.raises(LostLease):
        invoke(Jobs(case.factory), case, operation)
    with case.factory() as db:
        job = db.get(VideoJob, case.job_id)
        assert job.status == "running" and job.lease_token == new_token
        assert job.lease_expires_at > db.scalar(select(func.clock_timestamp()))
        assert db.get(Video, case.video_id).status == "processing"
        assert db.scalar(select(VideoAsset.id).where(VideoAsset.video_id == case.video_id)) is None


def test_publication_rolls_back_all_changes_on_asset_error(lease_case):
    from sqlalchemy.exc import IntegrityError

    case = lease_case
    duplicate = [case.assets[0], dict(case.assets[1], kind="playback")]
    with pytest.raises(IntegrityError):
        Jobs(case.factory).publish(case.claim, case.info, duplicate)
    assert_not_published(case)


def test_process_crash_keeps_job_and_new_worker_recovers(lease_case, tmp_path):
    import json
    from uuid import UUID

    from app.infrastructure.storage import Storage
    from app.infrastructure.transcoder import checksum
    from app.worker.main import Worker

    case = lease_case
    settings = get_settings().model_copy(update={"scratch_dir": str(tmp_path / "scratch")})
    storage = Storage(settings)
    try:
        source = tmp_path / "synthetic.mp4"
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
                "libx264",
                "-threads",
                "2",
                str(source),
            ],
            check=True,
            timeout=15,
            env={"PATH": "/usr/bin:/bin"},
        )
        key = f"sources/{case.video_id}/{uuid4()}/original"
        storage.upload(source, key, "application/octet-stream")
        with case.factory() as db:
            job = db.get(VideoJob, case.job_id)
            job.status, job.attempts, job.lease_token, job.lease_expires_at = (
                "queued",
                0,
                None,
                None,
            )
            db.get(Video, case.video_id).status = "queued"
            db.add(
                VideoAsset(
                    video_id=case.video_id,
                    kind="source",
                    object_key=key,
                    content_type="application/octet-stream",
                    size_bytes=source.stat().st_size,
                    sha256=checksum(source),
                )
            )
            db.commit()
        # Crash a separate process immediately after its committed claim. It never
        # runs finally/cleanup, and the next worker has no shared Python state.
        program = """import json,os,sys
from app.core.config import get_settings
from app.db.session import Database
from app.worker.jobs import Jobs
claim=Jobs(Database(get_settings()).session_factory).claim()
if claim is None or str(claim["id"]) != sys.argv[1]:
    os._exit(2)
print(json.dumps({"token":str(claim["token"])}),flush=True)
os._exit(23)
"""
        crashed = subprocess.run(
            [sys.executable, "-c", program, str(case.job_id)],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        assert crashed.returncode == 23
        previous = {"id": case.job_id, "token": UUID(json.loads(crashed.stdout)["token"])}
        with case.factory() as db:
            job = db.get(VideoJob, case.job_id)
            assert job.status == "running" and job.attempts == 1
            # Fast deterministic expiry; wall-clock waiting is covered by the lock tests.
            job.lease_expires_at = db.scalar(select(func.clock_timestamp())) - timedelta(seconds=1)
            db.commit()
        assert Worker(settings, case.factory, storage).run_once()
        with case.factory() as db:
            job, video = db.get(VideoJob, case.job_id), db.get(Video, case.video_id)
            assert job.status == "succeeded" and job.attempts == 2 and video.status == "ready"
            assets = db.scalars(
                select(VideoAsset).where(VideoAsset.video_id == case.video_id)
            ).all()
            assert {asset.kind for asset in assets} == {"source", "playback", "thumbnail"}
            assert all(
                storage.head(asset.object_key)["ContentLength"] == asset.size_bytes
                for asset in assets
            )
        with pytest.raises(LostLease):
            Jobs(case.factory).publish(previous, case.info, case.assets)
    finally:
        storage.close()
