from fastapi import HTTPException
from sqlalchemy import select

from app.modules.admin.models import ModerationAction
from app.modules.auth.models import User
from app.modules.moderation.models import Report
from app.modules.videos.models import Video


class ModerationRepository:
    def __init__(self, db):
        self.db = db

    def actor(self, user_id):
        actor = self.db.scalar(select(User).where(User.id == user_id).with_for_update(read=True))
        if actor is None or actor.role not in ("moderator", "admin"):
            raise HTTPException(403, "Недостаточно прав")
        return actor

    def video(self, identifier):
        video = self.db.scalar(
            select(Video)
            .where(Video.id == identifier, Video.deleted_at.is_(None))
            .with_for_update()
        )
        if video is None:
            raise HTTPException(404, "Видео не найдено")
        return video

    def report(self, identifier, lock=False):
        query = select(Report).where(Report.id == identifier)
        if lock:
            query = query.with_for_update().execution_options(populate_existing=True)
        row = self.db.scalar(query)
        if row is None:
            raise HTTPException(404, "Жалоба не найдена")
        return row

    def audit(self, actor, video, state, reason):
        previous = video.moderation_state
        if previous == state:
            return None
        video.moderation_state = state
        action = ModerationAction(
            actor_id=actor,
            video_id=video.id,
            action="hide" if state == "hidden" else "restore",
            previous_value=previous,
            new_value=state,
            reason=reason,
        )
        self.db.add(action)
        self.db.flush()
        return action
