import re
from pathlib import Path

import pytest

from app.core.errors import UpstreamInvalidResponseError
from app.ingestion.licensing import KNOWN_LICENSES, decide_full_text, normalize_license
from app.integrations.europepmc.jats import parse_jats
from app.storage.object_store import FilesystemObjectStore, ObjectStoreError
from tests.fakes import FULLTEXT_FIXTURE

ALL = frozenset(KNOWN_LICENSES)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("cc by", "CC-BY"),
        ("CC BY 4.0", "CC-BY"),
        ("cc by-nc-nd", "CC-BY-NC-ND"),
        ("CC-BY-NC-SA", "CC-BY-NC-SA"),
        ("cc0", "CC0"),
        ("http://creativecommons.org/licenses/by/3.0/", "CC-BY"),
        ("https://creativecommons.org/licenses/by-nc/4.0/legalcode", "CC-BY-NC"),
        ("https://creativecommons.org/publicdomain/zero/1.0/", "CC0"),
        ("cc by-xyz", None),
        ("author manuscript", None),
        ("", None),
        (None, None),
    ],
)
def test_license_normalization(value: str | None, expected: str | None) -> None:
    assert normalize_license(value) == expected


@pytest.mark.parametrize(
    ("is_oa", "license_value", "allowed", "stored", "reason"),
    [
        (True, "cc by", ALL, True, "open access under CC-BY"),
        (True, None, ALL, False, "no explicit open license"),
        (True, "publisher-specific", ALL, False, "unrecognised license"),
        (False, "cc by", ALL, False, "not in the open-access subset"),
        (True, "cc by-nc-nd", frozenset({"CC-BY", "CC0"}), False, "CC-BY-NC-ND is not allowed"),
    ],
    ids=["cc-by", "oa-without-license", "unknown-license", "not-oa", "policy-excludes"],
)
def test_full_text_license_decision(
    is_oa: bool, license_value: str | None, allowed: frozenset[str], stored: bool, reason: str
) -> None:
    decision = decide_full_text(is_open_access=is_oa, license_value=license_value, allowed=allowed)

    assert decision.allowed is stored
    assert reason in decision.reason


def test_jats_parser_extracts_sections_license_and_drops_non_prose() -> None:
    article = parse_jats(FULLTEXT_FIXTURE)

    assert article.title is not None and article.title.startswith("Treatment-associated TP53")
    assert article.license_url == "http://creativecommons.org/licenses/by/3.0/"
    kinds = [s.kind for s in article.sections]
    assert kinds[0] == "title" and kinds[1] == "abstract"
    assert {"introduction", "results", "discussion", "methods"} <= set(kinds)
    nested = [s.title for s in article.sections if s.kind == "results" and s.title and " > " in s.title]
    assert nested, "nested result subsections keep their heading path"
    text = "\n".join(p for s in article.sections for p in s.paragraphs)
    assert not re.search(r"</?[a-z][\w:-]*[\s>/]", text), "no XML/HTML tags leak into extracted text"
    assert "<10% tumor cells" in text, "literal '<' in prose is preserved"
    assert "SUPPLEMENTARY" not in " ".join(s.title or "" for s in article.sections)


@pytest.mark.parametrize(
    "payload",
    [b"<html>maintenance</html>", b"not xml", b'<!DOCTYPE a [<!ENTITY x "y">]><article>&x;</article>'],
    ids=["wrong-root", "garbage", "entities"],
)
def test_jats_parser_rejects_bad_payloads(payload: bytes) -> None:
    with pytest.raises(UpstreamInvalidResponseError):
        parse_jats(payload)


def test_object_store_is_content_addressed_and_rejects_path_escapes(tmp_path: Path) -> None:
    store = FilesystemObjectStore(tmp_path)

    stored = store.put("documents/pubmed/1/abstract/abc.txt", b"hello")
    again = store.put("documents/pubmed/1/abstract/abc.txt", b"hello")

    assert stored == again
    assert store.get(stored.key) == b"hello"
    with pytest.raises(ObjectStoreError, match="different content"):
        store.put("documents/pubmed/1/abstract/abc.txt", b"tampered")
    for bad in ("../etc/passwd", "/abs/path", "a/../../b", "UPPER/case", "a\\b", ""):
        with pytest.raises(ObjectStoreError):
            store.put(bad, b"x")
