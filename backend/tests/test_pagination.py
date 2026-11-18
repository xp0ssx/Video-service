import base64
import json
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.core.pagination import decode_cursor, encode_cursor
from app.modules.interactions.schemas import CreateComment
from app.modules.videos.feed import title_pattern


def test_cursor_round_trip_and_scope():
    at, identifier = datetime.now(UTC), uuid4()
    value = encode_cursor(at, identifier, "comments:test")
    assert decode_cursor(value, "comments:test") == (at, identifier)
    with pytest.raises(HTTPException):
        decode_cursor(value, "feed:test")


@pytest.mark.parametrize(
    "data",
    [
        {},
        [],
        {"at": "invalid", "id": str(uuid4()), "scope": "test"},
        {"at": "2026-01-01T00:00:00", "id": str(uuid4()), "scope": "test"},
        {"at": "2026-01-01T00:00:00Z", "id": 123, "scope": "test"},
        {"at": "2026-01-01T00:00:00Z", "id": str(uuid4()), "scope": "other"},
    ],
)
def test_malformed_cursor_is_validation_error(data):
    value = base64.urlsafe_b64encode(json.dumps(data).encode()).decode().rstrip("=")
    with pytest.raises(HTTPException) as result:
        decode_cursor(value, "test")
    assert result.value.status_code == 422


def test_literal_like_pattern_and_comment_preserves_whitespace():
    assert title_pattern("a!%_\\") == "%a!!!%!_\\%"
    assert CreateComment(body="  text\n").body == "  text\n"


@pytest.mark.parametrize("body", [" ", "\n\t\r", "\u2003\u00a0"])
def test_comment_rejects_whitespace_only(body):
    with pytest.raises(ValueError):
        CreateComment(body=body)
