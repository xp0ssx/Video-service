import threading
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert

from app.infrastructure.transcoder import LostLease
from app.modules.auth import models as auth_models  # noqa: F401
from app.modules.videos.models import Video, VideoAsset, VideoJob, WorkerHeartbeat


class Jobs:
    def __init__(self, factory):
        self.factory = factory

    def heartbeat(self, job_id=None, token=None):
        with self.factory() as db:
            # Preserve the lock order: worker heartbeat -> job. Acquire both before
            # sampling the lease clock; an ON CONFLICT wait can outlive the lease.
            db.execute(
                insert(WorkerHeartbeat)
                .values(worker_id="main", last_seen_at=func.clock_timestamp())
                .on_conflict_do_nothing()
            )
            heartbeat = db.scalar(
                select(WorkerHeartbeat).where(WorkerHeartbeat.worker_id == "main").with_for_update()
            )
            if job_id:
                job = self._lock_job(db, job_id)
                now = self._lease_time(db, job, token)
                job.heartbeat_at = now
                job.lease_expires_at = now + timedelta(seconds=60)
            else:
                now = db.scalar(select(func.clock_timestamp()))
            heartbeat.last_seen_at = now
            db.commit()

    def recover(self):
        now = datetime.now(UTC)
        with self.factory() as db:
            for job in db.scalars(
                select(VideoJob)
                .where(VideoJob.status == "running", VideoJob.lease_expires_at <= now)
                .with_for_update(skip_locked=True)
            ):
                video = db.scalar(select(Video).where(Video.id == job.video_id).with_for_update())
                job.status = "queued" if job.attempts < 3 else "failed"
                job.last_error_code = "lease_expired"
                job.lease_token, job.lease_expires_at = None, None
                job.run_after = now
                video.status = "queued" if job.status == "queued" else "failed"
                video.error_stage, video.error_code = "processing", "lease_expired"
            for video in db.scalars(
                select(Video)
                .where(Video.status == "uploading", Video.upload_expires_at <= now)
                .with_for_update(skip_locked=True)
            ):
                video.status, video.error_stage, video.error_code = (
                    "failed",
                    "upload",
                    "upload_expired",
                )
                video.upload_token, video.upload_expires_at = None, None
            db.commit()

    def claim(self):
        now = datetime.now(UTC)
        with self.factory() as db:
            job = db.scalar(
                select(VideoJob)
                .where(
                    VideoJob.status == "queued", VideoJob.run_after <= now, VideoJob.attempts < 3
                )
                .order_by(VideoJob.created_at)
                .with_for_update(skip_locked=True)
                .limit(1)
            )
            if job is None:
                return None
            video = db.scalar(select(Video).where(Video.id == job.video_id).with_for_update())
            source = db.scalar(
                select(VideoAsset).where(
                    VideoAsset.video_id == video.id, VideoAsset.kind == "source"
                )
            )
            if video.status != "queued" or source is None:
                job.status = "failed"
                video.status, video.error_stage, video.error_code = (
                    "failed",
                    "processing",
                    "missing_source",
                )
                db.commit()
                return None
            job.attempts += 1
            job.status, job.lease_token, job.lease_expires_at, job.heartbeat_at = (
                "running",
                uuid4(),
                now + timedelta(seconds=60),
                now,
            )
            video.status, video.error_stage, video.error_code = "processing", None, None
            result = {
                "id": job.id,
                "video_id": video.id,
                "token": job.lease_token,
                "key": source.object_key,
                "size": source.size_bytes,
                "sha256": source.sha256,
            }
            db.commit()
            return result

    def _lock_job(self, db, job_id):
        return db.scalar(
            select(VideoJob)
            .where(VideoJob.id == job_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )

    def _lease_time(self, db, job, token):
        # A separate query AFTER the row-lock waits. now()/CURRENT_TIMESTAMP and
        # statement_timestamp() can refer to a time before those waits finished.
        now = db.scalar(select(func.clock_timestamp()))
        if (
            job is None
            or job.status != "running"
            or job.lease_token != token
            or job.lease_expires_at is None
            or job.lease_expires_at <= now
        ):
            raise LostLease
        return now

    def locked(self, db, claim):
        job = self._lock_job(db, claim["id"])
        self._lease_time(db, job, claim["token"])
        return job

    def publish(self, claim, info, assets):
        with self.factory() as db:
            job = self.locked(db, claim)
            video = db.scalar(select(Video).where(Video.id == job.video_id).with_for_update())
            # Job -> Video remains the publication/recovery lock order. Revalidate
            # after the final wait; holding the job lock alone does not stop time.
            now = self._lease_time(db, job, claim["token"])
            for asset in assets:
                db.add(VideoAsset(video_id=video.id, **asset))
            for key in ("duration_ms", "width", "height"):
                setattr(video, key, info[key])
            video.status, video.error_stage, video.error_code = "ready", None, None
            if video.published_at is None:
                video.published_at = now
            job.status, job.lease_token, job.lease_expires_at = "succeeded", None, None
            db.commit()

    def fail(self, claim, code, terminal=False):
        with self.factory() as db:
            job = self.locked(db, claim)
            video = db.scalar(select(Video).where(Video.id == job.video_id).with_for_update())
            now = self._lease_time(db, job, claim["token"])
            retry = not terminal and job.attempts < 3
            job.status = "queued" if retry else "failed"
            job.last_error_code = code
            job.run_after = now + timedelta(seconds=10 if job.attempts == 1 else 60)
            job.lease_token, job.lease_expires_at = None, None
            video.status = "queued" if retry else "failed"
            video.error_stage, video.error_code = "processing", code
            db.commit()


class Lease:
    def __init__(self, jobs, claim, cancel):
        self.jobs, self.claim, self.cancel = jobs, claim, cancel
        self.done = threading.Event()

    def __enter__(self):
        self.thread = threading.Thread(target=self.pump, daemon=True)
        self.thread.start()
        return self

    def pump(self):
        while not self.done.wait(15):
            try:
                self.jobs.heartbeat(self.claim["id"], self.claim["token"])
            except Exception:
                self.cancel.set()
                return

    def __exit__(self, *args):
        self.done.set()
        self.thread.join(timeout=5)
