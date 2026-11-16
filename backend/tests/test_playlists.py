from uuid import uuid4

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.modules.channels.schemas import Metadata, Order, UpdateMetadata
from app.modules.playlists.service import validate_order


@pytest.mark.parametrize(
    "data",
    [
        {"title": " "},
        {"title": "x", "user_id": "fake"},
        {"title": "x", "description": "\x00"},
        {"title": None},
    ],
)
def test_metadata_rejects_invalid(data):
    with pytest.raises(ValidationError):
        Metadata(**data)


def test_trim_and_partial_update():
    assert Metadata(title="  Канал  ").title == "Канал"
    assert UpdateMetadata(description="x").model_dump(exclude_unset=True) == {"description": "x"}


def test_full_order_required():
    a, b = uuid4(), uuid4()
    validate_order([a, b], [b, a])
    for requested in ([a], [a, a], [a, uuid4()]):
        with pytest.raises(HTTPException):
            validate_order([a, b], requested)
    with pytest.raises(ValidationError):
        Order(video_ids=[a, a])
