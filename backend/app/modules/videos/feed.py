import hashlib

from fastapi import HTTPException
from sqlalchemy import select, tuple_

from app.core.pagination import decode_cursor, encode_cursor
from app.modules.access.policy import public_listing
from app.modules.auth.models import Channel, User
from app.modules.videos.models import Video


def title_pattern(query):
    # Explicit LIKE escape character; backslash is consequently literal too.
    return "%" + query.replace("!", "!!").replace("%", "!%").replace("_", "!_") + "%"


class FeedRepository:
    def __init__(self, db):
        self.db = db

    def page(self, query, after, limit, channel_id=None):
        statement = (
            select(
                Video.id,
                Video.title,
                Video.duration_ms,
                Video.views_count,
                Video.published_at,
                Channel.id.label("channel_id"),
                Channel.title.label("channel_title"),
                User.username,
            )
            .select_from(Video)
            .join(Channel, Channel.id == Video.channel_id)
            .join(User, User.id == Channel.user_id)
            .where(
                public_listing(),
                Video.published_at.is_not(None),
            )
        )
        if channel_id is not None:
            statement = statement.where(Video.channel_id == channel_id)
        if query:
            statement = statement.where(Video.title.ilike(title_pattern(query), escape="!"))
        if after:
            statement = statement.where(tuple_(Video.published_at, Video.id) < after)
        return self.db.execute(
            statement.order_by(Video.published_at.desc(), Video.id.desc()).limit(limit + 1)
        ).all()


class FeedService:
    def __init__(self, factory):
        self.factory = factory

    def page(self, query, cursor, limit, channel_id=None):
        query = query.strip()
        if "\x00" in query:
            raise HTTPException(422, "Недопустимый поисковый запрос")
        scope = f"feed:{channel_id or 'all'}:" + hashlib.sha256(query.encode()).hexdigest()
        after = decode_cursor(cursor, scope)
        with self.factory() as db:
            rows = FeedRepository(db).page(query, after, limit, channel_id)
            page = rows[:limit]
            return {
                "items": [
                    {
                        "id": row.id,
                        "title": row.title,
                        "duration_ms": row.duration_ms,
                        "views_count": row.views_count,
                        "published_at": row.published_at,
                        "channel": {
                            "id": row.channel_id,
                            "title": row.channel_title,
                            "username": row.username,
                        },
                        "thumbnail_url": f"/api/videos/{row.id}/thumbnail",
                        "watch_url": f"/watch/{row.id}",
                    }
                    for row in page
                ],
                "next_cursor": encode_cursor(page[-1].published_at, page[-1].id, scope)
                if len(rows) > limit
                else None,
            }
