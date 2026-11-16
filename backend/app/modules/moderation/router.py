from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request

from app.modules.auth.router import origin
from app.modules.moderation.schemas import CreateReport, Decision, StateChange
from app.modules.moderation.service import ModerationService
from app.modules.videos.router import Owner, Writer

router = APIRouter(tags=["moderation"])
Cursor = Annotated[str | None, Query(max_length=512)]
Limit = Annotated[int, Query(ge=1, le=100)]


def service(request: Request):
    return ModerationService(request.app.state.database.session_factory)


Service = Annotated[ModerationService, Depends(service)]


@router.post("/videos/{video_id}/reports", dependencies=[Depends(origin)])
def create(video_id: UUID, data: CreateReport, user: Writer, moderation: Service):
    return moderation.create(video_id, user, data)


@router.get("/moderation/reports")
def reports(
    user: Owner,
    moderation: Service,
    status: Literal["open", "dismissed", "actioned", "all"] = "open",
    cursor: Cursor = None,
    limit: Limit = 20,
):
    return moderation.reports(user, status, cursor, limit)


@router.patch("/moderation/reports/{identifier}", dependencies=[Depends(origin)])
def resolve(identifier: UUID, data: Decision, user: Writer, moderation: Service):
    return moderation.resolve(user, identifier, data)


@router.put("/moderation/videos/{identifier}/state", dependencies=[Depends(origin)])
def state(identifier: UUID, data: StateChange, user: Writer, moderation: Service):
    return moderation.state(user, identifier, data)


@router.get("/moderation/actions")
def actions(user: Owner, moderation: Service, cursor: Cursor = None, limit: Limit = 20):
    return moderation.actions(user, cursor, limit)
