"""The chunker is the only non-trivial logic in Day 1, so it gets the test.

Every case here is a real defect found in the actual LHDN PDFs, not a
hypothetical. See the docstrings for where each one came from.
"""

import re

from scripts.ingest import (
    LETTER_ROW,
    MAX_CHARS,
    NUMBER_ROW,
    Chunk,
    _chain,
    clean,
    follows,
    pack,
    split_sections,
    subsplit,
    table_rows,
)

GENERAL = re.compile(r"^(\d+\.\d+(?:\.\d+)*)\s+(\S.{0,90})$")
FAQ = re.compile(r"^(\d{1,3})\.\s+([A-Z].{14,})$")
PART = re.compile(r"^(PART\s+\d+)\s*[:.]\s*(.{0,60})$")


def test_splits_on_headings_and_ignores_toc_and_crossrefs():
    pages = [
        (
            8,
            [
                "1.0 INTRODUCTION",
                "To support the growth of the digital economy, e-Invoice is implemented in stages.",
                "1.1 About e-Invoice",
                "An e-Invoice is a digital representation of a transaction.",
                "Refer to 1.5 of the e-Invoice Guideline for further details.",
                "2.0 GETTING READY ........................................ 18",
            ],
        )
    ]
    out, _ = split_sections(pages, GENERAL, None)
    assert [c.section for c in out] == ["1.0", "1.1"]
    assert "for further details" in out[1].text  # cross-ref stays in the body
    assert not any("GETTING READY" in c.text for c in out)  # dot-leader TOC dropped


def test_a_date_is_not_a_section():
    """General Guideline p13 contains '1.7.2021' — a date, not section 1.7.2021."""
    pages = [
        (
            13,
            [
                "1.5 e-Invoice Implementation Timeline",
                "e-Invoice will be implemented in phases.",
                "1.7.2021 was the date the pilot was announced.",
                "1.6 Exemptions from implementing e-Invoice",
            ],
        )
    ]
    out, rejected = split_sections(pages, GENERAL, None)
    assert [c.section for c in out] == ["1.5", "1.6"]
    assert any("1.7.2021" in r for r in rejected)


def test_glossary_version_number_is_not_a_section():
    """General Guideline p61: 'Version 2.1' cannot follow section 4.0."""
    pages = [
        (
            61,
            [
                "4.0 GLOSSARY",
                "UBL refers to Universal Business Language",
                "2.1 refers to the schema version in use.",
            ],
        )
    ]
    out, rejected = split_sections(pages, GENERAL, None)
    assert [c.section for c in out] == ["4.0"]
    assert rejected


def test_faq_sub_bullets_do_not_restart_question_numbering():
    """FAQ p59: Q126's answer is a numbered list 1..4 — not questions 1..4."""
    pages = [
        (
            59,
            [
                "PART 5: DATA SECURITY",
                "126. How would IRBM monitor and audit the e-Invoice data security?",
                "1. IRBM will assess the data protection needs before monitoring begins.",
                "2. Implementation of data protection controls to protect the data.",
                "127. What measures protect the confidentiality of taxpayer data?",
            ],
        )
    ]
    out, _ = split_sections(pages, FAQ, PART)
    assert [c.section for c in out] == ["PART 5 Q126", "PART 5 Q127"]
    assert "IRBM will assess" in out[0].text  # the list stayed with its question


def test_numbering_continuation_rules():
    assert follows(None, (1,))
    assert follows((1, 6), (1, 6, 1))  # descend one level
    assert follows((1, 6, 8), (2,))  # 1.6.8 -> 2.0
    assert follows((2, 4, 1, 2), (2, 4, 2))  # climb back up
    assert not follows((1, 5), (1, 7, 2021))  # a date
    assert not follows((4,), (2, 1))  # backwards
    assert not follows((126,), (1,))  # sub-bullet


SUB = re.compile(r"^(\d{1,2}\.\d{1,2})(?!\.)\s+\S")


def test_oversized_section_splits_and_keeps_its_label():
    big = Chunk("3", "TRANSACTIONS WITH BUYERS", [(9, "x" * 80)] * 60)
    out = pack([big])
    assert len(out) > 1
    assert all(c.section == "3" and len(c.text) <= MAX_CHARS for c in out)


def test_size_split_cites_the_page_the_text_came_from():
    """A 30-page section must not cite its first page for every chunk."""
    big = Chunk("3", "TRANSACTIONS", [(9 + i // 20, "y" * 90) for i in range(60)])
    pages = [c.page for c in pack([big])]
    assert len(set(pages)) > 1 and pages == sorted(pages)


def test_subsplit_breaks_at_second_level_only():
    """Specific Guideline s14: split at 14.4/14.5, not at 14.4.5."""
    s = Chunk(
        "14",
        "E-COMMERCE TRANSACTIONS",
        [
            (106, "E-COMMERCE TRANSACTIONS"),
            (106, "14.1 E-commerce transaction means any sale or purchase of goods."),
            (107, "14.4 Issuance of e-Invoice from e-commerce platform provider"),
            (107, "14.4.1 Currently, e-commerce platform provider would issue an invoice."),
            (108, "14.4.5 In other words, the merchants are not required to issue."),
            (111, "14.5 Issuance of self-billed e-Invoice by the platform provider"),
        ],
    )
    out = subsplit(s, SUB)
    # The bare section title folds into 14.1; 14.4.x stays inside 14.4.
    assert [c.section for c in out] == ["14.1", "14.4", "14.5"]
    assert "14.4.1" in out[1].text and "14.4.5" in out[1].text
    # Pages follow the text, not the section start.
    assert [c.page for c in out] == [106, 107, 111]


def test_subsplit_ignores_a_quoted_number_from_another_section():
    s = Chunk(
        "14",
        "E-COMMERCE",
        [
            (106, "E-COMMERCE"),
            (106, "14.1 As described above."),
            (107, "3.1 of this Guideline explains the general treatment."),
        ],
    )
    assert [c.section for c in subsplit(s, SUB)] == ["14.1"]


def test_bare_section_title_merges_into_first_subsection():
    """'3 TRANSACTIONS WITH BUYERS' alone is context for 3.1, not a chunk."""
    s = Chunk(
        "3",
        "TRANSACTIONS WITH BUYERS",
        [
            (9, "TRANSACTIONS WITH BUYERS"),
            (9, "3.1 Currently, businesses will issue a receipt or invoice."),
        ],
    )
    out = subsplit(s, SUB)
    assert [c.section for c in out] == ["3.1"]
    assert out[0].text.startswith("TRANSACTIONS WITH BUYERS")


def test_pdf_glyphs_are_normalised():
    assert clean("Malaysia’s tax administration") == "Malaysia's tax administration"
    assert clean(" a bullet point") == "- a bullet point"


# --- table rows (Day 11) ----------------------------------------------------
#
# Day 10 measured context recall at 0.00 on cases whose answer turns on a single
# table cell. The figures were in the corpus the whole time, buried in 1,800-char
# blocks that ranked below question-shaped FAQ entries. These are those tables.

# Guideline 1.6.1, p15 -- verbatim shape, including the (i)(ii) nested under (c).
EXEMPT_161 = [
    (15, "For the purposes of e-Invoice, the following persons are currently"),
    (15, "exempted from issuing e-Invoice (including issuance of self-billed"),
    (15, "e-Invoice):"),
    (15, "(a) Ruler and Ruling Chief"),
    (15, "(b) Former Ruler and Ruling Chief"),
    (15, "(c) Government, state government and local authority"),
    (15, "(i) Ministry of Finance"),
    (15, "(ii) Accountant General's Department"),
    (15, "(d) Consular offices and diplomatic officers"),
    (15, "(e) Taxpayers with an annual turnover or revenue of less than"),
    (15, "RM3,000,000"),
]


def _body(c: Chunk) -> str:
    """The row itself, without the preamble that rides along as a header."""
    return "\n".join(t for _, t in c.lines)


def test_a_lettered_table_becomes_one_chunk_per_row_carrying_the_preamble():
    out = table_rows(Chunk("1.6.1", "Exempted persons", list(EXEMPT_161)))
    assert out is not None
    bodies = [_body(c) for c in out]
    assert [b[:3] for b in bodies] == ["(a)", "(b)", "(c)", "(d)", "(e)"]
    assert "RM3,000,000" in bodies[-1]

    preamble = "\n".join(t for _, t in EXEMPT_161[:3])
    assert all(c.header == preamble for c in out)  # a row still says what it is a row of
    assert all(c.text.startswith(preamble) for c in out)
    assert all(c.section == "1.6.1" for c in out)  # citations stay stable


def test_a_nested_roman_marker_does_not_cut_the_row_it_belongs_to():
    """(i) is one letter in parens, so LETTER_ROW fires on it. Without the chain
    check it would start a row -- orphaning (i) and truncating (c) of the detail
    that belongs to it."""
    assert LETTER_ROW.match("(i) Ministry of Finance")  # the pattern really does fire

    out = table_rows(Chunk("1.6.1", "Exempted persons", list(EXEMPT_161)))
    (c_row,) = [b for b in map(_body, out) if b.startswith("(c)")]
    assert "Ministry of Finance" in c_row
    assert "Accountant General" in c_row


def test_a_date_line_inside_a_table_does_not_start_a_row():
    """NUMBER_ROW fires on any line opening with a small number, "2 January 2026"
    among them. Only a real table numbers its rows consecutively."""
    assert NUMBER_ROW.match("2 January 2026 is when Phase 4 taxpayers must comply.")

    lines = [
        (63, "Types of income or expense requiring a self-billed e-Invoice:"),
        (63, "1 Payment to agents, dealers or distributors"),
        (63, "2 Goods sold or services rendered by foreign suppliers"),
        (63, "3 Profit distribution, e.g. dividend distribution"),
        (63, "2 January 2026 is when Phase 4 taxpayers must comply."),
        (63, "4 Claims or benefit payments from the insurance business"),
        (63, "5 Interest payment"),
    ]
    bodies = [_body(c) for c in table_rows(Chunk("8.3", "Self-billed", lines))]
    assert [b[0] for b in bodies] == ["1", "2", "3", "4", "5"]
    assert "2 January 2026" in bodies[2]  # stayed inside row 3, not a row of its own


def test_section_numbering_is_never_mistaken_for_a_table_row():
    """"3.7.1 For the purposes" has a dot after the digits, never a space."""
    assert NUMBER_ROW.match("6 Payment to agents")
    assert not NUMBER_ROW.match("3.7.1 For the purposes of this section")
    assert not NUMBER_ROW.match("2024 was the first phase")  # four digits, not a row
    assert LETTER_ROW.match("(e) Taxpayers with an annual turnover")
    assert not LETTER_ROW.match("(e)Taxpayers")  # the space is what marks a row
    assert not LETTER_ROW.match("(ii) Accountant General's Department")


def test_two_markers_are_a_sentence_not_a_table_and_fall_through_to_by_size():
    s = Chunk("2.3", "Types of e-Invoice", [
        (20, "There are two categories of document:"),
        (20, "(a) e-Invoice issued by the supplier"),
        (20, "(b) self-billed e-Invoice issued by the buyer"),
    ])
    assert table_rows(s) is None
    assert [c.text for c in pack([s])] == [s.text]  # one chunk, unchanged


def test_each_row_cites_its_own_page_not_the_page_the_table_opened_on():
    """The preamble rides in `header` and never in `lines` precisely for this:
    page comes from lines[0], so a row on p33 of a table opening on p32 cites p33."""
    lines = [
        (32, "Table 3.6: Activities requiring an e-Invoice"),
        (32, "(a) Sale of goods"),
        (32, "(b) Provision of services"),
        (33, "(c) Payment to agents"),
        (33, "(d) Foreign income"),
    ]
    out = table_rows(Chunk("3.6", "Activities", lines))
    assert [c.page for c in out] == [32, 32, 33, 33]
    assert all(c.header.startswith("Table 3.6") for c in out)


def test_chain_keeps_only_markers_that_step_by_one():
    assert _chain([(0, "a"), (1, "b"), (2, "c")]) == [(0, "a"), (1, "b"), (2, "c")]
    assert _chain([(0, "a"), (1, "c")]) == [(0, "a")]  # a gap is not a table
    assert _chain([(0, "3"), (1, "1"), (2, "2")]) == [(0, "3")]  # prose numbers
    assert _chain([(0, "c"), (1, "i"), (2, "d")]) == [(0, "c"), (2, "d")]  # nested
    assert _chain([(0, "a"), (1, "1")]) == [(0, "a")]  # letters and digits do not mix


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"ok  {name}")
    print("all chunking checks passed")
