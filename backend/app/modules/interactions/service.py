from fastapi import HTTPException
from sqlalchemy import select

from app.core.pagination import decode_cursor, encode_cursor
from app.modules.access.policy import can_watch
from app.modules.auth.models import User
from app.modules.interactions.repository import InteractionRepository
from app.modules.videos.models import Video


def comment_view(comment, username, likes=None, replies_count=0):
    return {
        "id": comment.id,
        "parent_id": comment.parent_id,
        "replies_count": replies_count,
        **(likes or {"likes_count": 0, "liked_by_me": False}),
        "body": comment.body,
        "created_at": comment.created_at,
        "author": {"id": comment.user_id, "username": username},
    }


class InteractionService:
    def __init__(self, factory):
        self.factory = factory

    def available(self, db, video_id, user_id, write=False):
        # Upload reservation locks User before Video. Take a compatible FK lock
        # first, avoiding Video -> User FK waits against that existing lock order.
        if write:
            db.scalar(
                select(User.id).where(User.id == user_id).with_for_update(read=True, key_share=True)
            )
        # Match the Video lock used by metadata/grant changes. Reads use SHARE;
        # mutations use UPDATE, then recheck can_watch against current grants.
        video = db.scalar(select(Video).where(Video.id == video_id).with_for_update(read=not write))
        if video is None or not can_watch(db, user_id, video):
            raise HTTPException(404, "Видео не найдено")
        return video

    def like(self, video_id, user_id, enabled):
        with self.factory() as db:
            self.available(db, video_id, user_id, True)
            repo = InteractionRepository(db)
            repo.set_like(video_id, user_id, enabled)
            result = repo.like_state(video_id, user_id)
            db.commit()
            return result

    def require_comment(self, repo, video_id, comment_id):
        comment = repo.comment(video_id, comment_id)
        if comment is None:
            raise HTTPException(404, "Комментарий не найден")
        return comment

    def comment_like(self, video_id, comment_id, user_id, enabled):
        with self.factory() as db:
            self.available(db, video_id, user_id, True)
            repo = InteractionRepository(db)
            self.require_comment(repo, video_id, comment_id)
            repo.set_comment_like(comment_id, user_id, enabled)
            result = repo.comment_like_state(comment_id, user_id)
            db.commit()
            return result

    def comments(self, video_id, user_id, cursor, limit, parent_id=None):
        with self.factory() as db:
            self.available(db, video_id, user_id)
            repo = InteractionRepository(db)
            if parent_id is not None:
                parent = self.require_comment(repo, video_id, parent_id)
                if parent.parent_id is not None:
                    raise HTTPException(404, "Ветка не найдена")
            scope = f"comments:{video_id}"
            if parent_id is not None:
                scope += f":replies:{parent_id}"
            after = decode_cursor(cursor, scope)
            rows = repo.comments(video_id, after, limit, parent_id)
            page = rows[:limit]
            likes, replies = repo.comment_states(
                video_id, [comment.id for comment, _ in page], user_id
            )
            return {
                "items": [
                    comment_view(
                        comment, username, likes.get(comment.id), replies.get(comment.id, 0)
                    )
                    for comment, username in page
                ],
                "next_cursor": encode_cursor(page[-1][0].created_at, page[-1][0].id, scope)
                if len(rows) > limit
                else None,
            }

    def add_comment(self, video_id, user_id, data):
        with self.factory() as db:
            self.available(db, video_id, user_id, True)
            repo = InteractionRepository(db)
            parent_id = None
            if data.parent_id is not None:
                parent = self.require_comment(repo, video_id, data.parent_id)
                parent_id = parent.parent_id or parent.id
            comment, username = repo.add_comment(video_id, user_id, data.body, parent_id)
            result = comment_view(comment, username)
            db.commit()
            return result
