from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session as DatabaseSession

from app.modules.auth.models import Session, User


class AuthRepository:
    def __init__(self, db: DatabaseSession):
        self.db = db

    def by_email(self, email: str):
        return self.db.scalar(select(User).where(User.email == email))

    def session(self, token_hash: str):
        return self.db.execute(
            select(Session, User)
            .join(User)
            .where(Session.token_hash == token_hash, Session.expires_at > datetime.now(UTC))
        ).first()
