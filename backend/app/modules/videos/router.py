import re
import secrets
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response

from app.modules.auth.repository import AuthRepository
from app.modules.auth.router import csrf, current, database, origin
from app.modules.auth.service import digest
from app.modules.videos.deletion import delete_owned
from app.modules.videos.feed import FeedService
from app.modules.videos.media import byte_range
from app.modules.videos.schemas import CreateVideo, UpdateVideo
from app.modules.videos.service import VideoService
from app.modules.videos.streaming import MediaResponse
from app.modules.videos.upload import receive
from app.modules.videos.views import StartView, ViewProgress, ViewService, viewer_key

router = APIRouter(tags=["videos"])


def owner(row: Annotated[Any, Depends(current)], db: Annotated[Any, Depends(database)]):
    user_id = row[1].id
    db.close()
    return user_id


def writer(row: Annotated[Any, Depends(csrf)], db: Annotated[Any, Depends(database)]):
    user_id = row[1].id
    db.close()
    return user_id


def viewer(request: Request):
    token = request.cookies.get("session_id")
    if not token or len(token) > 128:
        return None
    with request.app.state.database.session_factory() as db:
        row = AuthRepository(db).session(digest(token))
        return row[1].id if row else None


def service(request: Request):
    return VideoService(request.app.state.database.session_factory)


Writer = Annotated[UUID, Depends(writer)]
Owner = Annotated[UUID, Depends(owner)]
Viewer = Annotated[UUID | None, Depends(viewer)]
Service = Annotated[VideoService, Depends(service)]


def view_actor(request: Request, user: Viewer):
    if user is not None:
        with request.app.state.database.session_factory() as db:
            row = AuthRepository(db).session(digest(request.cookies.get("session_id", "")))
            if row is None:
                raise HTTPException(401, "Требуется вход")
            csrf(row, request.headers.get("X-CSRF-Token"))
    return user


ViewActor = Annotated[UUID | None, Depends(view_actor)]


def guest_token(request):
    token = request.cookies.get("video_viewer", "")
    return token if re.fullmatch(r"[A-Za-z0-9_-]{43}", token) else None


@router.post("/videos/{video_id}/views", dependencies=[Depends(origin)])
def start_view(
    video_id: UUID, data: StartView, request: Request, response: Response, user: ViewActor
):
    token = guest_token(request)
    fresh = user is None and token is None
    if fresh:
        token = secrets.token_urlsafe(32)
    value = ViewService(request.app.state.database.session_factory).start(
        user, viewer_key(user, token), video_id, data
    )
    if fresh:
        response.set_cookie(
            "video_viewer",
            token,
            max_age=30 * 86400,
            httponly=True,
            secure=request.app.state.settings.cookie_secure,
            samesite="lax",
            path="/",
        )
    return value


@router.post("/videos/{video_id}/views/{session_id}", dependencies=[Depends(origin)])
def progress_view(
    video_id: UUID, session_id: UUID, data: ViewProgress, request: Request, user: ViewActor
):
    token = guest_token(request)
    if user is None and token is None:
        raise HTTPException(404, "Сессия просмотра недоступна")
    return ViewService(request.app.state.database.session_factory).progress(
        user, viewer_key(user, token), video_id, session_id, data
    )


@router.get("/videos")
def feed(
    request: Request,
    q: Annotated[str, Query(max_length=200)] = "",
    cursor: Annotated[str | None, Query(max_length=512)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
):
    return FeedService(request.app.state.database.session_factory).page(q, cursor, limit)


@router.post("/videos", status_code=201, dependencies=[Depends(origin)])
def create(data: CreateVideo, user: Writer, videos: Service):
    return videos.create(user, data)


@router.put(
    "/videos/{video_id}/source",
    status_code=202,
    dependencies=[Depends(origin)],
    openapi_extra={
        "requestBody": {
            "content": {
                "application/octet-stream": {"schema": {"type": "string", "format": "binary"}}
            },
            "required": True,
        }
    },
)
async def source(video_id: UUID, request: Request, user: Writer, videos: Service):
    return await receive(request, videos, user, video_id)


@router.get("/me/videos")
def own(
    videos: Service,
    user: Owner,
    cursor: UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
):
    return videos.own(user, cursor=cursor, limit=limit)


@router.get("/me/videos/{video_id}")
def own_video(video_id: UUID, user: Owner, videos: Service):
    return videos.own(user, video_id)


@router.get("/videos/{video_id}")
def watch(video_id: UUID, user: Viewer, videos: Service):
    return videos.watch(user, video_id)


@router.patch("/videos/{video_id}", dependencies=[Depends(origin)])
def patch(video_id: UUID, data: UpdateVideo, user: Writer, videos: Service):
    return videos.patch(user, video_id, data)


@router.delete(
    "/videos/{video_id}",
    status_code=204,
    dependencies=[Depends(origin)],
    summary="Удалить собственное видео",
    description=(
        "204 после фиксации необратимого удаления и задания очистки. Доступ закрыт сразу; "
        "очистка S3 повторяется worker. Повтор владельца возвращает 204. "
        "Чужое или неизвестное видео — 404, включая admin/moderator. "
        "Требуются сессия, Origin и CSRF."
    ),
    responses={
        401: {"description": "Нет сессии"},
        403: {"description": "Origin или CSRF"},
        404: {"description": "Видео не найдено"},
    },
)
def delete_video(video_id: UUID, user: Writer, videos: Service):
    delete_owned(videos.factory, user, video_id)


@router.get("/videos/{video_id}/access")
def grants(video_id: UUID, user: Owner, videos: Service):
    return videos.grants(user, video_id)


@router.put("/videos/{video_id}/access/{username}", status_code=204, dependencies=[Depends(origin)])
def grant(video_id: UUID, username: str, user: Writer, videos: Service):
    videos.grants(user, video_id, username)


@router.delete(
    "/videos/{video_id}/access/{username}", status_code=204, dependencies=[Depends(origin)]
)
def revoke(video_id: UUID, username: str, user: Writer, videos: Service):
    videos.grants(user, video_id, username, True)


@router.get("/videos/{video_id}/media")
@router.head("/videos/{video_id}/media")
@router.get("/videos/{video_id}/thumbnail")
@router.head("/videos/{video_id}/thumbnail")
def media(video_id: UUID, request: Request, user: Viewer, videos: Service):
    asset = videos.watch(
        user, video_id, "playback" if request.url.path.endswith("/media") else "thumbnail"
    )
    size = asset["size_bytes"]
    selected = byte_range(request.headers.get("range"), size) if request.method == "GET" else None
    headers = {
        "Accept-Ranges": "bytes",
        "Content-Length": str(size),
        "Content-Type": asset["content_type"],
        "Cache-Control": "private, no-store",
        "X-Content-Type-Options": "nosniff",
    }
    storage = request.app.state.storage
    try:
        if storage.head(asset["object_key"])["ContentLength"] != size:
            raise RuntimeError("Asset size mismatch")
        if request.method == "HEAD":
            return Response(headers=headers)
        args = {"Range": f"bytes={selected[0]}-{selected[1]}"} if selected else {}
        response = storage.client.get_object(Bucket=storage.bucket, Key=asset["object_key"], **args)
    except Exception:
        raise HTTPException(503, "Медиа временно недоступно") from None
    body = response["Body"]
    if selected:
        first, last = selected
        headers.update(
            {
                "Content-Length": str(last - first + 1),
                "Content-Range": f"bytes {first}-{last}/{size}",
            }
        )

    def stream():
        try:
            while chunk := body.read(256 * 1024):
                yield chunk
        finally:
            body.close()

    return MediaResponse(
        stream(),
        status_code=206 if selected else 200,
        headers=headers,
        s3_body=body,
    )
