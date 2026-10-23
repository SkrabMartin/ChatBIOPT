from collections.abc import AsyncIterator

import httpx
import pytest

from app.core.errors import UpstreamError, UpstreamInvalidResponseError, UpstreamTimeoutError
from app.integrations.http import RateLimiter, RetryPolicy
from app.integrations.pubmed.client import PubMedClient
from tests.fakes import FIXTURE_PMIDS, FakeNCBI, Responder, body, esearch, raises, status


@pytest.fixture
async def pubmed(ncbi: FakeNCBI) -> AsyncIterator[PubMedClient]:
    async with httpx.AsyncClient(transport=httpx.MockTransport(ncbi.handle)) as http:
        yield PubMedClient(
            http,
            base_url="https://eutils.test/entrez/eutils",
            tool="bioresearch-ai",
            email="dev@example.org",
            api_key="secret-key",
            limiter=RateLimiter(1000),
            retry=RetryPolicy(max_attempts=3, backoff_base_s=0),
        )


async def test_search_sends_identity_and_paging_parameters(pubmed: PubMedClient, ncbi: FakeNCBI) -> None:
    hits = await pubmed.search("TP53 glioblastoma", limit=4, offset=8)

    assert hits.total_count == 1093
    assert hits.pmids == FIXTURE_PMIDS
    assert hits.query_translation and "glioblastoma" in hits.query_translation
    params = ncbi.calls("esearch")[0].url.params
    assert params["term"] == "TP53 glioblastoma"
    assert (params["retmax"], params["retstart"], params["db"]) == ("4", "8", "pubmed")
    assert (params["tool"], params["email"], params["api_key"]) == ("bioresearch-ai", "dev@example.org", "secret-key")


async def test_fetch_returns_records_in_requested_order_and_omits_unknown_ids(pubmed: PubMedClient) -> None:
    fetched = await pubmed.fetch(["18772890", "99999999", "42906764"])

    assert [f.record.pmid for f in fetched] == ["18772890", "42906764"]


async def test_transient_failures_are_retried_then_succeed(pubmed: PubMedClient, ncbi: FakeNCBI) -> None:
    ncbi.queue("esearch", status(503), raises(httpx.ConnectError("boom")))

    hits = await pubmed.search("q", limit=4, offset=0)

    assert hits.pmids == FIXTURE_PMIDS
    assert len(ncbi.calls("esearch")) == 3


async def test_retries_are_bounded(pubmed: PubMedClient, ncbi: FakeNCBI) -> None:
    ncbi.queue("esearch", status(429), status(502), status(500), status(200))

    with pytest.raises(UpstreamError, match="temporarily unavailable"):
        await pubmed.search("q", limit=4, offset=0)
    assert len(ncbi.calls("esearch")) == 3


async def test_persistent_timeout_is_reported_as_timeout(pubmed: PubMedClient, ncbi: FakeNCBI) -> None:
    ncbi.queue("efetch", *[raises(httpx.ReadTimeout("slow"))] * 3)

    with pytest.raises(UpstreamTimeoutError):
        await pubmed.fetch(["18772890"])


async def test_client_errors_are_not_retried(pubmed: PubMedClient, ncbi: FakeNCBI) -> None:
    ncbi.queue("esearch", status(400))

    with pytest.raises(UpstreamError, match="HTTP 400"):
        await pubmed.search("q", limit=4, offset=0)
    assert len(ncbi.calls("esearch")) == 1


@pytest.mark.parametrize(
    "responder",
    [body(b"<html>maintenance</html>"), body(b'{"esearchresult": {"ERROR": "x"}}'), esearch(["123", "../etc"])],
    ids=["html", "missing-fields", "bad-pmid"],
)
async def test_unexpected_search_payload_is_invalid_response(
    pubmed: PubMedClient, ncbi: FakeNCBI, responder: Responder
) -> None:
    ncbi.queue("esearch", responder)

    with pytest.raises(UpstreamInvalidResponseError):
        await pubmed.search("q", limit=4, offset=0)


async def test_rate_limiter_spaces_calls_by_the_configured_rate() -> None:
    now = 0.0
    slept: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        nonlocal now
        slept.append(seconds)
        now += seconds

    limiter = RateLimiter(4, clock=lambda: now, sleep=fake_sleep)
    for _ in range(3):
        await limiter.acquire()

    assert slept == [0.25, 0.25]


def test_retry_after_is_honoured_but_capped() -> None:
    policy = RetryPolicy(backoff_base_s=1)

    assert policy.delay(1, "2") == 2
    assert policy.delay(1, "3600") == 5
    assert 0.5 <= policy.delay(1, None) <= 1
    assert 2 <= policy.delay(3, "Wed, 21 Oct 2026 07:28:00 GMT") <= 4
