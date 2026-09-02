"""Rule engine tests. These double as documentation of the rules.

Each test names the section it pins, so a reviewer can check the assertion
against the source rather than against my reading of it.
"""

from datetime import date
from itertools import pairwise

import pytest

from app.rules.engine import (
    BusinessProfile,
    Transaction,
    crossing_threshold_date,
    determine,
    exempt_transaction,
    prorate_fy2022,
)
from app.rules.params import get, phase_table, relaxation_table

# --- the Day 2 audit cases, which the LLM got wrong -------------------------

def test_day2_q2_started_2024_rm2m_is_exempt_under_v48():
    """Day 2 Q2. The chain answered '1 January 2026'. §1.6.1(e) says exempt."""
    p = BusinessProfile(annual_turnover=2_000_000, commencement_year=2024,
                        has_related_company_over_threshold=False)
    d = determine(p, version="4.8")
    assert d.required is False
    assert d.implementation_date is None
    assert "1.6.1(e)" in d.reasons[0].section


def test_day2_q2_same_profile_under_v46_is_required_from_july_2026():
    """Same profile, older thresholds: RM2m clears the RM1m bar, so not exempt."""
    p = BusinessProfile(annual_turnover=2_000_000, commencement_year=2024)
    d = determine(p, version="4.6")
    assert d.required is True
    assert d.implementation_date == date(2026, 7, 1)
    assert d.relaxation_until == date(2027, 12, 31)


def test_day2_q7_phase4_relaxation_runs_to_end_of_2027_not_six_months():
    """Day 2 Q7. The chain said 'six months'. Table 16.1 row 4 says otherwise."""
    row = relaxation_table()[3]
    assert row["phase"] == 4
    assert row["until"] == "2027-12-31"
    p = BusinessProfile(annual_turnover=4_000_000, fy2022_turnover=4_000_000,
                        commencement_year=2020,
                        has_related_company_over_threshold=False)
    assert determine(p).relaxation_until == date(2027, 12, 31)


def test_phases_1_to_3_really_are_six_months():
    assert [r["until"] for r in relaxation_table()[:3]] == [
        "2025-01-31", "2025-06-30", "2025-12-31"
    ]


# --- the RM10,000 individual-invoice rule (Specific §3.7.2 Table 3.6 row 7) --

def test_rm12000_sale_in_jan_2026_requires_an_individual_einvoice():
    d = determine(BusinessProfile(annual_turnover=8_000_000, commencement_year=2020),
                  transaction=Transaction(amount=12_000, on=date(2026, 1, 15)))
    assert d.individual_over_10k_required is True
    assert d.consolidated_allowed is False


def test_same_sale_before_the_rule_takes_effect_may_still_be_consolidated():
    d = determine(BusinessProfile(annual_turnover=8_000_000, commencement_year=2020),
                  transaction=Transaction(amount=12_000, on=date(2025, 12, 31)))
    assert d.individual_over_10k_required is False
    assert d.consolidated_allowed is True


def test_exactly_rm10000_is_not_exceeding_so_consolidation_is_allowed():
    """Table 3.6 row 7 says 'exceeding RM10,000', not 'RM10,000 or more'."""
    d = determine(BusinessProfile(annual_turnover=8_000_000, commencement_year=2020),
                  transaction=Transaction(amount=10_000, on=date(2026, 6, 1)))
    assert d.individual_over_10k_required is False
    assert d.consolidated_allowed is True


def test_listed_industry_may_never_consolidate_even_for_a_small_sale():
    d = determine(
        BusinessProfile(annual_turnover=8_000_000, commencement_year=2020,
                        industry="automotive"),
        transaction=Transaction(amount=500, on=date(2026, 6, 1)))
    assert d.consolidated_allowed is False


# --- exemption and its §1.6.10 carve-outs -----------------------------------

def test_related_company_over_threshold_destroys_the_exemption():
    p = BusinessProfile(annual_turnover=900_000, commencement_year=2024,
                        has_related_company_over_threshold=True)
    d = determine(p, version="4.8")
    assert d.required is True
    assert any("1.6.10" in r.section for r in d.reasons)


def test_exactly_at_the_threshold_is_not_exempt():
    """§1.6.1(e) is 'less than RM3,000,000'."""
    p = BusinessProfile(annual_turnover=3_000_000, commencement_year=2020,
                        has_related_company_over_threshold=False)
    assert determine(p, version="4.8").required is True


def test_v46_has_no_carve_out_so_it_never_asks_about_related_companies():
    """Verified against the v4.6 PDF: no §1.6.10, no 'related company' anywhere."""
    p = BusinessProfile(annual_turnover=500_000, commencement_year=2024)
    d = determine(p, version="4.6")
    assert d.required is False and d.missing == []
    assert get("4.6")["carve_outs_apply"] is False


# --- missing input is reported, never guessed -------------------------------

def test_profile_without_turnover_reports_missing_not_a_date():
    d = determine(BusinessProfile(commencement_year=2024))
    assert d.required is None
    assert d.implementation_date is None
    assert "annual_turnover" in d.missing


def test_unknown_carve_out_status_is_missing_because_it_flips_the_answer():
    p = BusinessProfile(annual_turnover=900_000, commencement_year=2024)
    d = determine(p, version="4.8")
    assert d.required is None
    assert "has_related_company_over_threshold" in d.missing


def test_missing_turnover_still_answers_the_transaction_question():
    """The RM10,000 rule does not depend on the profile, so it still resolves."""
    d = determine(BusinessProfile(),
                  transaction=Transaction(amount=12_000, on=date(2026, 3, 1)))
    assert d.required is None and d.missing
    assert d.individual_over_10k_required is True


# --- the §1.5 phase table (Table 1.1) ---------------------------------------

@pytest.mark.parametrize("turnover,expected", [
    (100_000_001, date(2024, 8, 1)),   # phase 1: more than RM100m
    (100_000_000, date(2025, 1, 1)),   # phase 2 upper edge: "up to RM100 million"
    (25_000_001, date(2025, 1, 1)),
    (25_000_000, date(2025, 7, 1)),    # phase 3 upper edge
    (5_000_001, date(2025, 7, 1)),
    (5_000_000, date(2026, 1, 1)),     # phase 4: up to RM5m
])
def test_phase_table_boundaries(turnover, expected):
    p = BusinessProfile(annual_turnover=turnover, fy2022_turnover=turnover,
                        commencement_year=2020,
                        has_related_company_over_threshold=False)
    assert determine(p, version="4.8").implementation_date == expected


def test_phase_table_bands_are_contiguous_and_ordered():
    bands = phase_table()
    assert [b["phase"] for b in bands] == [1, 2, 3, 4]
    for upper, lower in pairwise(bands):
        assert upper["min_exclusive"] == lower["max_inclusive"]


# --- new-business and late-crossing rules -----------------------------------

def test_business_commencing_2026_onwards_gets_the_concessionary_date():
    p = BusinessProfile(annual_turnover=4_000_000, commencement_year=2026,
                        expected_first_year_turnover=4_000_000,
                        has_related_company_over_threshold=False)
    d = determine(p, version="4.8")
    assert d.implementation_date == date(2026, 7, 1)


def test_pre_2022_business_below_threshold_uses_concession_not_the_phase_table():
    """A carve-out victim whose FY2022 turnover never reached RM3m: FAQ Q11(a)."""
    p = BusinessProfile(annual_turnover=1_500_000, fy2022_turnover=1_500_000,
                        commencement_year=2019,
                        has_related_company_over_threshold=True)
    d = determine(p, version="4.8")
    assert d.implementation_date == date(2026, 7, 1)


def test_crossing_the_threshold_later_matches_the_faq_worked_example():
    """FAQ Q12: reaches the threshold in YA2027 -> 1 January 2029."""
    assert crossing_threshold_date(2027) == date(2029, 1, 1)


# --- invariants -------------------------------------------------------------

def test_every_reason_carries_a_section_reference():
    cases = [
        determine(BusinessProfile(annual_turnover=2_000_000, commencement_year=2024,
                                  has_related_company_over_threshold=False)),
        determine(BusinessProfile(annual_turnover=50_000_000, commencement_year=2020,
                                  has_related_company_over_threshold=False),
                  transaction=Transaction(amount=12_000, on=date(2026, 5, 1))),
        determine(BusinessProfile()),
    ]
    for d in cases:
        assert d.reasons
        for r in d.reasons:
            assert r.section.strip() and r.text.strip()


def test_unknown_version_is_rejected_rather_than_defaulted():
    with pytest.raises(ValueError, match="unknown guideline version"):
        determine(BusinessProfile(annual_turnover=1, commencement_year=2020),
                  version="9.9")


# --- commencement_date precision (Guideline §1.5, 2026 onwards) -------------

def test_november_2026_commencement_uses_the_commencement_date():
    p = BusinessProfile(annual_turnover=4_000_000, commencement_year=2026,
                        expected_first_year_turnover=4_000_000,
                        commencement_date=date(2026, 11, 15),
                        has_related_company_over_threshold=False)
    d = determine(p, version="4.8")
    assert d.implementation_date == date(2026, 11, 15)
    assert d.relaxation_until == date(2027, 12, 31)


def test_january_2026_commencement_still_takes_1_july_2026():
    p = BusinessProfile(annual_turnover=4_000_000, commencement_year=2026,
                        expected_first_year_turnover=4_000_000,
                        commencement_date=date(2026, 1, 20),
                        has_related_company_over_threshold=False)
    assert determine(p, version="4.8").implementation_date == date(2026, 7, 1)


def test_year_only_input_falls_back_and_says_precision_is_reduced():
    p = BusinessProfile(annual_turnover=4_000_000, commencement_year=2026,
                        expected_first_year_turnover=4_000_000,
                        has_related_company_over_threshold=False)
    d = determine(p, version="4.8")
    assert d.implementation_date == date(2026, 7, 1)
    assert any("commencement_date" in r.text for r in d.reasons)


# --- reason provenance ------------------------------------------------------

def test_faq_gap_fill_reasons_are_labelled_as_such():
    """Ambiguity items 1 and 2: the Guideline is silent, the FAQ fills the gap."""
    p = BusinessProfile(annual_turnover=1_500_000, fy2022_turnover=1_500_000,
                        commencement_year=2019,
                        has_related_company_over_threshold=True)
    d = determine(p, version="4.8")
    assert any(r.basis == "faq_gap_fill" for r in d.reasons)


def test_every_reason_has_a_known_basis():
    d = determine(BusinessProfile(annual_turnover=50_000_000, commencement_year=2020,
                                  has_related_company_over_threshold=False),
                  transaction=Transaction(amount=12_000, on=date(2026, 5, 1)))
    assert {r.basis for r in d.reasons} <= {"guideline", "specific", "faq_gap_fill"}
    assert any(r.basis == "specific" for r in d.reasons)


# --- §1.5: FY2022 turnover is a different figure from current turnover ------

def test_example_2_prorates_an_18_month_fy2022():
    """§1.5 Example 2, p14: RM60m over 18 months -> RM40m -> 1 January 2025."""
    p = BusinessProfile(annual_turnover=60_000_000, fy2022_turnover=60_000_000,
                        fy2022_period_months=18, commencement_year=2019,
                        has_related_company_over_threshold=False)
    d = determine(p, version="4.8")
    assert prorate_fy2022(p) == 40_000_000
    assert d.implementation_date == date(2025, 1, 1)
    assert any("pro-rates" in r.text for r in d.reasons)


def test_phase_table_follows_fy2022_not_current_turnover():
    """FY2022 RM8m, current RM2m: §1.5 p13 says later changes do not move the
    obligation, so this is phase 3 (1 July 2025), not phase 4."""
    p = BusinessProfile(annual_turnover=2_000_000, fy2022_turnover=8_000_000,
                        commencement_year=2019,
                        has_related_company_over_threshold=True)
    assert determine(p, version="4.8").implementation_date == date(2025, 7, 1)


def test_missing_fy2022_turnover_is_reported_not_substituted():
    p = BusinessProfile(annual_turnover=8_000_000, commencement_year=2019,
                        has_related_company_over_threshold=False)
    d = determine(p, version="4.8")
    assert d.implementation_date is None
    assert "fy2022_turnover" in d.missing


# --- §1.5: 2026 onwards branches on expected first-year turnover ------------

def test_2026_business_expecting_below_threshold_defers_to_the_crossing_year():
    p = BusinessProfile(annual_turnover=5_000_000, commencement_year=2026,
                        expected_first_year_turnover=1_000_000,
                        year_turnover_reached_threshold=2028,
                        has_related_company_over_threshold=False)
    d = determine(p, version="4.8")
    assert d.implementation_date == date(2030, 1, 1)


def test_2026_business_without_expected_turnover_cannot_pick_a_branch():
    p = BusinessProfile(annual_turnover=5_000_000, commencement_year=2026,
                        has_related_company_over_threshold=False)
    d = determine(p, version="4.8")
    assert d.implementation_date is None
    assert "expected_first_year_turnover" in d.missing


def test_2026_business_expecting_below_threshold_needs_the_crossing_year():
    p = BusinessProfile(annual_turnover=5_000_000, commencement_year=2026,
                        expected_first_year_turnover=1_000_000,
                        has_related_company_over_threshold=False)
    assert "year_turnover_reached_threshold" in determine(p, version="4.8").missing


# --- §1.6.5: an owned entity does not inherit its owner's exemption ---------

def test_owned_entity_over_threshold_is_required_and_cites_1_6_5():
    p = BusinessProfile(annual_turnover=8_000_000, fy2022_turnover=8_000_000,
                        commencement_year=2019, owned_by_exempt_person=True,
                        has_related_company_over_threshold=False)
    d = determine(p, version="4.8")
    assert d.required is True
    assert any("1.6.5" in r.section for r in d.reasons)


def test_owned_entity_below_threshold_keeps_its_own_turnover_exemption():
    """Reading (A): §1.6.5 blocks inheriting an exemption, it does not destroy
    the entity's own §1.6.1(e) one. Reading (B) would swallow every SME."""
    p = BusinessProfile(annual_turnover=900_000, commencement_year=2019,
                        owned_by_exempt_person=True,
                        has_related_company_over_threshold=False)
    assert determine(p, version="4.8").required is False


# --- §1.6.7: transaction-type exemptions, a separate determination ----------

def test_salary_and_zakat_need_no_einvoice():
    for kind in ("employment_income", "pension", "alimony", "zakat", "donation"):
        d = exempt_transaction(kind)
        assert d.required is False and d.scope == "transaction"
        assert any("1.6.7" in r.section for r in d.reasons)


def test_unlisted_share_disposal_flips_on_who_the_disposer_is():
    """§1.6.7(g) carries its own exception."""
    assert exempt_transaction("unlisted_share_disposal",
                              disposer_is_entity=False).required is False
    assert exempt_transaction("unlisted_share_disposal",
                              disposer_is_entity=True).required is True
    undecided = exempt_transaction("unlisted_share_disposal")
    assert undecided.required is None
    assert "disposer_is_entity" in undecided.missing


def test_unlisted_kind_is_not_silently_treated_as_exempt():
    d = exempt_transaction("consulting_fee")
    assert d.required is None and "kind" in d.missing


def test_scope_distinguishes_the_two_kinds_of_determination():
    assert determine(BusinessProfile()).scope == "business"
    assert exempt_transaction("zakat").scope == "transaction"
