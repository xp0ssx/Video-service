import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ModerationAction(Base):
    __tablename__ = "moderation_actions"
    # Only user role changes exist at this stage. Video targets and their FK are added
    # with the videos migration, so no reference to a nonexistent table is created.
    __table_args__ = (CheckConstraint("action = 'set_role'", name="action"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    actor_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    target_user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    action: Mapped[str] = mapped_column(String(32), default="set_role")
    previous_value: Mapped[str] = mapped_column(String(16))
    new_value: Mapped[str] = mapped_column(String(16))
    reason: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
