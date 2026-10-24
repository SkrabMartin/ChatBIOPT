"""Entity resolution: a gene mention or identifier, within an explicit organism, to NCBI Gene candidates.

Rules (each is covered by tests/test_entities.py):
- The organism is never inferred. A symbol is searched only within the requested taxon.
- Identifiers (NCBI Gene ID, Ensembl gene ID, HGNC ID, UniProt accession) are looked up by authoritative
  cross-reference, without an organism filter, then compared with the requested organism: an identifier of another
  species is `taxon_mismatch`, never silently mapped to an ortholog.
- A symbol shared by several genes is `ambiguous` with all candidates; nothing is picked on the user's behalf.
- Replaced NCBI Gene IDs follow `currentid` (flagged); discontinued ones are not resolved.
"""

import re
from dataclasses import dataclass, field
from typing import Literal, get_args

from app.integrations.ensembl.client import GENE_ID_PATTERN as ENSEMBL_GENE_PATTERN
from app.integrations.ensembl.client import PROVIDER as ENSEMBL
from app.integrations.ensembl.client import EnsemblClient
from app.integrations.ncbigene.client import GENE_ID_PATTERN as NCBI_GENE_ID
from app.integrations.ncbigene.client import PROVIDER as NCBI_GENE
from app.integrations.ncbigene.client import GeneSummary, NCBIGeneClient
from app.integrations.uniprot.client import ACCESSION_PATTERN as UNIPROT_ACCESSION_PATTERN
from app.integrations.uniprot.client import PROVIDER as UNIPROT
from app.integrations.uniprot.client import InactiveEntry, UniProtClient

RESOLVER_VERSION = "entity-resolver-v1"
HUMAN = 9606


@dataclass(frozen=True)
class Organism:
    taxon_id: int
    scientific_name: str
    common_name: str


# Organisms offered in the UI and searched for "this symbol exists elsewhere" hints. Any NCBI taxon ID is accepted.
ORGANISMS = (
    Organism(9606, "Homo sapiens", "human"),
    Organism(10090, "Mus musculus", "house mouse"),
    Organism(10116, "Rattus norvegicus", "Norway rat"),
    Organism(7955, "Danio rerio", "zebrafish"),
    Organism(7227, "Drosophila melanogaster", "fruit fly"),
    Organism(6239, "Caenorhabditis elegans", "nematode"),
    Organism(559292, "Saccharomyces cerevisiae S288C", "baker's yeast"),
    Organism(3702, "Arabidopsis thaliana", "thale cress"),
)

# Symbols as written in nomenclature and literature: letters (incl. Greek), digits, and . - _ / @ : ' ( ) or spaces.
# Quotes and brackets are excluded, so a validated symbol can be quoted into an Entrez query.
SYMBOL_PATTERN = re.compile(r"\w[\w .\-/@:'()]{0,63}")
NCBI_GENE_PATTERN = re.compile(NCBI_GENE_ID)
HGNC_PATTERN = re.compile(r"HGNC:(\d{1,7})", re.IGNORECASE)

InputKind = Literal["symbol", "ncbigene", "ensembl", "hgnc", "uniprot"]
Status = Literal["resolved", "ambiguous", "not_found", "taxon_mismatch"]
MatchType = Literal["official_symbol", "alias", "identifier", "replaced_identifier", "cross_reference"]

_PATTERNS: dict[InputKind, re.Pattern[str]] = {
    "symbol": SYMBOL_PATTERN,
    "ncbigene": NCBI_GENE_PATTERN,
    "ensembl": ENSEMBL_GENE_PATTERN,
    "hgnc": HGNC_PATTERN,
    "uniprot": UNIPROT_ACCESSION_PATTERN,
}


@dataclass(frozen=True)
class Candidate:
    gene: GeneSummary
    match_type: MatchType


@dataclass(frozen=True)
class Elsewhere:
    """The input names something in another organism (and not, or not only, in the requested one)."""

    namespace: str  # ncbigene | uniprot
    identifier: str
    label: str
    taxon_id: int
    organism: str


@dataclass(frozen=True)
class ProviderVersion:
    provider: str
    release: str | None


@dataclass
class Resolution:
    query: str
    taxon_id: int
    input_kind: InputKind
    status: Status = "not_found"
    candidates: list[Candidate] = field(default_factory=list)
    found_elsewhere: list[Elsewhere] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    providers: list[ProviderVersion] = field(default_factory=list)


def classify(query: str) -> InputKind:
    """Identifier shapes first; anything else is a symbol. Pass an explicit kind to override."""
    for kind in ("ncbigene", "ensembl", "hgnc", "uniprot"):
        if _PATTERNS[kind].fullmatch(query):
            return kind
    return "symbol"


def validate_input(query: str, kind: InputKind | None) -> InputKind:
    """The effective input kind; ValueError if the query does not have that kind's shape."""
    resolved = kind or classify(query)
    if not _PATTERNS[resolved].fullmatch(query):
        raise ValueError(f"not a valid {resolved} input")
    return resolved


assert set(_PATTERNS) == set(get_args(InputKind))


class EntityResolver:
    def __init__(self, ncbi: NCBIGeneClient, uniprot: UniProtClient, ensembl: EnsemblClient) -> None:
        self.ncbi = ncbi
        self.uniprot = uniprot
        self.ensembl = ensembl

    async def resolve(self, query: str, taxon_id: int, kind: InputKind | None = None) -> Resolution:
        query = query.strip()
        result = Resolution(query=query, taxon_id=taxon_id, input_kind=validate_input(query, kind))
        if result.input_kind == "symbol":
            await self._symbol(result)
        elif result.input_kind == "ncbigene":
            await self._gene_ids(result, [query], "identifier")
        elif result.input_kind == "ensembl":
            await self._ensembl(result)
        elif result.input_kind == "hgnc":
            await self._hgnc(result)
        else:
            await self._uniprot(result)
        result.providers.insert(0, ProviderVersion(NCBI_GENE, await self.ncbi.database_build()))
        return result

    async def _symbol(self, result: Resolution) -> None:
        genes = await self.ncbi.summaries(await self.ncbi.search_symbol(result.query, [result.taxon_id]))
        wanted = result.query.casefold()
        # NCBI's [sym] field also matches partial designations; only exact symbol or alias matches count.
        official = [g for g in genes if g.symbol.casefold() == wanted]
        aliases = [g for g in genes if g not in official and wanted in {a.casefold() for a in g.aliases}]
        result.candidates = [Candidate(g, "official_symbol") for g in official]
        result.candidates += [Candidate(g, "alias") for g in aliases]
        if len(official) == 1:
            result.status = "resolved"
            if aliases:
                others = ", ".join(f"{g.symbol} (NCBI Gene {g.gene_id})" for g in aliases)
                result.warnings.append(f"“{result.query}” is also an alias of {others}.")
        elif len(official) > 1 or len(aliases) > 1:
            result.status = "ambiguous"
        elif len(aliases) == 1:
            result.status = "resolved"
            gene = aliases[0]
            result.warnings.append(
                f"“{result.query}” is an alias; the official symbol is {gene.symbol} (NCBI Gene {gene.gene_id})."
            )
        else:
            result.found_elsewhere = await self._other_organisms(result.query, result.taxon_id)

    async def _other_organisms(self, symbol: str, taxon_id: int) -> list[Elsewhere]:
        """Common organisms where `symbol` is the official symbol: a hint, never a resolution."""
        taxa = [o.taxon_id for o in ORGANISMS if o.taxon_id != taxon_id]
        genes = await self.ncbi.summaries(await self.ncbi.search_symbol(symbol, taxa, limit=20))
        return [
            Elsewhere("ncbigene", g.gene_id, g.symbol, g.taxon_id, g.organism)
            for g in genes
            if g.symbol.casefold() == symbol.casefold()
        ]

    async def _gene_ids(self, result: Resolution, gene_ids: list[str], match: MatchType) -> None:
        live: list[Candidate] = []
        for gene in await self.ncbi.summaries(gene_ids):
            if gene.status == "replaced" and gene.current_id:
                result.warnings.append(f"NCBI Gene {gene.gene_id} was replaced by {gene.current_id}.")
                live += [Candidate(g, "replaced_identifier") for g in await self.ncbi.summaries([gene.current_id])]
            elif gene.status == "live":
                live.append(Candidate(gene, match))
            else:
                result.warnings.append(f"NCBI Gene {gene.gene_id} ({gene.symbol}) is {gene.status}.")
        result.candidates = [c for c in live if c.gene.taxon_id == result.taxon_id]
        result.found_elsewhere = [
            Elsewhere("ncbigene", c.gene.gene_id, c.gene.symbol, c.gene.taxon_id, c.gene.organism)
            for c in live
            if c.gene.taxon_id != result.taxon_id
        ]
        if len(result.candidates) == 1:
            result.status = "resolved"
        elif result.candidates:
            result.status = "ambiguous"
        elif result.found_elsewhere:
            result.status = "taxon_mismatch"

    async def _ensembl(self, result: Resolution) -> None:
        match = ENSEMBL_GENE_PATTERN.fullmatch(result.query)
        assert match is not None
        if match.group(2):
            result.warnings.append(
                f"Version .{match.group(2)} not checked: Ensembl cross-references are per stable ID."
            )
        result.providers.append(ProviderVersion(ENSEMBL, await self.ensembl.release()))
        ids = await self.ensembl.ncbi_gene_ids(match.group(1))
        if ids is None:
            result.warnings.append(f"Ensembl does not know {match.group(1)}.")
        elif not ids:
            result.warnings.append(f"Ensembl has no NCBI Gene cross-reference for {match.group(1)}.")
        else:
            await self._gene_ids(result, ids, "cross_reference")

    async def _hgnc(self, result: Resolution) -> None:
        match = HGNC_PATTERN.fullmatch(result.query)
        assert match is not None
        number = int(match.group(1))
        hgnc_id = f"HGNC:{number}"
        if result.taxon_id != HUMAN:
            result.warnings.append("HGNC identifiers name human genes only.")
        search = await self.uniprot.by_hgnc(number)
        result.providers.append(ProviderVersion(UNIPROT, search.release))
        gene_ids = sorted({g for e in search.entries if hgnc_id in e.hgnc_ids for g in e.gene_ids}, key=int)
        if gene_ids:
            await self._gene_ids(result, gene_ids, "cross_reference")
        else:
            result.warnings.append(f"No reviewed UniProtKB entry cross-references {hgnc_id}.")

    async def _uniprot(self, result: Resolution) -> None:
        accession, _, isoform = result.query.upper().partition("-")
        if isoform:
            result.warnings.append(f"Isoform -{isoform} not distinguished: genes are resolved from the entry.")
        search = await self.uniprot.by_accession(accession)
        result.providers.append(ProviderVersion(UNIPROT, search.release))
        entry = next((e for e in search.entries if e.accession == accession), None)
        inactive = next((i for i in search.inactive if i.accession == accession), None)
        if entry is None and inactive is not None:
            await self._inactive_uniprot(result, inactive)
        elif entry is None:
            result.warnings.append(f"UniProtKB has no entry {accession}.")
        elif entry.gene_ids:
            await self._gene_ids(result, entry.gene_ids, "cross_reference")
        else:
            result.warnings.append(f"UniProtKB {accession} has no NCBI Gene cross-reference.")
            if entry.taxon_id != result.taxon_id:
                result.status = "taxon_mismatch"
                result.found_elsewhere = [
                    Elsewhere("uniprot", accession, entry.entry_name, entry.taxon_id, entry.organism)
                ]

    async def _inactive_uniprot(self, result: Resolution, inactive: InactiveEntry) -> None:
        """Deleted entries are not resolved; merged/demerged ones are followed once to their successors."""
        reason = inactive.reason.lower()
        if not inactive.replaced_by:
            detail = f": {inactive.deleted_reason}" if inactive.deleted_reason else ""
            result.warnings.append(f"UniProtKB {inactive.accession} is inactive ({reason}{detail}).")
            return
        successors = inactive.replaced_by[:5]
        result.warnings.append(f"UniProtKB {inactive.accession} was {reason} into {', '.join(successors)}.")
        gene_ids: set[str] = set()
        for accession in successors:
            search = await self.uniprot.by_accession(accession)
            gene_ids.update(g for e in search.entries if e.accession == accession for g in e.gene_ids)
        if gene_ids:
            await self._gene_ids(result, sorted(gene_ids, key=int), "replaced_identifier")
