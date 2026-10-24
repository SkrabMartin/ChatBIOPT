import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.gateway.base import GatewayError
from app.ai.gateway.router import ModelRouter
from app.api.v1.runs import get_model_router
from app.core.errors import AppError, NotFoundError
from app.db.session import get_session
from app.models import Project
from app.retrieval.embeddings import EmbeddingSpace, resolve_space
from app.retrieval.search import retrieve
from app.schemas.retrieval import (
    ClaimRead,
    ClaimReview,
    RetrievalHitRead,
    RetrievalQuery,
    RetrievalResponse,
    ReviewStatus,
)
from app.services.claims import ClaimService, claims_csv

router = APIRouter()

Session = Annotated[AsyncSession, Depends(get_session)]


@router.post("/retrieval/query", response_model=RetrievalResponse, tags=["retrieval"])
async def retrieval_query(
    data: RetrievalQuery, session: Session, models: Annotated[ModelRouter, Depends(get_model_router)]
) -> RetrievalResponse:
    """Evidence passages from one project's library. Degraded modes (no embedding model) are reported in
    `warnings`, never silently."""
    if await session.get(Project, data.project_id) is None:
        raise NotFoundError("Project not found")
    space: EmbeddingSpace | None = None
    space_error: str | None = None
    if data.mode != "keyword":
        try:
            space = await resolve_space(models)
        except (GatewayError, AppError) as exc:
            space_error = str(exc)
    try:
        result = await retrieve(
            session, data.project_id, data.query, mode=data.mode, top_k=data.top_k, space=space, space_error=space_error
        )
    except GatewayError as exc:
        raise AppError(f"Embedding the query failed: {exc.message}") from exc
    min_similarity = space.profile.min_similarity if space else None
    return RetrievalResponse(
        query=data.query,
        mode=result.mode,
        retrieval_version=result.version,
        embedding_space=result.embedding_space,
        min_similarity=min_similarity,
        latency_ms=result.latency_ms,
        warnings=result.warnings,
        hits=[
            RetrievalHitRead(
                rank=rank,
                chunk_id=hit.chunk_id,
                text=hit.text,
                section=hit.section,
                section_title=hit.section_title,
                start_offset=hit.start_offset,
                end_offset=hit.end_offset,
                document_id=hit.document_id,
                document_kind=hit.document_kind,
                document_version=hit.document_version,
                source_id=hit.source_id,
                external_id=hit.external_id,
                source_title=hit.source_title,
                source_url=hit.source_url,
                published_year=hit.published_year,
                journal=hit.journal,
                keyword_rank=hit.keyword_rank,
                keyword_score=hit.keyword_score,
                keyword_coverage=hit.keyword_coverage,
                semantic_rank=hit.semantic_rank,
                similarity=hit.similarity,
                fused_score=hit.fused_score,
                counts_as_evidence=hit.is_evidence(min_similarity),
            )
            for rank, hit in enumerate(result.hits, start=1)
        ],
    )


@router.get(
    "/projects/{project_id}/claims",
    response_model=list[ClaimRead],
    tags=["evidence board"],
    responses={200: {"content": {"text/csv": {}}}},
)
async def list_claims(
    project_id: uuid.UUID,
    session: Session,
    review_status: ReviewStatus | None = None,
    format: Annotated[Literal["json", "csv"], Query(description="csv: one row per claim/passage")] = "json",
) -> list[ClaimRead] | Response:
    """Evidence board: every sentence of grounded answers with the passages it cites and its review status."""
    claims = await ClaimService(session).list_for_project(project_id, review_status)
    if format == "csv":
        return Response(
            claims_csv(claims),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="evidence-board-{project_id}.csv"'},
        )
    return claims


@router.patch("/claims/{claim_id}", response_model=ClaimRead, tags=["evidence board"])
async def review_claim(claim_id: uuid.UUID, data: ClaimReview, session: Session) -> ClaimRead:
    """Record a human judgement of whether the cited passages support the claim."""
    return await ClaimService(session).review(claim_id, data)
