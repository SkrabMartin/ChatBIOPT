"""Versioned document storage: idempotent on text checksum, append-only across versions."""

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ingestion.chunking import CHUNKER_VERSION, ChunkedDocument, Section, chunk_sections
from app.models import Chunk, Document, Source
from app.storage.object_store import ObjectStore, sha256_hex

DocumentKind = Literal["abstract", "fulltext"]


@dataclass(frozen=True)
class DocumentInput:
    kind: DocumentKind
    sections: list[Section]
    raw_payload: bytes
    raw_extension: str
    origin: str
    origin_url: str
    access: Literal["metadata", "open_access"]
    license: str | None
    license_url: str | None
    parser_version: str
    retrieved_at: datetime


@dataclass(frozen=True)
class StoredDocument:
    document: Document
    created: bool  # False: identical text already stored (no new version)
    chunks_created: int


async def store_document(
    session: AsyncSession, store: ObjectStore, source: Source, data: DocumentInput
) -> StoredDocument:
    """Persist `data` as the next version of (source, kind) unless the same text is already stored.

    The source row is locked for the duration so concurrent ingestions of the same publication (e.g. from two
    projects) cannot race on version numbers. Caller commits.
    """
    await session.execute(select(Source.id).where(Source.id == source.id).with_for_update())
    chunked = chunk_sections(data.sections)
    text = chunked.text.encode()
    text_sha = sha256_hex(text)

    existing = await session.scalar(
        select(Document).where(
            Document.source_id == source.id, Document.kind == data.kind, Document.text_sha256 == text_sha
        )
    )
    if existing is not None:
        # Same text; make sure chunks exist for the current chunker (it may be newer than the document).
        created = await _ensure_chunks(session, existing.id, chunked)
        return StoredDocument(existing, created=False, chunks_created=created)

    latest_version = await session.scalar(
        select(func.max(Document.version)).where(Document.source_id == source.id, Document.kind == data.kind)
    )
    base = f"{source.source_type}/{source.external_id}/{data.kind}"
    text_object = store.put(f"documents/{base}/{text_sha}.txt", text)
    raw_sha = sha256_hex(data.raw_payload)
    raw_object = store.put(f"raw/{base}/{raw_sha}.{data.raw_extension}", data.raw_payload)
    document = Document(
        source_id=source.id,
        kind=data.kind,
        version=(latest_version or 0) + 1,
        text_sha256=text_sha,
        text_length=len(chunked.text),
        text_storage_key=text_object.key,
        raw_storage_key=raw_object.key,
        raw_sha256=raw_sha,
        origin=data.origin,
        origin_url=data.origin_url,
        access=data.access,
        license=data.license,
        license_url=data.license_url,
        parser_version=data.parser_version,
        sections=[
            {"kind": s.kind, "title": s.title, "start_offset": s.start_offset, "end_offset": s.end_offset}
            for s in chunked.sections
        ],
        retrieved_at=data.retrieved_at,
    )
    session.add(document)
    await session.flush()
    created = await _ensure_chunks(session, document.id, chunked)
    return StoredDocument(document, created=True, chunks_created=created)


async def _ensure_chunks(session: AsyncSession, document_id: uuid.UUID, chunked: ChunkedDocument) -> int:
    have = await session.scalar(
        select(func.count(Chunk.id)).where(Chunk.document_id == document_id, Chunk.chunker_version == CHUNKER_VERSION)
    )
    if have:
        return 0
    session.add_all(
        Chunk(
            document_id=document_id,
            chunker_version=CHUNKER_VERSION,
            ordinal=c.ordinal,
            section=c.section,
            section_title=c.section_title,
            start_offset=c.start_offset,
            end_offset=c.end_offset,
            text=c.text,
            token_estimate=c.token_estimate,
        )
        for c in chunked.chunks
    )
    await session.flush()
    return len(chunked.chunks)
