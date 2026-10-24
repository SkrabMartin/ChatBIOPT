import uuid
from collections import defaultdict

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import ConflictError, NotFoundError
from app.core.logging import trace_id_var
from app.ingestion.chunking import CHUNKER_VERSION
from app.models import Chunk, Document, IngestionJob, Project, ProjectSource, Source, SourceIdentifier
from app.models.evidence import ACTIVE_INGESTION_STATUSES
from app.schemas.evidence import (
    AddSourceRequest,
    AddSourceResponse,
    ChunkRead,
    ChunkWithProvenance,
    DocumentRead,
    DocumentSummary,
    IngestionJobRead,
    Library,
    LibraryItem,
    SectionRead,
    SourceSummary,
)
from app.storage.object_store import ObjectStore


def source_summary(source: Source, identifiers: dict[str, str]) -> SourceSummary:
    record = source.record
    return SourceSummary(
        id=source.id,
        source_type=source.source_type,
        external_id=source.external_id,
        title=source.title,
        url=source.url,
        doi=identifiers.get("doi"),
        pmcid=identifiers.get("pmcid"),
        journal=record.get("journal"),
        published_year=record.get("published_year"),
    )


class EvidenceService:
    def __init__(self, session: AsyncSession, store: ObjectStore, settings: Settings) -> None:
        self.session = session
        self.store = store
        self.settings = settings

    async def _project(self, project_id: uuid.UUID) -> None:
        if await self.session.get(Project, project_id) is None:
            raise NotFoundError("Project not found")

    async def add_source(self, project_id: uuid.UUID, data: AddSourceRequest) -> AddSourceResponse:
        """Queue ingestion of a publication into the project's library. Idempotent while a job is active."""
        await self._project(project_id)
        known = await self.session.scalar(
            select(Source.id).where(Source.source_type == data.source, Source.external_id == data.identifier)
        )
        if known is not None:
            await self.session.execute(
                insert(ProjectSource).values(project_id=project_id, source_id=known).on_conflict_do_nothing()
            )
            await self.session.commit()  # keep the link even if the job insert below hits the active-job index
        return await self._enqueue(project_id, data.source, data.identifier, known)

    async def refresh_source(self, project_id: uuid.UUID, source_id: uuid.UUID) -> AddSourceResponse:
        link = await self.session.get(ProjectSource, (project_id, source_id))
        if link is None:
            raise NotFoundError("Source is not in this project")
        source = await self.session.get(Source, source_id)
        assert source is not None
        return await self._enqueue(project_id, source.source_type, source.external_id, source_id)

    async def _enqueue(
        self, project_id: uuid.UUID, source_type: str, external_id: str, source_id: uuid.UUID | None
    ) -> AddSourceResponse:
        job = IngestionJob(
            project_id=project_id,
            source_type=source_type,
            external_id=external_id,
            source_id=source_id,
            status="queued",
            max_attempts=self.settings.ingestion_max_attempts,
            steps=[],
            trace_id=trace_id_var.get(),
        )
        self.session.add(job)
        try:
            await self.session.commit()
            return AddSourceResponse(job=IngestionJobRead.model_validate(job), created=True)
        except IntegrityError:
            await self.session.rollback()
        active = await self.session.scalar(
            select(IngestionJob).where(
                IngestionJob.project_id == project_id,
                IngestionJob.source_type == source_type,
                IngestionJob.external_id == external_id,
                IngestionJob.status.in_(ACTIVE_INGESTION_STATUSES),
            )
        )
        if active is None:  # finished between our insert and lookup; the caller can simply retry
            raise ConflictError("Ingestion state changed concurrently; retry")
        return AddSourceResponse(job=IngestionJobRead.model_validate(active), created=False)

    async def remove_source(self, project_id: uuid.UUID, source_id: uuid.UUID) -> None:
        """Unlink from the project. Documents are shared public data and stay (citations may reference them)."""
        result = await self.session.execute(
            delete(ProjectSource)
            .where(ProjectSource.project_id == project_id, ProjectSource.source_id == source_id)
            .returning(ProjectSource.source_id)
        )
        if result.first() is None:
            raise NotFoundError("Source is not in this project")
        await self.session.commit()

    async def library(self, project_id: uuid.UUID) -> Library:
        await self._project(project_id)
        rows = (
            await self.session.execute(
                select(ProjectSource, Source)
                .join(Source, Source.id == ProjectSource.source_id)
                .where(ProjectSource.project_id == project_id)
                .order_by(ProjectSource.added_at.desc())
            )
        ).all()
        source_ids = [source.id for _, source in rows]
        identifiers = await self._identifiers(source_ids)
        latest_docs = await self._latest_documents(source_ids)
        chunk_counts = await self._chunk_counts([d.id for docs in latest_docs.values() for d in docs])
        jobs = (
            await self.session.scalars(
                select(IngestionJob)
                .where(IngestionJob.project_id == project_id)
                .order_by(IngestionJob.created_at.desc())
            )
        ).all()
        latest_job: dict[str, IngestionJob] = {}
        for job in jobs:
            latest_job.setdefault(job.external_id, job)

        items = [
            LibraryItem(
                source=source_summary(source, identifiers[source.id]),
                added_at=link.added_at,
                documents=[DocumentSummary.model_validate(d) for d in latest_docs[source.id]],
                chunk_counts={str(d.id): chunk_counts.get(d.id, 0) for d in latest_docs[source.id]},
                latest_job=(
                    IngestionJobRead.model_validate(latest_job[source.external_id])
                    if source.external_id in latest_job
                    else None
                ),
            )
            for link, source in rows
        ]
        linked = {source.external_id for _, source in rows}
        pending = [IngestionJobRead.model_validate(j) for j in latest_job.values() if j.external_id not in linked]
        return Library(items=items, pending_jobs=pending)

    async def job(self, job_id: uuid.UUID) -> IngestionJobRead:
        job = await self.session.get(IngestionJob, job_id)
        if job is None:
            raise NotFoundError("Ingestion job not found")
        return IngestionJobRead.model_validate(job)

    async def source_documents(self, source_id: uuid.UUID) -> list[DocumentSummary]:
        if await self.session.get(Source, source_id) is None:
            raise NotFoundError("Source not found")
        documents = await self.session.scalars(
            select(Document).where(Document.source_id == source_id).order_by(Document.kind, Document.version.desc())
        )
        return [DocumentSummary.model_validate(d) for d in documents]

    async def document(self, document_id: uuid.UUID) -> DocumentRead:
        document = await self.session.get(Document, document_id)
        if document is None:
            raise NotFoundError("Document not found")
        source = await self.session.get(Source, document.source_id)
        assert source is not None
        chunks = await self.session.scalars(
            select(Chunk)
            .where(Chunk.document_id == document_id, Chunk.chunker_version == CHUNKER_VERSION)
            .order_by(Chunk.ordinal)
        )
        latest = await self.session.scalar(
            select(func.max(Document.version)).where(
                Document.source_id == document.source_id, Document.kind == document.kind
            )
        )
        return DocumentRead(
            **DocumentSummary.model_validate(document).model_dump(),
            text=self.store.get(document.text_storage_key).decode(),
            sections=[SectionRead.model_validate(s) for s in document.sections],
            chunker_version=CHUNKER_VERSION,
            chunks=[ChunkRead.model_validate(c) for c in chunks],
            is_latest=document.version == latest,
            source=source_summary(source, (await self._identifiers([source.id]))[source.id]),
        )

    async def chunk(self, chunk_id: uuid.UUID) -> ChunkWithProvenance:
        chunk = await self.session.get(Chunk, chunk_id)
        if chunk is None:
            raise NotFoundError("Chunk not found")
        document = await self.session.get(Document, chunk.document_id)
        assert document is not None
        source = await self.session.get(Source, document.source_id)
        assert source is not None
        return ChunkWithProvenance(
            **ChunkRead.model_validate(chunk).model_dump(),
            document=DocumentSummary.model_validate(document),
            source=source_summary(source, (await self._identifiers([source.id]))[source.id]),
        )

    async def _identifiers(self, source_ids: list[uuid.UUID]) -> dict[uuid.UUID, dict[str, str]]:
        found: dict[uuid.UUID, dict[str, str]] = defaultdict(dict)
        if source_ids:
            for row in await self.session.scalars(
                select(SourceIdentifier).where(SourceIdentifier.source_id.in_(source_ids))
            ):
                found[row.source_id][row.scheme] = row.value
        return found

    async def _latest_documents(self, source_ids: list[uuid.UUID]) -> dict[uuid.UUID, list[Document]]:
        latest: dict[uuid.UUID, list[Document]] = defaultdict(list)
        if not source_ids:
            return latest
        ranked = (
            select(
                Document.id,
                func.row_number()
                .over(partition_by=(Document.source_id, Document.kind), order_by=Document.version.desc())
                .label("rank"),
            )
            .where(Document.source_id.in_(source_ids))
            .subquery()
        )
        documents = await self.session.scalars(
            select(Document).join(ranked, ranked.c.id == Document.id).where(ranked.c.rank == 1).order_by(Document.kind)
        )
        for document in documents:
            latest[document.source_id].append(document)
        return latest

    async def _chunk_counts(self, document_ids: list[uuid.UUID]) -> dict[uuid.UUID, int]:
        if not document_ids:
            return {}
        rows = await self.session.execute(
            select(Chunk.document_id, func.count(Chunk.id))
            .where(Chunk.document_id.in_(document_ids), Chunk.chunker_version == CHUNKER_VERSION)
            .group_by(Chunk.document_id)
        )
        return {document_id: count for document_id, count in rows}
