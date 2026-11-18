"""Bounded playback evidence and durable, rolling-window view deduplication."""

from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import uuid4

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import delete, select

from app.modules.access.policy import can_watch
from app.modules.auth.service import digest
from app.modules.videos.models import Video, VideoViewReceipt, VideoViewSession

WINDOW = timedelta(hours=24)
SESSION_TTL = timedelta(hours=1)
MAX_INTERVAL_MS = 8000


def utcnow():
    return datetime.now(UTC)


def viewer_key(user_id, anonymous_token):
    return digest(f"user:{user_id}" if user_id is not None else f"guest:{anonymous_token}")


class StartView(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    position_ms: int = Field(ge=0, le=86_400_000, strict=True)
    rate: float = Field(default=1, ge=0.25, le=2, allow_inf_nan=False)


class ViewProgress(StartView):
    sequence: int = Field(ge=1, le=2_147_483_647, strict=True)
    event: Literal["progress", "pause", "resume", "seek", "rate"] = "progress"


class ViewRepository:
    def __init__(self, db):
        self.db = db

    def accessible(self, video_id, user_id):
        # All writers (grant/revoke, moderation, deletion, progress) lock Video first.
        video = self.db.scalar(select(Video).where(Video.id == video_id).with_for_update())
        if video is None or not can_watch(self.db, user_id, video):
            raise HTTPException(404, "Видео не найдено")
        if not video.duration_ms or video.duration_ms <= 0:
            raise HTTPException(409, "Длительность видео пока недоступна")
        return video

    def recent(self, video_id, key, now):
        receipt = self.db.get(VideoViewReceipt, (video_id, key))
        return receipt is not None and receipt.counted_at > now - WINDOW

    def count(self, video, key, now):
        if self.recent(video.id, key, now):
            return False
        receipt = self.db.get(VideoViewReceipt, (video.id, key))
        if receipt is None:
            self.db.add(VideoViewReceipt(video_id=video.id, viewer_key=key, counted_at=now))
        else:
            receipt.counted_at = now
        video.views_count += 1
        return True


def threshold(video):
    return 10_000 if video.duration_ms >= 10_000 else (video.duration_ms + 1) // 2


def result(video, session, counted=False):
    return {
        "session_id": session.session_id,
        "threshold_ms": threshold(video),
        "played_ms": session.played_ms,
        "completed": session.completed,
        "counted": counted,
        "views_count": video.views_count,
    }


class ViewService:
    def __init__(self, factory, clock=None):
        self.factory = factory
        self.clock = clock or utcnow

    def start(self, user_id, key, video_id, data):
        with self.factory() as db:
            repo = ViewRepository(db)
            video = repo.accessible(video_id, user_id)
            now = self.clock()
            already_counted = repo.recent(video_id, key, now)
            session = db.get(VideoViewSession, (video_id, key))
            if session is None:
                session = VideoViewSession(video_id=video_id, viewer_key=key)
                db.add(session)
            # Replace the previous tab/reload session, never accumulate idle wall time.
            session.session_id = uuid4()
            session.position_ms = min(data.position_ms, video.duration_ms)
            session.played_ms, session.sequence = 0, 0
            session.rate, session.active = data.rate, True
            session.completed = already_counted
            session.updated_at, session.expires_at = now, now + SESSION_TTL
            db.flush()
            value = result(video, session)
            db.commit()
            return value

    def progress(self, user_id, key, video_id, session_id, data):
        with self.factory() as db:
            repo = ViewRepository(db)
            video = repo.accessible(video_id, user_id)
            now = self.clock()
            session = db.get(VideoViewSession, (video_id, key))
            if session is None or session.session_id != session_id or now >= session.expires_at:
                raise HTTPException(404, "Сессия просмотра недоступна")
            counted = False
            if not session.completed and data.sequence > session.sequence:
                elapsed = max(0, (now - session.updated_at).total_seconds() * 1000)
                position = min(data.position_ms, video.duration_ms)
                delta = position - session.position_ms
                budget = elapsed * session.rate
                # A discontinuity is a seek: discard the interval, even if the
                # client omitted the seek event. Silence >8s earns no playback.
                if (
                    session.active
                    and data.event not in ("seek", "resume")
                    and elapsed <= MAX_INTERVAL_MS
                    and 0 <= delta <= budget + 350
                ):
                    session.played_ms += int(min(delta, budget))
                session.position_ms, session.updated_at = position, now
                session.rate, session.sequence = data.rate, data.sequence
                session.active = data.event not in ("pause", "seek")
                if session.played_ms >= threshold(video):
                    counted = repo.count(video, key, now)
                    session.completed = True
            value = result(video, session, counted)
            db.commit()
            return value

    def cleanup(self, limit=500):
        """Bounded maintenance; historical totals do not depend on retained rows."""
        now = self.clock()
        with self.factory() as db:
            for model, condition in (
                (VideoViewSession, VideoViewSession.expires_at <= now),
                (VideoViewReceipt, VideoViewReceipt.counted_at <= now - WINDOW),
            ):
                # Skip active rows. Progress serializes through Video; a receipt
                # renewed after selection no longer matches the delete predicate.
                rows = db.execute(
                    select(model.video_id, model.viewer_key)
                    .join(Video, Video.id == model.video_id)
                    .where(condition)
                    .limit(limit)
                    .with_for_update(of=Video, skip_locked=True)
                ).all()
                for video_id, key in rows:
                    db.execute(
                        delete(model).where(
                            model.video_id == video_id, model.viewer_key == key, condition
                        )
                    )
            db.commit()
