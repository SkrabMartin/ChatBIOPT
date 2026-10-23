"""Contract check against the real NCBI service. Opt-in: `RUN_LIVE_TESTS=1 pytest -m live`.

Fixtures freeze PubMed's format; this catches the day PubMed changes it. Not run in CI by default to keep
the suite deterministic and to respect NCBI usage policy.
"""

import os

import httpx
import pytest

from app.integrations.http import RateLimiter, RetryPolicy
from app.integrations.pubmed.client import PubMedClient

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(os.environ.get("RUN_LIVE_TESTS") != "1", reason="set RUN_LIVE_TESTS=1 to call NCBI"),
]


async def test_real_pubmed_search_and_fetch_produce_valid_records() -> None:
    async with httpx.AsyncClient(timeout=15, headers={"User-Agent": "BioResearchAI-live-test"}) as http:
        pubmed = PubMedClient(
            http,
            base_url="https://eutils.ncbi.nlm.nih.gov/entrez/eutils",
            tool="bioresearch-ai",
            email=os.environ.get("NCBI_EMAIL"),
            api_key=os.environ.get("NCBI_API_KEY"),
            limiter=RateLimiter(2),
            retry=RetryPolicy(),
        )
        hits = await pubmed.search("TP53 glioblastoma", limit=5, offset=0)
        fetched = await pubmed.fetch([*hits.pmids, "18772890"])

    assert hits.total_count > 100
    assert len(hits.pmids) == 5
    records = {f.record.pmid: f.record for f in fetched}
    assert set(hits.pmids) <= set(records)
    tcga = records["18772890"]
    assert tcga.doi == "10.1038/nature07385"
    assert tcga.pmcid == "PMC2671642"
    assert tcga.published_year == 2008
