from fastapi import APIRouter

from app.modules.admin.router import router as admin_router
from app.modules.auth.router import router as auth_router
from app.modules.interactions.router import router as interactions_router
from app.modules.videos.router import router as videos_router

router = APIRouter(prefix="/api")
router.include_router(auth_router)
router.include_router(admin_router)

router.include_router(videos_router)

router.include_router(interactions_router)
