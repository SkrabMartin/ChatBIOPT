import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError
from app.db.session import get_session
from app.models import Evaluation

router = APIRouter(prefix="/evaluations", tags=["evaluations"])

Session = Annotated[AsyncSession, Depends(get_session)]


class EvaluationSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    kind: Literal["retrieval", "answers", "entities"]
    dataset: str
    dataset_version: str
    dataset_sha256: str
    split: Literal["dev", "test"]
    git_commit: str | None
    summary: dict[str, dict[str, float]]
    created_at: datetime


class EvaluationRead(EvaluationSummary):
    report: dict[str, Any]


@router.get("", response_model=list[EvaluationSummary])
async def list_evaluations(
    session: Session, kind: Literal["retrieval", "answers", "entities"] | None = None
) -> list[EvaluationSummary]:
    """Recorded evaluation runs, newest first. Runs are produced by the CLI runners (python -m app.evals.*)."""
    query = select(Evaluation).order_by(Evaluation.created_at.desc()).limit(200)
    if kind:
        query = query.where(Evaluation.kind == kind)
    return [EvaluationSummary.model_validate(e) for e in await session.scalars(query)]


@router.get("/{evaluation_id}", response_model=EvaluationRead)
async def get_evaluation(evaluation_id: uuid.UUID, session: Session) -> EvaluationRead:
    evaluation = await session.get(Evaluation, evaluation_id)
    if evaluation is None:
        raise NotFoundError("Evaluation not found")
    return EvaluationRead.model_validate(evaluation)
