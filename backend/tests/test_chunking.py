import pytest

from app.ingestion.chunking import Section, chunk_sections, section_kind, split_sentences


def sentences(text: str) -> list[str]:
    return [text[s:e] for s, e in split_sentences(text)]


def test_sentence_split_respects_abbreviations_initials_and_decimals() -> None:
    text = (
        "Mutations were reported by Smith et al. in 2019. See Fig. 2 for details. "
        "J. Doe confirmed a 2.5-fold increase (p < 0.05). TP53 R175H is common! Is it causal? Unclear."
    )

    assert sentences(text) == [
        "Mutations were reported by Smith et al. in 2019.",
        "See Fig. 2 for details.",
        "J. Doe confirmed a 2.5-fold increase (p < 0.05).",
        "TP53 R175H is common!",
        "Is it causal?",
        "Unclear.",
    ]


def test_sentence_split_keeps_text_without_terminal_punctuation() -> None:
    assert sentences("A heading-like fragment without a period") == ["A heading-like fragment without a period"]


def long_section(kind: str, sentence_count: int) -> Section:
    sentence = "The p53 protein regulates the cell cycle and apoptosis in response to DNA damage. "
    return Section(kind, kind.capitalize(), [sentence * sentence_count])


def test_offsets_address_exactly_the_chunk_text() -> None:
    doc = chunk_sections(
        [Section("title", None, ["A title"]), long_section("introduction", 40), long_section("results", 25)]
    )

    assert doc.chunks
    for chunk in doc.chunks:
        assert doc.text[chunk.start_offset : chunk.end_offset] == chunk.text
    assert [c.ordinal for c in doc.chunks] == list(range(len(doc.chunks)))


def test_chunks_never_cross_sections_and_end_on_sentences() -> None:
    doc = chunk_sections([long_section("methods", 60), long_section("results", 60)], target_tokens=100, max_tokens=150)

    spans = {s.kind: (s.start_offset, s.end_offset) for s in doc.sections}
    for chunk in doc.chunks:
        start, end = spans[chunk.section]
        assert start <= chunk.start_offset and chunk.end_offset <= end
        assert chunk.text.endswith(".")
        assert chunk.token_estimate <= 150
    assert {c.section for c in doc.chunks} == {"methods", "results"}


def test_consecutive_chunks_overlap_by_one_sentence() -> None:
    doc = chunk_sections([long_section("results", 60)], target_tokens=100, max_tokens=150)

    first, second = doc.chunks[0], doc.chunks[1]
    last_sentence_of_first = sentences(first.text)[-1]
    assert second.text.startswith(last_sentence_of_first)
    assert second.start_offset < first.end_offset


def test_oversized_sentence_is_split_at_whitespace_within_the_ceiling() -> None:
    giant = " ".join(f"GENE{i}" for i in range(2000)) + "."

    doc = chunk_sections([Section("results", "Results", [giant])], target_tokens=100, max_tokens=150)

    assert len(doc.chunks) > 1
    assert all(c.token_estimate <= 150 for c in doc.chunks)
    assert all(doc.text[c.start_offset : c.end_offset] == c.text for c in doc.chunks)
    assert " ".join(c.text for c in doc.chunks) == giant


def test_chunking_is_deterministic() -> None:
    sections = [long_section("introduction", 30), long_section("discussion", 30)]

    assert chunk_sections(sections) == chunk_sections(sections)


def test_empty_sections_and_paragraphs_are_dropped() -> None:
    doc = chunk_sections([Section("abstract", "Abstract", ["  ", ""]), Section("results", "Results", ["Found it."])])

    assert [s.kind for s in doc.sections] == ["results"]
    assert doc.text == "Results\n\nFound it."
    assert [c.text for c in doc.chunks] == ["Found it."]


@pytest.mark.parametrize(
    ("title", "sec_type", "kind"),
    [
        ("MATERIALS AND METHODS", None, "methods"),
        ("Results and Discussion", None, "results"),
        ("Background", None, "introduction"),
        ("Concluding remarks", None, "conclusion"),
        (None, "methods", "methods"),
        ("Acknowledgements", None, "other"),
    ],
)
def test_section_kind_normalization(title: str | None, sec_type: str | None, kind: str) -> None:
    assert section_kind(title, sec_type) == kind
