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
    p = BusinessProfile(annual_turnover=4_000_000, commencement_year=2020)
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
    p = BusinessProfile(annual_turnover=turnover, commencement_year=2020,
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
                        has_related_company_over_threshold=False)
    d = determine(p, version="4.8")
    assert d.implementation_date == date(2026, 7, 1)


def test_pre_2022_business_below_threshold_uses_concession_not_the_phase_table():
    """A carve-out victim whose YA2022 turnover never reached RM3m: FAQ Q11(a)."""
    p = BusinessProfile(annual_turnover=1_500_000, commencement_year=2019,
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
