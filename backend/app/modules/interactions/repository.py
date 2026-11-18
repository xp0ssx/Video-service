from sqlalchemy import delete, func, select, tuple_
from sqlalchemy.dialects.postgresql import insert

from app.modules.auth.models import User
from app.modules.interactions.models import Comment, CommentLike, Like


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

    def comments(self, video_id, after, limit, parent_id=None):
        query = (
            select(Comment, User.username)
            .join(User, User.id == Comment.user_id)
            .where(Comment.video_id == video_id, Comment.parent_id == parent_id)
        )
        if after:
            query = query.where(tuple_(Comment.created_at, Comment.id) > after)
        return self.db.execute(
            query.order_by(Comment.created_at, Comment.id).limit(limit + 1)
        ).all()

    def add_comment(self, video_id, user_id, body, parent_id=None):
        comment = Comment(video_id=video_id, user_id=user_id, body=body, parent_id=parent_id)
        self.db.add(comment)
        self.db.flush()
        username = self.db.scalar(select(User.username).where(User.id == user_id))
        return comment, username

    def comment(self, video_id, comment_id):
        return self.db.scalar(
            select(Comment).where(Comment.id == comment_id, Comment.video_id == video_id)
        )

    def comment_states(self, video_id, identifiers, user_id):
        # Two bounded aggregate queries for the entire page, never one per comment.
        if not identifiers:
            return {}, {}
        likes = {
            identifier: {"likes_count": count, "liked_by_me": bool(mine)}
            for identifier, count, mine in self.db.execute(
                select(
                    CommentLike.comment_id,
                    func.count(),
                    func.count().filter(CommentLike.user_id == user_id),
                )
                .where(CommentLike.comment_id.in_(identifiers))
                .group_by(CommentLike.comment_id)
            )
        }
        replies = dict(
            self.db.execute(
                select(Comment.parent_id, func.count())
                .where(Comment.video_id == video_id, Comment.parent_id.in_(identifiers))
                .group_by(Comment.parent_id)
            ).all()
        )
        return likes, replies

    def comment_like_state(self, comment_id, user_id):
        count, mine = self.db.execute(
            select(func.count(), func.count().filter(CommentLike.user_id == user_id)).where(
                CommentLike.comment_id == comment_id
            )
        ).one()
        return {"likes_count": count, "liked_by_me": bool(mine)}

    def set_comment_like(self, comment_id, user_id, enabled):
        if enabled:
            self.db.execute(
                insert(CommentLike)
                .values(comment_id=comment_id, user_id=user_id)
                .on_conflict_do_nothing()
            )
        else:
            self.db.execute(
                delete(CommentLike).where(
                    CommentLike.comment_id == comment_id, CommentLike.user_id == user_id
                )
            )
