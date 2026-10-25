from typing import Annotated, Any

from fastapi import APIRouter, Depends, Path, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import rate_limit
from app.db.session import get_session
from app.integrations.pubmed.client import PubMedClient
from app.schemas.literature import (
    PMID_PATTERN,
    LiteratureRecord,
    LiteratureSearchRequest,
    LiteratureSearchResponse,
    LiteratureSource,
)
from app.services.literature import LiteratureService

router = APIRouter(prefix="/literature", tags=["literature"])


def get_pubmed_client(request: Request) -> PubMedClient:
    client: PubMedClient = request.app.state.pubmed
    return client


def literature_service(
    session: Annotated[AsyncSession, Depends(get_session)],
    pubmed: Annotated[PubMedClient, Depends(get_pubmed_client)],
) -> LiteratureService:
    return LiteratureService(session, pubmed)


Literature = Annotated[LiteratureService, Depends(literature_service)]

_UPSTREAM_ERRORS: dict[int | str, dict[str, Any]] = {
    502: {"description": "Provider unavailable or returned an invalid response"},
    504: {"description": "Provider timed out"},
}


@router.post(
    "/search",
    response_model=LiteratureSearchResponse,
    responses=_UPSTREAM_ERRORS,
    dependencies=[Depends(rate_limit("provider"))],
)
async def search_literature(data: LiteratureSearchRequest, literature: Literature) -> LiteratureSearchResponse:
    """Read-only PubMed search. Every returned record is persisted with its raw payload and checksum."""
    return await literature.search(data)


@router.get(
    "/{source}/{identifier}",
    response_model=LiteratureRecord,
    responses=_UPSTREAM_ERRORS,
    dependencies=[Depends(rate_limit("provider"))],
)
async def get_literature_record(
    source: LiteratureSource,
    identifier: Annotated[str, Path(pattern=PMID_PATTERN.pattern, description="PubMed ID")],
    literature: Literature,
    refresh: Annotated[bool, Query(description="Re-fetch from the provider instead of the stored snapshot")] = False,
) -> LiteratureRecord:
    return await literature.get(source, identifier, refresh=refresh)
