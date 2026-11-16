from fastapi import HTTPException

from app.modules.admin.repository import AdminRepository
from app.modules.auth.schemas import lookup_username


class AdminService:
    def __init__(self, db):
        self.db = db
        self.repository = AdminRepository(db)

    def find(self, username):
        # Exact normalized lookup, consistent with private grants; no unbounded listing.
        user = self.repository.by_username(lookup_username(username))
        return [user] if user else []

    def set_role(self, actor, user_id, change):
        try:
            user = self.repository.lock_user(user_id)
            if user is None:
                raise HTTPException(404, "Пользователь не найден")
            if user.role == "admin":
                raise HTTPException(403, "Роль администратора изменять нельзя")
            previous = user.role
            if previous == change.role:
                self.db.commit()
                return user
            user.role = change.role
            self.repository.record_role_change(actor.id, user, previous, change.reason)
            self.db.flush()
            self.db.commit()
            return user
        except Exception:
            self.db.rollback()
            raise
