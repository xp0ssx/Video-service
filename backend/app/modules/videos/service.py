from datetime import UTC, datetime, timedelta
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import exists, select, tuple_
from sqlalchemy.dialects.postgresql import insert

from app.modules.access.policy import can_watch
from app.modules.auth.models import Channel, User
from app.modules.auth.schemas import lookup_username
from app.modules.interactions.repository import InteractionRepository
from app.modules.videos.models import Video, VideoAccess, VideoAsset, VideoJob
from app.modules.videos.repository import VideoRepository


def view(db, video, studio=False, author=None, accepted=None):
    username = (
        author
        if author is not None
        else db.scalar(select(User.username).join(Channel).where(Channel.id == video.channel_id))
    )
    result = {
        key: getattr(video, key)
        for key in (
            "id",
            "title",
            "description",
            "visibility",
            "duration_ms",
            "width",
            "height",
            "views_count",
        )
    }
    result["author"] = username
    if studio:
        result.update(
            {
                key: getattr(video, key)
                for key in ("status", "error_stage", "error_code", "moderation_state", "created_at")
            }
        )
        result["source_accepted"] = (
            accepted
            if accepted is not None
            else (
                db.scalar(
                    select(VideoAsset.id).where(
                        VideoAsset.video_id == video.id, VideoAsset.kind == "source"
                    )
                )
                is not None
            )
        )
    return result


class VideoService:
    def __init__(self, factory):
        self.factory = factory

    def create(self, user_id, data):
        with self.factory() as db:
            channel = VideoRepository(db).channel(user_id)
            video = Video(channel_id=channel.id, **data.model_dump())
            db.add(video)
            db.flush()
            result = view(db, video, True)
            db.commit()
            return result

    def own(self, user_id, video_id=None, cursor=None, limit=20):
        with self.factory() as db:
            repo = VideoRepository(db)
            if video_id:
                return view(db, repo.owned(video_id, user_id), True)
            query = (
                select(Video)
                .select_from(Video)
                .join(Channel, Channel.id == Video.channel_id)
                .where(Channel.user_id == user_id, Video.deleted_at.is_(None))
            )
            if cursor:
                # A deleted owner's video remains usable only as a pagination anchor.
                reference = repo.owned(cursor, user_id, include_deleted=True)
                query = query.where(
                    tuple_(Video.created_at, Video.id) < (reference.created_at, reference.id)
                )
            source_exists = exists(
                select(VideoAsset.id).where(
                    VideoAsset.video_id == Video.id, VideoAsset.kind == "source"
                )
            )
            query = query.add_columns(User.username, source_exists).join(
                User, User.id == Channel.user_id
            )
            rows = db.execute(
                query.order_by(Video.created_at.desc(), Video.id.desc()).limit(limit)
            ).all()
            return {
                "items": [
                    view(db, video, True, author, accepted) for video, author, accepted in rows
                ],
                "next_cursor": str(rows[-1][0].id) if len(rows) == limit else None,
            }

    def watch(self, user_id, video_id, kind=None):
        with self.factory() as db:
            video = db.get(Video, video_id)
            if video is None or not can_watch(db, user_id, video):
                raise HTTPException(404, "Видео не найдено")
            if kind:
                asset = db.scalar(
                    select(VideoAsset).where(
                        VideoAsset.video_id == video_id, VideoAsset.kind == kind
                    )
                )
                if asset is None:
                    raise HTTPException(404, "Видео не найдено")
                return {
                    key: getattr(asset, key) for key in ("object_key", "content_type", "size_bytes")
                }
            result = view(db, video)
            result.update(InteractionRepository(db).like_state(video_id, user_id))
            return result

    def patch(self, user_id, video_id, data):
        with self.factory() as db:
            video = VideoRepository(db).owned(video_id, user_id, True)
            for key, value in data.model_dump(exclude_unset=True).items():
                setattr(video, key, value)
            result = view(db, video, True)
            db.commit()
            return result

    def grants(self, user_id, video_id, username=None, remove=False):
        if username is not None:
            username = lookup_username(username)
        with self.factory() as db:
            VideoRepository(db).owned(video_id, user_id, True)
            if username is None:
                return [
                    {"username": name}
                    for name in db.scalars(
                        select(User.username)
                        .join(VideoAccess, VideoAccess.user_id == User.id)
                        .where(VideoAccess.video_id == video_id)
                        .order_by(User.username)
                    )
                ]
            target = db.scalar(select(User).where(User.username == username))
            if target is None:
                raise HTTPException(404, "Пользователь не найден")
            if target.id == user_id:
                raise HTTPException(422, "Владелец уже имеет доступ")
            if remove:
                grant = db.get(VideoAccess, (video_id, target.id))
                if grant:
                    db.delete(grant)
            else:
                db.execute(
                    insert(VideoAccess)
                    .values(video_id=video_id, user_id=target.id, granted_by=user_id)
                    .on_conflict_do_nothing()
                )
            db.commit()

    def reserve(self, user_id, video_id):
        now = datetime.now(UTC)
        with self.factory() as db:
            db.scalar(select(User).where(User.id == user_id).with_for_update())
            repo = VideoRepository(db)
            video = repo.owned(video_id, user_id, True)
            expired = db.scalars(
                select(Video)
                .join(Channel)
                .where(
                    Channel.user_id == user_id,
                    Video.status == "uploading",
                    Video.upload_expires_at <= now,
                )
                .with_for_update(of=Video)
            ).all()
            for old in expired:
                old.status, old.error_stage, old.error_code = "failed", "upload", "upload_expired"
                old.upload_token, old.upload_expires_at = None, None
            db.flush()
            if (
                db.scalar(
                    select(VideoAsset.id).where(
                        VideoAsset.video_id == video_id, VideoAsset.kind == "source"
                    )
                )
                or video.status not in ("awaiting_upload", "failed")
                or (video.status == "failed" and video.error_stage != "upload")
            ):
                raise HTTPException(409, "Исходник уже принят или загрузка занята")
            if db.scalar(
                select(Video.id)
                .join(Channel)
                .where(Channel.user_id == user_id, Video.status == "uploading")
            ):
                raise HTTPException(429, "У пользователя уже есть активная загрузка")
            video.status, video.upload_token, video.upload_expires_at = (
                "uploading",
                uuid4(),
                now + timedelta(minutes=15),
            )
            video.error_code, video.error_stage = None, None
            token = video.upload_token
            db.commit()
            return token

    def accepted(self, user_id, video_id, token, key, size, sha):
        with self.factory() as db:
            video = VideoRepository(db).owned(video_id, user_id, True)
            if (
                video.status != "uploading"
                or video.upload_token != token
                or video.upload_expires_at <= datetime.now(UTC)
            ):
                raise HTTPException(409, "Резервирование истекло")
            db.add(
                VideoAsset(
                    video_id=video_id,
                    kind="source",
                    object_key=key,
                    content_type="application/octet-stream",
                    size_bytes=size,
                    sha256=sha,
                )
            )
            db.add(VideoJob(video_id=video_id))
            video.status, video.upload_token, video.upload_expires_at = "queued", None, None
            db.commit()
            return {"id": video_id, "status": "queued"}

    def store_source(self, storage, path, user_id, video_id, token, size, sha):
        from app.infrastructure.transcoder import LostLease
        from app.modules.videos.deletion import storage_writer

        key = f"sources/{video_id}/{token}/original"
        try:
            with storage_writer(self.factory, video_id):
                storage.upload(path, key, "application/octet-stream")
                if storage.head(key)["ContentLength"] != size:
                    raise HTTPException(503, "Хранилище не подтвердило размер файла")
                return self.accepted(user_id, video_id, token, key, size, sha)
        except LostLease:
            raise HTTPException(404, "Видео не найдено") from None

    def upload_failed(self, user_id, video_id, token, code):
        with self.factory() as db:
            video = VideoRepository(db).owned(video_id, user_id, True)
            if video.upload_token == token and video.status == "uploading":
                video.status, video.error_stage, video.error_code = "failed", "upload", code
                video.upload_token, video.upload_expires_at = None, None
                db.commit()
