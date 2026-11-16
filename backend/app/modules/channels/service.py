from fastapi import HTTPException
from sqlalchemy import delete, func, select, tuple_
from sqlalchemy.dialects.postgresql import insert

from app.core.pagination import decode_cursor, encode_cursor
from app.modules.auth.models import Channel, User
from app.modules.channels.repository import ChannelRepository
from app.modules.playlists.models import Playlist, Subscription
from app.modules.videos.feed import FeedService


def channel_view(channel, username):
    return {
        "id": channel.id,
        "title": channel.title,
        "description": channel.description,
        "username": username,
    }


def playlist_view(playlist, username):
    return {
        "id": playlist.id,
        "title": playlist.title,
        "description": playlist.description,
        "username": username,
    }


def page(rows, limit, scope, render):
    selected = rows[:limit]
    return {
        "items": [render(row) for row in selected],
        "next_cursor": encode_cursor(selected[-1].created_at, selected[-1].id, scope)
        if len(rows) > limit
        else None,
    }


class ChannelService:
    def __init__(self, factory):
        self.factory = factory

    def get(self, username, user_id):
        with self.factory() as db:
            channel, name = ChannelRepository(db).named(username)
            result = channel_view(channel, name)
            count, mine = db.execute(
                select(func.count(), func.count().filter(Subscription.user_id == user_id)).where(
                    Subscription.channel_id == channel.id
                )
            ).one()
            return {
                **result,
                "subscribers_count": count,
                "subscribed_by_me": bool(mine),
                "owned_by_me": channel.user_id == user_id,
            }

    def patch(self, user_id, data):
        with self.factory() as db:
            channel = ChannelRepository(db).own(user_id)
            for key, value in data.model_dump(exclude_unset=True).items():
                setattr(channel, key, value)
            db.commit()
        with self.factory() as db:
            name = db.scalar(select(User.username).where(User.id == user_id))
        return self.get(name, user_id)

    def subscribe(self, username, user_id, enabled):
        with self.factory() as db:
            channel, _ = ChannelRepository(db).named(username)
            if channel.user_id == user_id:
                raise HTTPException(422, "Нельзя подписаться на свой канал")
            # Serialize mutations of the same channel before updating/counting.
            db.scalar(select(Channel).where(Channel.id == channel.id).with_for_update())
            if enabled:
                db.execute(
                    insert(Subscription)
                    .values(user_id=user_id, channel_id=channel.id)
                    .on_conflict_do_nothing()
                )
            else:
                db.execute(
                    delete(Subscription).where(
                        Subscription.user_id == user_id, Subscription.channel_id == channel.id
                    )
                )
            db.commit()
        return self.get(username, user_id)

    def listing(self, username, kind, cursor, limit):
        with self.factory() as db:
            channel, name = ChannelRepository(db).named(username)
            if kind == "videos":
                return FeedService(self.factory).page("", cursor, limit, channel.id)
            scope = f"playlists:{channel.id}"
            after = decode_cursor(cursor, scope)
            query = select(Playlist).where(Playlist.channel_id == channel.id)
            if after:
                query = query.where(tuple_(Playlist.created_at, Playlist.id) < after)
            rows = db.scalars(
                query.order_by(Playlist.created_at.desc(), Playlist.id.desc()).limit(limit + 1)
            ).all()
            return page(rows, limit, scope, lambda row: playlist_view(row, name))

    def subscriptions(self, user_id, cursor, limit):
        scope = f"subscriptions:{user_id}"
        after = decode_cursor(cursor, scope)
        with self.factory() as db:
            query = (
                select(Channel, User.username)
                .join(Subscription, Subscription.channel_id == Channel.id)
                .join(User, User.id == Channel.user_id)
                .where(Subscription.user_id == user_id)
            )
            if after:
                query = query.where(tuple_(Channel.created_at, Channel.id) < after)
            rows = db.execute(
                query.order_by(Channel.created_at.desc(), Channel.id.desc()).limit(limit + 1)
            ).all()
            selected = rows[:limit]
            return {
                "items": [channel_view(channel, name) for channel, name in selected],
                "next_cursor": encode_cursor(selected[-1][0].created_at, selected[-1][0].id, scope)
                if len(rows) > limit
                else None,
            }
