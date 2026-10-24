"""ORM models. Importing this package registers every table on `Base.metadata` (needed by Alembic)."""

from app.models.entities import Entity, EntityEdge, ProjectEntity
from app.models.evidence import Chunk, Document, IngestionJob, ProjectSource
from app.models.literature import LiteratureSearch, Source, SourceIdentifier
from app.models.retrieval import ChunkEmbedding, Claim, ClaimEvidence, Evaluation, RunEvidence
from app.models.runs import Run
from app.models.workspace import Conversation, Message, Project

__all__ = [
    "Chunk",
    "ChunkEmbedding",
    "Claim",
    "ClaimEvidence",
    "Conversation",
    "Document",
    "Entity",
    "EntityEdge",
    "Evaluation",
    "IngestionJob",
    "LiteratureSearch",
    "Message",
    "Project",
    "ProjectEntity",
    "ProjectSource",
    "Run",
    "RunEvidence",
    "Source",
    "SourceIdentifier",
]
