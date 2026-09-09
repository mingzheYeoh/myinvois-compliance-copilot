"""Check an invoice against Appendix 1. Pure Python: no LLM, no retrieval.

Same reasoning as the rule engine. Whether a field is mandatory is a fact in a
published table, and a model reading that table is a worse oracle than the table.
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

RULES = Path(__file__).resolve().parents[3] / "data" / "rules" / "invoice_fields.json"


class FieldIssue(BaseModel):
    no: int
    name: str
    category: str | None
    status: str
    condition: str | None = None
    section: str


class FieldReport(BaseModel):
    valid: bool
    checked: int = 0
    present: list[str] = Field(default_factory=list)
    missing_mandatory: list[FieldIssue] = Field(default_factory=list)
    check_conditional: list[FieldIssue] = Field(default_factory=list)
    unknown_keys: list[str] = Field(default_factory=list)


@lru_cache(maxsize=1)
def _table() -> dict[str, Any]:
    return json.loads(RULES.read_text(encoding="utf-8"))


def _key(name: str) -> str:
    """Match on letters and digits only, so 'buyer_tin', 'Buyer's TIN' and
    'buyerTin' all resolve to the same field."""
    return re.sub(r"[^a-z0-9]", "", name.lower())


# A few field names the guideline writes formally but users type informally.
ALIASES = {
    "supplierid": "supplierstin",
    "buyerid": "buyerstin",
    "invoicenumber": "einvoicecodenumber",
    "invoicedate": "einvoicedateandtime",
    "currency": "invoicecurrencycode",
    "total": "totalpayableamount",
    "grandtotal": "totalpayableamount",
    "linetotal": "subtotal",
}


def validate_fields(invoice: dict[str, Any]) -> FieldReport:
    """Report mandatory fields absent from `invoice`, each with its source ref."""
    doc = _table()
    src = doc["_source"]
    ref = f"Guideline v{src['version']} §{src['section']}"

    supplied: set[str] = set()
    for k, v in invoice.items():
        if v is None or (isinstance(v, str) and not v.strip()):
            continue  # present-but-empty is not supplied
        key = _key(k)
        supplied.add(ALIASES.get(key, key))

    report = FieldReport(valid=True)
    known: set[str] = set()
    for f in doc["fields"]:
        key = _key(f["name"])
        known.add(key)
        issue = FieldIssue(no=f["no"], name=f["name"], category=f["category"],
                           status=f["status"], condition=f["condition"],
                           section=f"{ref}, p{f['page']}")
        if key in supplied:
            report.present.append(f["name"])
        elif f["status"] == "mandatory":
            report.missing_mandatory.append(issue)
        elif f["status"] == "conditional":
            report.check_conditional.append(issue)

    report.checked = len(doc["fields"])
    report.unknown_keys = sorted(
        k for k in {ALIASES.get(_key(k), _key(k)) for k in invoice} if k not in known)
    report.valid = not report.missing_mandatory
    return report


def appendix_page(page: int) -> dict[str, Any] | None:
    """The Appendix 1 rows printed on one page, rendered for reading.

    Appendix 1 is the one thing this project cites that is not in the chunk
    corpus -- the field table is read from data/rules, so /chunk had nothing
    behind these citations and opening one was a dead click. Rendering the rows
    from the same table the checker consulted keeps "every citation can be
    opened" true, and shows the reader what actually decided the answer rather
    than prose that merely describes it.

    The version reported is the table's own, not the citation's, so a stale
    citation shows the version it really got -- the same rule the chunk path
    follows for section and page.
    """
    doc = _table()
    src = doc["_source"]
    rows = [f for f in doc["fields"] if f["page"] == page]
    if not rows:
        return None
    lines = [
        f"{f['no']}. {f['name']} [{f['status']}"
        + (f", {f['condition']}" if f["condition"] else "") + "]"
        + (f"\n    {f['description']}" if f.get("description") else "")
        for f in rows
    ]
    return {"doc": "Guideline", "version": src["version"], "section": src["section"],
            "title": src["table"], "page": page, "content": "\n".join(lines)}


def field_list(status: str = "mandatory") -> list[FieldIssue]:
    """The Appendix 1 rows of a given status, for "what fields do I need?".

    Answering that from the table rather than from retrieved prose is the same
    principle as the rule engine: it is a published list, so read the list.
    """
    doc = _table()
    src = doc["_source"]
    return [
        FieldIssue(no=f["no"], name=f["name"], category=f["category"],
                   status=f["status"], condition=f["condition"],
                   section=f"Guideline v{src['version']} §{src['section']}, p{f['page']}")
        for f in doc["fields"] if f["status"] == status
    ]
