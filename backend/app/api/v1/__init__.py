from fastapi import APIRouter

from app.api.v1 import evidence, health, literature, retrieval, runs, workspace

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(health.router)
api_router.include_router(workspace.router)
api_router.include_router(literature.router)
api_router.include_router(runs.router)
api_router.include_router(evidence.router)
api_router.include_router(retrieval.router)
