"""The golden set.

The structural test is free and always runs -- it catches the typo'd id or the
route name that does not exist, which is how a regression suite usually breaks.
The full run spends real quota, so it is marked `slow` and deselected by default;
CI runs it on demand with `pytest -m slow`.
"""

from __future__ import annotations

import pytest

from scripts.eval import load_cases

ROUTES = {"general_qa", "applicability", "field_check"}


CASE_COUNT = 21  # 20 from Day 7, plus q21 (reasoning from silence) added on Day 11


def test_golden_set_is_wellformed():
    cases = load_cases()
    assert len(cases) == CASE_COUNT
    assert len({c["id"] for c in cases}) == CASE_COUNT
    for c in cases:
        assert c["expected_route"] in ROUTES, c["id"]
        assert c["why"].strip(), f"{c['id']} has no stated source"
        # An expectation that asserts nothing about the answer is not a test.
        assert c["expected_facts"] or c["expected_sections"], c["id"]
        for key in ("expected_sections", "expected_facts", "must_not_contain"):
            assert isinstance(c.get(key, []), list), f"{c['id']}.{key}"


def test_every_intent_and_both_failure_modes_are_covered():
    cases = load_cases()
    assert {c["expected_route"] for c in cases} >= ROUTES
    assert any(len(c["turns"]) > 1 for c in cases), "no multi-turn case"
    assert any("Not covered in the guidelines" in f
               for c in cases for f in c["expected_facts"]), "no abstention case"


@pytest.mark.slow
def test_golden_set_passes():
    from scripts.eval import run_all

    results = run_all(load_cases())
    bad = [f"{r.id}: {'; '.join(r.problems)}" for r in results if not r.ok]
    assert not bad, "\n" + "\n".join(bad)


def test_daily_quota_abort_skips_the_rest(monkeypatch):
    """A TPD 429 will not clear in 90 seconds. Grinding through the remaining
    cases to rediscover that cost 40 minutes on the second full run."""
    from scripts import eval as ev

    tried = []

    def boom(graph, case):
        tried.append(case["id"])
        raise ev.DailyQuotaGone("TPD: Limit 200000, Used 199671")

    monkeypatch.setattr(ev, "run_case", boom)
    results = ev.run_all(load_cases()[:3], graph=object())
    assert tried == ["q01"], "kept calling after the quota was gone"
    assert len(results) == 3
    assert all("not run" in r.problems[0] for r in results)
