"""Version-keyed rule parameters. Every number cites the section it came from.

The two tables live in data/rules/*.json so a reviewer can diff them against the
PDF without reading Python. Everything else is small enough to sit here.

Adding a guideline version means adding a key to PARAMS -- never editing an
existing one. Old versions stay reproducible because the rule engine is
version-parameterised, and archived PDFs are kept in data/raw/archive/.
"""

from __future__ import annotations

import json
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Any

RULES_DIR = Path(__file__).resolve().parents[3] / "data" / "rules"


@lru_cache(maxsize=2)
def _table(name: str) -> dict[str, Any]:
    return json.loads((RULES_DIR / f"{name}.json").read_text(encoding="utf-8"))


def phase_table() -> list[dict[str, Any]]:
    """Guideline s1.5 Table 1.1, p12. Identical in v4.6 and v4.8."""
    return _table("phase_table")["phases"]


def relaxation_table() -> list[dict[str, Any]]:
    """Specific Guideline s16.1 Table 16.1, p121. Identical in v4.7 and v4.8."""
    return _table("relaxation_table")["phases"]


# Concessionary / new-business implementation date. Same in both versions.
#   v4.8: Guideline s1.5, p13   v4.6: Guideline s1.5, p14
CONCESSIONARY_DATE = date(2026, 7, 1)

PARAMS: dict[str, dict[str, Any]] = {
    "4.8": {
        "guideline_version": "4.8",
        "guideline_published": date(2026, 8, 30),
        "specific_version": "4.8",
        # s1.6.1(e), p15: "Taxpayers with an annual turnover or revenue of less
        # than RM3,000,000". Strictly less than -- exactly RM3,000,000 is NOT
        # exempt.
        "exemption_threshold": 3_000_000,
        "exemption_ref": "Guideline v4.8 §1.6.1(e), p15",
        # s1.6.10, p17: the turnover exemption does NOT apply where the taxpayer
        # has (a) a non-individual shareholder, (b) a holding company, or (c) a
        # related company / joint venture, with turnover of at least RM3,000,000.
        # "related company" per s2 Promotion of Investments Act 1986.
        "carve_outs_apply": True,
        "carve_out_threshold": 3_000_000,
        "carve_out_ref": "Guideline v4.8 §1.6.10, p17",
        # s1.5, p13: "For new businesses or operations commencing from the year
        # 2023 to 2025 with an annual turnover or revenue of at least
        # RM3,000,000, the e-Invoice implementation date is 1 July 2026."
        "new_business_2023_2025_threshold": 3_000_000,
        "new_business_2023_2025_ref": "Guideline v4.8 §1.5, p13",
        # s1.5, p13-14: commencing 2026 onwards -> 1 July 2026 or upon the
        # operation commencement date; if the first year's turnover is expected
        # to be less than RM3,000,000, the date is 1 January in the second year
        # following the year turnover reached RM3,000,000.
        "new_business_2026_ref": "Guideline v4.8 §1.5, p13-14",
        "late_crossing_offset_years": 2,  # "second year following" -> year + 2
        "late_crossing_ref": "Guideline v4.8 §1.5, p14",
        # Specific Guideline s3.7.2 Table 3.6 row 7, p33: "All industries - Any
        # single transaction with a value exceeding RM10,000 ... effective
        # starting 1 January 2026." Exceeding, so exactly RM10,000 may still be
        # consolidated.
        "individual_invoice_threshold": 10_000,
        "individual_invoice_from": date(2026, 1, 1),
        "individual_invoice_ref": "Specific Guideline v4.8 §3.7.2 Table 3.6 row 7, p33",
        # Specific Guideline s3.7.2 Table 3.6, p32-33: industries that may never
        # consolidate, regardless of amount. Values are the engine's identifiers.
        "no_consolidation_industries": {
            "automotive": "Sale of any motor vehicle",
            "aviation": "Sale of flight ticket; private charter",
            "construction": "Construction contractor undertaking a construction contract",
            "betting_gaming": "Pay-out to winners (casino and gaming machines excepted)",
            "agents_dealers_distributors": "Payments to agents, dealers or distributors",
            "electricity": "Distribution, supply or sale of electricity (from 1 Jan 2026)",
            "telecommunication": "Postpaid / internet subscription and device sales"
                                 " (from 1 Jan 2026)",
        },
        # §1.6.7, p16-17: transaction types for which no e-Invoice (including
        # self-billed) is required, whatever the business's own status. Keys are
        # the engine's identifiers; (g) carries its own exception.
        "exempt_transaction_types": {
            "employment_income": "Employment income.",
            "pension": "Pension.",
            "alimony": "Alimony.",
            "dividend": "Distribution of dividend in specific circumstances (see "
                        "Section 11 of the e-Invoice Specific Guideline).",
            "zakat": "Zakat.",
            "exchange_traded_securities": "Contract value for the buying or selling of "
                                          "securities or derivatives traded on a stock "
                                          "or derivatives exchange in Malaysia or "
                                          "elsewhere.",
            "unlisted_share_disposal": "Disposal of shares of a company not listed on "
                                       "the stock exchange - EXCEPT where the disposer "
                                       "is a company, limited liability partnership, "
                                       "trust body or co-operative society, in which "
                                       "case an e-Invoice IS required.",
            "donation": "Donations or contributions received, as specified in Question "
                        "1, Part A of the e-Invoice FAQs for Donations or Contributions.",
        },
        "exempt_transaction_ref": "Guideline v4.8 §1.6.7, p16-17",
        # §1.6.5, p16: the §1.6.1 exemption belongs to the listed persons only.
        # An entity owned by one of them does not inherit it and implements per
        # the §1.5 timeline. Read as NOT destroying the entity's own §1.6.1(e)
        # turnover exemption -- otherwise §1.6.10(a), which deliberately
        # requires a NON-INDIVIDUAL shareholder at RM3m, would be redundant.
        "owned_entity_ref": "Guideline v4.8 §1.6.5, p16",
        "no_consolidation_ref": "Specific Guideline v4.8 §3.7.2 Table 3.6, p32-33",
    },
    "4.6": {
        "guideline_version": "4.6",
        "guideline_published": date(2025, 12, 7),
        "specific_version": "4.7",
        # s1.6.1(e), p15 of the v4.6 PDF: "less than RM1,000,000".
        "exemption_threshold": 1_000_000,
        "exemption_ref": "Guideline v4.6 §1.6.1(e), p15",
        # v4.6 has no §1.6.10 -- verified: 0 occurrences of "1.6.10",
        # "related company" or "subsidiary of a holding" anywhere in the v4.6
        # PDF. The group carve-outs were introduced in v4.8.
        "carve_outs_apply": False,
        "carve_out_threshold": None,
        "carve_out_ref": "not present in v4.6",
        # s1.5, p14 of the v4.6 PDF: "at least RM1,000,000 ... 1 July 2026".
        "new_business_2023_2025_threshold": 1_000_000,
        "new_business_2023_2025_ref": "Guideline v4.6 §1.5, p14",
        "new_business_2026_ref": "Guideline v4.6 §1.5, p14",
        "late_crossing_offset_years": 2,
        "late_crossing_ref": "Guideline v4.6 §1.5, p14",
        # The RM10,000 rule is in the Specific Guideline, which pairs with v4.6
        # at version 4.7; Table 3.6 row 7 is identical there.
        "individual_invoice_threshold": 10_000,
        "individual_invoice_from": date(2026, 1, 1),
        "individual_invoice_ref": "Specific Guideline v4.7 §3.7.2 Table 3.6 row 7, p33",
        "no_consolidation_industries": {
            "automotive": "Sale of any motor vehicle",
            "aviation": "Sale of flight ticket; private charter",
            "construction": "Construction contractor undertaking a construction contract",
            "betting_gaming": "Pay-out to winners (casino and gaming machines excepted)",
            "agents_dealers_distributors": "Payments to agents, dealers or distributors",
            "electricity": "Distribution, supply or sale of electricity (from 1 Jan 2026)",
            "telecommunication": "Postpaid / internet subscription and device sales"
                                 " (from 1 Jan 2026)",
        },
        # §1.6.7, p16-17: transaction types for which no e-Invoice (including
        # self-billed) is required, whatever the business's own status. Keys are
        # the engine's identifiers; (g) carries its own exception.
        "exempt_transaction_types": {
            "employment_income": "Employment income.",
            "pension": "Pension.",
            "alimony": "Alimony.",
            "dividend": "Distribution of dividend in specific circumstances (see "
                        "Section 11 of the e-Invoice Specific Guideline).",
            "zakat": "Zakat.",
            "exchange_traded_securities": "Contract value for the buying or selling of "
                                          "securities or derivatives traded on a stock "
                                          "or derivatives exchange in Malaysia or "
                                          "elsewhere.",
            "unlisted_share_disposal": "Disposal of shares of a company not listed on "
                                       "the stock exchange - EXCEPT where the disposer "
                                       "is a company, limited liability partnership, "
                                       "trust body or co-operative society, in which "
                                       "case an e-Invoice IS required.",
            "donation": "Donations or contributions received, as specified in Question "
                        "1, Part A of the e-Invoice FAQs for Donations or Contributions.",
        },
        "exempt_transaction_ref": "Guideline v4.6 §1.6.7, p16-17",
        # §1.6.5, p16: the §1.6.1 exemption belongs to the listed persons only.
        # An entity owned by one of them does not inherit it and implements per
        # the §1.5 timeline. Read as NOT destroying the entity's own §1.6.1(e)
        # turnover exemption -- otherwise §1.6.10(a), which deliberately
        # requires a NON-INDIVIDUAL shareholder at RM3m, would be redundant.
        "owned_entity_ref": "Guideline v4.6 §1.6.5, p16",
        "no_consolidation_ref": "Specific Guideline v4.7 §3.7.2 Table 3.6, p32-33",
    },
}


def get(version: str) -> dict[str, Any]:
    if version not in PARAMS:
        raise ValueError(f"unknown guideline version {version!r}; have {sorted(PARAMS)}")
    return PARAMS[version]
