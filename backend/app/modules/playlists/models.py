import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Subscription(Base):
    __tablename__ = "subscriptions"
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), primary_key=True)
    channel_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("channels.id"), primary_key=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Playlist(Base):
    __tablename__ = "playlists"
    __table_args__ = (Index("ix_playlists_channel_created", "channel_id", "created_at", "id"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    channel_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("channels.id"))
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="", server_default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class PlaylistItem(Base):
    __tablename__ = "playlist_items"
    __table_args__ = (
        UniqueConstraint("playlist_id", "position", deferrable=True, initially="DEFERRED"),
        CheckConstraint("position >= 0", name="position"),
    )
    playlist_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("playlists.id", ondelete="CASCADE"), primary_key=True
    )
    video_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("videos.id"), primary_key=True, index=True
    )
    position: Mapped[int]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
