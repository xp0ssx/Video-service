from sqlalchemy import select

from app.modules.admin.models import ModerationAction
from app.modules.auth.models import User


class AdminRepository:
    def __init__(self, db):
        self.db = db

    def by_username(self, username):
        return self.db.scalar(select(User).where(User.username == username))

    def lock_user(self, user_id):
        return self.db.scalar(select(User).where(User.id == user_id).with_for_update())

    def record_role_change(self, actor_id, user, previous, reason):
        self.db.add(
            ModerationAction(
                actor_id=actor_id,
                target_user_id=user.id,
                previous_value=previous,
                new_value=user.role,
                reason=reason,
            )
        )
