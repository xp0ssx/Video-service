from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request

from app.modules.auth.router import origin
from app.modules.channels.schemas import Metadata, Order, UpdateMetadata
from app.modules.channels.service import ChannelService
from app.modules.playlists.service import PlaylistService
from app.modules.videos.router import Owner, Viewer, Writer

router = APIRouter(tags=["channels and playlists"])
Cursor = Annotated[str | None, Query(max_length=512)]
Limit = Annotated[int, Query(ge=1, le=100)]


def channels(request: Request):
    return ChannelService(request.app.state.database.session_factory)


def playlists(request: Request):
    return PlaylistService(request.app.state.database.session_factory)


Channels = Annotated[ChannelService, Depends(channels)]
Playlists = Annotated[PlaylistService, Depends(playlists)]


@router.get("/channels/{username}")
def channel(username: str, user: Viewer, service: Channels):
    return service.get(username, user)


@router.patch("/me/channel", dependencies=[Depends(origin)])
def patch_channel(data: UpdateMetadata, user: Writer, service: Channels):
    return service.patch(user, data)


@router.get("/channels/{username}/videos")
def channel_videos(username: str, service: Channels, cursor: Cursor = None, limit: Limit = 20):
    return service.listing(username, "videos", cursor, limit)


@router.get("/channels/{username}/playlists")
def channel_playlists(username: str, service: Channels, cursor: Cursor = None, limit: Limit = 20):
    return service.listing(username, "playlists", cursor, limit)


@router.put("/channels/{username}/subscription", dependencies=[Depends(origin)])
def subscribe(username: str, user: Writer, service: Channels):
    return service.subscribe(username, user, True)


@router.delete("/channels/{username}/subscription", dependencies=[Depends(origin)])
def unsubscribe(username: str, user: Writer, service: Channels):
    return service.subscribe(username, user, False)


@router.get("/me/subscriptions")
def subscriptions(user: Owner, service: Channels, cursor: Cursor = None, limit: Limit = 20):
    return service.subscriptions(user, cursor, limit)


@router.post("/playlists", status_code=201, dependencies=[Depends(origin)])
def create_playlist(data: Metadata, user: Writer, service: Playlists):
    return service.create(user, data)


@router.get("/playlists/{identifier}")
def playlist(
    identifier: UUID, user: Viewer, service: Playlists, cursor: Cursor = None, limit: Limit = 20
):
    return service.get(identifier, user, cursor, limit)


@router.get("/me/playlists/{identifier}")
def studio_playlist(
    identifier: UUID, user: Owner, service: Playlists, cursor: Cursor = None, limit: Limit = 20
):
    return service.get(identifier, user, cursor, limit, True)


@router.patch("/playlists/{identifier}", status_code=204, dependencies=[Depends(origin)])
def patch_playlist(identifier: UUID, data: UpdateMetadata, user: Writer, service: Playlists):
    service.mutate(identifier, user, "patch", data)


@router.delete("/playlists/{identifier}", status_code=204, dependencies=[Depends(origin)])
def delete_playlist(identifier: UUID, user: Writer, service: Playlists):
    service.mutate(identifier, user, "delete")


@router.put(
    "/playlists/{identifier}/items/{video_id}", status_code=204, dependencies=[Depends(origin)]
)
def add_item(identifier: UUID, video_id: UUID, user: Writer, service: Playlists):
    service.mutate(identifier, user, "add", video_id=video_id)


@router.delete(
    "/playlists/{identifier}/items/{video_id}", status_code=204, dependencies=[Depends(origin)]
)
def remove_item(identifier: UUID, video_id: UUID, user: Writer, service: Playlists):
    service.mutate(identifier, user, "remove", video_id=video_id)


@router.put("/playlists/{identifier}/order", status_code=204, dependencies=[Depends(origin)])
def order(identifier: UUID, data: Order, user: Writer, service: Playlists):
    service.mutate(identifier, user, "order", data)
