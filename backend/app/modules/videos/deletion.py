"""Owner deletion is a DB transaction; external cleanup is a durable worker job."""

import hashlib
from contextlib import contextmanager

from sqlalchemy import delete, func, select, update

from app.infrastructure.transcoder import LostLease
from app.modules.admin.models import ModerationAction
from app.modules.interactions.models import Comment, Like
from app.modules.moderation.models import Report
from app.modules.playlists.models import PlaylistItem
from app.modules.videos.models import (
    Video,
    VideoAccess,
    VideoDeletion,
    VideoViewReceipt,
    VideoViewSession,
)
from app.modules.videos.repository import VideoRepository

DELETED_TEXT = "Содержимое удалено владельцем"


def storage_lock_id(video_id):
    # Stable signed bigint. A collision only serializes unrelated writes/cleanup.
    return int.from_bytes(
        hashlib.blake2b(f"video-storage:{video_id}".encode(), digest_size=8).digest(),
        "big",
        signed=True,
    )


@contextmanager
def storage_writer(factory, video_id):
    """Fence uploads against cleanup without delaying the tombstone commit."""
    with factory() as db:
        db.execute(select(func.pg_advisory_xact_lock(storage_lock_id(video_id))))
        video = db.get(Video, video_id)
        if video is None or video.deleted_at is not None:
            raise LostLease
        yield
        db.commit()


def delete_owned(factory, user_id, video_id):
    with factory() as db:
        video = VideoRepository(db).owned(video_id, user_id, True, include_deleted=True)
        if video.deleted_at is not None:
            return
        video.deleted_at = db.scalar(select(func.clock_timestamp()))
        video.title, video.description = DELETED_TEXT, ""
        video.duration_ms, video.width, video.height = None, None, None
        video.upload_token, video.upload_expires_at = None, None
        video.error_stage, video.error_code = None, None
        # All interacting writers hold the video lock before adding these rows.
        # CommentLike and replies have real ON DELETE CASCADE constraints.
        for model in (VideoAccess, Like, Comment, PlaylistItem, VideoViewSession, VideoViewReceipt):
            db.execute(delete(model).where(model.video_id == video_id))
        # Keep immutable action identities, actors, transitions and resolution FKs.
        # Free text can quote sensitive deleted content, so remove it as well.
        db.execute(update(Report).where(Report.video_id == video_id).values(body=DELETED_TEXT))
        db.execute(
            update(Report)
            .where(Report.video_id == video_id, Report.review_note.is_not(None))
            .values(review_note=DELETED_TEXT)
        )
        db.execute(
            update(ModerationAction)
            .where(ModerationAction.video_id == video_id)
            .values(reason=DELETED_TEXT)
        )
        db.add(VideoDeletion(video_id=video_id))
        db.commit()
