import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import rate_limit, requires, requires_library
from app.core.config import Settings, get_settings
from app.db.session import get_session
from app.schemas.evidence import (
    AddSourceRequest,
    AddSourceResponse,
    ChunkWithProvenance,
    DocumentRead,
    DocumentSummary,
    IngestionJobRead,
    Library,
)
from app.services.evidence import EvidenceService
from app.storage.object_store import ObjectStore

router = APIRouter(tags=["evidence"])


def get_object_store(request: Request) -> ObjectStore:
    store: ObjectStore = request.app.state.object_store
    return store


def evidence_service(
    session: Annotated[AsyncSession, Depends(get_session)],
    store: Annotated[ObjectStore, Depends(get_object_store)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> EvidenceService:
    return EvidenceService(session, store, settings)


Evidence = Annotated[EvidenceService, Depends(evidence_service)]


@router.get(
    "/projects/{project_id}/sources", response_model=Library, dependencies=[Depends(requires("project_id", "viewer"))]
)
async def get_library(project_id: uuid.UUID, evidence: Evidence) -> Library:
    """The project's evidence library: sources, their latest document versions and ingestion status."""
    return await evidence.library(project_id)


@router.post(
    "/projects/{project_id}/sources",
    response_model=AddSourceResponse,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(requires("project_id", "editor")), Depends(rate_limit("provider"))],
)
async def add_source(project_id: uuid.UUID, data: AddSourceRequest, evidence: Evidence) -> AddSourceResponse:
    """Queue ingestion (metadata, abstract, license-gated open-access full text, chunks). Processed by the worker."""
    return await evidence.add_source(project_id, data)


@router.post(
    "/projects/{project_id}/sources/{source_id}/refresh",
    response_model=AddSourceResponse,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(requires("project_id", "editor")), Depends(rate_limit("provider"))],
)
async def refresh_source(project_id: uuid.UUID, source_id: uuid.UUID, evidence: Evidence) -> AddSourceResponse:
    """Re-fetch from the providers; a new document version is created only if the text changed."""
    return await evidence.refresh_source(project_id, source_id)


@router.delete(
    "/projects/{project_id}/sources/{source_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(requires("project_id", "editor"))],
)
async def remove_source(project_id: uuid.UUID, source_id: uuid.UUID, evidence: Evidence) -> Response:
    await evidence.remove_source(project_id, source_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/ingestion-jobs/{job_id}", response_model=IngestionJobRead, dependencies=[Depends(requires("job_id", "viewer"))]
)
async def get_ingestion_job(job_id: uuid.UUID, evidence: Evidence) -> IngestionJobRead:
    return await evidence.job(job_id)


@router.get(
    "/sources/{source_id}/documents",
    response_model=list[DocumentSummary],
    dependencies=[Depends(requires_library("source_id"))],
)
async def list_source_documents(source_id: uuid.UUID, evidence: Evidence) -> list[DocumentSummary]:
    """All stored versions (newest first per kind). Old versions remain addressable."""
    return await evidence.source_documents(source_id)


@router.get(
    "/documents/{document_id}", response_model=DocumentRead, dependencies=[Depends(requires_library("document_id"))]
)
async def get_document(document_id: uuid.UUID, evidence: Evidence) -> DocumentRead:
    return await evidence.document(document_id)


@router.get(
    "/chunks/{chunk_id}", response_model=ChunkWithProvenance, dependencies=[Depends(requires_library("chunk_id"))]
)
async def get_chunk(chunk_id: uuid.UUID, evidence: Evidence) -> ChunkWithProvenance:
    return await evidence.chunk(chunk_id)
