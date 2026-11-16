import base64
import binascii
import json
import re
from datetime import UTC, datetime
from uuid import UUID

from fastapi import HTTPException


def encode_cursor(at: datetime, identifier: UUID, scope: str) -> str:
    data = json.dumps(
        {"at": at.astimezone(UTC).isoformat(), "id": str(identifier), "scope": scope},
        separators=(",", ":"),
    ).encode()
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def decode_cursor(value: str | None, scope: str):
    if value is None:
        return None
    try:
        if not 1 <= len(value) <= 512 or not re.fullmatch(r"[A-Za-z0-9_-]+", value):
            raise ValueError
        data = json.loads(
            base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)
        )
        if (
            not isinstance(data, dict)
            or set(data) != {"at", "id", "scope"}
            or data["scope"] != scope
        ):
            raise ValueError
        if not all(isinstance(data[key], str) for key in ("at", "id", "scope")):
            raise ValueError
        at = datetime.fromisoformat(data["at"])
        if at.tzinfo is None or at.utcoffset() is None:
            raise ValueError
        return at.astimezone(UTC), UUID(data["id"])
    except (ValueError, TypeError, OverflowError, binascii.Error, UnicodeDecodeError):
        raise HTTPException(422, "Недопустимый cursor") from None
