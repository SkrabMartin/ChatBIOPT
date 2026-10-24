"""Extract readable, sectioned text from Europe PMC JATS full-text XML.

Kept: article title, abstract, body section paragraphs, figure/table captions. Dropped: math, table cells,
reference lists, supplementary material and back matter. Inline markup is flattened to plain text.
The XML is untrusted input: it is parsed with defusedxml and nothing in it is ever executed or rendered as HTML.
"""

import re
import xml.etree.ElementTree as ET  # only walks trees produced by defusedxml
from dataclasses import dataclass

from defusedxml import ElementTree as SafeET

from app.core.errors import UpstreamInvalidResponseError
from app.ingestion.chunking import Section, section_kind

PARSER_VERSION = "jats-v1"

_XLINK_HREF = "{http://www.w3.org/1999/xlink}href"
_MATHML = "{http://www.w3.org/1998/Math/MathML}"
# Subtrees whose text is not prose.
_SKIP = frozenset(
    {
        "table",
        "disp-formula",
        "inline-formula",
        "tex-math",
        "graphic",
        "media",
        "alternatives",
        "object-id",
        "supplementary-material",
    }
)
_WHITESPACE = re.compile(r"\s+")


@dataclass(frozen=True)
class JatsArticle:
    title: str | None
    sections: list[Section]
    license_url: str | None
    license_type: str | None


def _text(element: ET.Element) -> str:
    parts: list[str] = []

    def walk(node: ET.Element) -> None:
        tag = node.tag if isinstance(node.tag, str) else ""
        if tag in _SKIP or tag.startswith(_MATHML):
            if node.tail:
                parts.append(node.tail)
            return
        if node.text:
            parts.append(node.text)
        for child in node:
            walk(child)
        if node is not element and node.tail:
            parts.append(node.tail)

    walk(element)
    return _WHITESPACE.sub(" ", "".join(parts)).strip()


def _paragraphs(container: ET.Element) -> list[str]:
    """Paragraphs directly in `container` plus figure/table captions, in document order (not nested sections)."""
    found: list[str] = []
    for child in container:
        if child.tag == "p":
            if text := _text(child):
                found.append(text)
            # Captions of figures/tables nested inside the paragraph.
            for nested in child.iter():
                if nested.tag in ("fig", "table-wrap") and (caption := nested.find("caption")) is not None:
                    if text := _text(caption):
                        found.append(text)
        elif child.tag in ("fig", "table-wrap") and (caption := child.find("caption")) is not None:
            if text := _text(caption):
                found.append(text)
        elif child.tag in ("list", "boxed-text", "disp-quote"):
            if text := _text(child):
                found.append(text)
    return found


def _walk_sections(sec: ET.Element, path: list[str], kind: str | None, out: list[Section]) -> None:
    title = _text(title_el) if (title_el := sec.find("title")) is not None else None
    if sec.get("sec-type") == "supplementary-material":
        return
    heading = [*path, title] if title else path
    own_kind = kind or section_kind(title, sec.get("sec-type"))
    if paragraphs := _paragraphs(sec):
        out.append(Section(own_kind, " > ".join(heading) or None, paragraphs))
    for child in sec.findall("sec"):
        _walk_sections(child, heading, own_kind, out)


def parse_jats(xml: bytes) -> JatsArticle:
    try:
        root = SafeET.fromstring(xml)
    except (SafeET.ParseError, ValueError) as exc:
        raise UpstreamInvalidResponseError("Europe PMC returned malformed full-text XML") from exc
    if root.tag != "article":
        raise UpstreamInvalidResponseError("Europe PMC returned an unexpected full-text document")

    meta = root.find("front/article-meta")
    title_el = meta.find("title-group/article-title") if meta is not None else None
    title = _text(title_el) if title_el is not None else None
    sections: list[Section] = []
    if title:
        sections.append(Section("title", None, [title]))

    if meta is not None:
        # The main abstract only (no "graphical"/"teaser" variants), which may itself be structured.
        for abstract in meta.findall("abstract"):
            if abstract.get("abstract-type") not in (None, "", "summary"):
                continue
            if abstract.findall("sec"):
                for sec in abstract.findall("sec"):
                    label = _text(t) if (t := sec.find("title")) is not None else None
                    if paragraphs := _paragraphs(sec):
                        sections.append(Section("abstract", f"Abstract > {label}" if label else "Abstract", paragraphs))
            elif paragraphs := _paragraphs(abstract):
                sections.append(Section("abstract", "Abstract", paragraphs))
            break

    body = root.find("body")
    if body is not None:
        if paragraphs := _paragraphs(body):
            sections.append(Section("other", None, paragraphs))
        for sec in body.findall("sec"):
            _walk_sections(sec, [], None, sections)

    license_el = meta.find("permissions/license") if meta is not None else None
    license_url = None
    license_type = None
    if license_el is not None:
        license_type = license_el.get("license-type")
        license_url = license_el.get(_XLINK_HREF)
        if license_url is None and (ext := license_el.find(".//ext-link")) is not None:
            license_url = ext.get(_XLINK_HREF)
    return JatsArticle(title=title, sections=sections, license_url=license_url, license_type=license_type)
