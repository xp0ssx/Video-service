from fastapi import HTTPException
from sqlalchemy import select

from app.modules.auth.models import Channel
from app.modules.videos.models import Video


class VideoRepository:
    def __init__(self, db):
        self.db = db

    def owned(self, video_id, user_id, lock=False, include_deleted=False):
        query = select(Video).join(Channel).where(Video.id == video_id, Channel.user_id == user_id)
        if not include_deleted:
            query = query.where(Video.deleted_at.is_(None))
        if lock:
            query = query.with_for_update(of=Video)
        video = self.db.scalar(query)
        if video is None:
            raise HTTPException(404, "Видео не найдено")
        return video

    def channel(self, user_id):
        return self.db.scalar(select(Channel).where(Channel.user_id == user_id))
