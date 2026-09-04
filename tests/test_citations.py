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


def test_every_rule_engine_citation_is_parseable():
    """A page RANGE silently vanishes: CITE ends in p(digits) followed by the
    closing bracket, so "p32-33" matches nothing and the citation is dropped
    from the answer audit and from /chat's citation list. Day 8: the industries
    that may never consolidate were answered correctly and cited invisibly.
    Chunks carry one page each, so a range cannot be checked against one either.
    """
    from app.rag.citations import CITE
    from app.rules import params

    bad = [
        f"{version} {key} = {value}"
        for version in ("4.8", "4.6")
        for key, value in params.get(version).items()
        if key.endswith("_ref") and isinstance(value, str)
        and value.startswith(("Guideline", "Specific Guideline", "FAQ"))
        and not CITE.fullmatch(f"[{value}]")
    ]
    assert not bad, "unparseable citations: " + "; ".join(bad)
