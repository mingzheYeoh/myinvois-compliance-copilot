"""Deterministic e-Invoice compliance rules. No LLM, no retrieval, no I/O.

Every conclusion carries the section it came from, and anything the profile does
not say is reported in `missing` rather than assumed. A determination reports in `missing`
only what it could not conclude without: the engine declines to guess an
implementation date, but it still states what already follows (exempt on
turnover alone, or required-but-undated), because asking for input we do not
need is how a question the user did ask goes unanswered.
"""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field

from app.rules import params


class BusinessProfile(BaseModel):
    annual_turnover: float | None = None
    commencement_year: int | None = None
    # Exact commencement date, when known. For businesses commencing 2026
    # onwards §1.5 gives "1 July 2026 or upon the operation commencement date";
    # without the day and month the engine can only assume 1 July 2026.
    commencement_date: date | None = None
    # §1.5, p12: the phase table is keyed on FY2022 turnover ONLY -- the FY2022
    # audited accounts, or the YA2022 tax return where there are none. This is a
    # different figure from annual_turnover (current), which drives the
    # §1.6.1(e) exemption test. §1.5, p13 is explicit that later changes in
    # turnover do not change an obligation once the timeline is determined.
    fy2022_turnover: float | None = None
    # §1.5 item 3 and Example 2: where the FY2022 accounting period was not 12
    # months, turnover is pro-rated to 12 months before Table 1.1 is read.
    fy2022_period_months: int = 12
    # §1.5, p13: selects the branch for businesses commencing 2026 onwards.
    expected_first_year_turnover: float | None = None
    # §1.5, p14: needed only when expected first-year turnover is below the
    # threshold, where the date is 1 January of the second year following the
    # year turnover actually reached it.
    year_turnover_reached_threshold: int | None = None
    has_related_company_over_threshold: bool | None = None
    # §1.6.5, p16: an entity owned by a person exempt under §1.6.1(a)-(d) does
    # not inherit that exemption. It keeps its own §1.6.1(e) turnover exemption.
    owned_by_exempt_person: bool | None = None
    industry: str | None = None
    is_individual: bool | None = None


class Transaction(BaseModel):
    """A single sale, for the consolidation rules. Optional to `determine`."""

    amount: float
    on: date


class Reason(BaseModel):
    """`basis` says how load-bearing the source is.

    guideline / specific -- stated directly in that document.
    faq_gap_fill        -- the Guideline is silent and the rule is taken from
                           the FAQ, which is older than v4.8 and ranks lowest in
                           the precedence order. Answers built on one of these
                           must tell the user to confirm with LHDN.
    """

    text: str
    section: str
    basis: Literal["guideline", "specific", "faq_gap_fill"] = "guideline"


class Determination(BaseModel):
    # "business" -> required means this taxpayer must issue e-Invoices.
    # "transaction" -> required means an e-Invoice is needed for THIS kind of
    # transaction. Same field, two meanings; the discriminator keeps a caller
    # from inverting a compliance answer.
    scope: Literal["business", "transaction"] = "business"
    required: bool | None = None
    implementation_date: date | None = None
    relaxation_until: date | None = None
    consolidated_allowed: bool | None = None
    individual_over_10k_required: bool | None = None
    reasons: list[Reason] = Field(default_factory=list)
    missing: list[str] = Field(default_factory=list)


def prorate_fy2022(profile: BusinessProfile) -> float | None:
    """§1.5 item 3: an FY2022 period that is not 12 months is scaled to 12.

    Example 2, p14: an 18-month FY2022 with turnover of RM60 million gives a
    12-month equivalent of RM40 million, which is phase 2 (1 January 2025).
    """
    if profile.fy2022_turnover is None:
        return None
    months = profile.fy2022_period_months or 12
    return profile.fy2022_turnover / months * 12


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
    if implementation >= date(2026, 1, 1):
        # Row 4 covers the up-to-RM5m phase, listing both the 1 Jan and 1 Jul
        # 2026 dates under one end date; a later commencement sits in it too.
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
            section=p["no_consolidation_ref"], basis="specific"))
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
            section=p["individual_invoice_ref"], basis="specific"))
    elif over:
        out.reasons.append(Reason(
            text=f"Single transaction of RM{txn.amount:,.0f} exceeds RM{thr:,.0f}, but "
                 f"the rule only takes effect on {p['individual_invoice_from']}, so "
                 f"consolidation is still allowed on {txn.on}.",
            section=p["individual_invoice_ref"], basis="specific"))
    else:
        out.reasons.append(Reason(
            text=f"Single transaction of RM{txn.amount:,.0f} does not exceed "
                 f"RM{thr:,.0f}, so it may be consolidated.",
            section=p["individual_invoice_ref"], basis="specific"))


def determine(
    profile: BusinessProfile,
    version: str = "4.8",
    transaction: Transaction | None = None,
) -> Determination:
    """Decide whether e-Invoice applies, from when, and until when relaxed."""
    p = params.get(version)
    out = Determination()

    _consolidation(transaction, profile, p, out)

    turnover = profile.annual_turnover
    threshold = p["exemption_threshold"]

    # A conclusion that already follows from what we were given beats a request
    # for more input. §1.6.5 settles ownership on its own: the exemption is not
    # inherited. When ownership is all the user told us, that IS the answer, and
    # asking for a turnover figure answers a question they did not ask.
    if profile.owned_by_exempt_person:
        out.reasons.append(Reason(
            text="Being owned by a person who is exempt under §1.6.1(a)-(d) does not "
                 "pass that exemption on: the entity is assessed in its own right and "
                 "implements e-Invoice on the §1.5 timeline, unless its own annual "
                 "turnover exempts it under §1.6.1(e).",
            section=p["owned_entity_ref"], basis="guideline"))
        if turnover is None:
            return out

    # The §1.6.1(e) exemption test needs turnover alone. The commencement year
    # matters only AFTER that test fails, to place the taxpayer on the §1.5
    # timeline -- demanding it up front blocked answers that already followed.
    if turnover is None:
        out.missing.append("annual_turnover")
        out.reasons.append(Reason(
            text="Cannot determine the implementation date without annual_turnover.",
            section=f"Guideline v{p['guideline_version']} §1.5", basis="guideline"))
        return out

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
                    section=p["carve_out_ref"], basis="guideline"))
                return out
            if profile.has_related_company_over_threshold:
                out.reasons.append(Reason(
                    text=f"Turnover of RM{turnover:,.0f} is below the RM{threshold:,.0f} "
                         f"exemption, but a related company / holding company / "
                         f"non-individual shareholder has turnover of at least "
                         f"RM{p['carve_out_threshold']:,.0f}, so the exemption is lost.",
                    section=p["carve_out_ref"], basis="guideline"))
            else:
                out.required = False
                out.reasons.append(Reason(
                    text=f"Annual turnover of RM{turnover:,.0f} is less than "
                         f"RM{threshold:,.0f}, and no group carve-out applies, so the "
                         f"taxpayer is exempt from issuing e-Invoice (including "
                         f"self-billed e-Invoice).",
                    section=p["exemption_ref"], basis="guideline"))
                return out
        else:
            out.required = False
            out.reasons.append(Reason(
                text=f"Annual turnover of RM{turnover:,.0f} is less than "
                     f"RM{threshold:,.0f}, so the taxpayer is exempt from issuing "
                     f"e-Invoice (including self-billed e-Invoice). This version has "
                     f"no group carve-out.",
                section=p["exemption_ref"], basis="guideline"))
            return out

    # --- Not exempt: find the implementation date ----------------------------
    # Not exempt is itself a conclusion: an e-Invoice IS required. Only the date
    # needs the commencement year, so report that and keep the answer we have.
    out.required = True
    if profile.commencement_year is None:
        out.missing.append("commencement_year")
        out.reasons.append(Reason(
            text="Turnover does not meet the exemption, so an e-Invoice is required. "
                 "§1.5 places a taxpayer on the implementation timeline by the year "
                 "the business commenced, so that year is needed before a date can "
                 "be given.",
            section=f"Guideline v{p['guideline_version']} §1.5", basis="guideline"))
        return out
    year = profile.commencement_year

    if year <= 2022:
        fy22 = prorate_fy2022(profile)
        if fy22 is None:
            out.missing.append("fy2022_turnover")
            out.reasons.append(Reason(
                text="Table 1.1 is read from FY2022 turnover, not current turnover, "
                     "so the FY2022 audited figure (or the YA2022 tax return figure) "
                     "is needed before an implementation date can be given.",
                section=f"Guideline v{p['guideline_version']} §1.5, p12", basis="guideline"))
            return out
        scaled = ""
        if (profile.fy2022_period_months or 12) != 12:
            scaled = (f" FY2022 covered {profile.fy2022_period_months} months, so "
                      f"RM{profile.fy2022_turnover:,.0f} pro-rates to RM{fy22:,.0f} "
                      f"over 12 months.")
        if fy22 < threshold:
            # Reached only via a §1.6.10 carve-out. FAQ Q11(a) puts a taxpayer
            # whose YA2022 turnover never reached the threshold on the
            # concessionary date rather than on the Table 1.1 phase date.
            out.implementation_date = params.CONCESSIONARY_DATE
            out.reasons.append(Reason(
                text=f"In operation as at YA2022 but FY2022 turnover of RM{fy22:,.0f} "
                     f"did not reach RM{threshold:,.0f}, so the concessionary "
                     f"implementation date of {params.CONCESSIONARY_DATE} applies "
                     f"rather than a Table 1.1 phase date.{scaled}",
                section="FAQ §PART 1 Q11(a), p4", basis="faq_gap_fill"))
        else:
            band = _phase_for(fy22)
            out.implementation_date = date.fromisoformat(band["implementation_date"])
            out.reasons.append(Reason(
                text=f"FY2022 turnover of RM{fy22:,.0f} ({band['label']}) is phase "
                     f"{band['phase']}, so the implementation date is "
                     f"{out.implementation_date}.{scaled}",
                section=f"Guideline v{p['guideline_version']} §1.5 Table 1.1, p12",
                basis="guideline"))
    elif 2023 <= year <= 2025:
        out.implementation_date = params.CONCESSIONARY_DATE
        if turnover < threshold:
            out.reasons.append(Reason(
                text=f"Business commenced in {year} (2023-2025) with turnover of "
                     f"RM{turnover:,.0f}, below RM{threshold:,.0f}, but it cannot meet "
                     f"the exemption criteria, so the concessionary implementation "
                     f"date of {params.CONCESSIONARY_DATE} applies.",
                section="FAQ §PART 1 Q12(c), p7", basis="faq_gap_fill"))
        else:
            out.reasons.append(Reason(
                text=f"Business commenced in {year} (2023-2025) with turnover of "
                     f"RM{turnover:,.0f}, at or above "
                     f"RM{p['new_business_2023_2025_threshold']:,.0f}, so the "
                     f"implementation date is {params.CONCESSIONARY_DATE}.",
                section=p["new_business_2023_2025_ref"], basis="guideline"))
    else:
        # §1.5, p13-14 branches on EXPECTED FIRST-YEAR turnover, not on a date
        # comparison. "1 July 2026 or upon the operation commencement date"
        # only arises inside the at-or-above branch, where the "or" settles
        # timing within 2026.
        expected = profile.expected_first_year_turnover
        if expected is None:
            out.missing.append("expected_first_year_turnover")
            out.reasons.append(Reason(
                text="For a business commencing 2026 onwards the rule branches on the "
                     "expected first-year turnover: at or above "
                     f"RM{threshold:,.0f} gives 1 July 2026 or the commencement date; "
                     f"below it defers the date until turnover actually reaches "
                     f"RM{threshold:,.0f}. Without it neither branch can be selected.",
                section=p["new_business_2026_ref"], basis="guideline"))
            return out
        if expected >= threshold:
            if profile.commencement_date is not None:
                out.implementation_date = max(params.CONCESSIONARY_DATE,
                                              profile.commencement_date)
                precision = (f"Commenced {profile.commencement_date}, so the date is "
                             f"{out.implementation_date}.")
            else:
                out.implementation_date = params.CONCESSIONARY_DATE
                precision = (f"Only the commencement year is known, so "
                             f"{params.CONCESSIONARY_DATE} is assumed. Supply "
                             f"commencement_date for a precise answer.")
            out.reasons.append(Reason(
                text=f"Business commenced in {year} (2026 onwards) with expected "
                     f"first-year turnover of RM{expected:,.0f}, at or above "
                     f"RM{threshold:,.0f}, so the implementation date is 1 July 2026 "
                     f"or the operation commencement date. {precision}",
                section=p["new_business_2026_ref"], basis="guideline"))
        else:
            reached = profile.year_turnover_reached_threshold
            if reached is None:
                out.missing.append("year_turnover_reached_threshold")
                out.reasons.append(Reason(
                    text=f"Expected first-year turnover of RM{expected:,.0f} is below "
                         f"RM{threshold:,.0f}, so the implementation date is 1 January "
                         f"of the second year following the year turnover actually "
                         f"reached RM{threshold:,.0f}. That year is needed.",
                    section=p["late_crossing_ref"], basis="guideline"))
                return out
            out.implementation_date = crossing_threshold_date(reached, version)
            out.reasons.append(Reason(
                text=f"Expected first-year turnover of RM{expected:,.0f} was below "
                     f"RM{threshold:,.0f}; turnover reached RM{threshold:,.0f} in "
                     f"{reached}, so the implementation date is 1 January of the "
                     f"second year following, i.e. {out.implementation_date}.",
                section=p["late_crossing_ref"], basis="guideline"))

    out.relaxation_until = _relaxation_until(out.implementation_date)
    if out.relaxation_until:
        out.reasons.append(Reason(
            text=f"The interim relaxation period runs until {out.relaxation_until}. "
                 f"Note that only phases 1-3 are six months; the up-to-RM5-million "
                 f"phase runs to 31 December 2027.",
            section=f"Specific Guideline v{p['specific_version']} §16.1 Table 16.1, p121",
            basis="specific"))
    return out


def crossing_threshold_date(reached_in_year: int, version: str = "4.8") -> date:
    """A taxpayer that crosses the threshold later: 1 January in the second year
    following the year turnover reached it (Guideline §1.5, p14). The FAQ worked
    example confirms the arithmetic: reached in YA2027 -> 1 January 2029."""
    p = params.get(version)
    return date(reached_in_year + p["late_crossing_offset_years"], 1, 1)


def reference_facts(version: str = "4.8") -> list[Reason]:
    """The engine's tables as citable facts, for questions with no profile.

    "What is the threshold?" and "How long is the Phase 4 relaxation?" are
    answerable without knowing anything about the asker -- but the numbers must
    still come from here rather than from the model reading prose. Day 2 Q7 is
    the reason: §16.1's lead sentence says six months, Table 16.1 row 4 says
    31 December 2027, and the model believed the sentence.
    """
    p = params.get(version)
    gl, sp = p["guideline_version"], p["specific_version"]
    facts = [Reason(
        text=f"Exemption: a taxpayer with annual turnover or revenue of less than "
             f"RM{p['exemption_threshold']:,.0f} is exempt from issuing e-Invoice "
             f"(including self-billed e-Invoice).",
        section=p["exemption_ref"], basis="guideline")]
    if p["carve_outs_apply"]:
        facts.append(Reason(
            text=f"That exemption is lost if the taxpayer has a non-individual "
                 f"shareholder, a holding company, or a related company / joint "
                 f"venture with turnover of at least RM{p['carve_out_threshold']:,.0f}. "
                 f"'Related company' takes its section 2 Promotion of Investments Act "
                 f"1986 meaning.",
            section=p["carve_out_ref"], basis="guideline"))
    for b in params.phase_table():
        facts.append(Reason(
            text=f"Phase {b['phase']} ({b['label']}): implementation date "
                 f"{b['implementation_date']}.",
            section=f"Guideline v{gl} §1.5 Table 1.1, p12", basis="guideline"))
    for r in params.relaxation_table():
        note = f" {r['note']}" if r.get("note") else ""
        facts.append(Reason(
            text=f"Phase {r['phase']} interim relaxation period: {r['from']} until "
                 f"{r['until']}.{note}",
            section=f"Specific Guideline v{sp} §16.1 Table 16.1, p121", basis="specific"))
    facts.append(Reason(
        text=f"A single transaction exceeding "
             f"RM{p['individual_invoice_threshold']:,.0f} may not be included in a "
             f"consolidated e-Invoice and needs its own e-Invoice, effective "
             f"{p['individual_invoice_from']}.",
        section=p["individual_invoice_ref"], basis="specific"))
    # Both tables below were already in params, and neither was citable without a
    # profile. "Which industries can never consolidate?" and "do I e-Invoice a
    # dividend?" then had to be answered from retrieved prose, and Day 8 they
    # were: the answers came back citing §16.2 and §11 instead of the tables.
    facts.append(Reason(
        text="Consolidated e-Invoice is never allowed, at any value, for these "
             "activities: "
             + "; ".join(p["no_consolidation_industries"].values()) + ".",
        section=p["no_consolidation_ref"], basis="specific"))
    # One rule, one fact -- as with the phase and relaxation rows. Rolled into a
    # single blob the dividend clause was buried, and its own text ("see Section
    # 11 of the e-Invoice Specific Guideline") sent the answer off to cite the
    # cross-reference instead of the Guideline section that grants the exemption.
    for kind in p["exempt_transaction_types"].values():
        facts.append(Reason(
            text=f"No e-Invoice (including self-billed e-Invoice) is required for "
                 f"this transaction type: {kind}",
            section=p["exempt_transaction_ref"], basis="guideline"))
    return facts


def exempt_transaction(kind: str, version: str = "4.8",
                       disposer_is_entity: bool | None = None) -> Determination:
    """§1.6.7: is an e-Invoice required for this KIND of transaction?

    Separate from `determine` because it answers a different question: a fully
    compliant business still issues no e-Invoice for salary or zakat. The
    `scope` discriminator on the result says which question was answered.
    """
    p = params.get(version)
    out = Determination(scope="transaction")
    types = p["exempt_transaction_types"]
    if kind not in types:
        out.reasons.append(Reason(
            text=f"{kind!r} is not one of the transaction types listed as not "
                 f"requiring an e-Invoice. The listed types are: "
                 f"{', '.join(sorted(types))}.",
            section=p["exempt_transaction_ref"], basis="guideline"))
        out.missing.append("kind")
        return out
    if kind == "unlisted_share_disposal" and disposer_is_entity is None:
        out.missing.append("disposer_is_entity")
        out.reasons.append(Reason(
            text="Disposal of unlisted shares does not require an e-Invoice EXCEPT "
                 "where the disposer is a company, limited liability partnership, "
                 "trust body or co-operative society. Which applies is not stated, "
                 "and it flips the answer.",
            section=p["exempt_transaction_ref"], basis="guideline"))
        return out
    if kind == "unlisted_share_disposal" and disposer_is_entity:
        out.required = True
        out.reasons.append(Reason(
            text="The disposer is a company / LLP / trust body / co-operative society, "
                 "so the §1.6.7(g) exception applies and an e-Invoice IS required.",
            section=p["exempt_transaction_ref"], basis="guideline"))
        return out
    out.required = False
    out.reasons.append(Reason(
        text=f"No e-Invoice (including self-billed e-Invoice) is required for this "
             f"transaction type: {types[kind]}",
        section=p["exempt_transaction_ref"], basis="guideline"))
    return out
