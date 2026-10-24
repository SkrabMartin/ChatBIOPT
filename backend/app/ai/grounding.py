"""Grounded answering: evidence packaging for the prompt and server-side validation of what the model cited.

The model writes `[E1]`-style markers. The server keeps only markers that name evidence actually given to this
run, strips the rest (counted as invalid), and splits the answer into sentence-level claims. A valid marker means
"the model pointed at this passage", not "the passage supports the sentence"; that judgement is left to review.
"""

import re
from dataclasses import dataclass, field

from app.ingestion.chunking import split_sentences
from app.retrieval.search import Hit

EXTRACTION_VERSION = "citation-markers-v1"
ANSWER_CLASSES = ("supported", "partially_supported", "conflicting", "insufficient_evidence")

_STATUS = re.compile(r"^\s*\**\s*STATUS\s*:\s*\**\s*([a-z_]+)\s*\**\s*$", re.IGNORECASE)
# "[E1]", "[E1, E3]", "[E1][E2]", "[E 2]"
_MARKER = re.compile(r"\[\s*(E\s*\d+(?:\s*[,;]\s*E\s*\d+)*)\s*\]", re.IGNORECASE)
_EVIDENCE_TAG = re.compile(r"</?\s*evidence\s*>", re.IGNORECASE)


def label(index: int) -> str:
    return f"E{index + 1}"


def render_evidence(hits: list[Hit]) -> str:
    """Evidence block for the prompt. Passage text cannot close or reopen the block."""
    parts: list[str] = []
    for index, hit in enumerate(hits):
        where = " > ".join(p for p in (hit.section_title or hit.section,) if p)
        meta = " · ".join(str(p) for p in (f"PMID {hit.external_id}", hit.journal, hit.published_year, where) if p)
        body = _EVIDENCE_TAG.sub("[tag removed]", hit.text)
        parts.append(f"[{label(index)}] {hit.source_title} ({meta})\n{body}")
    return "<evidence>\n" + "\n\n".join(parts) + "\n</evidence>"


@dataclass(frozen=True)
class ClaimDraft:
    ordinal: int
    text: str
    labels: list[str]


@dataclass
class ParsedAnswer:
    content: str
    declared_status: str | None
    answer_class: str
    claims: list[ClaimDraft]
    cited_labels: set[str]
    stats: dict[str, int] = field(default_factory=dict)


def _normalize_label(raw: str) -> str:
    return "E" + re.sub(r"\D", "", raw)


def parse_answer(raw: str, valid_labels: set[str]) -> ParsedAnswer:
    lines = raw.strip().splitlines()
    declared: str | None = None
    if lines and (match := _STATUS.match(lines[0])):
        value = match.group(1).lower()
        declared = value if value in ANSWER_CLASSES else None
        lines = lines[1:]
    body = "\n".join(lines).strip()

    valid_markers = invalid_markers = 0

    def keep_valid(match: re.Match[str]) -> str:
        nonlocal valid_markers, invalid_markers
        labels = [_normalize_label(part) for part in re.split(r"[,;]", match.group(1))]
        kept = [lab for lab in labels if lab in valid_labels]
        valid_markers += len(kept)
        invalid_markers += len(labels) - len(kept)
        return f"[{', '.join(kept)}]" if kept else ""

    content = _MARKER.sub(keep_valid, body)
    content = re.sub(r"[ \t]+([.,;:])", r"\1", content)  # tidy space left by a removed marker

    claims: list[ClaimDraft] = []
    cited: set[str] = set()
    for paragraph in re.split(r"\n\s*\n", content):
        for line in paragraph.splitlines():
            text = line.strip().lstrip("-*•#> ").strip()
            for start, end in split_sentences(text):
                sentence = text[start:end].strip()
                labels = sorted({_normalize_label(m) for g in _MARKER.findall(sentence) for m in re.split(r"[,;]", g)})
                plain = re.sub(r"\s+([.,;:!?])", r"\1", _MARKER.sub("", sentence)).strip()
                if sum(ch.isalpha() for ch in plain) < 12:
                    continue  # headings, list markers, fragments: not claims
                claims.append(ClaimDraft(len(claims), plain, labels))
                cited.update(labels)

    uncited = sum(1 for c in claims if not c.labels)
    if declared == "insufficient_evidence":
        answer_class = "insufficient_evidence"
    elif not cited:
        # The model answered but pointed at no evidence: nothing here is grounded.
        answer_class = "unclassified"
    elif declared == "conflicting":
        answer_class = "conflicting"
    elif declared == "partially_supported" or uncited:
        answer_class = "partially_supported"
    elif declared == "supported":
        answer_class = "supported"
    else:
        answer_class = "unclassified"

    return ParsedAnswer(
        content=content,
        declared_status=declared,
        answer_class=answer_class,
        claims=claims,
        cited_labels=cited,
        stats={
            "evidence": len(valid_labels),
            "cited_evidence": len(cited),
            "valid_markers": valid_markers,
            "invalid_markers": invalid_markers,
            "claims": len(claims),
            "uncited_claims": uncited,
        },
    )


def insufficient_evidence_message(source_count: int, warnings: list[str]) -> str:
    lines = [
        "**Insufficient evidence.** No passage in this project's evidence library matched the question closely "
        f"enough to answer it from sources ({source_count} source(s) searched).",
        "",
        "Add relevant publications from Literature to the project, or rephrase the question with specific gene, "
        "variant or disease names.",
    ]
    lines += [f"\n_Note: {w}_" for w in warnings]
    return "\n".join(lines)
