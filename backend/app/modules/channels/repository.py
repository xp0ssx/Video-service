from fastapi import HTTPException
from sqlalchemy import select

from app.modules.auth.models import Channel, User
from app.modules.auth.schemas import normalize
from app.modules.playlists.models import Playlist


class ChannelRepository:
    def __init__(self, db):
        self.db = db

    def named(self, username):
        row = self.db.execute(
            select(Channel, User.username)
            .join(User, User.id == Channel.user_id)
            .where(User.username == normalize(username))
        ).first()
        if row is None:
            raise HTTPException(404, "Канал не найден")
        return row

    def own(self, user_id):
        return self.db.scalar(select(Channel).where(Channel.user_id == user_id))

    def playlist(self, identifier, user_id=None, lock=False):
        query = select(Playlist).where(Playlist.id == identifier)
        if lock:
            query = query.with_for_update()
        row = self.db.scalar(query)
        if row is None or (lock and self.db.get(Channel, row.channel_id).user_id != user_id):
            raise HTTPException(404, "Плейлист не найден")
        return row
