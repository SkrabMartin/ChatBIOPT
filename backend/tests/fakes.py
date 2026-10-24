"""Test doubles for external services, served through `httpx.MockTransport`.

FakeNCBI answers like PubMed using recorded fixtures (abstracts and affiliations replaced with placeholders);
tests queue scripted responses per endpoint to simulate outages, throttling and malformed payloads.
FakeOllama speaks Ollama's `/api/tags` and streaming `/api/chat` protocol with scriptable output and failures.
FakeEntitySources serves NCBI Gene, UniProtKB, Ensembl and Open Targets from recorded responses (fixtures/entities).
"""

import asyncio
import hashlib
import json
import math
import re
import xml.etree.ElementTree as ET
from collections import defaultdict, deque
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any

import httpx

FIXTURES = Path(__file__).parent / "fixtures" / "pubmed"
EPMC_FIXTURES = Path(__file__).parent / "fixtures" / "europepmc"
ENTITY_FIXTURES = Path(__file__).parent / "fixtures" / "entities"
ESEARCH_FIXTURE = json.loads((FIXTURES / "esearch_tp53_glioblastoma.json").read_text(encoding="utf-8"))
EFETCH_FIXTURE = (FIXTURES / "efetch_tp53_glioblastoma.xml").read_bytes()
FIXTURE_PMIDS: list[str] = ESEARCH_FIXTURE["esearchresult"]["idlist"]
# Open-access article (CC BY 3.0) with Europe PMC full text; see fixtures/europepmc/NOTICE.txt.
OA_PMID = "29416795"
OA_PMCID = "PMC5788663"
FULLTEXT_FIXTURE = (EPMC_FIXTURES / f"fulltext_{OA_PMCID}.xml").read_bytes()

Responder = Callable[[httpx.Request], httpx.Response]


def _efetch_subset(pmids: list[str], transform: Callable[[bytes], bytes] | None = None) -> bytes:
    """EFetch fixtures filtered to the requested PMIDs, like the real service (unknown IDs are simply absent)."""
    root = ET.fromstring(EFETCH_FIXTURE)  # noqa: S314 - trusted local fixture
    extra = ET.fromstring((FIXTURES / f"efetch_{OA_PMID}.xml").read_bytes())  # noqa: S314
    root.extend(list(extra))
    for article in list(root):
        if article.findtext(".//PMID") not in pmids:
            root.remove(article)
    payload = ET.tostring(root, encoding="utf-8")
    return transform(payload) if transform else payload


class FakeNCBI:
    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self._scripted: dict[str, deque[Responder]] = defaultdict(deque)
        # Applied to every EFetch payload (simulate PubMed revising a record).
        self.efetch_transform: Callable[[bytes], bytes] | None = None

    def queue(self, endpoint: str, *responders: Responder) -> None:
        """Serve these responses, in order, for the next calls to `endpoint` ("esearch" or "efetch")."""
        self._scripted[endpoint].extend(responders)

    def calls(self, endpoint: str) -> list[httpx.Request]:
        return [r for r in self.requests if r.url.path.endswith(f"/{endpoint}.fcgi")]

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        endpoint = request.url.path.rsplit("/", 1)[-1].removesuffix(".fcgi")
        if self._scripted[endpoint]:
            return self._scripted[endpoint].popleft()(request)
        if endpoint == "esearch":
            return httpx.Response(200, json=ESEARCH_FIXTURE)
        if endpoint == "efetch":
            pmids = request.url.params["id"].split(",")
            return httpx.Response(
                200, content=_efetch_subset(pmids, self.efetch_transform), headers={"Content-Type": "text/xml"}
            )
        return httpx.Response(404)


def status(code: int, headers: dict[str, str] | None = None) -> Responder:
    return lambda _request: httpx.Response(code, headers=headers)


def raises(exc: Exception) -> Responder:
    def responder(_request: httpx.Request) -> httpx.Response:
        raise exc

    return responder


def esearch(pmids: list[str], count: int | None = None) -> Responder:
    body = {"esearchresult": {"count": str(len(pmids) if count is None else count), "idlist": pmids}}
    return lambda _request: httpx.Response(200, json=body)


def body(content: bytes, code: int = 200) -> Responder:
    return lambda _request: httpx.Response(code, content=content)


OLLAMA_DIGEST = "a" * 64


def fake_embedding(text: str, dimensions: int = 768) -> list[float]:
    """Deterministic bag-of-words vector: texts sharing words are similar, so semantic tests are meaningful."""
    text = text.removeprefix("search_query: ").removeprefix("search_document: ")
    vector = [0.0] * dimensions
    for word in re.findall(r"\w+", text.lower()):
        vector[int(hashlib.sha256(word.encode()).hexdigest(), 16) % dimensions] += 1.0
    norm = math.sqrt(sum(v * v for v in vector)) or 1.0
    return [v / norm for v in vector]


class FakeOllama:
    def __init__(self) -> None:
        self.pulled: set[str] = {"qwen2.5:1.5b", "nomic-embed-text"}
        self.embed_requests: list[list[str]] = []
        self.embed_status = 200
        self.reachable = True
        self.chunks: list[str] = ["Hello", " world", "."]
        # Raw NDJSON lines to emit instead of the normal stream (for malformed/error streams).
        self.raw_lines: list[str] | None = None
        self.http_status = 200
        # When set, the stream pauses after the first chunk until the event is set (tests cancel/timeout here).
        self.hold: asyncio.Event | None = None
        self.first_chunk_sent = asyncio.Event()
        self.chat_payloads: list[dict[str, object]] = []
        # Optional per-request responder: payload -> chunks (overrides `chunks`).
        self.reply: Callable[[dict[str, Any]], list[str]] | None = None

    def handle(self, request: httpx.Request) -> httpx.Response:
        if not self.reachable:
            raise httpx.ConnectError("connection refused", request=request)
        if request.url.path == "/api/tags":
            models = [{"name": name, "digest": OLLAMA_DIGEST} for name in sorted(self.pulled)]
            return httpx.Response(200, json={"models": models})
        if request.url.path == "/api/chat":
            payload = json.loads(request.content)
            self.chat_payloads.append(payload)
            if payload["model"] not in self.pulled:
                return httpx.Response(404, json={"error": f"model '{payload['model']}' not found"})
            if self.http_status != 200:
                return httpx.Response(self.http_status, json={"error": "boom"})
            chunks = self.reply(payload) if self.reply else self.chunks
            return httpx.Response(200, content=self._stream(chunks), headers={"Content-Type": "application/x-ndjson"})
        if request.url.path == "/api/embed":
            payload = json.loads(request.content)
            if payload["model"] not in self.pulled:
                return httpx.Response(404, json={"error": "model not found"})
            if self.embed_status != 200:
                return httpx.Response(self.embed_status, json={"error": "boom"})
            self.embed_requests.append(payload["input"])
            return httpx.Response(200, json={"embeddings": [fake_embedding(text) for text in payload["input"]]})
        return httpx.Response(404)

    async def _stream(self, chunks: list[str]) -> AsyncIterator[bytes]:
        if self.raw_lines is not None:
            for line in self.raw_lines:
                yield (line + "\n").encode()
            return
        for index, text in enumerate(chunks):
            yield (json.dumps({"message": {"role": "assistant", "content": text}, "done": False}) + "\n").encode()
            if index == 0:
                self.first_chunk_sent.set()
                if self.hold is not None:
                    await self.hold.wait()
        done = {"done": True, "done_reason": "stop", "prompt_eval_count": 42, "eval_count": len(chunks)}
        yield (json.dumps(done) + "\n").encode()


class FakeEuropePMC:
    """Europe PMC search (OA status + license) and fullTextXML, from recorded fixtures by default."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.search_results: dict[str, dict[str, object]] = {
            pmid: json.loads((EPMC_FIXTURES / f"search_{pmid}.json").read_text(encoding="utf-8"))
            for pmid in (OA_PMID, "18772890", "42857877")
        }
        self.full_texts: dict[str, bytes] = {OA_PMCID: FULLTEXT_FIXTURE}
        self._scripted: dict[str, deque[Responder]] = defaultdict(deque)

    def queue(self, endpoint: str, *responders: Responder) -> None:
        """`endpoint` is "search" or "fulltext"."""
        self._scripted[endpoint].extend(responders)

    def calls(self, endpoint: str) -> list[httpx.Request]:
        suffix = "/search" if endpoint == "search" else "/fullTextXML"
        return [r for r in self.requests if r.url.path.endswith(suffix)]

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        endpoint = "search" if request.url.path.endswith("/search") else "fulltext"
        if self._scripted[endpoint]:
            return self._scripted[endpoint].popleft()(request)
        if endpoint == "search":
            pmid = request.url.params["query"].split()[0].removeprefix("EXT_ID:")
            return httpx.Response(200, json=self.search_results.get(pmid, {"resultList": {"result": []}}))
        pmcid = request.url.path.split("/")[-2]
        if pmcid in self.full_texts:
            return httpx.Response(200, content=self.full_texts[pmcid])
        # Like the real service: 500, not 404, for articles outside the OA subset.
        return httpx.Response(500, json={"status": 500, "error": "Internal Server Error"})


def _entity_fixture(name: str) -> Any:
    return json.loads((ENTITY_FIXTURES / name).read_text(encoding="utf-8"))


GENE_BUILD = "Build261023-2230m.1"
UNIPROT_FIXTURE = _entity_fixture("uniprot_entries.json")
UNIPROT_INACTIVE = _entity_fixture("uniprot_inactive.json")
ENSEMBL_FIXTURE = _entity_fixture("ensembl.json")
OPENTARGETS_TP53 = _entity_fixture("opentargets_tp53.json")
_SYM_TERM = re.compile(r'^"(?P<symbol>[^"]+)"\[sym\] AND \((?P<taxa>[^)]*)\) AND alive\[prop\]$')


class FakeEntitySources:
    """NCBI Gene (E-utilities db=gene), UniProtKB search, Ensembl xrefs and Open Targets GraphQL.

    Endpoints for `queue`/`calls`: "gene.esearch", "gene.esummary", "gene.einfo", "uniprot", "ensembl",
    "opentargets". Unscripted calls answer like the real services over the recorded records.
    """

    def __init__(self) -> None:
        self.requests: list[tuple[str, httpx.Request]] = []
        self._scripted: dict[str, deque[Responder]] = defaultdict(deque)
        summaries = _entity_fixture("gene_esummary.json")["result"]
        self.genes: dict[str, dict[str, Any]] = {uid: summaries[uid] for uid in summaries["uids"]}
        # Copies: tests mutate these to simulate provider data changes.
        self.uniprot_entries: list[dict[str, Any]] = list(UNIPROT_FIXTURE["results"])
        # Inactive entries (deleted / merged / demerged) only come back for accession queries, like the real API.
        self.uniprot_inactive: list[dict[str, Any]] = list(UNIPROT_INACTIVE["results"])
        self.ensembl_xrefs: dict[str, list[dict[str, Any]]] = dict(ENSEMBL_FIXTURE["xrefs"])

    def queue(self, endpoint: str, *responders: Responder) -> None:
        self._scripted[endpoint].extend(responders)

    def calls(self, endpoint: str) -> list[httpx.Request]:
        return [r for e, r in self.requests if e == endpoint]

    @staticmethod
    def endpoint(request: httpx.Request) -> str | None:
        host = request.url.host
        if host == "rest.uniprot.org":
            return "uniprot"
        if host == "rest.ensembl.org":
            return "ensembl"
        if host == "api.platform.opentargets.org":
            return "opentargets"
        if host == "eutils.ncbi.nlm.nih.gov" and request.url.params.get("db") == "gene":
            return "gene." + request.url.path.rsplit("/", 1)[-1].removesuffix(".fcgi")
        return None

    def handle(self, request: httpx.Request, endpoint: str) -> httpx.Response:
        self.requests.append((endpoint, request))
        if self._scripted[endpoint]:
            return self._scripted[endpoint].popleft()(request)
        if endpoint == "gene.esearch":
            return self._gene_search(request.url.params["term"], int(request.url.params.get("retmax", 20)))
        if endpoint == "gene.esummary":
            ids = request.url.params["id"].split(",")
            result: dict[str, Any] = {"uids": [i for i in ids if i in self.genes]}
            for gene_id in ids:
                result[gene_id] = self.genes.get(gene_id, {"uid": gene_id, "error": "cannot get document summary"})
            return httpx.Response(200, json={"header": {"type": "esummary"}, "result": result})
        if endpoint == "gene.einfo":
            return httpx.Response(200, json={"einforesult": {"dbinfo": [{"dbname": "gene", "dbbuild": GENE_BUILD}]}})
        if endpoint == "uniprot":
            return self._uniprot(request.url.params["query"])
        if endpoint == "ensembl":
            return self._ensembl(request.url.path)
        if endpoint == "opentargets":
            variables = json.loads(request.content)["variables"]
            payload = json.loads(json.dumps(OPENTARGETS_TP53))
            if variables["id"] == payload["data"]["target"]["id"]:
                rows = payload["data"]["target"]["associatedDiseases"]["rows"]
                payload["data"]["target"]["associatedDiseases"]["rows"] = rows[: variables["size"]]
            else:
                payload["data"]["target"] = None  # what Open Targets answers for non-human or unknown IDs
            return httpx.Response(200, json=payload)
        return httpx.Response(404)

    def _gene_search(self, term: str, retmax: int) -> httpx.Response:
        match = _SYM_TERM.match(term)
        if not match:
            return httpx.Response(200, json={"esearchresult": {"ERROR": f"unsupported term {term}"}})
        symbol = match.group("symbol").casefold()
        taxa = {int(t) for t in re.findall(r"(\d+)\[taxid\]", match.group("taxa"))}
        ids = [
            uid
            for uid, gene in self.genes.items()
            if gene["organism"]["taxid"] in taxa
            and str(gene["status"]) in ("", "0")
            and (
                gene["name"].casefold() == symbol
                or symbol in {a.strip().casefold() for a in gene["otheraliases"].split(",")}
            )
        ]
        return httpx.Response(200, json={"esearchresult": {"count": str(len(ids)), "idlist": ids[:retmax]}})

    def _uniprot(self, query: str) -> httpx.Response:
        def xrefs(entry: dict[str, Any], database: str) -> set[str]:
            return {x["id"] for x in entry.get("uniProtKBCrossReferences", []) if x["database"] == database}

        if m := re.fullmatch(r"accession:(\S+)", query):
            hits = [e for e in self.uniprot_entries + self.uniprot_inactive if e["primaryAccession"] == m.group(1)]
        elif m := re.fullmatch(r"\(xref:geneid-(\d+)\) AND \(organism_id:(\d+)\) AND \(reviewed:true\)", query):
            hits = [
                e
                for e in self.uniprot_entries
                if m.group(1) in xrefs(e, "GeneID") and e["organism"]["taxonId"] == int(m.group(2))
            ]
        elif m := re.fullmatch(r"\(xref:hgnc-(\d+)\) AND \(reviewed:true\)", query):
            hits = [e for e in self.uniprot_entries if f"HGNC:{m.group(1)}" in xrefs(e, "HGNC")]
        else:
            return httpx.Response(400, json={"messages": [f"unsupported query {query}"]})
        headers = {
            "X-UniProt-Release": UNIPROT_FIXTURE["release"],
            "X-UniProt-Release-Date": UNIPROT_FIXTURE["release_date"],
        }
        return httpx.Response(200, json={"results": hits}, headers=headers)

    def _ensembl(self, path: str) -> httpx.Response:
        if path == "/info/data":
            return httpx.Response(200, json=ENSEMBL_FIXTURE["info_data"])
        stable_id = path.rsplit("/", 1)[-1]
        if stable_id in self.ensembl_xrefs:
            return httpx.Response(200, json=self.ensembl_xrefs[stable_id])
        return httpx.Response(400, json={"error": f"ID '{stable_id}' not found"})


class FakeUpstreams:
    """Routes outbound requests by host, like the real network would."""

    def __init__(
        self,
        ncbi: FakeNCBI,
        ollama: FakeOllama,
        europepmc: FakeEuropePMC | None = None,
        entities: FakeEntitySources | None = None,
    ) -> None:
        self.ncbi = ncbi
        self.ollama = ollama
        self.europepmc = europepmc or FakeEuropePMC()
        self.entities = entities or FakeEntitySources()

    def handle(self, request: httpx.Request) -> httpx.Response:
        if request.url.host == "ollama.test":
            return self.ollama.handle(request)
        if request.url.host == "www.ebi.ac.uk":
            return self.europepmc.handle(request)
        if endpoint := FakeEntitySources.endpoint(request):
            return self.entities.handle(request, endpoint)
        return self.ncbi.handle(request)
