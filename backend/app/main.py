from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api import router as api_router
from app.core.config import Settings, get_settings
from app.db.session import Database
from app.health import router as health_router
from app.infrastructure.storage import Storage


def create_app(settings: Settings | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(application: FastAPI):
        configuration = settings or get_settings()
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
    application.include_router(health_router)
    application.include_router(api_router)
    return application


app = create_app()
