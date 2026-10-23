"""Test double for NCBI E-utilities, served through `httpx.MockTransport`.

By default it answers like PubMed using recorded fixtures (abstracts and affiliations replaced with placeholders).
Tests can queue scripted responses per endpoint to simulate outages, throttling and malformed payloads.
"""

import json
import xml.etree.ElementTree as ET
from collections import defaultdict, deque
from collections.abc import Callable
from pathlib import Path

import httpx

FIXTURES = Path(__file__).parent / "fixtures" / "pubmed"
ESEARCH_FIXTURE = json.loads((FIXTURES / "esearch_tp53_glioblastoma.json").read_text(encoding="utf-8"))
EFETCH_FIXTURE = (FIXTURES / "efetch_tp53_glioblastoma.xml").read_bytes()
FIXTURE_PMIDS: list[str] = ESEARCH_FIXTURE["esearchresult"]["idlist"]

Responder = Callable[[httpx.Request], httpx.Response]


def _efetch_subset(pmids: list[str]) -> bytes:
    """EFetch fixture filtered to the requested PMIDs, like the real service (unknown IDs are simply absent)."""
    root = ET.fromstring(EFETCH_FIXTURE)  # noqa: S314 - trusted local fixture
    for article in list(root):
        if article.findtext(".//PMID") not in pmids:
            root.remove(article)
    return ET.tostring(root, encoding="utf-8")


class FakeNCBI:
    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self._scripted: dict[str, deque[Responder]] = defaultdict(deque)

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
            return httpx.Response(200, content=_efetch_subset(pmids), headers={"Content-Type": "text/xml"})
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
