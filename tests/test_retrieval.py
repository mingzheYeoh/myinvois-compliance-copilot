"""The parsing contract behind section-pinned retrieval.

`search_sections()` exists because appending "§1.6.1(e)" to the query string
retrieves nothing -- the guideline body never writes its own section numbers, so
the label matches no text. The cited section is fetched by metadata instead.

Its SQL half needs a live Postgres and is NOT exercised here: that `§1.6.1(e)`
resolves to the row holding RM3,000,000 rather than to the 1.6 heading is proved
end-to-end by the golden set (`tests/test_golden.py`, marked `slow`). What is
covered here is the parsing the SQL depends on, which is where a change to the
citation format would break pinned retrieval silently -- returning nothing rather
than raising, so every affected answer would simply get worse.
"""

from app.rag.retriever import LONG, MARKER, REF, latest_versions


def test_ref_splits_an_engine_citation_into_document_and_section():
    assert REF.match("Guideline v4.8 §1.6.1(e)").groups() == ("Guideline", "1.6.1(e)")
    assert REF.match("Specific Guideline v4.8 §11.1.2").groups() == (
        "Specific Guideline", "11.1.2")
    assert REF.match("FAQ v2026-05-05 §PART 1 Q12").groups() == ("FAQ", "PART 1 Q12")


def test_ref_stops_at_the_page_so_a_full_citation_parses_the_same():
    """The engine writes refs without a page; an answer's citation carries one.
    Both must yield the same section or the pin and the audit disagree."""
    assert REF.match("Guideline v4.8 §1.6.1(e), p15").group(2) == "1.6.1(e)"


def test_a_table_label_is_stripped_before_the_section_lookup():
    """The engine cites "§16.1 Table 16.1"; the stored section is "16.1"."""
    section = REF.match("Specific Guideline v4.8 §16.1 Table 16.1").group(2)
    assert section.split(" Table")[0] == "16.1"


def test_the_row_marker_is_what_makes_a_table_citation_resolve():
    """Now that a table is one chunk per row, "§1.6.1(e)" has to reach the row
    holding RM3,000,000 and not whichever row of 1.6.1 sorts first. PINNED_SQL
    orders the marker match ahead of everything else, so extracting it matters."""
    assert MARKER.search("1.6.1(e)").group(1) == "(e)"
    assert MARKER.search("11.1.2") is None
    assert MARKER.search("Appendix 1") is None


def test_every_corpus_document_has_a_citation_name():
    """A document in the manifest with no entry in LONG is unreachable by pinned
    retrieval: search_sections would quietly return nothing for all its sections."""
    corpus = {v.split(":")[0] for v in latest_versions()}
    assert corpus == set(LONG.values())


def test_a_statutory_reference_is_not_looked_up_as_a_guideline_section():
    """Answers cite "Section 108 of the Income Tax Act 1967" alongside guideline
    sections. REF matches the shape, so the doc-name lookup is what rejects it."""
    m = REF.match("Income Tax Act v1967 §108")
    assert m is not None
    assert LONG.get(m.group(1).strip()) is None
