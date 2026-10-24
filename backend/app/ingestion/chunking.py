"""Render structured article text and split it into addressable chunks.

Strategy `section-sentence-v1`:
- chunks never cross a section boundary (Methods and Results carry different evidential weight);
- chunks end on sentence boundaries; one trailing sentence is repeated at the start of the next chunk;
- target ~350 tokens, hard ceiling 600 (the blueprint's 300-700 starting range), estimated as 4 chars/token;
- every chunk records `[start_offset, end_offset)` into the rendered document text, and
  `text[start_offset:end_offset] == chunk.text` holds by construction.
"""

import math
import re
from dataclasses import dataclass

CHUNKER_VERSION = "section-sentence-v1"
CHARS_PER_TOKEN = 4

SECTION_KINDS = ("title", "abstract", "introduction", "methods", "results", "discussion", "conclusion", "other")
_KIND_KEYWORDS = (
    ("abstract", ("abstract", "summary")),
    ("introduction", ("introduction", "background")),
    ("methods", ("method", "materials", "experimental procedure", "patients and", "study design")),
    ("results", ("result", "findings")),
    ("discussion", ("discussion",)),
    ("conclusion", ("conclusion", "concluding")),
)


def section_kind(title: str | None, sec_type: str | None = None) -> str:
    """Normalize a heading (or JATS sec-type) to one of SECTION_KINDS."""
    for candidate in (sec_type, title):
        lowered = (candidate or "").lower()
        for kind, keywords in _KIND_KEYWORDS:
            if any(k in lowered for k in keywords):
                return kind
    return "other"


@dataclass(frozen=True)
class Section:
    kind: str
    title: str | None
    paragraphs: list[str]


@dataclass(frozen=True)
class Chunk:
    ordinal: int
    section: str
    section_title: str | None
    start_offset: int
    end_offset: int
    text: str

    @property
    def token_estimate(self) -> int:
        return estimate_tokens(self.text)


@dataclass(frozen=True)
class SectionSpan:
    kind: str
    title: str | None
    start_offset: int
    end_offset: int


@dataclass(frozen=True)
class ChunkedDocument:
    text: str
    sections: list[SectionSpan]
    chunks: list[Chunk]


def estimate_tokens(text: str) -> int:
    return math.ceil(len(text) / CHARS_PER_TOKEN)


# Words after which a period does not end a sentence.
_ABBREVIATIONS = frozenset(
    "al e.g i.e fig figs vs approx ca no nos dr ref refs eq eqs cf spp sp etc mr ms prof st suppl vol resp".split()
)
_BOUNDARY = re.compile(r"[.!?][\"')\]]*\s+(?=[\"'(\[]?[A-Z0-9])")


def split_sentences(paragraph: str) -> list[tuple[int, int]]:
    """Sentence spans `(start, end)` within `paragraph` (end exclusive, trailing whitespace excluded)."""
    spans: list[tuple[int, int]] = []
    start = 0
    for match in _BOUNDARY.finditer(paragraph):
        punct = match.start()
        word = re.search(r"(\S+)$", paragraph[start:punct])
        token = word.group(1).lower().rstrip(".") if word else ""
        if token in _ABBREVIATIONS or (len(token) == 1 and token.isalpha()):
            continue  # "et al.", "Fig.", initials such as "J."
        end = match.end() - (len(match.group(0)) - len(match.group(0).rstrip()))
        spans.append((start, end))
        start = match.end()
    if paragraph[start:].strip():
        spans.append((start, len(paragraph.rstrip())))
    return spans


def chunk_sections(
    sections: list[Section], *, target_tokens: int = 350, max_tokens: int = 600, overlap_sentences: int = 1
) -> ChunkedDocument:
    parts: list[str] = []
    cursor = 0
    chunks: list[Chunk] = []

    def emit(text: str) -> int:
        nonlocal cursor
        offset = cursor
        parts.append(text)
        cursor += len(text)
        return offset

    pending: list[tuple[Section, int, list[tuple[int, int]]]] = []
    for section in sections:
        paragraphs = [p.strip() for p in section.paragraphs if p.strip()]
        if not paragraphs:
            continue
        if parts:
            emit("\n\n")
        section_start = cursor
        if section.title and section.kind != "title":
            emit(section.title + "\n\n")
        sentences: list[tuple[int, int]] = []
        for index, paragraph in enumerate(paragraphs):
            if index:
                emit("\n\n")
            base = emit(paragraph)
            for start, end in split_sentences(paragraph):
                sentences.extend(_bounded(base + start, base + end, paragraph, base, max_tokens))
        pending.append((section, section_start, sentences))

    text = "".join(parts)
    spans: list[SectionSpan] = []
    for index, (section, section_start, sentences) in enumerate(pending):
        section_end = pending[index + 1][1] - 2 if index + 1 < len(pending) else len(text)
        spans.append(SectionSpan(section.kind, section.title, section_start, section_end))
        for start, end in _pack(sentences, text, target_tokens, max_tokens, overlap_sentences):
            chunks.append(Chunk(len(chunks), section.kind, section.title, start, end, text[start:end]))
    return ChunkedDocument(text=text, sections=spans, chunks=chunks)


def _bounded(start: int, end: int, paragraph: str, base: int, max_tokens: int) -> list[tuple[int, int]]:
    """Split a sentence longer than `max_tokens` at whitespace (rare: tables flattened to prose, gene lists)."""
    limit = max_tokens * CHARS_PER_TOKEN
    pieces: list[tuple[int, int]] = []
    while end - start > limit:
        local = paragraph[start - base : start - base + limit]
        cut = local.rfind(" ")
        cut = cut if cut > 0 else limit
        pieces.append((start, start + cut))
        start += cut
        while start < end and paragraph[start - base].isspace():
            start += 1
    pieces.append((start, end))
    return pieces


def _pack(
    sentences: list[tuple[int, int]], text: str, target_tokens: int, max_tokens: int, overlap: int
) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    index = 0
    while index < len(sentences):
        first = index
        last = index
        while last + 1 < len(sentences):
            size = estimate_tokens(text[sentences[first][0] : sentences[last + 1][1]])
            current = estimate_tokens(text[sentences[first][0] : sentences[last][1]])
            if size > max_tokens or current >= target_tokens:
                break
            last += 1
        spans.append((sentences[first][0], sentences[last][1]))
        if last + 1 >= len(sentences):
            break
        # Overlap: restart from the trailing sentence(s), but always make progress.
        index = max(last + 1 - overlap, first + 1)
    return spans
