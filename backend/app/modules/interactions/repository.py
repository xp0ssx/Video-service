from sqlalchemy import delete, func, select, tuple_
from sqlalchemy.dialects.postgresql import insert

from app.modules.auth.models import User
from app.modules.interactions.models import Comment, Like


class InteractionRepository:
    def __init__(self, db):
        self.db = db

    def like_state(self, video_id, user_id):
        count, mine = self.db.execute(
            select(func.count(), func.count().filter(Like.user_id == user_id)).where(
                Like.video_id == video_id
            )
        ).one()
        return {"likes_count": count, "liked_by_me": bool(mine)}

    def set_like(self, video_id, user_id, enabled):
        if enabled:
            self.db.execute(
                insert(Like).values(video_id=video_id, user_id=user_id).on_conflict_do_nothing()
            )
        else:
            self.db.execute(delete(Like).where(Like.video_id == video_id, Like.user_id == user_id))

    def comments(self, video_id, after, limit):
        query = (
            select(Comment, User.username)
            .join(User, User.id == Comment.user_id)
            .where(Comment.video_id == video_id)
        )
        if after:
            query = query.where(tuple_(Comment.created_at, Comment.id) > after)
        return self.db.execute(
            query.order_by(Comment.created_at, Comment.id).limit(limit + 1)
        ).all()

    def add_comment(self, video_id, user_id, body):
        comment = Comment(video_id=video_id, user_id=user_id, body=body)
        self.db.add(comment)
        self.db.flush()
        username = self.db.scalar(select(User.username).where(User.id == user_id))
        return comment, username
