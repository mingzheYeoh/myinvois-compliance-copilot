"""Daily LLM token budget, kept in Postgres so it survives container restarts.

The default of 150,000 was set against Groq's 200,000 TPD cap for the gpt-oss
models: we want our own "quota exhausted" message, not Groq's 429 in the middle
of a graph run. With Azure primary there is no comparable hard cap, so the same
number is a COST guard instead -- set DAILY_TOKEN_BUDGET to what a day of this
service is worth to you.

One counter serves both providers, so an Azure day can lock out the Groq
fallback. That is deliberate for now (the point is a ceiling on spend, whoever
serves it) but it is the obvious thing to split if the two ever need separate
limits.

Since Day 8 every LLM call is charged here from get_llm(), not from the /chat
handler, so scripts and the API share one ledger.
"""

from __future__ import annotations

import os

import psycopg

DEFAULT_BUDGET = 150_000
# Same 200K TPD pool for both gpt-oss sizes; 8K TPM is the binding constraint.
PRIMARY_MODEL = "openai/gpt-oss-120b"
SMALL_MODEL = "openai/gpt-oss-20b"
# MEASURED, not assumed: groq/compound-mini is documented as having no daily
# token limit, but it is an agentic system backed by gpt-oss-120b. A request to
# it while 120b's TPD was spent returned
#   429 ... model `openai/gpt-oss-120b` ... TPD: Limit 200000 ... 'type': 'compound'
# so it draws on the same pool and cannot serve as the fallback for a 120b TPD
# exhaustion. It also rejects json_schema structured output.
COMPOUND_MODEL = "groq/compound-mini"
# gpt-oss-20b is metered separately (it kept serving while 120b was exhausted)
# and supports json_schema, so classification nodes can fall back to it safely.
#
# generate CANNOT. Measured on the Day 2 question set: with generate on 20b,
# "How long is the relaxation period for Phase 4?" answered "six (6) months"
# from §16.1's prose while the rule engine's Table 16.1 fact sat in the same
# prompt -- the exact Day 2 failure this project exists to prevent. A wrong
# compliance answer is worse than no answer, so there is no generate fallback:
# the API returns its quota message instead.
FALLBACK_MODEL = SMALL_MODEL


class QuotaExhausted(RuntimeError):
    """Raised rather than answering a compliance question on a weaker model."""

SCHEMA = """
CREATE TABLE IF NOT EXISTS token_budget (
    id          int PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    day         date NOT NULL DEFAULT CURRENT_DATE,
    tokens_used bigint NOT NULL DEFAULT 0
);
INSERT INTO token_budget (id) VALUES (1) ON CONFLICT (id) DO NOTHING;
"""


def limit() -> int:
    return int(os.getenv("DAILY_TOKEN_BUDGET", DEFAULT_BUDGET))


def _connect():
    return psycopg.connect(os.environ["DATABASE_URL"])


def init() -> None:
    with _connect() as conn:
        conn.execute(SCHEMA)
        conn.commit()


def used(conn: psycopg.Connection | None = None) -> int:
    """Tokens spent today. The single row rolls over on the first read of a new
    day, so no scheduled job is needed."""
    close = conn is None
    conn = conn or _connect()
    try:
        row = conn.execute(
            "UPDATE token_budget SET day = CURRENT_DATE, tokens_used = "
            "CASE WHEN day = CURRENT_DATE THEN tokens_used ELSE 0 END "
            "WHERE id = 1 RETURNING tokens_used"
        ).fetchone()
        conn.commit()
        return row[0] if row else 0
    finally:
        if close:
            conn.close()


def spend(tokens: int) -> int:
    """Record usage and return the new total."""
    with _connect() as conn:
        used(conn)  # roll the day over first
        row = conn.execute(
            "UPDATE token_budget SET tokens_used = tokens_used + %s WHERE id = 1 "
            "RETURNING tokens_used", (tokens,)
        ).fetchone()
        conn.commit()
        return row[0]


def exhausted() -> bool:
    return used() >= limit()


def remaining() -> int:
    return max(0, limit() - used())


def chosen_model(small: bool = False) -> str:
    """Which model should serve the next call.

    Falls back to compound-mini once the gpt-oss daily budget is spent, so the
    service degrades rather than stopping.
    """
    if small:
        # Classification is safe on the small model, spent budget or not.
        return SMALL_MODEL
    if exhausted():
        raise QuotaExhausted(
            "Daily token budget spent; refusing to answer on a weaker model.")
    return PRIMARY_MODEL
