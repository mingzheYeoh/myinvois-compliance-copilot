"""Deterministic e-Invoice compliance rules. No LLM, no retrieval, no I/O.

Every conclusion carries the section it came from, and anything the profile does
not say is reported in `missing` rather than assumed. A determination with a
non-empty `missing` list has `required=None`: the engine declines to answer
rather than guess, because guessing an implementation date is the one failure
this project cannot ship.
"""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel, Field

from app.rules import params


class BusinessProfile(BaseModel):
    annual_turnover: float | None = None
    commencement_year: int | None = None
    # Year the turnover figure relates to. For businesses trading in 2022 the
    # phase table is keyed on FY2022 (Guideline §1.5, p12).
    fy_basis_year: int | None = None
    has_related_company_over_threshold: bool | None = None
    industry: str | None = None
    is_individual: bool | None = None


class Transaction(BaseModel):
    """A single sale, for the consolidation rules. Optional to `determine`."""

    amount: float
    on: date


class Reason(BaseModel):
    text: str
    section: str


class Determination(BaseModel):
    required: bool | None = None
    implementation_date: date | None = None
    relaxation_until: date | None = None
    consolidated_allowed: bool | None = None
    individual_over_10k_required: bool | None = None
    reasons: list[Reason] = Field(default_factory=list)
    missing: list[str] = Field(default_factory=list)


def _phase_for(turnover: float) -> dict:
    for band in params.phase_table():
        lo, hi = band["min_exclusive"], band["max_inclusive"]
        if (lo is None or turnover > lo) and (hi is None or turnover <= hi):
            return band
    raise AssertionError("phase table does not cover the real line")  # pragma: no cover


def _relaxation_until(implementation: date | None) -> date | None:
    """Table 16.1 is keyed on the implementation date, not on turnover.

    Both new-business dates (1 Jan 2026 and 1 Jul 2026) sit in row 4.
    """
    if implementation is None:
        return None
    for row in params.relaxation_table():
        if date.fromisoformat(row["from"]) == implementation:
            return date.fromisoformat(row["until"])
    if implementation == params.CONCESSIONARY_DATE:
        return date.fromisoformat(params.relaxation_table()[3]["until"])
    return None


def _consolidation(txn: Transaction | None, profile: BusinessProfile, p: dict, out: Determination):
    if txn is None:
        return
    industries = p["no_consolidation_industries"]
    if profile.industry and profile.industry in industries:
        out.consolidated_allowed = False
        out.individual_over_10k_required = False
        out.reasons.append(Reason(
            text=f"{profile.industry}: {industries[profile.industry]} - consolidated "
                 f"e-Invoice is never allowed, regardless of amount.",
            section=p["no_consolidation_ref"]))
        return
    over = txn.amount > p["individual_invoice_threshold"]
    in_force = txn.on >= p["individual_invoice_from"]
    out.individual_over_10k_required = over and in_force
    out.consolidated_allowed = not (over and in_force)
    thr = p["individual_invoice_threshold"]
    if over and in_force:
        out.reasons.append(Reason(
            text=f"Single transaction of RM{txn.amount:,.0f} on {txn.on} exceeds "
                 f"RM{thr:,.0f} and the rule is in force from "
                 f"{p['individual_invoice_from']}, so an individual e-Invoice is "
                 f"required and it may not be consolidated.",
            section=p["individual_invoice_ref"]))
    elif over:
        out.reasons.append(Reason(
            text=f"Single transaction of RM{txn.amount:,.0f} exceeds RM{thr:,.0f}, but "
                 f"the rule only takes effect on {p['individual_invoice_from']}, so "
                 f"consolidation is still allowed on {txn.on}.",
            section=p["individual_invoice_ref"]))
    else:
        out.reasons.append(Reason(
            text=f"Single transaction of RM{txn.amount:,.0f} does not exceed "
                 f"RM{thr:,.0f}, so it may be consolidated.",
            section=p["individual_invoice_ref"]))


def determine(
    profile: BusinessProfile,
    version: str = "4.8",
    transaction: Transaction | None = None,
) -> Determination:
    """Decide whether e-Invoice applies, from when, and until when relaxed."""
    p = params.get(version)
    out = Determination()

    _consolidation(transaction, profile, p, out)

    if profile.annual_turnover is None:
        out.missing.append("annual_turnover")
    if profile.commencement_year is None:
        out.missing.append("commencement_year")
    if out.missing:
        out.reasons.append(Reason(
            text="Cannot determine the implementation date without "
                 + " and ".join(out.missing) + ".",
            section=f"Guideline v{p['guideline_version']} §1.5"))
        return out

    turnover = profile.annual_turnover
    year = profile.commencement_year
    threshold = p["exemption_threshold"]

    # --- Exemption (§1.6.1(e)), and its carve-outs (§1.6.10, v4.8 only) -------
    if turnover < threshold:
        if p["carve_outs_apply"]:
            if profile.has_related_company_over_threshold is None:
                out.missing.append("has_related_company_over_threshold")
                out.reasons.append(Reason(
                    text=f"Turnover of RM{turnover:,.0f} is below the RM{threshold:,.0f} "
                         f"exemption, but the exemption does not apply if there is a "
                         f"non-individual shareholder, holding company, related company "
                         f"or joint venture with turnover of at least "
                         f"RM{p['carve_out_threshold']:,.0f}. That is not stated in the "
                         f"profile, and it flips the answer.",
                    section=p["carve_out_ref"]))
                return out
            if profile.has_related_company_over_threshold:
                out.reasons.append(Reason(
                    text=f"Turnover of RM{turnover:,.0f} is below the RM{threshold:,.0f} "
                         f"exemption, but a related company / holding company / "
                         f"non-individual shareholder has turnover of at least "
                         f"RM{p['carve_out_threshold']:,.0f}, so the exemption is lost.",
                    section=p["carve_out_ref"]))
            else:
                out.required = False
                out.reasons.append(Reason(
                    text=f"Annual turnover of RM{turnover:,.0f} is less than "
                         f"RM{threshold:,.0f}, and no group carve-out applies, so the "
                         f"taxpayer is exempt from issuing e-Invoice (including "
                         f"self-billed e-Invoice).",
                    section=p["exemption_ref"]))
                return out
        else:
            out.required = False
            out.reasons.append(Reason(
                text=f"Annual turnover of RM{turnover:,.0f} is less than "
                     f"RM{threshold:,.0f}, so the taxpayer is exempt from issuing "
                     f"e-Invoice (including self-billed e-Invoice). This version has "
                     f"no group carve-out.",
                section=p["exemption_ref"]))
            return out

    # --- Not exempt: find the implementation date ----------------------------
    out.required = True

    # Only a taxpayer that lost the exemption to a §1.6.10 carve-out reaches
    # here with turnover below the threshold. FAQ Q11(a) / Q12(c) put that
    # taxpayer on the concessionary date, not on the phase table.
    below_threshold = turnover < threshold

    if year <= 2022 and below_threshold:
        out.implementation_date = params.CONCESSIONARY_DATE
        out.reasons.append(Reason(
            text=f"In operation as at YA2022 but YA2022 turnover of RM{turnover:,.0f} "
                 f"did not reach RM{threshold:,.0f}, so the concessionary "
                 f"implementation date of {params.CONCESSIONARY_DATE} applies rather "
                 f"than the Table 1.1 phase date.",
            section="FAQ §PART 1 Q11(a), p4"))
    elif year <= 2022:
        band = _phase_for(turnover)
        out.implementation_date = date.fromisoformat(band["implementation_date"])
        basis = profile.fy_basis_year or 2022
        out.reasons.append(Reason(
            text=f"In operation as at YA2022 with turnover of RM{turnover:,.0f} "
                 f"({band['label']}), which is phase {band['phase']}, so the "
                 f"implementation date is {out.implementation_date}. The threshold is "
                 f"read from FY2022 accounts; the profile gives FY{basis}.",
            section=f"Guideline v{p['guideline_version']} §1.5 Table 1.1, p12"))
    elif 2023 <= year <= 2025:
        out.implementation_date = params.CONCESSIONARY_DATE
        if below_threshold:
            out.reasons.append(Reason(
                text=f"Business commenced in {year} (2023-2025) with turnover of "
                     f"RM{turnover:,.0f}, below RM{threshold:,.0f}, but it cannot meet "
                     f"the exemption criteria, so the concessionary implementation "
                     f"date of {params.CONCESSIONARY_DATE} applies.",
                section="FAQ §PART 1 Q12(c), p7"))
        else:
            out.reasons.append(Reason(
                text=f"Business commenced in {year} (2023-2025) with turnover of "
                     f"RM{turnover:,.0f}, at or above "
                     f"RM{p['new_business_2023_2025_threshold']:,.0f}, so the "
                     f"implementation date is {params.CONCESSIONARY_DATE}.",
                section=p["new_business_2023_2025_ref"]))
    else:
        out.implementation_date = params.CONCESSIONARY_DATE
        out.reasons.append(Reason(
            text=f"Business commenced in {year} (2026 onwards), so the implementation "
                 f"date is {params.CONCESSIONARY_DATE} or the operation commencement "
                 f"date, whichever is later. First-year turnover of RM{turnover:,.0f}"
                 + (f" is below RM{threshold:,.0f}, but the exemption is lost to a "
                    f"group carve-out." if below_threshold
                    else f" is at or above RM{threshold:,.0f}."),
            section=p["new_business_2026_ref"]))

    out.relaxation_until = _relaxation_until(out.implementation_date)
    if out.relaxation_until:
        out.reasons.append(Reason(
            text=f"The interim relaxation period runs until {out.relaxation_until}. "
                 f"Note that only phases 1-3 are six months; the up-to-RM5-million "
                 f"phase runs to 31 December 2027.",
            section=f"Specific Guideline v{p['specific_version']} §16.1 Table 16.1, p121"))
    return out


def crossing_threshold_date(reached_in_year: int, version: str = "4.8") -> date:
    """A taxpayer that crosses the threshold later: 1 January in the second year
    following the year turnover reached it (Guideline §1.5, p14). The FAQ worked
    example confirms the arithmetic: reached in YA2027 -> 1 January 2029."""
    p = params.get(version)
    return date(reached_in_year + p["late_crossing_offset_years"], 1, 1)
