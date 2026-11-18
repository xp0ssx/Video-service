from fastapi import HTTPException
from sqlalchemy import func, select, tuple_

from app.core.pagination import decode_cursor, encode_cursor
from app.modules.admin.models import ModerationAction
from app.modules.auth.models import Channel
from app.modules.interactions.service import InteractionService
from app.modules.moderation.models import Report
from app.modules.moderation.repository import ModerationRepository
from app.modules.videos.models import Video


def report_view(row):
    return {
        key: getattr(row, key)
        for key in (
            "id",
            "video_id",
            "reporter_id",
            "reason",
            "body",
            "status",
            "reviewed_by",
            "review_note",
            "resolved_action_id",
            "created_at",
            "reviewed_at",
        )
    }


def action_view(row):
    return {
        key: getattr(row, key)
        for key in (
            "id",
            "actor_id",
            "video_id",
            "target_user_id",
            "action",
            "previous_value",
            "new_value",
            "reason",
            "created_at",
        )
    }


class ModerationService:
    def __init__(self, factory):
        self.factory = factory

    def create(self, video_id, user_id, data):
        with self.factory() as db:
            InteractionService(self.factory).available(db, video_id, user_id, True)
            row = db.scalar(
                select(Report).where(Report.video_id == video_id, Report.reporter_id == user_id)
            )
            if row is None:
                row = Report(video_id=video_id, reporter_id=user_id, **data.model_dump())
                db.add(row)
                db.flush()
            result = {"id": row.id, "status": row.status}
            db.commit()
            return result

    def reports(self, user_id, status, cursor, limit):
        scope = f"reports:{status}"
        after = decode_cursor(cursor, scope)
        with self.factory() as db:
            ModerationRepository(db).actor(user_id)
            query = select(Report, Channel.user_id).join_from(Report, Video).join(Channel)
            query = query.where(Video.deleted_at.is_(None))
            if status != "all":
                query = query.where(Report.status == status)
            if after:
                query = query.where(tuple_(Report.created_at, Report.id) > after)
            rows = db.execute(query.order_by(Report.created_at, Report.id).limit(limit + 1)).all()
            selected = rows[:limit]
            return {
                "items": [{**report_view(row), "owner_id": owner} for row, owner in selected],
                "next_cursor": encode_cursor(selected[-1][0].created_at, selected[-1][0].id, scope)
                if len(rows) > limit
                else None,
            }

    def resolve(self, user_id, identifier, data):
        with self.factory() as db:
            repo = ModerationRepository(db)
            repo.actor(user_id)
            # All moderation transitions lock Video -> Report; no inversion with
            # upload, grants, interactions or worker Job -> Video publication.
            reference = repo.report(identifier)
            video = repo.video(reference.video_id)
            row = repo.report(identifier, True)
            if row.status != "open":
                raise HTTPException(409, "Жалоба уже рассмотрена")
            action = None
            if data.status == "actioned":
                action = repo.audit(user_id, video, "hidden", data.reason)
                if action is None:
                    raise HTTPException(409, "Видео уже скрыто; отклоните дублирующую жалобу")
            row.status, row.review_note, row.reviewed_by = data.status, data.reason, user_id
            row.reviewed_at = db.scalar(select(func.clock_timestamp()))
            row.resolved_action_id = action.id if action else None
            db.flush()
            result = report_view(row)
            db.commit()
            return result

    def state(self, user_id, identifier, data):
        with self.factory() as db:
            repo = ModerationRepository(db)
            repo.actor(user_id)
            video = repo.video(identifier)
            action = repo.audit(user_id, video, data.state, data.reason)
            result = {
                "video_id": video.id,
                "state": video.moderation_state,
                "action_id": action.id if action else None,
            }
            db.commit()
            return result

    def actions(self, user_id, cursor, limit):
        scope = "moderation-actions"
        after = decode_cursor(cursor, scope)
        with self.factory() as db:
            ModerationRepository(db).actor(user_id)
            query = select(ModerationAction)
            if after:
                query = query.where(
                    tuple_(ModerationAction.created_at, ModerationAction.id) < after
                )
            rows = db.scalars(
                query.order_by(
                    ModerationAction.created_at.desc(), ModerationAction.id.desc()
                ).limit(limit + 1)
            ).all()
            selected = rows[:limit]
            return {
                "items": [action_view(row) for row in selected],
                "next_cursor": encode_cursor(selected[-1].created_at, selected[-1].id, scope)
                if len(rows) > limit
                else None,
            }
