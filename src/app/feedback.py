"""Reports of wrong answers, and the trace that produced each one.

The point is not a dashboard. It is that a user saying "this is wrong" turns into
a LangSmith execution tree someone can open, and from there into a golden case.
So the row carries the run id: without it a report is an anecdote, and the run is
where the retrieval, the rule engine's determination and the prompt are visible.

Deliberately no free-text field and no user identifier. A one-click report cannot
carry a name, an email or a complaint, so there is nothing here to leak and
nothing to moderate. The question and answer are stored because they ARE the
report -- a thread id alone would be unreadable a week later.
"""

from __future__ import annotations

import json
import os
from typing import Any

import psycopg

SCHEMA = """
CREATE TABLE IF NOT EXISTS feedback (
    id         bigserial PRIMARY KEY,
    thread_id  text NOT NULL,
    -- One row per reported answer: a second click on the same answer is the same
    -- report, not a second one, and the UI cannot tell that a POST already landed.
    message_id text NOT NULL UNIQUE,
    question   text NOT NULL,
    answer     text NOT NULL,
    citations  jsonb NOT NULL DEFAULT '[]'::jsonb,
    route      text,
    model      text,
    run_id     text,
    created_at timestamptz NOT NULL DEFAULT now()
);
"""


def _connect():
    return psycopg.connect(os.environ["DATABASE_URL"])


def init() -> None:
    with _connect() as conn:
        conn.execute(SCHEMA)
        conn.commit()


def record(entry: dict[str, Any]) -> bool:
    """Store one report. False means this answer was already reported."""
    with _connect() as conn:
        row = conn.execute(
            """INSERT INTO feedback
                 (thread_id, message_id, question, answer, citations, route, model, run_id)
               VALUES (%(thread_id)s, %(message_id)s, %(question)s, %(answer)s,
                       %(citations)s, %(route)s, %(model)s, %(run_id)s)
               ON CONFLICT (message_id) DO NOTHING
               RETURNING id""",
            {**entry, "citations": json.dumps(entry.get("citations", []))},
        ).fetchone()
        conn.commit()
        return row is not None


def recent(limit: int = 20) -> list[dict[str, Any]]:
    with _connect() as conn:
        cur = conn.execute(
            "SELECT thread_id, message_id, question, answer, citations, route, model,"
            "       run_id, created_at FROM feedback ORDER BY created_at DESC LIMIT %s",
            (limit,),
        )
        cols = [c.name for c in cur.description]
        return [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]
