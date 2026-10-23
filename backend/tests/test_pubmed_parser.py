from datetime import UTC, datetime

import pytest

from app.core.errors import UpstreamInvalidResponseError
from app.integrations.pubmed.parser import NO_TITLE, NORMALIZER_VERSION, parse_efetch
from app.schemas.literature import LiteratureRecord
from tests.fakes import EFETCH_FIXTURE

NOW = datetime(2026, 10, 23, 12, 0, tzinfo=UTC)


def parse(xml: bytes) -> dict[str, LiteratureRecord]:
    return {record.pmid: record for record, _raw in parse_efetch(xml, NOW)}


def article(inner: str, pubmed_data: str = "") -> bytes:
    return (
        "<PubmedArticleSet><PubmedArticle><MedlineCitation><PMID>123</PMID><Article>"
        f"{inner}</Article></MedlineCitation><PubmedData>{pubmed_data}</PubmedData></PubmedArticle>"
        "</PubmedArticleSet>"
    ).encode()


def test_recorded_fixture_normalizes_core_fields() -> None:
    records = parse(EFETCH_FIXTURE)

    tcga = records["18772890"]
    assert tcga.title == "Comprehensive genomic characterization defines human glioblastoma genes and core pathways."
    assert tcga.authors == ["Cancer Genome Atlas Research Network"]
    assert tcga.doi == "10.1038/nature07385"
    assert tcga.pmcid == "PMC2671642"
    assert tcga.published_year == 2008
    assert tcga.published_date == "2008-10-23"
    assert tcga.journal == "Nature"
    assert tcga.links.pubmed == "https://pubmed.ncbi.nlm.nih.gov/18772890/"
    assert tcga.links.doi == "https://doi.org/10.1038/nature07385"
    assert tcga.links.pmc == "https://pmc.ncbi.nlm.nih.gov/articles/PMC2671642/"
    assert tcga.retrieved_at == NOW
    assert tcga.normalizer_version == NORMALIZER_VERSION
    assert len(tcga.raw_payload_sha256) == 64

    review = records["42906764"]
    assert review.pmcid is None and review.links.pmc is None
    assert "Review" in review.publication_types
    assert review.authors[0] == "Hagstotz A"


def test_reference_list_identifiers_do_not_leak_into_the_record() -> None:
    """The TCGA record cites hundreds of papers; their ArticleIds must not override the record's own."""
    tcga = parse(EFETCH_FIXTURE)["18772890"]

    assert tcga.pmid == "18772890"
    assert tcga.doi == "10.1038/nature07385"


def test_structured_abstract_keeps_labels_and_flattens_inline_markup() -> None:
    record = parse(EFETCH_FIXTURE)["42857877"]

    assert [s.label for s in record.abstract_sections] == [
        "INTRODUCTION",
        "AIMS",
        "MATERIALS AND METHODS",
        "RESULTS",
        "CONCLUSION",
    ]
    methods = record.abstract_sections[2].text
    assert "<sub>" not in methods and methods.endswith("2 tail text.")
    assert record.abstract is not None and record.abstract.startswith("INTRODUCTION: ")
    assert record.published_date == "2026-12"


def test_checksum_is_stable_and_tracks_payload_changes() -> None:
    first = parse(EFETCH_FIXTURE)["18772890"].raw_payload_sha256
    again = parse(EFETCH_FIXTURE)["18772890"].raw_payload_sha256
    changed = parse(EFETCH_FIXTURE.replace(b"core pathways.", b"core pathways (revised)."))["18772890"]

    assert first == again
    assert changed.raw_payload_sha256 != first


@pytest.mark.parametrize(
    ("ids", "doi", "pmcid"),
    [
        ('<ArticleId IdType="doi">not-a-doi</ArticleId><ArticleId IdType="pmc">12345</ArticleId>', None, None),
        (
            '<ArticleId IdType="doi">10.1000/xyz123</ArticleId><ArticleId IdType="pmc">PMC99</ArticleId>',
            "10.1000/xyz123",
            "PMC99",
        ),
        ('<ArticleId IdType="doi">javascript:alert(1)</ArticleId>', None, None),
    ],
    ids=["malformed-dropped", "valid-kept", "script-url-dropped"],
)
def test_identifiers_are_syntax_checked_before_linking(ids: str, doi: str | None, pmcid: str | None) -> None:
    record = parse(article("<ArticleTitle>T</ArticleTitle>", f"<ArticleIdList>{ids}</ArticleIdList>"))["123"]

    assert (record.doi, record.pmcid) == (doi, pmcid)
    assert record.links.doi == (f"https://doi.org/{doi}" if doi else None)


@pytest.mark.parametrize(
    ("pub_date", "year", "date"),
    [
        ("<Year>2019</Year><Month>Feb</Month><Day>30</Day>", 2019, "2019-02"),
        ("<Year>2019</Year><Month>07</Month>", 2019, "2019-07"),
        ("<Year>2019</Year><Season>Spring</Season>", 2019, "2019"),
        ("<MedlineDate>1998 Dec-1999 Jan</MedlineDate>", 1998, "1998"),
        ("<MedlineDate>Summer</MedlineDate>", None, None),
    ],
    ids=["invalid-day", "numeric-month", "season", "medline-range", "medline-no-year"],
)
def test_publication_dates_keep_only_the_precision_pubmed_gives(
    pub_date: str, year: int | None, date: str | None
) -> None:
    xml = article(
        f"<Journal><JournalIssue><PubDate>{pub_date}</PubDate></JournalIssue></Journal><ArticleTitle>T</ArticleTitle>"
    )

    record = parse(xml)["123"]

    assert (record.published_year, record.published_date) == (year, date)


def test_missing_title_uses_pubmed_placeholder_and_vernacular_fallback() -> None:
    assert parse(article("<ArticleTitle></ArticleTitle>"))["123"].title == NO_TITLE
    vernacular = article("<ArticleTitle/><VernacularTitle>Titre original</VernacularTitle>")
    assert parse(vernacular)["123"].title == "Titre original"


def test_book_articles_are_supported() -> None:
    xml = (
        b"<PubmedArticleSet><PubmedBookArticle><BookDocument><PMID>20301295</PMID>"
        b"<Book><BookTitle>GeneReviews</BookTitle><PubDate><Year>1993</Year></PubDate></Book>"
        b"<ArticleTitle>Li-Fraumeni Syndrome</ArticleTitle></BookDocument>"
        b'<PubmedBookData><ArticleIdList><ArticleId IdType="pubmed">20301295</ArticleId></ArticleIdList>'
        b"</PubmedBookData></PubmedBookArticle></PubmedArticleSet>"
    )

    record = parse(xml)["20301295"]

    assert record.title == "Li-Fraumeni Syndrome"
    assert record.published_year == 1993


def test_records_without_valid_pmid_are_skipped() -> None:
    xml = article("<ArticleTitle>T</ArticleTitle>").replace(b"<PMID>123</PMID>", b"<PMID>12a</PMID>")

    assert parse(xml) == {}


@pytest.mark.parametrize(
    "payload",
    [
        b"not xml",
        b"<eSearchResult><ERROR>bad</ERROR></eSearchResult>",
        b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa"><!ENTITY b "&a;&a;">]>'
        b"<PubmedArticleSet>&b;</PubmedArticleSet>",
    ],
    ids=["garbage", "wrong-root", "entity-expansion"],
)
def test_malformed_or_hostile_xml_is_an_upstream_error(payload: bytes) -> None:
    with pytest.raises(UpstreamInvalidResponseError):
        parse_efetch(payload, NOW)
