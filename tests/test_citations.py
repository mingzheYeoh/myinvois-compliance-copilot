"""The citation auditor is what backs every 'citations check out' claim, so it
gets its own test - including the fullwidth brackets the model actually emitted.
"""

from scripts.ask import cited


def test_parses_both_bracket_styles():
    assert cited("threshold is RM1m [FAQ v2026-05-05 §PART 3 Q89, p40]") == {
        ("FAQ", "2026-05-05", "PART 3 Q89", "40")
    }
    assert cited("below RM 1 million【FAQ v2026-05-05 §PART 3 Q89, p40】") == {
        ("FAQ", "2026-05-05", "PART 3 Q89", "40")
    }


def test_parses_dotted_sections_and_multiword_docs():
    got = cited("a [Specific Guideline v4.8 §17.1, p123] and b [Guideline v4.8 §2.4.1.2, p31]")
    assert got == {("Specific Guideline", "4.8", "17.1", "123"),
                   ("Guideline", "4.8", "2.4.1.2", "31")}


def test_uncited_text_yields_nothing():
    assert cited("Not covered in the guidelines.") == set()
