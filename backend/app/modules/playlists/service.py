from datetime import UTC, datetime

from fastapi import HTTPException
from sqlalchemy import delete, func, select

from app.core.pagination import decode_cursor, encode_cursor
from app.modules.access.policy import watch_listing
from app.modules.auth.models import Channel, User
from app.modules.channels.repository import ChannelRepository
from app.modules.channels.service import playlist_view
from app.modules.playlists.models import Playlist, PlaylistItem
from app.modules.videos.models import Video


def validate_order(current, requested):
    if len(requested) != len(set(requested)) or set(current) != set(requested):
        raise HTTPException(422, "Передайте полный набор видео без повторов")


def video_view(video):
    return {
        "id": video.id,
        "title": video.title,
        "duration_ms": video.duration_ms,
        "thumbnail_url": f"/api/videos/{video.id}/thumbnail",
        "watch_url": f"/watch/{video.id}",
    }


class PlaylistService:
    def __init__(self, factory):
        self.factory = factory

    def create(self, user_id, data):
        with self.factory() as db:
            channel = ChannelRepository(db).own(user_id)
            row = Playlist(channel_id=channel.id, **data.model_dump())
            db.add(row)
            db.flush()
            identifier = row.id
            db.commit()
        return self.get(identifier, user_id)

    def get(self, identifier, user_id, cursor=None, limit=20, studio=False):
        with self.factory() as db:
            repo = ChannelRepository(db)
            row = repo.playlist(identifier, user_id, studio)
            channel = db.get(Channel, row.channel_id)
            username = db.scalar(select(User.username).where(User.id == channel.user_id))
            scope = f"playlist:{identifier}"
            after = decode_cursor(cursor, scope)
            query = (
                select(Video, PlaylistItem.position)
                .join(PlaylistItem, PlaylistItem.video_id == Video.id)
                .where(PlaylistItem.playlist_id == identifier)
            )
            if not studio:
                query = query.where(watch_listing(user_id))
            if after:
                anchor = (
                    select(PlaylistItem.position)
                    .join(Video, Video.id == PlaylistItem.video_id)
                    .where(
                        PlaylistItem.playlist_id == identifier, PlaylistItem.video_id == after[1]
                    )
                )
                if not studio:
                    anchor = anchor.where(watch_listing(user_id))
                position = db.scalar(anchor)
                if position is None:
                    raise HTTPException(422, "Список изменился; загрузите его заново")
                query = query.where(PlaylistItem.position > position)
            rows = db.execute(query.order_by(PlaylistItem.position).limit(limit + 1)).all()
            selected = rows[:limit]
            return {
                **playlist_view(row, username),
                "owned_by_me": channel.user_id == user_id,
                "items": [
                    {
                        **video_view(video),
                        **(
                            {
                                "status": video.status,
                                "visibility": video.visibility,
                                "moderation_state": video.moderation_state,
                            }
                            if studio
                            else {}
                        ),
                    }
                    for video, _ in selected
                ],
                "next_cursor": encode_cursor(
                    datetime(1970, 1, 1, tzinfo=UTC), selected[-1][0].id, scope
                )
                if len(rows) > limit
                else None,
            }

    def mutate(self, identifier, user_id, action, data=None, video_id=None):
        with self.factory() as db:
            row = ChannelRepository(db).playlist(identifier, user_id, True)
            if action == "patch":
                for key, value in data.model_dump(exclude_unset=True).items():
                    setattr(row, key, value)
            elif action == "delete":
                db.delete(row)
            elif action == "order":
                items = db.scalars(
                    select(PlaylistItem).where(PlaylistItem.playlist_id == identifier)
                ).all()
                validate_order([item.video_id for item in items], data.video_ids)
                order = {value: index for index, value in enumerate(data.video_ids)}
                for item in items:
                    item.position = order[item.video_id]
            elif action == "add":
                video = db.get(Video, video_id)
                if video is None or video.channel_id != row.channel_id:
                    raise HTTPException(404, "Собственное видео не найдено")
                if db.get(PlaylistItem, (identifier, video_id)) is None:
                    position = db.scalar(
                        select(func.max(PlaylistItem.position)).where(
                            PlaylistItem.playlist_id == identifier
                        )
                    )
                    db.add(
                        PlaylistItem(
                            playlist_id=identifier,
                            video_id=video_id,
                            position=0 if position is None else position + 1,
                        )
                    )
            elif action == "remove":
                db.execute(
                    delete(PlaylistItem).where(
                        PlaylistItem.playlist_id == identifier, PlaylistItem.video_id == video_id
                    )
                )
            db.commit()
