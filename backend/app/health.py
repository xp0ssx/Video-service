import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/health", tags=["health"])


@router.get("/live")
def live() -> dict[str, str]:
    """Process liveness; deliberately independent of PostgreSQL and S3."""
    return {"status": "ok"}


@router.get("/ready")
def ready(request: Request) -> JSONResponse:
    checks = {}
    for name, dependency in (
        ("database", request.app.state.database),
        ("storage", request.app.state.storage),
    ):
        try:
            dependency.check_ready()
            checks[name] = "ok"
        except Exception:
            # Driver exception messages may contain DSNs, keys or request headers.
            logger.warning("Readiness dependency failed: %s", name)
            checks[name] = "error"
    healthy = all(value == "ok" for value in checks.values())
    return JSONResponse(
        status_code=200 if healthy else 503,
        content={"status": "ok" if healthy else "not_ready", "checks": checks},
    )
