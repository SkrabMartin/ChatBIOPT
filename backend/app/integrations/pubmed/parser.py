"""Normalize PubMed EFetch XML into `LiteratureRecord`s.

Upstream text is untrusted data: it is flattened to plain text, identifiers are syntax-checked before any link
is built, and nothing from the payload is ever interpreted as markup or instructions.
"""

import hashlib
import logging
import re
import xml.etree.ElementTree as ET  # only serializes trusted, already-parsed trees; parsing uses defusedxml
from datetime import datetime
from urllib.parse import quote

from defusedxml import ElementTree as SafeET

from app.core.errors import UpstreamInvalidResponseError
from app.schemas.literature import (
    DOI_PATTERN,
    PMCID_PATTERN,
    PMID_PATTERN,
    AbstractSection,
    LiteratureLinks,
    LiteratureRecord,
)

NORMALIZER_VERSION = "pubmed-v1"
NO_TITLE = "[No title available]"  # PubMed's own convention for untitled records

logger = logging.getLogger(__name__)

_MONTHS = {m: i for i, m in enumerate(("jan feb mar apr may jun jul aug sep oct nov dec").split(), start=1)}
_WHITESPACE = re.compile(r"\s+")


def _text(element: ET.Element | None) -> str | None:
    """All text inside `element` (inline markup such as <i>/<sub> flattened), whitespace-collapsed."""
    if element is None:
        return None
    value = _WHITESPACE.sub(" ", "".join(element.itertext())).strip()
    return value or None


def _month(value: str | None) -> int | None:
    if not value:
        return None
    if value.isdigit():
        number = int(value)
        return number if 1 <= number <= 12 else None
    return _MONTHS.get(value[:3].lower())


def _publication_date(pub_date: ET.Element | None) -> tuple[int | None, str | None]:
    if pub_date is None:
        return None, None
    year_text = pub_date.findtext("Year")
    if year_text is None:
        # MedlineDate is free text such as "2019 Jan-Feb" or "1998 Dec-1999 Jan": only the first year is reliable.
        match = re.match(r"\s*(\d{4})", pub_date.findtext("MedlineDate") or "")
        return (int(match.group(1)), match.group(1)) if match else (None, None)
    if not year_text.isdigit():
        return None, None
    year = int(year_text)
    month = _month(pub_date.findtext("Month"))
    if month is None:
        return year, f"{year:04d}"
    day_text = pub_date.findtext("Day")
    if day_text and day_text.isdigit():
        try:
            return year, datetime(year, month, int(day_text)).date().isoformat()
        except ValueError:
            pass
    return year, f"{year:04d}-{month:02d}"


def _authors(author_list: ET.Element | None) -> list[str]:
    if author_list is None:
        return []
    names: list[str] = []
    for author in author_list.findall("Author"):
        if (collective := _text(author.find("CollectiveName"))) is not None:
            names.append(collective)
            continue
        last = _text(author.find("LastName"))
        initials = _text(author.find("Initials")) or _text(author.find("ForeName"))
        if last:
            names.append(f"{last} {initials}" if initials else last)
    return names


def _abstract(abstract: ET.Element | None) -> list[AbstractSection]:
    if abstract is None:
        return []
    sections: list[AbstractSection] = []
    for part in abstract.findall("AbstractText"):
        if (text := _text(part)) is not None:
            sections.append(AbstractSection(label=part.get("Label") or None, text=text))
    return sections


def _identifiers(article_ids: ET.Element | None) -> dict[str, str]:
    """Record-level IDs only. ArticleIdLists nested in ReferenceList describe *cited* works and are ignored."""
    found: dict[str, str] = {}
    if article_ids is None:
        return found
    for item in article_ids.findall("ArticleId"):
        kind, value = item.get("IdType"), (item.text or "").strip()
        if kind and value and kind not in found:
            found[kind] = value
    return found


def _valid(pattern: re.Pattern[str], value: str | None) -> str | None:
    return value if value is not None and pattern.fullmatch(value) else None


def _record(element: ET.Element, retrieved_at: datetime, canonical: str) -> LiteratureRecord | None:
    if element.tag == "PubmedArticle":
        citation = element.find("MedlineCitation")
        article = citation.find("Article") if citation is not None else None
        container, pub_date = citation, article.find("Journal/JournalIssue/PubDate") if article is not None else None
        title = _text(article.find("ArticleTitle")) if article is not None else None
        title = title or (_text(article.find("VernacularTitle")) if article is not None else None)
        abstract_el = article.find("Abstract") if article is not None else None
        authors_el = article.find("AuthorList") if article is not None else None
        journal = _text(article.find("Journal/Title")) if article is not None else None
        types = [t for t in (_text(p) for p in element.iterfind(".//PublicationTypeList/PublicationType")) if t]
        ids = _identifiers(element.find("PubmedData/ArticleIdList"))
    elif element.tag == "PubmedBookArticle":
        container = element.find("BookDocument")
        book = container.find("Book") if container is not None else None
        pub_date = book.find("PubDate") if book is not None else None
        title = _text(container.find("ArticleTitle")) if container is not None else None
        title = title or (_text(book.find("BookTitle")) if book is not None else None)
        abstract_el = container.find("Abstract") if container is not None else None
        authors_el = container.find("AuthorList") if container is not None else None
        journal = None
        types = [t for t in (_text(p) for p in element.iterfind(".//PublicationType")) if t]
        ids = _identifiers(element.find("PubmedBookData/ArticleIdList"))
    else:
        return None

    pmid = _valid(PMID_PATTERN, (container.findtext("PMID") or "").strip() if container is not None else None)
    if pmid is None:
        logger.warning("pubmed record without a valid PMID skipped", extra={"element": element.tag})
        return None

    doi = _valid(DOI_PATTERN, ids.get("doi"))
    pmcid = _valid(PMCID_PATTERN, ids.get("pmc"))
    year, date = _publication_date(pub_date)
    sections = _abstract(abstract_el)
    source_url = f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/"

    return LiteratureRecord(
        source="pubmed",
        source_id=pmid,
        pmid=pmid,
        pmcid=pmcid,
        doi=doi,
        title=title or NO_TITLE,
        abstract="\n\n".join(f"{s.label}: {s.text}" if s.label else s.text for s in sections) or None,
        abstract_sections=sections,
        authors=_authors(authors_el),
        journal=journal,
        published_year=year,
        published_date=date,
        publication_types=types,
        source_url=source_url,
        links=LiteratureLinks(
            pubmed=source_url,
            doi=f"https://doi.org/{quote(doi, safe='/:;()._-')}" if doi else None,
            pmc=f"https://pmc.ncbi.nlm.nih.gov/articles/{pmcid}/" if pmcid else None,
        ),
        retrieved_at=retrieved_at,
        raw_payload_sha256=hashlib.sha256(canonical.encode()).hexdigest(),
        normalizer_version=NORMALIZER_VERSION,
    )


def parse_efetch(xml: bytes, retrieved_at: datetime) -> list[tuple[LiteratureRecord, str]]:
    """Return `(record, canonical raw XML of that record)` pairs in document order."""
    try:
        root = SafeET.fromstring(xml)
    except (SafeET.ParseError, ValueError) as exc:
        # defusedxml raises ValueError subclasses for entity/DTD abuse.
        raise UpstreamInvalidResponseError("PubMed returned malformed XML") from exc
    if root.tag != "PubmedArticleSet":
        raise UpstreamInvalidResponseError("PubMed returned an unexpected XML document")

    results: list[tuple[LiteratureRecord, str]] = []
    for element in root:
        # C14N gives a byte-stable serialization, so the checksum identifies the exact payload we normalized.
        canonical = ET.canonicalize(ET.tostring(element, encoding="unicode"))
        record = _record(element, retrieved_at, canonical)
        if record is not None:
            results.append((record, canonical))
    return results
