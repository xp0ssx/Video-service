import anyio
from fastapi.responses import StreamingResponse
from starlette.concurrency import run_in_threadpool


class MediaResponse(StreamingResponse):
    """Release the S3 socket even when the ASGI send fails or the task is cancelled."""

    def __init__(self, *args, s3_body, **kwargs):
        self.s3_body = s3_body
        super().__init__(*args, **kwargs)

    async def __call__(self, scope, receive, send):
        try:
            await super().__call__(scope, receive, send)
        finally:
            with anyio.CancelScope(shield=True):
                await run_in_threadpool(self.s3_body.close)
