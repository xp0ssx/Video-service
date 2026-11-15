import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

from app.api import router as api_router
from app.core.config import Settings, get_settings
from app.db.session import Database
from app.health import router as health_router
from app.infrastructure.storage import Storage


def create_app(settings: Settings | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(application: FastAPI):
        configuration = settings or get_settings()
        application.state.settings = configuration
        database = Database(configuration)
        storage = Storage(configuration)
        application.state.database = database
        application.state.storage = storage
        try:
            yield
        finally:
            storage.close()
            database.close()

    application = FastAPI(
        title="Video-service API",
        version="0.1.0",
        description="Базовый API Video-service. Предметные функции добавляются отдельно.",
        lifespan=lifespan,
    )

    @application.middleware("http")
    async def private_auth(request: Request, call_next):
        request.state.request_id = uuid.uuid4().hex
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        if request.url.path.startswith(("/api/",)):
            response.headers["Cache-Control"] = "private, no-store"
        return response

    @application.exception_handler(HTTPException)
    async def http_error(request, error):
        return JSONResponse(
            status_code=error.status_code,
            content={
                "code": f"http_{error.status_code}",
                "message": str(error.detail),
                "request_id": request.state.request_id,
            },
            headers=error.headers,
        )

    @application.exception_handler(RequestValidationError)
    async def invalid_request(request, error):
        return JSONResponse(
            status_code=422,
            content={
                "code": "validation_error",
                "message": "Проверьте поля формы",
                "request_id": request.state.request_id,
            },
        )

    application.include_router(health_router)
    application.include_router(api_router)
    return application


app = create_app()
