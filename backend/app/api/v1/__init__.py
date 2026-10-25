from fastapi import APIRouter, Depends

from app.api.v1 import auth, entities, evaluations, evidence, health, literature, retrieval, runs, workspace
from app.auth.deps import current_actor

api_router = APIRouter(prefix="/api/v1")
# Public: readiness probe and sign-in only.
api_router.include_router(health.router)
api_router.include_router(auth.public_router)

# Everything else requires a session (and the CSRF header on state-changing methods). Project-scoped routes
# additionally declare the role they need (app.auth.deps.requires); tests/test_security.py audits every route.
protected = APIRouter(dependencies=[Depends(current_actor)])
protected.include_router(auth.router)
protected.include_router(auth.admin_router)
protected.include_router(workspace.router)
protected.include_router(literature.router)
protected.include_router(runs.router)
protected.include_router(evidence.router)
protected.include_router(retrieval.router)
protected.include_router(evaluations.router)
protected.include_router(entities.router)
api_router.include_router(protected)
