"""M08 acceptance: species and identifier correctness of entity resolution, and typed edges with provenance.

Provider responses are recorded from the real services (fixtures/entities); see tests/fakes.py.
"""

from typing import Any

import httpx
import pytest
from httpx import AsyncClient

from tests.fakes import GENE_BUILD, UNIPROT_FIXTURE, FakeEntitySources, raises, status

HUMAN, MOUSE, FLY = 9606, 10090, 7227
UNIPROT_RELEASE = UNIPROT_FIXTURE["release"]


async def resolve(client: AsyncClient, query: str, taxon_id: int, **extra: Any) -> dict[str, Any]:
    response = await client.post("/api/v1/entities/resolve", json={"query": query, "taxon_id": taxon_id, **extra})
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def genes(result: dict[str, Any]) -> list[tuple[str, int, str]]:
    return [(c["gene_id"], c["taxon_id"], c["match_type"]) for c in result["candidates"]]


def elsewhere(result: dict[str, Any]) -> set[tuple[str, int]]:
    return {(e["identifier"], e["taxon_id"]) for e in result["found_elsewhere"]}


async def new_project(client: AsyncClient) -> str:
    return str((await client.post("/api/v1/projects", json={"title": "Entities"})).json()["id"])


async def add_gene(client: AsyncClient, project_id: str, gene_id: str, taxon_id: int) -> httpx.Response:
    return await client.post(
        f"/api/v1/projects/{project_id}/entities",
        json={"gene_id": gene_id, "taxon_id": taxon_id, "query": gene_id, "match_type": "identifier"},
    )


# --- resolution: the organism is part of the identity ---------------------------------------------------------


@pytest.mark.parametrize(
    ("query", "taxon_id", "expected"),
    [
        ("TP53", HUMAN, ("7157", HUMAN, "official_symbol")),
        ("Trp53", MOUSE, ("22059", MOUSE, "official_symbol")),
        # The same text in another organism is another gene: mouse Trp53, reached through its alias Tp53.
        ("TP53", MOUSE, ("22059", MOUSE, "alias")),
        ("apoe", HUMAN, ("348", HUMAN, "official_symbol")),
        ("Apoe", MOUSE, ("11816", MOUSE, "official_symbol")),
    ],
)
async def test_symbol_resolves_only_within_the_requested_organism(
    client: AsyncClient, entity_sources: FakeEntitySources, query: str, taxon_id: int, expected: tuple[str, int, str]
) -> None:
    result = await resolve(client, query, taxon_id)

    assert result["status"] == "resolved"
    assert genes(result) == [expected]
    # The organism is never widened or inferred: the only search is restricted to the requested taxon.
    terms = [r.url.params["term"] for r in entity_sources.calls("gene.esearch")]
    assert terms == [f'"{query}"[sym] AND ({taxon_id}[taxid]) AND alive[prop]']
    assert result["providers"][0] == {"provider": "NCBI Gene", "release": GENE_BUILD}


async def test_alias_match_names_the_official_symbol(client: AsyncClient) -> None:
    result = await resolve(client, "TP53", MOUSE)

    assert result["warnings"] == ["“TP53” is an alias; the official symbol is Trp53 (NCBI Gene 22059)."]


async def test_ambiguous_alias_returns_every_candidate_and_picks_none(client: AsyncClient) -> None:
    result = await resolve(client, "p40", HUMAN)

    assert result["status"] == "ambiguous"
    assert sorted(genes(result)) == [("5594", HUMAN, "alias"), ("8626", HUMAN, "alias")]  # MAPK1, TP63


async def test_symbol_absent_in_organism_is_not_found_with_hints_elsewhere(client: AsyncClient) -> None:
    result = await resolve(client, "APOE", FLY)

    assert result["status"] == "not_found"
    assert result["candidates"] == []
    assert elsewhere(result) == {("348", HUMAN), ("11816", MOUSE)}


@pytest.mark.parametrize(
    ("query", "input_kind", "warning"),
    [
        ("7157", "ncbigene", None),
        ("ENSG00000141510", "ensembl", None),
        ("ENSG00000141510.20", "ensembl", "Version .20 not checked: Ensembl cross-references are per stable ID."),
        ("HGNC:11998", "hgnc", None),
        ("hgnc:11998", "hgnc", None),
        ("P04637", "uniprot", None),
        ("P04637-2", "uniprot", "Isoform -2 not distinguished: genes are resolved from the entry."),
    ],
)
async def test_identifiers_resolve_through_cross_references_to_the_same_gene(
    client: AsyncClient, query: str, input_kind: str, warning: str | None
) -> None:
    result = await resolve(client, query, HUMAN)

    assert result["input_kind"] == input_kind
    assert result["status"] == "resolved"
    assert [(c["gene_id"], c["symbol"], c["taxon_id"]) for c in result["candidates"]] == [("7157", "TP53", HUMAN)]
    assert result["warnings"] == ([warning] if warning else [])


async def test_identifier_provenance_names_each_provider_release(client: AsyncClient) -> None:
    by_ensembl = await resolve(client, "ENSG00000141510", HUMAN)
    by_uniprot = await resolve(client, "P04637", HUMAN)

    assert by_ensembl["providers"] == [
        {"provider": "NCBI Gene", "release": GENE_BUILD},
        {"provider": "Ensembl", "release": "116"},
    ]
    assert by_uniprot["providers"][1] == {"provider": "UniProt", "release": UNIPROT_RELEASE}
    assert by_uniprot["resolver_version"] == "entity-resolver-v1"


@pytest.mark.parametrize(
    ("query", "taxon_id", "found_in"),
    [
        ("7157", MOUSE, ("7157", HUMAN)),  # human NCBI Gene ID asked for in mouse
        ("22059", HUMAN, ("22059", MOUSE)),
        ("ENSMUSG00000059552", HUMAN, ("22059", MOUSE)),  # mouse Ensembl ID asked for in human
        ("P02340", HUMAN, ("22059", MOUSE)),  # P53_MOUSE
        ("HGNC:11998", MOUSE, ("7157", HUMAN)),
    ],
)
async def test_identifier_of_another_species_is_a_mismatch_never_an_ortholog(
    client: AsyncClient, query: str, taxon_id: int, found_in: tuple[str, int]
) -> None:
    result = await resolve(client, query, taxon_id)

    assert result["status"] == "taxon_mismatch"
    assert result["candidates"] == []
    assert elsewhere(result) == {found_in}


async def test_replaced_gene_id_follows_current_id_and_says_so(client: AsyncClient) -> None:
    result = await resolve(client, "6248", HUMAN)  # RSC1A1, replaced by 84301 (DDI2)

    assert result["status"] == "resolved"
    assert genes(result) == [("84301", HUMAN, "replaced_identifier")]
    assert result["warnings"] == ["NCBI Gene 6248 was replaced by 84301."]


async def test_discontinued_gene_id_is_not_resolved(client: AsyncClient) -> None:
    result = await resolve(client, "102724970", HUMAN)

    assert result["status"] == "not_found"
    assert result["warnings"] == ["NCBI Gene 102724970 (LOC102724970) is discontinued."]


@pytest.mark.parametrize(
    ("query", "warning"),
    [
        ("ENSG00000999999", "Ensembl does not know ENSG00000999999."),
        ("Q00000", "UniProtKB has no entry Q00000."),
        ("Q9ZZZ9", "UniProtKB Q9ZZZ9 is inactive (deleted: Not part of a reference proteome)."),
        ("999999999", None),
        ("NOTAGENE1", None),
    ],
)
async def test_unknown_inputs_are_not_found_rather_than_errors(
    client: AsyncClient, query: str, warning: str | None
) -> None:
    result = await resolve(client, query, HUMAN)

    assert result["status"] == "not_found"
    assert result["candidates"] == []
    assert result["warnings"] == ([warning] if warning else [])


async def test_merged_uniprot_accession_follows_its_successor_and_says_so(
    client: AsyncClient, entity_sources: FakeEntitySources
) -> None:
    # Synthetic record shaped per UniProt's OpenAPI schema (EntryInactiveReason); no live merged example was found.
    entity_sources.uniprot_inactive.append(
        {
            "entryType": "Inactive",
            "primaryAccession": "P0XXX1",
            "inactiveReason": {"inactiveReasonType": "MERGED", "mergeDemergeTos": ["P04637"]},
        }
    )

    result = await resolve(client, "P0XXX1", HUMAN)

    assert result["status"] == "resolved"
    assert genes(result) == [("7157", HUMAN, "replaced_identifier")]
    assert result["warnings"] == ["UniProtKB P0XXX1 was merged into P04637."]


async def test_explicit_kind_overrides_identifier_detection(
    client: AsyncClient, entity_sources: FakeEntitySources
) -> None:
    result = await resolve(client, "7157", HUMAN, kind="symbol")

    assert result["input_kind"] == "symbol"
    assert result["status"] == "not_found"
    assert len(entity_sources.calls("gene.esearch")) == 2  # requested organism, then the hint search


@pytest.mark.parametrize(
    "body",
    [
        {"query": 'TP53"[sym] OR "x', "taxon_id": HUMAN},  # cannot break out of the quoted Entrez term
        {"query": "TP53", "taxon_id": HUMAN, "kind": "ncbigene"},
        {"query": "ENSG123", "taxon_id": HUMAN, "kind": "ensembl"},
        {"query": "   ", "taxon_id": HUMAN},
        {"query": "TP53", "taxon_id": 0},
        {"query": "TP53"},
    ],
)
async def test_invalid_input_is_rejected_before_any_provider_call(
    client: AsyncClient, entity_sources: FakeEntitySources, body: dict[str, Any]
) -> None:
    response = await client.post("/api/v1/entities/resolve", json=body)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"
    assert entity_sources.requests == []


async def test_provider_outage_is_an_error_not_an_empty_result(
    client: AsyncClient, entity_sources: FakeEntitySources
) -> None:
    entity_sources.queue("gene.esearch", *[status(503)] * 3)
    entity_sources.queue("ensembl", raises(httpx.ConnectTimeout("slow")), *[status(500)] * 3)

    symbol = await client.post("/api/v1/entities/resolve", json={"query": "TP53", "taxon_id": HUMAN})
    ensembl = await client.post("/api/v1/entities/resolve", json={"query": "ENSG00000141510", "taxon_id": HUMAN})

    assert symbol.status_code == 502
    assert symbol.json()["error"]["code"] == "upstream_unavailable"
    assert ensembl.status_code == 502


async def test_transient_provider_failure_is_retried(client: AsyncClient, entity_sources: FakeEntitySources) -> None:
    entity_sources.queue("gene.esearch", status(429), status(503))

    result = await resolve(client, "TP53", HUMAN)

    assert result["status"] == "resolved"
    assert len(entity_sources.calls("gene.esearch")) == 3


async def test_organisms_list_offers_explicit_taxa(client: AsyncClient) -> None:
    organisms = (await client.get("/api/v1/entities/organisms")).json()

    assert {"taxon_id": HUMAN, "scientific_name": "Homo sapiens", "common_name": "human"} in organisms
    assert len({o["taxon_id"] for o in organisms}) == len(organisms)


# --- import: typed edges with provenance --------------------------------------------------------------------


async def test_human_gene_import_builds_typed_edges_with_provenance(client: AsyncClient) -> None:
    project_id = await new_project(client)

    response = await add_gene(client, project_id, "7157", HUMAN)

    assert response.status_code == 201, response.text
    pinned = response.json()
    gene = pinned["entity"]
    assert (gene["kind"], gene["namespace"], gene["accession"], gene["taxon_id"], gene["label"]) == (
        "gene",
        "ncbigene",
        "7157",
        HUMAN,
        "TP53",
    )
    assert (gene["provider"], gene["provider_release"]) == ("NCBI Gene", GENE_BUILD)
    assert len(gene["raw_payload_sha256"]) == 64
    assert pinned["warnings"] == []
    xrefs = {(x["namespace"], x["id"]): x for x in gene["xrefs"]}
    assert set(xrefs) == {
        ("ensembl", "ENSG00000141510"),
        ("hgnc", "HGNC:11998"),
        ("uniprot", "P04637"),
        ("omim", "191170"),
    }
    ensembl = xrefs[("ensembl", "ENSG00000141510")]
    assert (ensembl["source"], ensembl["release"], ensembl["reciprocal"]) == ("UniProt", UNIPROT_RELEASE, True)
    assert ensembl["versions"] == ["20"]
    assert ensembl["checked_against"] == "Ensembl 116"
    assert gene["attributes"]["disease_associations"]["status"] == "stored"

    detail = (await client.get(f"/api/v1/entities/{gene['id']}")).json()
    encodes = [e for e in detail["edges"] if e["predicate"] == "encodes"]
    diseases = [e for e in detail["edges"] if e["predicate"] == "associated_with_disease"]
    assert [(e["neighbor"]["namespace"], e["neighbor"]["accession"], e["neighbor"]["taxon_id"]) for e in encodes] == [
        ("uniprot", "P04637", HUMAN)
    ]
    assert encodes[0]["score"] is None
    assert encodes[0]["provider_release"] == UNIPROT_RELEASE
    assert len(diseases) == 5
    scores = [e["score"] for e in diseases]
    assert scores == sorted(scores, reverse=True)
    assert all(0 <= s <= 1 for s in scores)
    top = diseases[0]
    assert (top["neighbor"]["kind"], top["neighbor"]["namespace"], top["neighbor"]["accession"]) == (
        "disease",
        "mondo",
        "MONDO:0018875",
    )
    assert top["neighbor"]["label"] == "Li-Fraumeni syndrome"
    assert top["neighbor"]["taxon_id"] is None
    assert top["provider"] == "Open Targets"
    assert top["evidence"]["rank"] == 1
    assert top["evidence"]["total_associations"] == 5198
    assert "not a measure of causality" in top["evidence"]["note"].lower()

    # The protein knows its gene from the other side of the same typed edge.
    protein = (await client.get(f"/api/v1/entities/{encodes[0]['neighbor']['id']}")).json()
    assert protein["attributes"]["reviewed"] is True
    assert [(e["predicate"], e["direction"], e["neighbor"]["accession"]) for e in protein["edges"]] == [
        ("encodes", "in", "7157")
    ]


async def test_mouse_gene_import_has_no_disease_associations(
    client: AsyncClient, entity_sources: FakeEntitySources
) -> None:
    project_id = await new_project(client)

    response = await add_gene(client, project_id, "22059", MOUSE)

    assert response.status_code == 201, response.text
    gene = response.json()["entity"]
    assert (gene["label"], gene["taxon_id"], gene["organism"]) == ("Trp53", MOUSE, "Mus musculus")
    assert response.json()["warnings"] == [
        "Open Targets covers human targets only: no disease associations for Mus musculus."
    ]
    assert gene["attributes"]["disease_associations"] == {"status": "not_covered"}
    assert entity_sources.calls("opentargets") == []
    edges = (await client.get(f"/api/v1/entities/{gene['id']}")).json()["edges"]
    assert [(e["predicate"], e["neighbor"]["accession"], e["neighbor"]["taxon_id"]) for e in edges] == [
        ("encodes", "P02340", MOUSE)
    ]
    assert not any(x["namespace"] == "hgnc" for x in gene["xrefs"])  # HGNC names human genes only


@pytest.mark.parametrize(
    ("gene_id", "taxon_id", "code", "error"),
    [
        ("7157", MOUSE, 409, "taxon_mismatch"),
        ("6248", HUMAN, 409, "conflict"),  # replaced
        ("102724970", HUMAN, 409, "conflict"),  # discontinued
        ("999999999", HUMAN, 404, "not_found"),
    ],
)
async def test_import_refuses_wrong_organism_and_dead_ids(
    client: AsyncClient, gene_id: str, taxon_id: int, code: int, error: str
) -> None:
    project_id = await new_project(client)

    response = await add_gene(client, project_id, gene_id, taxon_id)

    assert response.status_code == code
    assert response.json()["error"]["code"] == error
    assert (await client.get(f"/api/v1/projects/{project_id}/entities")).json() == []


async def test_unconfirmed_ensembl_cross_reference_is_flagged_and_not_used(
    client: AsyncClient, entity_sources: FakeEntitySources
) -> None:
    # Ensembl maps the UniProt-asserted gene ID to nothing: the cross-reference is not reciprocal.
    entity_sources.ensembl_xrefs["ENSG00000141510"] = []
    project_id = await new_project(client)

    response = await add_gene(client, project_id, "7157", HUMAN)

    assert response.status_code == 201
    gene = response.json()["entity"]
    ensembl = next(x for x in gene["xrefs"] if x["namespace"] == "ensembl")
    assert ensembl["reciprocal"] is False
    assert gene["attributes"]["disease_associations"] == {"status": "no_target"}
    assert entity_sources.calls("opentargets") == []
    assert response.json()["warnings"] == [
        "Ensembl 116 does not map ENSG00000141510 back to NCBI Gene 7157; the cross-reference is kept but not used.",
        "No reciprocally confirmed Ensembl gene ID: disease associations not retrieved.",
    ]


async def test_open_targets_outage_keeps_the_previous_association_snapshot(
    client: AsyncClient, entity_sources: FakeEntitySources
) -> None:
    project_id = await new_project(client)
    first = (await add_gene(client, project_id, "7157", HUMAN)).json()["entity"]
    entity_sources.queue("opentargets", *[status(503)] * 3)

    second = await add_gene(client, project_id, "7157", HUMAN)

    assert second.status_code == 201
    assert second.json()["entity"]["attributes"]["disease_associations"]["status"] == "unavailable"
    assert any("Open Targets unavailable" in w for w in second.json()["warnings"])
    edges = (await client.get(f"/api/v1/entities/{first['id']}")).json()["edges"]
    assert sum(e["predicate"] == "associated_with_disease" for e in edges) == 5


async def test_target_that_disappears_clears_stale_associations(
    client: AsyncClient, entity_sources: FakeEntitySources
) -> None:
    project_id = await new_project(client)
    gene = (await add_gene(client, project_id, "7157", HUMAN)).json()["entity"]
    # A later Ensembl release no longer maps the gene ID back: current data has no usable target.
    entity_sources.ensembl_xrefs["ENSG00000141510"] = []

    again = await add_gene(client, project_id, "7157", HUMAN)

    assert again.json()["entity"]["attributes"]["disease_associations"] == {"status": "no_target"}
    edges = (await client.get(f"/api/v1/entities/{gene['id']}")).json()["edges"]
    assert [e["predicate"] for e in edges] == ["encodes"]


async def test_core_provider_outage_aborts_the_import(client: AsyncClient, entity_sources: FakeEntitySources) -> None:
    project_id = await new_project(client)
    entity_sources.queue("uniprot", *[status(503)] * 3)

    response = await add_gene(client, project_id, "7157", HUMAN)

    assert response.status_code == 502
    assert (await client.get(f"/api/v1/projects/{project_id}/entities")).json() == []


async def test_reimport_refreshes_without_duplicates_and_entities_are_shared(client: AsyncClient) -> None:
    a, b = await new_project(client), await new_project(client)

    first = (await add_gene(client, a, "7157", HUMAN)).json()
    again = (await add_gene(client, a, "7157", HUMAN)).json()
    other = (await add_gene(client, b, "7157", HUMAN)).json()

    assert first["entity"]["id"] == again["entity"]["id"] == other["entity"]["id"]
    assert len((await client.get(f"/api/v1/projects/{a}/entities")).json()) == 1
    edges = (await client.get(f"/api/v1/entities/{first['entity']['id']}")).json()["edges"]
    assert sorted(e["predicate"] for e in edges) == ["associated_with_disease"] * 5 + ["encodes"]


async def test_unpinning_keeps_the_entity_for_other_projects(client: AsyncClient) -> None:
    project_id = await new_project(client)
    gene = (await add_gene(client, project_id, "7157", HUMAN)).json()["entity"]

    removed = await client.delete(f"/api/v1/projects/{project_id}/entities/{gene['id']}")

    assert removed.status_code == 204
    assert (await client.get(f"/api/v1/projects/{project_id}/entities")).json() == []
    assert (await client.get(f"/api/v1/entities/{gene['id']}")).status_code == 200
    again = await client.delete(f"/api/v1/projects/{project_id}/entities/{gene['id']}")
    assert again.status_code == 404


async def test_unknown_project_and_entity_are_404(client: AsyncClient) -> None:
    missing = "00000000-0000-0000-0000-000000000000"

    assert (await client.get(f"/api/v1/projects/{missing}/entities")).status_code == 404
    assert (await add_gene(client, missing, "7157", HUMAN)).status_code == 404
    assert (await client.get(f"/api/v1/entities/{missing}")).status_code == 404
