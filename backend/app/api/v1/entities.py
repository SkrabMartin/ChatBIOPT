import uuid
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import rate_limit, requires
from app.core.config import Settings, get_settings
from app.db.session import get_session
from app.entities.resolver import ORGANISMS, RESOLVER_VERSION
from app.entities.service import EntityProviders, EntityService
from app.models import Entity, ProjectEntity
from app.schemas.entities import (
    AddEntityRequest,
    CandidateRead,
    EdgeRead,
    ElsewhereRead,
    EntityDetail,
    EntityRead,
    EntitySummary,
    OrganismRead,
    ProjectEntityRead,
    ProviderVersionRead,
    ResolutionRead,
    ResolveRequest,
)

router = APIRouter(tags=["entities"])

_UPSTREAM_ERRORS: dict[int | str, dict[str, Any]] = {
    502: {"description": "Provider unavailable or returned an invalid response"},
    504: {"description": "Provider timed out"},
}


def entity_service(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> EntityService:
    providers: EntityProviders = request.app.state.entity_providers
    return EntityService(session, providers, settings)


Entities = Annotated[EntityService, Depends(entity_service)]


def _project_entity(link: ProjectEntity, entity: Entity) -> ProjectEntityRead:
    return ProjectEntityRead(
        entity=EntityRead.model_validate(entity),
        query=link.query,
        match_type=link.match_type,
        warnings=link.warnings,
        added_at=link.added_at,
    )


@router.get("/entities/organisms", response_model=list[OrganismRead])
async def list_organisms() -> list[OrganismRead]:
    """Organisms offered for resolution. Any NCBI Taxonomy ID is accepted; these are searched for hints."""
    return [OrganismRead(**o.__dict__) for o in ORGANISMS]


@router.post(
    "/entities/resolve",
    response_model=ResolutionRead,
    responses=_UPSTREAM_ERRORS,
    dependencies=[Depends(rate_limit("provider"))],
)
async def resolve_entity(data: ResolveRequest, entities: Entities) -> ResolutionRead:
    """Resolve a gene symbol, alias or identifier within one organism. Read-only: nothing is stored."""
    result = await entities.resolve(data.query, data.taxon_id, None if data.kind == "auto" else data.kind)
    return ResolutionRead(
        query=result.query,
        taxon_id=result.taxon_id,
        input_kind=result.input_kind,
        status=result.status,
        candidates=[
            CandidateRead(
                gene_id=c.gene.gene_id,
                symbol=c.gene.symbol,
                name=c.gene.name,
                taxon_id=c.gene.taxon_id,
                organism=c.gene.organism,
                match_type=c.match_type,
                aliases=c.gene.aliases,
                chromosome=c.gene.chromosome,
                map_location=c.gene.map_location,
                official_nomenclature=c.gene.official_nomenclature,
            )
            for c in result.candidates
        ],
        found_elsewhere=[ElsewhereRead(**e.__dict__) for e in result.found_elsewhere],
        warnings=result.warnings,
        providers=[ProviderVersionRead(**p.__dict__) for p in result.providers],
        resolver_version=RESOLVER_VERSION,
        retrieved_at=datetime.now(UTC),
    )


@router.get("/entities/{entity_id}", response_model=EntityDetail)
async def get_entity(entity_id: uuid.UUID, entities: Entities) -> EntityDetail:
    """A stored entity with its cross-references, provenance and typed edges.

    Entities are shared public reference data (NCBI Gene, UniProtKB, Open Targets): any signed-in user may read
    them. Which projects pinned an entity is not exposed here.
    """
    entity, edges = await entities.detail(entity_id)
    return EntityDetail(
        **EntityRead.model_validate(entity).model_dump(),
        edges=[
            EdgeRead(
                id=edge.id,
                predicate=edge.predicate,
                direction=direction,
                neighbor=EntitySummary.model_validate(neighbor),
                provider=edge.provider,
                provider_release=edge.provider_release,
                score=edge.score,
                evidence=edge.evidence,
                retrieved_at=edge.retrieved_at,
            )
            for edge, neighbor, direction in edges
        ],
    )


@router.get(
    "/projects/{project_id}/entities",
    response_model=list[ProjectEntityRead],
    dependencies=[Depends(requires("project_id", "viewer"))],
)
async def list_project_entities(project_id: uuid.UUID, entities: Entities) -> list[ProjectEntityRead]:
    return [_project_entity(link, entity) for link, entity in await entities.project_entities(project_id)]


@router.post(
    "/projects/{project_id}/entities",
    response_model=ProjectEntityRead,
    status_code=status.HTTP_201_CREATED,
    responses={**_UPSTREAM_ERRORS, 409: {"description": "Gene is not live or belongs to another organism"}},
    dependencies=[Depends(requires("project_id", "editor")), Depends(rate_limit("provider"))],
)
async def add_project_entity(project_id: uuid.UUID, data: AddEntityRequest, entities: Entities) -> ProjectEntityRead:
    """Import a gene (NCBI Gene, UniProtKB, Ensembl, Open Targets for human) and pin it to the project.

    Adding an already pinned gene refreshes its snapshot and relations.
    """
    imported = await entities.add_to_project(project_id, data.gene_id, data.taxon_id, data.query, data.match_type)
    link, entity = await entities.project_entity(project_id, imported.entity_id)
    return _project_entity(link, entity)


@router.delete(
    "/projects/{project_id}/entities/{entity_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(requires("project_id", "editor"))],
)
async def remove_project_entity(project_id: uuid.UUID, entity_id: uuid.UUID, entities: Entities) -> Response:
    """Unpin the gene from the project. The stored entity and its relations stay for other projects."""
    await entities.remove_from_project(project_id, entity_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
