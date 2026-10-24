import csv
import io
import uuid
from collections import defaultdict
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError
from app.models import Chunk, Claim, ClaimEvidence, Conversation, Document, Message, Project, Run, Source
from app.schemas.retrieval import ClaimEvidenceRead, ClaimRead, ClaimReview, ReviewStatus


class ClaimService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_for_project(self, project_id: uuid.UUID, review_status: ReviewStatus | None) -> list[ClaimRead]:
        if await self.session.get(Project, project_id) is None:
            raise NotFoundError("Project not found")
        query = (
            select(Claim, Run, Conversation)
            .join(Run, Run.id == Claim.run_id)
            .join(Conversation, Conversation.id == Run.conversation_id)
            .where(Claim.project_id == project_id)
            .order_by(Claim.created_at.desc(), Claim.ordinal)
        )
        if review_status is not None:
            query = query.where(Claim.review_status == review_status)
        rows = (await self.session.execute(query)).all()
        return await self._read([(claim, run, conversation) for claim, run, conversation in rows])

    async def review(self, claim_id: uuid.UUID, data: ClaimReview) -> ClaimRead:
        claim = await self.session.get(Claim, claim_id)
        if claim is None:
            raise NotFoundError("Claim not found")
        claim.review_status = data.review_status
        claim.review_note = data.review_note
        claim.reviewed_at = None if data.review_status == "requires_review" else datetime.now(UTC)
        await self.session.commit()
        run = await self.session.get(Run, claim.run_id)
        assert run is not None
        conversation = await self.session.get(Conversation, run.conversation_id)
        assert conversation is not None
        return (await self._read([(claim, run, conversation)]))[0]

    async def _read(self, rows: list[tuple[Claim, Run, Conversation]]) -> list[ClaimRead]:
        claim_ids = [claim.id for claim, _, _ in rows]
        evidence: dict[uuid.UUID, list[ClaimEvidenceRead]] = defaultdict(list)
        if claim_ids:
            for link, chunk, document, source in (
                await self.session.execute(
                    select(ClaimEvidence, Chunk, Document, Source)
                    .join(Chunk, Chunk.id == ClaimEvidence.chunk_id)
                    .join(Document, Document.id == Chunk.document_id)
                    .join(Source, Source.id == Document.source_id)
                    .where(ClaimEvidence.claim_id.in_(claim_ids))
                    .order_by(ClaimEvidence.label)
                )
            ).all():
                evidence[link.claim_id].append(
                    ClaimEvidenceRead(
                        label=link.label,
                        stance=link.stance,
                        chunk_id=chunk.id,
                        text=chunk.text,
                        section=chunk.section,
                        section_title=chunk.section_title,
                        document_id=document.id,
                        document_kind=document.kind,
                        document_version=document.version,
                        external_id=source.external_id,
                        source_title=source.title,
                        published_year=source.record.get("published_year"),
                    )
                )
        questions = await self._questions([run for _, run, _ in rows])
        return [
            ClaimRead(
                id=claim.id,
                project_id=claim.project_id,
                run_id=claim.run_id,
                message_id=claim.message_id,
                conversation_id=conversation.id,
                conversation_title=conversation.title,
                question=questions.get(run.id),
                ordinal=claim.ordinal,
                claim_text=claim.claim_text,
                support_state="cited" if claim.support_state == "cited" else "uncited",
                review_status=_status(claim.review_status),
                review_note=claim.review_note,
                answer_class=run.answer_class,
                model_id=run.model_id,
                extraction_version=claim.extraction_version,
                created_at=claim.created_at,
                reviewed_at=claim.reviewed_at,
                evidence=evidence[claim.id],
            )
            for claim, run, conversation in rows
        ]

    async def _questions(self, runs: list[Run]) -> dict[uuid.UUID, str]:
        """The user message each run answered."""
        found: dict[uuid.UUID, str] = {}
        for run in {r.id: r for r in runs}.values():
            content = await self.session.scalar(
                select(Message.content).where(
                    Message.conversation_id == run.conversation_id, Message.sequence == run.context_until_sequence
                )
            )
            if content is not None:
                found[run.id] = content
        return found


def _status(value: str) -> ReviewStatus:
    if value in ("requires_review", "verified", "disputed", "rejected"):
        return value  # type: ignore[return-value]
    raise ValueError(f"unknown review status {value!r}")


def claims_csv(claims: list[ClaimRead]) -> str:
    """Evidence board export: one row per (claim, cited passage); uncited claims get one row without a passage."""
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(
        [
            "claim_id",
            "claim",
            "support_state",
            "review_status",
            "review_note",
            "answer_class",
            "question",
            "label",
            "pmid",
            "source_title",
            "year",
            "section",
            "document_kind",
            "document_version",
            "chunk_id",
            "run_id",
            "model_id",
            "created_at",
        ]
    )
    for claim in claims:
        base = [claim.id, claim.claim_text, claim.support_state, claim.review_status, claim.review_note or ""]
        tail = [claim.run_id, claim.model_id, claim.created_at.isoformat()]
        passages: list[ClaimEvidenceRead | None] = list(claim.evidence) or [None]
        for ev in passages:
            passage = (
                [
                    ev.label,
                    ev.external_id,
                    ev.source_title,
                    ev.published_year or "",
                    ev.section_title or ev.section,
                    ev.document_kind,
                    ev.document_version,
                    ev.chunk_id,
                ]
                if ev
                else ["", "", "", "", "", "", "", ""]
            )
            writer.writerow([*base, claim.answer_class or "", claim.question or "", *passage, *tail])
    return buffer.getvalue()
