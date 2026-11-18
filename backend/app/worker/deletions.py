"""Retryable, narrowly scoped S3 cleanup in the existing worker process."""

import logging
from datetime import timedelta

from sqlalchemy import delete, func, select

from app.modules.videos.deletion import storage_lock_id
from app.modules.videos.models import Video, VideoAsset, VideoDeletion, VideoJob
from app.worker.cleanup import KEY

logger = logging.getLogger(__name__)


class Deletions:
    def __init__(self, factory, storage):
        self.factory, self.storage = factory, storage

    def objects(self, video_id, operation):
        client, bucket = self.storage.client, self.storage.bucket
        for prefix in (f"sources/{video_id}/", f"processed/{video_id}/"):
            for page in client.get_paginator(operation).paginate(Bucket=bucket, Prefix=prefix):
                for item in page.get(
                    "Contents" if operation == "list_objects_v2" else "Uploads", []
                ):
                    match = KEY.fullmatch(item["Key"])
                    if not match or match[1] != str(video_id):
                        raise ValueError("Unexpected object scope")
                    yield item

    def clean(self, db, video_id):
        # Never follow an arbitrary VideoAsset key into another video's prefix.
        for asset in db.scalars(select(VideoAsset).where(VideoAsset.video_id == video_id)):
            match = KEY.fullmatch(asset.object_key)
            if not match or match[1] != str(video_id):
                raise ValueError("Unexpected asset scope")
        for prefix in (f"sources/{video_id}/", f"processed/{video_id}/"):
            if db.scalar(
                select(VideoAsset.id).where(
                    VideoAsset.video_id != video_id, VideoAsset.object_key.startswith(prefix)
                )
            ):
                raise ValueError("Object referenced by another video")
        client, bucket = self.storage.client, self.storage.bucket
        for upload in self.objects(video_id, "list_multipart_uploads"):
            client.abort_multipart_upload(
                Bucket=bucket, Key=upload["Key"], UploadId=upload["UploadId"]
            )
        for obj in self.objects(video_id, "list_objects_v2"):
            client.delete_object(Bucket=bucket, Key=obj["Key"])
        # Do not announce completion if the store still lists objects/parts.
        if next(self.objects(video_id, "list_objects_v2"), None) or next(
            self.objects(video_id, "list_multipart_uploads"), None
        ):
            raise OSError("Cleanup verification failed")
        # Preserve the worker's Job -> Video order. Tombstones are never purged.
        db.scalar(select(VideoJob).where(VideoJob.video_id == video_id).with_for_update())
        video = db.scalar(select(Video).where(Video.id == video_id).with_for_update())
        if video is None or video.deleted_at is None:
            raise ValueError("Tombstone missing")
        db.execute(delete(VideoAsset).where(VideoAsset.video_id == video_id))
        db.execute(delete(VideoJob).where(VideoJob.video_id == video_id))

    def run(self, limit=10):
        with self.factory() as db:
            identifiers = list(
                db.scalars(
                    select(VideoDeletion.video_id)
                    .where(VideoDeletion.run_after <= func.clock_timestamp())
                    .order_by(VideoDeletion.run_after)
                    .limit(limit)
                )
            )
        for video_id in identifiers:
            with self.factory() as db:
                row = db.scalar(
                    select(VideoDeletion)
                    .where(
                        VideoDeletion.video_id == video_id,
                        VideoDeletion.run_after <= func.clock_timestamp(),
                    )
                    .with_for_update(skip_locked=True)
                )
                if row is None:
                    continue
                now = db.scalar(select(func.clock_timestamp()))
                if not db.scalar(select(func.pg_try_advisory_xact_lock(storage_lock_id(video_id)))):
                    row.run_after = now + timedelta(seconds=5)
                    row.last_error_code = "writer_active"
                    db.commit()
                    continue
                # A queue row alone is never permission to delete external data.
                if db.scalar(select(Video.deleted_at).where(Video.id == video_id)) is None:
                    row.status, row.last_error_code = "retry", "tombstone_missing"
                    row.run_after = now + timedelta(hours=1)
                    db.commit()
                    logger.error(
                        "Video cleanup rejected video_id=%s code=tombstone_missing", video_id
                    )
                    continue
                row.attempts += 1
                try:
                    self.clean(db, video_id)
                except (OSError, ValueError) as error:
                    code = (
                        "object_scope_invalid"
                        if isinstance(error, ValueError)
                        else "storage_unavailable"
                    )
                    self.retry(row, now, code)
                except Exception:
                    # Do not log exceptions: S3/SQL errors may contain credentials.
                    self.retry(row, now, "storage_unavailable")
                else:
                    row.status, row.last_error_code = "complete", None
                    row.cleaned_at = now
                    # Retain reconciliation after completion: an upload could outlive
                    # a broken DB connection that previously held its advisory lock.
                    row.run_after = now + timedelta(hours=1)
                db.commit()

    def retry(self, row, now, code):
        row.status, row.last_error_code = "retry", code
        row.run_after = now + timedelta(seconds=min(3600, 10 * 2 ** min(row.attempts - 1, 9)))
        logger.warning(
            "Video cleanup pending video_id=%s code=%s attempt=%s", row.video_id, code, row.attempts
        )
