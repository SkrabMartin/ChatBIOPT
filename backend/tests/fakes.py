"""Test doubles for external services, served through `httpx.MockTransport`.

FakeNCBI answers like PubMed using recorded fixtures (abstracts and affiliations replaced with placeholders);
tests queue scripted responses per endpoint to simulate outages, throttling and malformed payloads.
FakeOllama speaks Ollama's `/api/tags` and streaming `/api/chat` protocol with scriptable output and failures.
"""

import asyncio
import json
import xml.etree.ElementTree as ET
from collections import defaultdict, deque
from collections.abc import AsyncIterator, Callable
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


OLLAMA_DIGEST = "a" * 64


class FakeOllama:
    def __init__(self) -> None:
        self.pulled: set[str] = {"qwen2.5:1.5b"}
        self.reachable = True
        self.chunks: list[str] = ["Hello", " world", "."]
        # Raw NDJSON lines to emit instead of the normal stream (for malformed/error streams).
        self.raw_lines: list[str] | None = None
        self.http_status = 200
        # When set, the stream pauses after the first chunk until the event is set (tests cancel/timeout here).
        self.hold: asyncio.Event | None = None
        self.first_chunk_sent = asyncio.Event()
        self.chat_payloads: list[dict[str, object]] = []

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
            return httpx.Response(200, content=self._stream(), headers={"Content-Type": "application/x-ndjson"})
        return httpx.Response(404)

    async def _stream(self) -> AsyncIterator[bytes]:
        if self.raw_lines is not None:
            for line in self.raw_lines:
                yield (line + "\n").encode()
            return
        for index, text in enumerate(self.chunks):
            yield (json.dumps({"message": {"role": "assistant", "content": text}, "done": False}) + "\n").encode()
            if index == 0:
                self.first_chunk_sent.set()
                if self.hold is not None:
                    await self.hold.wait()
        done = {"done": True, "done_reason": "stop", "prompt_eval_count": 42, "eval_count": len(self.chunks)}
        yield (json.dumps(done) + "\n").encode()


class FakeUpstreams:
    """Routes outbound requests by host, like the real network would."""

    def __init__(self, ncbi: FakeNCBI, ollama: FakeOllama) -> None:
        self.ncbi = ncbi
        self.ollama = ollama

    def handle(self, request: httpx.Request) -> httpx.Response:
        if request.url.host == "ollama.test":
            return self.ollama.handle(request)
        return self.ncbi.handle(request)
