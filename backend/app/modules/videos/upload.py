import asyncio
import hashlib
import shutil
import tempfile
import threading
from pathlib import Path

import anyio
from fastapi import HTTPException
from starlette.concurrency import run_in_threadpool

from app.worker.cleanup import Cleanup

SLOTS = threading.BoundedSemaphore(2)


async def receive(request, service, user_id, video_id):
    settings = request.app.state.settings
    storage = request.app.state.storage
    if request.headers.get("content-type", "").split(";")[0] != "application/octet-stream":
        raise HTTPException(415, "Ожидается application/octet-stream")
    if not SLOTS.acquire(blocking=False):
        raise HTTPException(429, "Заняты все слоты загрузки")
    token, temporary, output = None, None, None
    code = "upload_interrupted"
    try:
        root = Path(settings.scratch_dir)
        await run_in_threadpool(root.mkdir, parents=True, exist_ok=True)
        if (
            await run_in_threadpool(shutil.disk_usage, root)
        ).free < settings.scratch_min_free_bytes:
            raise HTTPException(503, "Недостаточно места для загрузки")
        await run_in_threadpool(Cleanup(service.factory, settings, storage).scratch)
        token = await run_in_threadpool(service.reserve, user_id, video_id)
        temporary = await run_in_threadpool(
            tempfile.TemporaryDirectory, prefix=f"upload-{token}-", dir=root
        )
        path = Path(temporary.name) / "source"
        size, sha = 0, hashlib.sha256()
        async with asyncio.timeout(900):
            output = await run_in_threadpool(path.open, "wb")
            async for chunk in request.stream():
                size += len(chunk)
                if size > settings.max_upload_bytes:
                    code = "file_too_large"
                    raise HTTPException(413, "Файл превышает допустимый размер")
                for offset in range(0, len(chunk), 1024 * 1024):
                    part = chunk[offset : offset + 1024 * 1024]
                    await run_in_threadpool(output.write, part)
                    sha.update(part)
            await run_in_threadpool(output.close)
            output = None
            if not size:
                code = "empty_file"
                raise HTTPException(422, "Пустой файл")
            key = f"sources/{video_id}/{token}/original"
            code = "storage_unavailable"
            await run_in_threadpool(storage.upload, path, key, "application/octet-stream")
            head = await run_in_threadpool(storage.head, key)
            if head["ContentLength"] != size:
                raise HTTPException(503, "Хранилище не подтвердило размер файла")
            return await run_in_threadpool(
                service.accepted, user_id, video_id, token, key, size, sha.hexdigest()
            )
    except HTTPException:
        raise
    except TimeoutError:
        code = "upload_timeout"
        raise HTTPException(408, "Время загрузки истекло") from None
    except Exception:
        raise HTTPException(503, "Загрузка прервана; проверьте статус в Studio") from None
    finally:
        try:
            with anyio.CancelScope(shield=True):
                if output:
                    await run_in_threadpool(output.close)
                if temporary:
                    await run_in_threadpool(temporary.cleanup)
                if token:
                    try:
                        await run_in_threadpool(
                            service.upload_failed, user_id, video_id, token, code
                        )
                    except Exception:
                        # A database outage leaves the reservation for expiry recovery.
                        pass
        finally:
            SLOTS.release()
