"""EntityResolutionService: resolve mentions, import a gene with its typed relations, link genes to projects.

Import of one NCBI gene (always re-fetched, so a repeated import refreshes the snapshot):
1. NCBI Gene summary: must be live and belong to the requested organism.
2. UniProtKB reviewed entries cross-referencing the gene in that organism -> protein entities + `encodes` edges.
3. Ensembl gene IDs asserted by UniProt are checked reciprocally against Ensembl (Ensembl must map them back to the
   same NCBI Gene ID); unconfirmed IDs are kept but flagged and never used to query other providers.
4. Human genes only: Open Targets disease associations of the confirmed Ensembl ID -> disease entities +
   `associated_with_disease` edges with the association score. Open Targets failures degrade to a warning (the
   previous association snapshot is kept); NCBI Gene, UniProt and Ensembl failures abort the import.
"""

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import ConflictError, NotFoundError, UpstreamError
from app.entities.resolver import EntityResolver, InputKind, Resolution
from app.integrations.ensembl.client import GENE_ID_PATTERN as ENSEMBL_GENE_PATTERN
from app.integrations.ensembl.client import PROVIDER as ENSEMBL
from app.integrations.ensembl.client import EnsemblClient
from app.integrations.http import RateLimiter
from app.integrations.ncbigene.client import PROVIDER as NCBI_GENE
from app.integrations.ncbigene.client import GeneSummary, NCBIGeneClient
from app.integrations.opentargets.client import HUMAN_TAXON, OpenTargetsClient, TargetAssociations
from app.integrations.opentargets.client import PROVIDER as OPEN_TARGETS
from app.integrations.uniprot.client import PROVIDER as UNIPROT
from app.integrations.uniprot.client import ProteinEntry, UniProtClient
from app.models import Entity, EntityEdge, Project, ProjectEntity

ASSOCIATION_NOTE = (
    "Open Targets association score (0-1): aggregated evidence that the gene is associated with the disease. "
    "Not a measure of causality or of a variant's functional impact."
)


class TaxonMismatchError(ConflictError):
    code = "taxon_mismatch"


@dataclass(frozen=True)
class EntityProviders:
    ncbi: NCBIGeneClient
    uniprot: UniProtClient
    ensembl: EnsemblClient
    opentargets: OpenTargetsClient

    @classmethod
    def from_settings(cls, http: httpx.AsyncClient, settings: Settings, ncbi_limiter: RateLimiter) -> "EntityProviders":
        return cls(
            ncbi=NCBIGeneClient.from_settings(http, settings, ncbi_limiter),
            uniprot=UniProtClient.from_settings(http, settings),
            ensembl=EnsemblClient.from_settings(http, settings),
            opentargets=OpenTargetsClient.from_settings(http, settings),
        )

    @property
    def resolver(self) -> EntityResolver:
        return EntityResolver(self.ncbi, self.uniprot, self.ensembl)


@dataclass(frozen=True)
class GeneImport:
    entity_id: uuid.UUID
    warnings: list[str]


@dataclass(frozen=True)
class AssociationOutcome:
    status: dict[str, Any]  # stored on the gene as `attributes.disease_associations`
    replace: bool  # True: current data replaces the stored snapshot (with nothing if `target` is None)
    target: TargetAssociations | None = None


def _raw(payload: Any) -> tuple[str, str]:
    text = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return text, hashlib.sha256(text.encode()).hexdigest()


def _xref(namespace: str, identifier: str, source: str, release: str | None, **extra: Any) -> dict[str, Any]:
    return {"namespace": namespace, "id": identifier, "source": source, "release": release, **extra}


def disease_curie(opentargets_id: str) -> tuple[str, str]:
    """Open Targets ids (MONDO_0018875, Orphanet_524) -> (namespace, CURIE): ("mondo", "MONDO:0018875")."""
    prefix, sep, local = opentargets_id.partition("_")
    if not sep or not prefix or not local:
        return "opentargets", opentargets_id
    return prefix.lower()[:32], f"{prefix}:{local}"


class EntityService:
    def __init__(self, session: AsyncSession, providers: EntityProviders, settings: Settings) -> None:
        self.session = session
        self.providers = providers
        self.settings = settings

    async def resolve(self, query: str, taxon_id: int, kind: InputKind | None) -> Resolution:
        return await self.providers.resolver.resolve(query, taxon_id, kind)

    async def _upsert_entity(self, **values: Any) -> uuid.UUID:
        raw_payload, sha = _raw(values.pop("raw"))
        values |= {"raw_payload": raw_payload, "raw_payload_sha256": sha}
        statement = insert(Entity).values(id=uuid.uuid4(), **values)
        upsert = statement.on_conflict_do_update(
            index_elements=["namespace", "accession"],
            set_={k: statement.excluded[k] for k in values if k not in ("namespace", "accession")},
        ).returning(Entity.id)
        entity_id: uuid.UUID = (await self.session.execute(upsert)).scalar_one()
        return entity_id

    async def import_gene(self, gene_id: str, taxon_id: int) -> GeneImport:
        ncbi, uniprot, ensembl = self.providers.ncbi, self.providers.uniprot, self.providers.ensembl
        summaries = await ncbi.summaries([gene_id])
        if not summaries:
            raise NotFoundError(f"NCBI Gene has no record {gene_id}")
        gene = summaries[0]
        if gene.status != "live":
            replaced = f"; current ID is {gene.current_id}" if gene.current_id else ""
            raise ConflictError(f"NCBI Gene {gene_id} is {gene.status}{replaced}")
        if gene.taxon_id != taxon_id:
            raise TaxonMismatchError(
                f"NCBI Gene {gene_id} ({gene.symbol}) is from {gene.organism} (taxon {gene.taxon_id}), "
                f"not taxon {taxon_id}"
            )
        build = await ncbi.database_build()
        retrieved_at = datetime.now(UTC)
        warnings: list[str] = []

        search = await uniprot.reviewed_for_gene(gene_id, taxon_id)
        proteins = [e for e in search.entries if e.taxon_id == taxon_id and gene_id in e.gene_ids]
        for skipped in (e for e in search.entries if e not in proteins):
            warnings.append(
                f"UniProtKB {skipped.accession} skipped: organism or NCBI Gene cross-reference does not match."
            )
        if not proteins:
            warnings.append(f"No reviewed UniProtKB entry cross-references NCBI Gene {gene_id} in {gene.organism}.")

        ensembl_versions: dict[str, set[str]] = {}
        for entry in proteins:
            for value in entry.ensembl_genes:
                match = ENSEMBL_GENE_PATTERN.fullmatch(value)
                if match:
                    ensembl_versions.setdefault(match.group(1), set()).update(
                        [match.group(2)] if match.group(2) else []
                    )
        ensembl_release = await ensembl.release() if ensembl_versions else None
        confirmed: list[str] = []
        for stable_id in sorted(ensembl_versions):
            back = await ensembl.ncbi_gene_ids(stable_id)
            if back is not None and gene_id in back:
                confirmed.append(stable_id)
            else:
                warnings.append(
                    f"Ensembl {ensembl_release} does not map {stable_id} back to NCBI Gene {gene_id}; "
                    "the cross-reference is kept but not used."
                )

        xrefs = [
            _xref(
                "ensembl",
                stable_id,
                UNIPROT,
                search.release,
                versions=sorted(versions, key=int),
                reciprocal=stable_id in confirmed,
                checked_against=f"{ENSEMBL} {ensembl_release}",
            )
            for stable_id, versions in sorted(ensembl_versions.items())
        ]
        xrefs += [
            _xref("hgnc", hgnc, UNIPROT, search.release) for hgnc in sorted({h for p in proteins for h in p.hgnc_ids})
        ]
        xrefs += [_xref("uniprot", p.accession, UNIPROT, search.release) for p in proteins]
        xrefs += [_xref("omim", mim, NCBI_GENE, build) for mim in gene.mim]

        associations = await self._associations(gene, confirmed, warnings)
        gene_entity = await self._upsert_entity(
            kind="gene",
            namespace="ncbigene",
            accession=gene.gene_id,
            taxon_id=gene.taxon_id,
            organism=gene.organism,
            label=gene.symbol,
            name=gene.name or None,
            xrefs=xrefs,
            attributes={
                "aliases": gene.aliases,
                "chromosome": gene.chromosome,
                "map_location": gene.map_location,
                "official_nomenclature": gene.official_nomenclature,
                "status": gene.status,
                "disease_associations": associations.status,
            },
            provider=NCBI_GENE,
            provider_release=build,
            raw=gene.raw,
            retrieved_at=retrieved_at,
        )
        await self._replace_proteins(gene_entity, gene, proteins, search.release, retrieved_at)
        if associations.replace:
            await self._replace_associations(gene_entity, associations.target, retrieved_at)
        return GeneImport(entity_id=gene_entity, warnings=warnings)

    async def _associations(self, gene: GeneSummary, confirmed: list[str], warnings: list[str]) -> AssociationOutcome:
        """Current Open Targets associations; an outage or disabled setting keeps the previous snapshot."""
        limit = self.settings.entity_disease_associations
        if gene.taxon_id != HUMAN_TAXON:
            warnings.append(f"Open Targets covers human targets only: no disease associations for {gene.organism}.")
            return AssociationOutcome({"status": "not_covered"}, replace=True)
        if limit == 0:
            return AssociationOutcome({"status": "disabled"}, replace=False)
        if not confirmed:
            warnings.append("No reciprocally confirmed Ensembl gene ID: disease associations not retrieved.")
            return AssociationOutcome({"status": "no_target"}, replace=True)
        if len(confirmed) > 1:
            warnings.append(f"Several Ensembl gene IDs ({', '.join(confirmed)}); associations use {confirmed[0]}.")
        try:
            target = await self.providers.opentargets.associations(confirmed[0], limit)
        except UpstreamError as exc:
            warnings.append(f"Open Targets unavailable ({exc.message}): disease associations not refreshed.")
            return AssociationOutcome({"status": "unavailable", "target": confirmed[0]}, replace=False)
        if target is None:
            warnings.append(f"Open Targets has no target {confirmed[0]}.")
            return AssociationOutcome({"status": "no_target", "target": confirmed[0]}, replace=True)
        if target.symbol.casefold() != gene.symbol.casefold():
            warnings.append(
                f"Open Targets names {target.ensembl_id} {target.symbol}; NCBI Gene names it {gene.symbol}."
            )
        status = {
            "status": "stored",
            "target": target.ensembl_id,
            "total": target.total_associations,
            "stored": len(target.associations),
            "data_version": target.data_version,
            "api_version": target.api_version,
        }
        return AssociationOutcome(status, replace=True, target=target)

    async def _replace_proteins(
        self,
        gene_entity: uuid.UUID,
        gene: GeneSummary,
        proteins: list[ProteinEntry],
        release: str | None,
        retrieved_at: datetime,
    ) -> None:
        await self.session.execute(
            delete(EntityEdge).where(
                EntityEdge.source_entity_id == gene_entity,
                EntityEdge.predicate == "encodes",
                EntityEdge.provider == UNIPROT,
            )
        )
        for entry in proteins:
            protein = await self._upsert_entity(
                kind="protein",
                namespace="uniprot",
                accession=entry.accession,
                taxon_id=entry.taxon_id,
                organism=entry.organism,
                label=entry.entry_name or entry.accession,
                name=entry.protein_name,
                xrefs=[_xref("ncbigene", g, UNIPROT, release) for g in entry.gene_ids]
                + [_xref("hgnc", h, UNIPROT, release) for h in entry.hgnc_ids]
                + [_xref("ensembl", e, UNIPROT, release) for e in entry.ensembl_genes],
                attributes={
                    "reviewed": entry.reviewed,
                    "gene_symbols": entry.gene_symbols,
                    "gene_synonyms": entry.gene_synonyms,
                    "length": entry.length,
                    "sequence_version": entry.sequence_version,
                },
                provider=UNIPROT,
                provider_release=release,
                raw=entry.raw,
                retrieved_at=retrieved_at,
            )
            await self.session.execute(
                insert(EntityEdge)
                .values(
                    id=uuid.uuid4(),
                    source_entity_id=gene_entity,
                    predicate="encodes",
                    target_entity_id=protein,
                    provider=UNIPROT,
                    provider_release=release,
                    score=None,
                    evidence={"basis": f"UniProtKB {entry.accession} cross-references NCBI Gene {gene.gene_id}"},
                    retrieved_at=retrieved_at,
                )
                .on_conflict_do_nothing()  # a concurrent import of the same gene already wrote it
            )

    async def _replace_associations(
        self, gene_entity: uuid.UUID, target: TargetAssociations | None, retrieved_at: datetime
    ) -> None:
        await self.session.execute(
            delete(EntityEdge).where(
                EntityEdge.source_entity_id == gene_entity,
                EntityEdge.predicate == "associated_with_disease",
                EntityEdge.provider == OPEN_TARGETS,
            )
        )
        if target is None:
            return
        for rank, association in enumerate(target.associations, start=1):
            namespace, curie = disease_curie(association.disease_id)
            disease = await self._upsert_entity(
                kind="disease",
                namespace=namespace,
                accession=curie,
                taxon_id=None,
                organism=None,
                label=association.disease_name,
                name=None,
                xrefs=[_xref("opentargets", association.disease_id, OPEN_TARGETS, target.data_version)],
                attributes={},
                provider=OPEN_TARGETS,
                provider_release=target.data_version,
                raw={"id": association.disease_id, "name": association.disease_name},
                retrieved_at=retrieved_at,
            )
            await self.session.execute(
                insert(EntityEdge)
                .values(
                    id=uuid.uuid4(),
                    source_entity_id=gene_entity,
                    predicate="associated_with_disease",
                    target_entity_id=disease,
                    provider=OPEN_TARGETS,
                    provider_release=target.data_version,
                    score=association.score,
                    evidence={
                        "target": target.ensembl_id,
                        "rank": rank,
                        "total_associations": target.total_associations,
                        "datatype_scores": association.datatype_scores,
                        "note": ASSOCIATION_NOTE,
                    },
                    retrieved_at=retrieved_at,
                )
                .on_conflict_do_nothing()
            )

    async def add_to_project(
        self, project_id: uuid.UUID, gene_id: str, taxon_id: int, query: str | None, match_type: str | None
    ) -> GeneImport:
        if await self.session.get(Project, project_id) is None:
            raise NotFoundError("Project not found")
        imported = await self.import_gene(gene_id, taxon_id)
        statement = insert(ProjectEntity).values(
            project_id=project_id,
            entity_id=imported.entity_id,
            query=query,
            match_type=match_type,
            warnings=imported.warnings,
        )
        await self.session.execute(
            statement.on_conflict_do_update(
                index_elements=["project_id", "entity_id"],
                set_={"query": query, "match_type": match_type, "warnings": imported.warnings},
            )
        )
        await self.session.commit()
        return imported

    async def project_entities(self, project_id: uuid.UUID) -> list[tuple[ProjectEntity, Entity]]:
        if await self.session.get(Project, project_id) is None:
            raise NotFoundError("Project not found")
        rows = await self.session.execute(
            select(ProjectEntity, Entity)
            .join(Entity, Entity.id == ProjectEntity.entity_id)
            .where(ProjectEntity.project_id == project_id)
            .order_by(ProjectEntity.added_at.desc())
        )
        return [(link, entity) for link, entity in rows]

    async def project_entity(self, project_id: uuid.UUID, entity_id: uuid.UUID) -> tuple[ProjectEntity, Entity]:
        link = await self.session.get(ProjectEntity, (project_id, entity_id))
        entity = await self.session.get(Entity, entity_id)
        if link is None or entity is None:
            raise NotFoundError("Entity is not linked to this project")
        return link, entity

    async def remove_from_project(self, project_id: uuid.UUID, entity_id: uuid.UUID) -> None:
        link, _ = await self.project_entity(project_id, entity_id)
        await self.session.delete(link)
        await self.session.commit()

    async def detail(self, entity_id: uuid.UUID) -> tuple[Entity, list[tuple[EntityEdge, Entity, str]]]:
        """The entity and its edges as (edge, neighbour, direction "out" | "in")."""
        entity = await self.session.get(Entity, entity_id)
        if entity is None:
            raise NotFoundError("Entity not found")
        outgoing = await self.session.execute(
            select(EntityEdge, Entity)
            .join(Entity, Entity.id == EntityEdge.target_entity_id)
            .where(EntityEdge.source_entity_id == entity_id)
        )
        incoming = await self.session.execute(
            select(EntityEdge, Entity)
            .join(Entity, Entity.id == EntityEdge.source_entity_id)
            .where(EntityEdge.target_entity_id == entity_id)
        )
        edges = [(e, n, "out") for e, n in outgoing] + [(e, n, "in") for e, n in incoming]
        edges.sort(key=lambda t: (t[0].predicate, -(t[0].score or 0.0), t[1].label))
        return entity, edges
