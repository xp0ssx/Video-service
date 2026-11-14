import hashlib
import secrets
from datetime import UTC, datetime, timedelta

from argon2 import PasswordHasher
from argon2.exceptions import VerificationError
from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError

from app.modules.auth.models import Channel, Session, User
from app.modules.auth.repository import AuthRepository

hasher = PasswordHasher(time_cost=2, memory_cost=19456, parallelism=1)
DUMMY_HASH = hasher.hash(secrets.token_urlsafe(32))


def digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class AuthService:
    def __init__(self, db):
        self.db = db
        self.repository = AuthRepository(db)

    def register(self, data, role="user"):
        user = User(
            email=str(data.email),
            username=data.username,
            password_hash=hasher.hash(data.password.get_secret_value()),
            role=role,
        )
        try:
            self.db.add(user)
            self.db.flush()
            self.db.add(Channel(user_id=user.id, title=user.username))
            self.db.flush()
        except IntegrityError:
            self.db.rollback()
            raise HTTPException(409, "Email или username уже занят") from None
        return user

    def login(self, data):
        user = self.repository.by_email(str(data.email))
        try:
            hasher.verify(
                user.password_hash if user else DUMMY_HASH, data.password.get_secret_value()
            )
        except VerificationError:
            raise HTTPException(401, "Неверный email или пароль") from None
        if user is None:
            raise HTTPException(401, "Неверный email или пароль")
        return user

    def issue(self, user, previous=None):
        if previous:
            old = self.repository.session(digest(previous))
            if old:
                self.db.delete(old[0])
        token = secrets.token_urlsafe(32)
        session = Session(
            user_id=user.id,
            token_hash=digest(token),
            csrf_token=secrets.token_hex(32),
            expires_at=datetime.now(UTC) + timedelta(days=7),
        )
        self.db.add(session)
        self.db.flush()
        return token, session


def payload(user, session):
    return {
        "user": {"id": str(user.id), "username": user.username, "role": user.role},
        "csrf_token": session.csrf_token,
    }
