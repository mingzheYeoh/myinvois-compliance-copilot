"""One definition of what a citation looks like, and one place that normalises it.

The pattern used to live in three files (the API, ask.py, eval.py) with a fourth
copy in a test. Three copies of a regex is three chances for them to drift apart,
and the one that matters here is the page: CITE requires `p<digits>`, so any
citation carrying a page RANGE parses as no citation at all and is silently
dropped from the answer's citation list.

Day 8 hit that from the data side -- five params.py refs written "p32-33" -- and
fixed it by normalising the params. Day 11 hit it from the other side: the model
emitted "[Guideline v4.8 §Appendix 1, p44-p50]" on its own, and the citation
vanished again. Normalising the data was never enough; the model's output needs
the same treatment, so the normaliser lives with the pattern and both sides call it.
"""

from __future__ import annotations

import re

CITE = re.compile(r"[\[【]([^\]】]+?) v([^ \]】]+) §([^,\]】]+), ?p(\d+)[\]】]")

# "p44-p50", "p32-33", "p32–33" (en dash), "pp. 44-50", "pp.44—50" -> "p44".
# The p prefix is what keeps this off ordinary hyphenated numbers: a year range
# like "2024-2025" or "Phase 1-3" has no p in front of it and is left alone.
PAGE_RANGE = re.compile(r"\bpp?\.?\s*(\d+)\s*[-‐-―]\s*(?:pp?\.?\s*)?\d+", re.I)
# A plural "pp. 44" with no range still has to become the singular the pattern wants.
PAGE_PLURAL = re.compile(r"\bpp\.?\s*(\d+)", re.I)


def normalise_pages(text: str) -> str:
    """Collapse any page range to the page the cited text starts on.

    A range is not more precise than its first page -- it is less: the reader is
    sent to a span instead of a place. Taking the first page keeps the citation
    parseable and points where the section begins, which is what every other
    citation in the corpus already does.
    """
    return PAGE_PLURAL.sub(r"p\1", PAGE_RANGE.sub(r"p\1", text))


def parse(text: str) -> set[tuple[str, str, str, str]]:
    """Every (doc, version, section, page) citation in `text`, ranges folded first."""
    return {m.groups() for m in CITE.finditer(normalise_pages(text))}
