from fastapi import APIRouter

from app.api.v1 import health, literature, workspace

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(health.router)
api_router.include_router(workspace.router)
api_router.include_router(literature.router)
