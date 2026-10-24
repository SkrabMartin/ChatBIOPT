import uuid
from datetime import UTC, datetime

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError
from app.integrations.pubmed.client import FetchedRecord, PubMedClient
from app.models import LiteratureSearch, Source, SourceIdentifier
from app.schemas.literature import (
    LiteratureRecord,
    LiteratureSearchRequest,
    LiteratureSearchResponse,
    LiteratureSource,
)


class LiteratureService:
    """Read-only literature lookup. Records shown to users are exactly those returned by the provider and
    persisted here with their raw payload; nothing is synthesized."""

    def __init__(self, session: AsyncSession, pubmed: PubMedClient) -> None:
        self.session = session
        self.pubmed = pubmed

    async def search(self, request: LiteratureSearchRequest) -> LiteratureSearchResponse:
        hits = await self.pubmed.search(request.query, limit=request.limit, offset=request.offset)
        fetched = await self.pubmed.fetch(hits.pmids)
        retrieved_at = fetched[0].record.retrieved_at if fetched else datetime.now(UTC)

        for item in fetched:
            await upsert_source(self.session, item)
        search = LiteratureSearch(
            source_type="pubmed",
            query=request.query,
            query_translation=hits.query_translation,
            limit=request.limit,
            offset=request.offset,
            total_count=hits.total_count,
            result_external_ids=[item.record.source_id for item in fetched],
            retrieved_at=retrieved_at,
        )
        self.session.add(search)
        await self.session.commit()

        return LiteratureSearchResponse(
            search_id=search.id,
            source="pubmed",
            query=request.query,
            query_translation=hits.query_translation,
            total_count=hits.total_count,
            limit=request.limit,
            offset=request.offset,
            retrieved_at=retrieved_at,
            items=[item.record for item in fetched],
        )

    async def get(self, source: LiteratureSource, identifier: str, *, refresh: bool) -> LiteratureRecord:
        if not refresh:
            stored = await self.session.scalar(
                select(Source.record).where(Source.source_type == source, Source.external_id == identifier)
            )
            if stored is not None:
                return LiteratureRecord.model_validate(stored)

        fetched = await self.pubmed.fetch([identifier])
        if not fetched:
            raise NotFoundError(f"PubMed has no record with PMID {identifier}")
        await upsert_source(self.session, fetched[0])
        await self.session.commit()
        return fetched[0].record


async def upsert_source(session: AsyncSession, item: FetchedRecord) -> uuid.UUID:
    """Insert or refresh the snapshot of a PubMed record and its identifiers. Caller commits."""
    record = item.record
    values = {
        "source_type": record.source,
        "external_id": record.source_id,
        "url": record.source_url,
        "title": record.title,
        "record": record.model_dump(mode="json"),
        "raw_payload": item.raw_payload,
        "raw_payload_sha256": record.raw_payload_sha256,
        "normalizer_version": record.normalizer_version,
        "retrieved_at": record.retrieved_at,
    }
    statement = insert(Source).values(**values, first_retrieved_at=record.retrieved_at)
    # Latest snapshot wins; first_retrieved_at keeps when we first saw the record.
    upsert = statement.on_conflict_do_update(index_elements=[Source.source_type, Source.external_id], set_=values)
    source_id: uuid.UUID = (await session.execute(upsert.returning(Source.id))).scalar_one()
    await session.execute(delete(SourceIdentifier).where(SourceIdentifier.source_id == source_id))
    identifiers = {"pmid": record.pmid, "doi": record.doi, "pmcid": record.pmcid}
    session.add_all(
        SourceIdentifier(source_id=source_id, scheme=scheme, value=value)
        for scheme, value in identifiers.items()
        if value is not None
    )
    await session.flush()
    return source_id
