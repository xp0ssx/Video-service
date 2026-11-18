import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Video(Base):
    __tablename__ = "videos"
    __table_args__ = (
        CheckConstraint("visibility IN ('public','private')", name="visibility"),
        CheckConstraint(
            "status IN ('awaiting_upload','uploading','queued','processing','ready','failed')",
            name="status",
        ),
        CheckConstraint("moderation_state IN ('visible','hidden')", name="moderation_state"),
        CheckConstraint("views_count >= 0", name="views_count"),
        Index("ix_videos_channel_created", "channel_id", "created_at", "id"),
        Index(
            "ix_videos_public_published",
            "published_at",
            "id",
            postgresql_where=text(
                "visibility='public' AND status='ready' AND moderation_state='visible'"
            ),
        ),
    )
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    channel_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("channels.id"))
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="", server_default="")
    visibility: Mapped[str] = mapped_column(String(16), default="private", server_default="private")
    status: Mapped[str] = mapped_column(
        String(24), default="awaiting_upload", server_default="awaiting_upload"
    )
    moderation_state: Mapped[str] = mapped_column(
        String(16), default="visible", server_default="visible"
    )
    duration_ms: Mapped[int | None]
    views_count: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0")
    width: Mapped[int | None]
    height: Mapped[int | None]
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    upload_token: Mapped[uuid.UUID | None]
    upload_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_stage: Mapped[str | None] = mapped_column(String(24))
    error_code: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class VideoAsset(Base):
    __tablename__ = "video_assets"
    __table_args__ = (
        UniqueConstraint("video_id", "kind"),
        CheckConstraint("kind IN ('source','playback','thumbnail')", name="kind"),
        CheckConstraint("size_bytes > 0", name="size"),
    )
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    video_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("videos.id"))
    kind: Mapped[str] = mapped_column(String(16))
    object_key: Mapped[str] = mapped_column(Text, unique=True)
    content_type: Mapped[str] = mapped_column(String(64))
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    sha256: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class VideoJob(Base):
    __tablename__ = "video_jobs"
    __table_args__ = (
        CheckConstraint("status IN ('queued','running','succeeded','failed')", name="status"),
        CheckConstraint("attempts BETWEEN 0 AND 3", name="attempts"),
        Index("ix_video_jobs_queue", "status", "run_after", "created_at"),
    )
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    video_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("videos.id"), unique=True)
    status: Mapped[str] = mapped_column(String(16), default="queued", server_default="queued")
    attempts: Mapped[int] = mapped_column(default=0, server_default="0")
    run_after: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    lease_token: Mapped[uuid.UUID | None]
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error_code: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class VideoAccess(Base):
    __tablename__ = "video_access"
    video_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("videos.id"), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), primary_key=True, index=True)
    granted_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class WorkerHeartbeat(Base):
    __tablename__ = "worker_heartbeats"
    worker_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class VideoDeletion(Base):
    """Durable cleanup and periodic reconciliation of a permanent tombstone."""

    __tablename__ = "video_deletions"
    __table_args__ = (
        CheckConstraint("status IN ('pending','retry','complete')", name="status"),
        CheckConstraint("attempts >= 0", name="attempts"),
        Index("ix_video_deletions_run_after", "run_after"),
    )
    video_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("videos.id"), primary_key=True)
    status: Mapped[str] = mapped_column(String(16), default="pending", server_default="pending")
    attempts: Mapped[int] = mapped_column(default=0, server_default="0")
    run_after: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_error_code: Mapped[str | None] = mapped_column(String(64))
    cleaned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class VideoViewSession(Base):
    __tablename__ = "video_view_sessions"
    __table_args__ = (
        UniqueConstraint("session_id"),
        CheckConstraint("played_ms >= 0 AND position_ms >= 0 AND sequence >= 0", name="progress"),
        CheckConstraint("rate BETWEEN 0.25 AND 2", name="rate"),
        Index("ix_video_view_sessions_expires_at", "expires_at"),
    )
    video_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("videos.id", ondelete="CASCADE"), primary_key=True
    )
    viewer_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    session_id: Mapped[uuid.UUID] = mapped_column(default=uuid.uuid4)
    position_ms: Mapped[int] = mapped_column(BigInteger)
    played_ms: Mapped[int] = mapped_column(BigInteger, default=0)
    sequence: Mapped[int] = mapped_column(default=0)
    rate: Mapped[float]
    active: Mapped[bool]
    completed: Mapped[bool] = mapped_column(default=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class VideoViewReceipt(Base):
    __tablename__ = "video_view_receipts"
    __table_args__ = (Index("ix_video_view_receipts_counted_at", "counted_at"),)
    video_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("videos.id", ondelete="CASCADE"), primary_key=True
    )
    viewer_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    counted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
