"""Project-scoped passage retrieval: lexical (PostgreSQL FTS), semantic (pgvector) and hybrid (RRF).

Scope: only the latest document version of each source linked to the project, using the full text when one
exists (its abstract section makes the separate abstract document redundant). A project never sees another
project's library, through either index.

Fusion uses Reciprocal Rank Fusion on ranks; raw BM25-style and cosine scores are not comparable and are never
mixed directly.
"""

import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Literal, Protocol

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.gateway.base import GatewayError
from app.ingestion.chunking import CHUNKER_VERSION
from app.retrieval.embeddings import EmbeddingSpace, embed_query

RETRIEVAL_VERSION = "hybrid-rrf-v1"
RRF_K = 60
CANDIDATES_PER_RETRIEVER = 50
# A keyword hit matching a single common word ("role", "effect") is not evidence on its own.
MIN_KEYWORD_COVERAGE = 0.5

Mode = Literal["keyword", "semantic", "hybrid"]

_ELIGIBLE = """
WITH latest AS (
    SELECT DISTINCT ON (d.source_id, d.kind) d.id, d.source_id, d.kind
    FROM documents d
    JOIN project_sources ps ON ps.source_id = d.source_id AND ps.project_id = :project_id
    ORDER BY d.source_id, d.kind, d.version DESC
),
eligible AS (
    SELECT l.id FROM latest l
    WHERE l.kind = 'fulltext'
       OR NOT EXISTS (SELECT 1 FROM latest f WHERE f.source_id = l.source_id AND f.kind = 'fulltext')
)
"""

_KEYWORD = text(
    _ELIGIBLE  # noqa: S608 - concatenation of module constants; all user input is bound as parameters
    + """,
parts AS (
    SELECT DISTINCT t FROM (SELECT plainto_tsquery('english', w) AS t FROM unnest(CAST(:words AS text[])) AS w) p
    WHERE t::text <> ''
),
query AS (SELECT string_agg('(' || t::text || ')', ' | ')::tsquery AS q, count(*) AS n FROM parts)
SELECT c.id, ts_rank_cd(c.search_vector, query.q) AS score,
       (SELECT count(*) FROM parts WHERE c.search_vector @@ parts.t)::float / query.n AS coverage
FROM chunks c, query
WHERE c.document_id IN (SELECT id FROM eligible)
  AND c.chunker_version = :chunker_version
  AND c.search_vector @@ query.q
ORDER BY score DESC, c.id
LIMIT :limit
"""
)

_SEMANTIC = text(
    _ELIGIBLE  # noqa: S608 - concatenation of module constants; all user input is bound as parameters
    + """
SELECT c.id, 1 - (e.embedding <=> CAST(:vector AS vector)) AS similarity
FROM chunk_embeddings e
JOIN chunks c ON c.id = e.chunk_id
WHERE e.embedding_space = :space
  AND c.document_id IN (SELECT id FROM eligible)
  AND c.chunker_version = :chunker_version
ORDER BY e.embedding <=> CAST(:vector AS vector), c.id
LIMIT :limit
"""
)

_DETAILS = text(
    """
SELECT c.id AS chunk_id, c.text, c.section, c.section_title, c.start_offset, c.end_offset,
       d.id AS document_id, d.kind AS document_kind, d.version AS document_version,
       s.id AS source_id, s.external_id, s.title AS source_title, s.url AS source_url,
       (s.record ->> 'published_year')::int AS published_year, s.record ->> 'journal' AS journal
FROM chunks c
JOIN documents d ON d.id = c.document_id
JOIN sources s ON s.id = d.source_id
WHERE c.id = ANY(CAST(:ids AS uuid[]))
"""
)

_WORD = re.compile(r"[\w][\w.+-]*")


@dataclass
class Hit:
    chunk_id: uuid.UUID
    keyword_rank: int | None = None
    keyword_score: float | None = None
    # Share of the query's (stemmed, stop-word-free) terms that occur in the chunk.
    keyword_coverage: float | None = None
    semantic_rank: int | None = None
    similarity: float | None = None
    fused_score: float = 0.0
    # 0-3 relevance grade from the reranker (None: not reranked or ungraded).
    rerank_score: int | None = None
    # Filled by `_details`.
    text: str = ""
    section: str = ""
    section_title: str | None = None
    start_offset: int = 0
    end_offset: int = 0
    document_id: uuid.UUID | None = None
    document_kind: str = ""
    document_version: int = 0
    source_id: uuid.UUID | None = None
    external_id: str = ""
    source_title: str = ""
    source_url: str = ""
    published_year: int | None = None
    journal: str | None = None

    def is_evidence(self, min_similarity: float | None) -> bool:
        """A lexical match counts when it covers at least half of the query's terms (exact identifiers always
        do); otherwise the semantic similarity must reach the embedding profile's calibrated threshold."""
        if self.keyword_coverage is not None and self.keyword_coverage >= MIN_KEYWORD_COVERAGE:
            return True
        return self.similarity is not None and min_similarity is not None and self.similarity >= min_similarity


class Reranker(Protocol):
    """Re-sorts the top candidates; implemented by app.retrieval.rerank.LLMReranker."""

    version: str
    max_candidates: int

    async def __call__(self, question: str, hits: list[Hit]) -> list[Hit]: ...


@dataclass
class RetrievalResult:
    mode: Mode
    version: str
    embedding_space: str | None
    hits: list[Hit]
    latency_ms: int
    # e.g. "semantic retrieval unavailable: ..." when hybrid degraded to keyword-only. Never silent.
    warnings: list[str] = field(default_factory=list)
    reranker: str | None = None
    rerank_ms: int | None = None


def rrf(rankings: list[list[uuid.UUID]], k: int = RRF_K) -> dict[uuid.UUID, float]:
    scores: dict[uuid.UUID, float] = {}
    for ranking in rankings:
        for rank, chunk_id in enumerate(ranking, start=1):
            scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.0 / (k + rank)
    return scores


async def retrieve(
    session: AsyncSession,
    project_id: uuid.UUID,
    query: str,
    *,
    mode: Mode,
    top_k: int,
    space: EmbeddingSpace | None,
    space_error: str | None = None,
    reranker: Reranker | None = None,
) -> RetrievalResult:
    """`space` is required for semantic/hybrid; pass `space_error` when it could not be resolved so the degraded
    mode is reported rather than hidden."""
    started = time.perf_counter()
    warnings: list[str] = []
    hits: dict[uuid.UUID, Hit] = {}
    keyword_ranking: list[uuid.UUID] = []
    semantic_ranking: list[uuid.UUID] = []

    if mode in ("keyword", "hybrid"):
        words = _WORD.findall(query)
        rows = (
            await session.execute(
                _KEYWORD,
                {
                    "project_id": project_id,
                    "words": words,
                    "chunker_version": CHUNKER_VERSION,
                    "limit": CANDIDATES_PER_RETRIEVER,
                },
            )
        ).all()
        for rank, (chunk_id, score, coverage) in enumerate(rows, start=1):
            hit = hits.setdefault(chunk_id, Hit(chunk_id))
            hit.keyword_rank, hit.keyword_score, hit.keyword_coverage = rank, float(score), float(coverage)
            keyword_ranking.append(chunk_id)

    effective_mode: Mode = mode
    if mode in ("semantic", "hybrid"):
        if space is None:
            warnings.append(f"Semantic retrieval unavailable: {space_error or 'no embedding model'}")
            effective_mode = "keyword" if mode == "hybrid" else mode
        else:
            vector = await embed_query(space, query)
            rows = (
                await session.execute(
                    _SEMANTIC,
                    {
                        "project_id": project_id,
                        "space": space.name,
                        "vector": "[" + ",".join(f"{v:.8g}" for v in vector) + "]",
                        "chunker_version": CHUNKER_VERSION,
                        "limit": CANDIDATES_PER_RETRIEVER,
                    },
                )
            ).all()
            for rank, (chunk_id, similarity) in enumerate(rows, start=1):
                hit = hits.setdefault(chunk_id, Hit(chunk_id))
                hit.semantic_rank, hit.similarity = rank, float(similarity)
                semantic_ranking.append(chunk_id)

    fused = rrf([r for r in (keyword_ranking, semantic_ranking) if r])
    for chunk_id, score in fused.items():
        hits[chunk_id].fused_score = score
    ordered = sorted(hits.values(), key=lambda h: (-h.fused_score, str(h.chunk_id)))
    version = RETRIEVAL_VERSION if effective_mode == "hybrid" else f"{effective_mode}-v1"
    rerank_ms: int | None = None
    applied: str | None = None
    if reranker is not None and len(ordered) > 1:
        candidates = ordered[: max(top_k, reranker.max_candidates)]
        await _details(session, candidates)
        rerank_started = time.perf_counter()
        try:
            ordered = await reranker(query, candidates)
            applied = reranker.version
            version = f"{version}+{reranker.version}"
        except GatewayError as exc:
            # Fused order is still a valid ranking; say that reranking did not happen.
            warnings.append(f"Reranking skipped: {exc.message}")
            ordered = candidates
        rerank_ms = round((time.perf_counter() - rerank_started) * 1000)
    ordered = ordered[:top_k]
    await _details(session, [h for h in ordered if not h.text])
    return RetrievalResult(
        mode=effective_mode,
        version=version,
        embedding_space=space.name if space and effective_mode != "keyword" else None,
        hits=ordered,
        latency_ms=round((time.perf_counter() - started) * 1000),
        warnings=warnings,
        reranker=applied,
        rerank_ms=rerank_ms,
    )


async def _details(session: AsyncSession, hits: list[Hit]) -> None:
    if not hits:
        return
    rows = (await session.execute(_DETAILS, {"ids": [h.chunk_id for h in hits]})).mappings().all()
    by_id = {row["chunk_id"]: row for row in rows}
    for hit in hits:
        row = by_id[hit.chunk_id]
        for key in (
            "text",
            "section",
            "section_title",
            "start_offset",
            "end_offset",
            "document_id",
            "document_kind",
            "document_version",
            "source_id",
            "external_id",
            "source_title",
            "source_url",
            "published_year",
            "journal",
        ):
            setattr(hit, key, row[key])
