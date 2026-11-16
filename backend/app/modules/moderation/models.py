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


class Report(Base):
    __tablename__ = "reports"
    __table_args__ = (
        UniqueConstraint("video_id", "reporter_id"),
        CheckConstraint("reason IN ('spam','copyright','abuse','other')", name="reason"),
        CheckConstraint("status IN ('open','dismissed','actioned')", name="status"),
        CheckConstraint(
            "char_length(body) BETWEEN 1 AND 2000 AND body ~ '[^[:space:]]'", name="body"
        ),
        CheckConstraint(
            "(status = 'open' AND reviewed_by IS NULL AND reviewed_at IS NULL AND "
            "review_note IS NULL AND resolved_action_id IS NULL) OR (status IN "
            "('dismissed','actioned') AND reviewed_by IS NOT NULL AND reviewed_at IS NOT "
            "NULL AND char_length(review_note) BETWEEN 1 AND 2000 AND (status = "
            "'actioned') = (resolved_action_id IS NOT NULL))",
            name="resolution",
        ),
        Index("ix_reports_status_created", "status", "created_at", "id"),
    )
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    video_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("videos.id"))
    reporter_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    reason: Mapped[str] = mapped_column(String(16))
    body: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default="open", server_default="open")
    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    review_note: Mapped[str | None] = mapped_column(Text)
    resolved_action_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("moderation_actions.id")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
