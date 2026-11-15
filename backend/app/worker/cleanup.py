import re
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

from sqlalchemy import select, text

from app.modules.videos.models import Video, VideoAsset, VideoJob

KEY = re.compile(
    r"^(?:sources|processed)/([0-9a-f-]{36})/([0-9a-f-]{36})/(?:original|playback\.mp4|thumbnail\.jpg)$"
)


class Cleanup:
    def __init__(self, factory, settings, storage):
        self.factory, self.settings, self.storage = factory, settings, storage

    def unreferenced(self, key):
        match = KEY.fullmatch(key)
        if not match:
            return False
        try:
            UUID(match[1])
            token = UUID(match[2])
        except ValueError:
            return False
        now = datetime.now(UTC)
        with self.factory() as db:
            if db.scalar(select(VideoAsset.id).where(VideoAsset.object_key == key)):
                return False
            if db.scalar(
                select(Video.id).where(Video.upload_token == token, Video.upload_expires_at > now)
            ):
                return False
            return (
                db.scalar(
                    select(VideoJob.id).where(
                        VideoJob.lease_token == token, VideoJob.lease_expires_at > now
                    )
                )
                is None
            )

    def scratch(self):
        cutoff = (datetime.now(UTC) - timedelta(hours=24)).timestamp()
        root = Path(self.settings.scratch_dir)
        if not root.exists():
            return
        with self.factory() as db:
            db.execute(text("SELECT 1"))
            active = {
                str(value)
                for value in db.scalars(
                    select(Video.upload_token).where(Video.upload_expires_at > datetime.now(UTC))
                )
            }
            active |= {
                str(value)
                for value in db.scalars(
                    select(VideoJob.lease_token).where(
                        VideoJob.lease_expires_at > datetime.now(UTC)
                    )
                )
            }
        for path in root.iterdir():
            if (
                path.is_symlink()
                or not path.is_dir()
                or not path.name.startswith(("upload-", "job-"))
            ):
                continue
            if path.stat().st_mtime < cutoff and not any(token in path.name for token in active):
                shutil.rmtree(path)

    def run(self):
        """S3 cleanup requires the single-worker deployment invariant.

        Only worker.main calls this, synchronously after run_once completes.
        API calls scratch() only, on its separate volume. Do not run this from
        another process/thread or scale workers without coordinating cleanup
        and publication; the reference check plus S3 delete is not atomic.
        """
        self.scratch()
        cutoff = datetime.now(UTC) - timedelta(hours=24)
        # A database outage aborts cleanup; uncertainty never authorizes deletion.
        with self.factory() as db:
            db.execute(text("SELECT 1"))
        client, bucket = self.storage.client, self.storage.bucket
        for prefix in ("sources/", "processed/"):
            for page in client.get_paginator("list_objects_v2").paginate(
                Bucket=bucket, Prefix=prefix
            ):
                for obj in page.get("Contents", []):
                    if obj["LastModified"] < cutoff and self.unreferenced(obj["Key"]):
                        client.delete_object(Bucket=bucket, Key=obj["Key"])
        for page in client.get_paginator("list_multipart_uploads").paginate(Bucket=bucket):
            for upload in page.get("Uploads", []):
                if upload["Initiated"] < cutoff and self.unreferenced(upload["Key"]):
                    client.abort_multipart_upload(
                        Bucket=bucket, Key=upload["Key"], UploadId=upload["UploadId"]
                    )
