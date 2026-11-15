import pytest
from fastapi import HTTPException

from app.modules.videos.media import byte_range


@pytest.mark.parametrize(
    "header,result",
    [
        (None, None),
        ("bytes=0-9", (0, 9)),
        ("bytes=90-999", (90, 99)),
        ("bytes=-200", (0, 99)),
        ("bytes=99-", (99, 99)),
    ],
)
def test_valid_ranges(header, result):
    assert byte_range(header, 100) == result


@pytest.mark.parametrize(
    "header",
    [
        "bytes=-",
        "bytes=-0",
        "bytes=100-",
        "bytes=5-1",
        "bytes=0-1,2-3",
        "items=0-5",
        "bytes= 0-1",
        "bytes=１２-９９",
    ],
)
def test_invalid_ranges(header):
    with pytest.raises(HTTPException) as error:
        byte_range(header, 100)
    assert error.value.status_code == 416


def test_s3_body_closed_on_client_disconnect():
    import io

    import anyio
    from starlette.requests import ClientDisconnect

    from app.modules.videos.streaming import MediaResponse

    body = io.BytesIO(b"synthetic media")
    response = MediaResponse(iter([b"synthetic media"]), s3_body=body)

    async def receive():
        return {"type": "http.disconnect"}

    async def send(message):
        if message["type"] == "http.response.body":
            raise OSError("Synthetic disconnect")

    async def run():
        with pytest.raises(ClientDisconnect):
            await response({"type": "http", "asgi": {"spec_version": "2.4"}}, receive, send)

    anyio.run(run)
    assert body.closed
