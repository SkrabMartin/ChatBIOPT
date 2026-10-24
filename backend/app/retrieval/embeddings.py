"""Embedding of chunks into a named embedding space."""

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.gateway.base import EmbeddingGateway, GatewayError
from app.ai.gateway.router import ModelRouter
from app.ai.registry import EmbeddingProfile
from app.models import Chunk, ChunkEmbedding


@dataclass(frozen=True)
class EmbeddingSpace:
    profile: EmbeddingProfile
    gateway: EmbeddingGateway
    # "<profile>@<revision>": a different model or model revision is a different space.
    name: str


async def resolve_space(router: ModelRouter, profile_id: str | None = None) -> EmbeddingSpace:
    """The current space of the (default) embedding profile; raises GatewayError if the model is unavailable."""
    profile = router.embedding_profile(profile_id)
    gateway = router.embedder(profile)
    availability = await gateway.availability()
    if not availability.available:
        raise GatewayError("model_unavailable", availability.reason or f"Embedding model {profile.model} unavailable")
    return EmbeddingSpace(profile, gateway, f"{profile.id}@{availability.revision or 'unversioned'}")


async def embed_query(space: EmbeddingSpace, query: str) -> list[float]:
    return (await space.gateway.embed([space.profile.query_prefix + query], space.profile.dimensions))[0]


async def embed_missing_chunks(session: AsyncSession, space: EmbeddingSpace, chunk_ids: list[uuid.UUID]) -> int:
    """Embed the given chunks that have no vector in `space` yet. Idempotent; caller commits. Returns count."""
    if not chunk_ids:
        return 0
    done = set(
        await session.scalars(
            select(ChunkEmbedding.chunk_id).where(
                ChunkEmbedding.embedding_space == space.name, ChunkEmbedding.chunk_id.in_(chunk_ids)
            )
        )
    )
    pending = (
        await session.execute(
            select(Chunk.id, Chunk.section_title, Chunk.text)
            .where(Chunk.id.in_([c for c in chunk_ids if c not in done]))
            .order_by(Chunk.document_id, Chunk.ordinal)
        )
    ).all()
    profile = space.profile
    for start in range(0, len(pending), profile.batch_size):
        batch = pending[start : start + profile.batch_size]
        texts = [profile.document_prefix + (f"{title}\n{text}" if title else text) for _, title, text in batch]
        vectors = await space.gateway.embed(texts, profile.dimensions)
        await session.execute(
            insert(ChunkEmbedding)
            .values(
                [
                    {
                        "chunk_id": chunk_id,
                        "embedding_space": space.name,
                        "profile_id": profile.id,
                        "dimensions": profile.dimensions,
                        "embedding": vector,
                    }
                    for (chunk_id, _, _), vector in zip(batch, vectors, strict=True)
                ]
            )
            .on_conflict_do_nothing()
        )
    return len(pending)
