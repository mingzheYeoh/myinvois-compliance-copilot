"""Extract Appendix 1 (mandatory/optional e-Invoice fields) into data/rules/.

    uv run python scripts/extract_fields.py

pdfplumber's extract_tables() handles these pages; the only cleanup needed is
the letter-spacing artefact ("S upplier's Name") and the status marker, which
LHDN embeds in the field-name cell rather than giving it a column.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pdfplumber

ROOT = Path(__file__).resolve().parents[1]
PDF = ROOT / "data" / "raw" / "irbm-e-invoice-guideline.pdf"
OUT = ROOT / "data" / "rules" / "invoice_fields.json"
PAGES = range(44, 53)

SPACED = re.compile(r"^([A-Za-z]{1,2}) (?=[a-z\-])")
MARKER = re.compile(r"\[(Optional|Mandatory[^\]]*)\]", re.I)


def clean(cell: str) -> str:
    return SPACED.sub(r"\1", " ".join((cell or "").split())).replace("\u2019", "'")


def main() -> int:
    fields, category = [], None
    with pdfplumber.open(PDF) as pdf:
        for pg in PAGES:
            for row in pdf.pages[pg - 1].extract_tables()[0]:
                cells = [clean(c) for c in row if c and c.strip()]
                if not cells or cells[0] in ("No.", "No"):
                    continue
                if len(cells) == 1:
                    # A category banner such as "Parties" is short and headline
                    # shaped. Footnotes ("* characters). For taxpayers who...")
                    # also arrive as single cells and must not become categories.
                    banner = cells[0]
                    if len(banner) <= 40 and banner[:1].isupper() and "." not in banner:
                        category = banner
                    continue
                if not re.fullmatch(r"\d+\.", cells[0]):
                    continue
                num = int(cells[0].rstrip("."))
                name, desc = cells[1], (cells[2] if len(cells) > 2 else "")
                m = MARKER.search(name)
                marker = m.group(1) if m else ""
                status = ("optional" if marker.lower() == "optional"
                          else "conditional" if marker else "mandatory")
                fields.append({
                    "no": num,
                    "name": MARKER.sub("", name).strip(),
                    "category": category,
                    "status": status,
                    "condition": marker if status == "conditional" else None,
                    # Appendix 1 is the general list. The self-billed and
                    # consolidated variants are Specific Guideline appendix
                    # tables, which are not ingested yet.
                    "applies_to": "all",
                    "page": pg,
                    "description": desc,
                })
    doc = {
        "_source": {
            "doc": "e-Invoice Guideline",
            "version": "4.8",
            "published": "2026-08-30",
            "section": "Appendix 1",
            "table": "List of mandatory and optional fields for the e-Invoice",
            "pages": [PAGES.start, PAGES.stop - 1],
            "extraction": "pdfplumber extract_tables() per page, cleaned by "
                          "scripts/extract_fields.py; status is parsed from the "
                          "[Optional] / [Mandatory, where ...] marker LHDN embeds "
                          "in the field-name cell",
            "note": "'applies_to' is 'all' for every row: Appendix 1 is the general "
                    "list. Self-billed and consolidated field variants live in "
                    "Specific Guideline appendix tables and are not ingested yet.",
        },
        "fields": fields,
    }
    OUT.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    counts = {s: sum(f["status"] == s for f in fields) for s in
              ("mandatory", "conditional", "optional")}
    print(f"{len(fields)} fields -> {OUT.relative_to(ROOT)}  {counts}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
