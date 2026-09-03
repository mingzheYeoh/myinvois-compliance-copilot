"""API tests. The graph is stubbed throughout: these must never spend quota."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import budget
from app.api import main


class FakeGraph:
    """Stands in for the compiled LangGraph. Records what it was asked."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def invoke(self, state, config=None):
        self.calls.append({"state": state, "config": config})
        return {"answer": "Phase 4 runs until 31 December 2027 "
                          "[Specific Guideline v4.8 §16.1 Table 16.1, p121].",
                "intent": "applicability"}


@pytest.fixture
def client(monkeypatch):
    fake = FakeGraph()
    monkeypatch.setattr(main, "graph", lambda: fake)
    monkeypatch.setattr(budget, "exhausted", lambda: False)
    monkeypatch.setattr(budget, "spend", lambda n: n)
    monkeypatch.setattr(budget, "used", lambda conn=None: 0)
    main.limiter.reset()
    c = TestClient(main.app, raise_server_exceptions=False)
    c.fake = fake
    return c


# --- /chat ------------------------------------------------------------------

def test_chat_returns_answer_citations_route_and_thread(client):
    r = client.post("/chat", json={"message": "How long is the Phase 4 relaxation?"})
    assert r.status_code == 200
    body = r.json()
    assert body["route"] == "applicability"
    assert body["thread_id"]
    assert body["citations"] == [
        {"doc": "Specific Guideline", "version": "4.8",
         "section": "16.1 Table 16.1", "page": 121}
    ]


def test_thread_id_is_passed_to_the_checkpointer(client):
    client.post("/chat", json={"message": "hello there", "thread_id": "t-42"})
    assert client.fake.calls[-1]["config"]["configurable"]["thread_id"] == "t-42"


def test_input_over_the_cap_is_rejected_with_413(client):
    r = client.post("/chat", json={"message": "x" * (main.MAX_INPUT_CHARS + 1)})
    assert r.status_code == 413
    assert r.json()["limit"] == main.MAX_INPUT_CHARS
    assert not client.fake.calls  # never reached the graph


def test_input_at_exactly_the_cap_is_allowed(client):
    r = client.post("/chat", json={"message": "x" * main.MAX_INPUT_CHARS})
    assert r.status_code == 200


def test_empty_message_is_rejected(client):
    assert client.post("/chat", json={"message": "   "}).status_code == 400


def test_budget_exhaustion_returns_429_with_a_reset_time(client, monkeypatch):
    monkeypatch.setattr(budget, "exhausted", lambda: True)
    r = client.post("/chat", json={"message": "anything"})
    assert r.status_code == 429
    body = r.json()
    assert "quota" in body["error"].lower()
    assert body["resets_at"].endswith("+00:00")
    assert not client.fake.calls


def test_rate_limit_kicks_in_after_ten_chat_requests(client):
    codes = [client.post("/chat", json={"message": "hi there"}).status_code
             for _ in range(12)]
    assert codes[:10] == [200] * 10
    assert 429 in codes[10:]


def test_provider_throttling_becomes_a_busy_message_not_a_traceback(client, monkeypatch):
    def boom(state, config=None):
        raise RuntimeError("Error code: 429 - rate_limit_exceeded")

    monkeypatch.setattr(client.fake, "invoke", boom)
    r = client.post("/chat", json={"message": "hello"})
    assert r.status_code == 503
    assert "busy" in r.json()["error"].lower()
    assert "Traceback" not in r.text


# --- /validate --------------------------------------------------------------

def test_validate_round_trip_needs_no_llm(client):
    r = client.post("/validate", json={"invoice": {
        "Supplier's Name": "ACME Sdn Bhd", "Buyer's Name": "Bob"}})
    assert r.status_code == 200
    body = r.json()
    assert body["valid"] is False
    assert body["checked"] == 55
    names = [f["name"] for f in body["missing_mandatory"]]
    assert "Supplier's TIN" in names
    assert body["missing_mandatory"][0]["section"].startswith("Guideline v4.8 §Appendix 1")
    assert not client.fake.calls  # deterministic path, no graph, no tokens


def test_validate_keeps_working_when_the_budget_is_gone(client, monkeypatch):
    monkeypatch.setattr(budget, "exhausted", lambda: True)
    r = client.post("/validate", json={"invoice": {"Supplier's Name": "ACME"}})
    assert r.status_code == 200


def test_validate_has_a_higher_rate_limit_than_chat(client):
    codes = [client.post("/validate", json={"invoice": {}}).status_code
             for _ in range(12)]
    assert codes == [200] * 12  # 30/min, so 12 is fine


# --- /health ----------------------------------------------------------------

def test_health_reports_versions_and_budget(client):
    body = client.get("/health").json()
    assert body["db"] in ("ok", "fail")
    assert body["guideline_versions"]["general_guideline"] == "4.8"
    assert body["budget"]["limit"] == budget.limit()


def test_index_is_served(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "not tax or legal advice" in r.text


# --- the blocking rule (regression: a defaulted field is not a profile) -----

def test_a_defaulted_profile_field_does_not_make_a_generic_question_blocking():
    """fy2022_period_months defaults to 12, so it is never None. Treating any
    non-null field as "the user told us about their business" made "what is the
    exemption threshold?" demand a turnover figure."""
    from app.graph.graph import DECISION_FIELDS

    assert "fy2022_period_months" not in DECISION_FIELDS
    assert "annual_turnover" in DECISION_FIELDS


def test_budget_exhaustion_falls_back_to_the_separately_metered_model(monkeypatch):
    """compound-mini is backed by gpt-oss-120b and shares its TPD pool, so it
    cannot be the fallback. gpt-oss-20b is metered separately."""
    import pytest as _pytest

    from app import budget as b

    monkeypatch.setattr(b, "used", lambda conn=None: b.limit() + 1)
    # Classification degrades; answering does not.
    assert b.chosen_model(small=True) == b.SMALL_MODEL
    with _pytest.raises(b.QuotaExhausted):
        b.chosen_model(small=False)
    assert b.FALLBACK_MODEL != b.COMPOUND_MODEL


def test_primary_model_is_used_while_budget_remains(monkeypatch):
    from app import budget as b

    monkeypatch.setattr(b, "used", lambda conn=None: 0)
    assert b.chosen_model(small=False) == b.PRIMARY_MODEL


def test_provider_daily_cap_is_not_reported_as_transient_busy(client, monkeypatch):
    """A TPD exhaustion will not clear "in a moment", so it must not say so."""
    def boom(state, config=None):
        raise RuntimeError("Error code: 429 ... on tokens per day (TPD): Limit 200000")

    monkeypatch.setattr(client.fake, "invoke", boom)
    r = client.post("/chat", json={"message": "hello"})
    assert r.status_code == 429
    assert "quota" in r.json()["error"].lower()
    assert "moment" not in r.json()["error"].lower()
    assert r.json()["resets_at"]


def test_a_mandatory_fields_question_pre_routes_to_field_check():
    """Day 7 q08: the 20b router sent this to general_qa, which answered from
    §4.0 prose instead of the deterministic Appendix 1 table. The field-list
    check runs before the applicability keywords, so "which fields are required
    for a self-billed e-Invoice" is a field question, not a timeline one."""
    from app.graph.graph import pre_route

    assert pre_route("What are the mandatory fields in an e-Invoice?") == "field_check"
    assert pre_route("Which fields are required for a self-billed e-Invoice?") == "field_check"
    assert pre_route("How long is the relaxation period for Phase 4?") == "applicability"
    assert pre_route("What is an e-Invoice?") is None


def test_determination_dates_are_rendered_the_way_the_guidelines_write_them():
    """The engine emits ISO dates and the model quotes the block close to
    verbatim, so "the implementation date is 2025-01-01" reached the user.
    Page ranges and version numbers must survive untouched."""
    from app.graph.graph import _dates_as_prose

    assert _dates_as_prose("date is 2025-01-01.") == "date is 1 January 2025."
    assert _dates_as_prose("until 2027-12-31") == "until 31 December 2027"
    assert _dates_as_prose("p32-33 v4.8 row 7") == "p32-33 v4.8 row 7"
