from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request

from app.modules.auth.router import origin
from app.modules.interactions.schemas import CreateComment
from app.modules.interactions.service import InteractionService
from app.modules.videos.router import Viewer, Writer

router = APIRouter(prefix="/videos", tags=["interactions"])


def service(request: Request):
    return InteractionService(request.app.state.database.session_factory)


Service = Annotated[InteractionService, Depends(service)]


@router.put("/{video_id}/like", dependencies=[Depends(origin)])
def like(video_id: UUID, user: Writer, interactions: Service):
    return interactions.like(video_id, user, True)


@router.delete("/{video_id}/like", dependencies=[Depends(origin)])
def unlike(video_id: UUID, user: Writer, interactions: Service):
    return interactions.like(video_id, user, False)


@router.get("/{video_id}/comments")
def comments(
    video_id: UUID,
    user: Viewer,
    interactions: Service,
    cursor: Annotated[str | None, Query(max_length=512)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
):
    return interactions.comments(video_id, user, cursor, limit)


@router.post("/{video_id}/comments", status_code=201, dependencies=[Depends(origin)])
def add_comment(video_id: UUID, data: CreateComment, user: Writer, interactions: Service):
    return interactions.add_comment(video_id, user, data)


@router.get("/{video_id}/comments/{comment_id}/replies")
def replies(
    video_id: UUID,
    comment_id: UUID,
    user: Viewer,
    interactions: Service,
    cursor: Annotated[str | None, Query(max_length=512)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
):
    return interactions.comments(video_id, user, cursor, limit, comment_id)


@router.put("/{video_id}/comments/{comment_id}/like", dependencies=[Depends(origin)])
def like_comment(video_id: UUID, comment_id: UUID, user: Writer, interactions: Service):
    return interactions.comment_like(video_id, comment_id, user, True)


@router.delete("/{video_id}/comments/{comment_id}/like", dependencies=[Depends(origin)])
def unlike_comment(video_id: UUID, comment_id: UUID, user: Writer, interactions: Service):
    return interactions.comment_like(video_id, comment_id, user, False)
