"""Hybrid retrieval: pgvector nearest-neighbour + Postgres full-text, fused with RRF.

Both rankings are produced and fused inside one SQL statement, so retrieval is a
single round trip and Postgres stays the only datastore.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import psycopg
from pgvector.psycopg import register_vector
from sentence_transformers import SentenceTransformer

RAW = Path(__file__).resolve().parents[3] / "data" / "raw"
RRF_K = 60  # standard RRF damping constant; larger = flatter rank weighting
POOL = 20  # candidates taken from each ranking before fusion

# bge-* expects this instruction on the query side only, never on documents.
QUERY_PREFIX = "Represent this sentence for searching relevant passages: "

SQL = """
WITH vec AS (
    SELECT id, row_number() OVER (ORDER BY embedding <=> %(qvec)s) AS rank
    FROM chunks WHERE doc || ':' || version = ANY(%(versions)s)
    ORDER BY embedding <=> %(qvec)s LIMIT %(pool)s
), fts AS (
    SELECT id, row_number() OVER (ORDER BY ts_rank(tsv, q) DESC) AS rank
    FROM chunks, websearch_to_tsquery('english', %(qtext)s) q
    WHERE doc || ':' || version = ANY(%(versions)s) AND tsv @@ q
    ORDER BY ts_rank(tsv, q) DESC LIMIT %(pool)s
)
SELECT c.doc, c.version, c.section, c.section_title, c.page, c.content,
       COALESCE(1.0 / (%(rrf)s + vec.rank), 0)
     + COALESCE(1.0 / (%(rrf)s + fts.rank), 0) AS score,
       vec.rank, fts.rank
FROM chunks c
LEFT JOIN vec ON vec.id = c.id
LEFT JOIN fts ON fts.id = c.id
WHERE vec.id IS NOT NULL OR fts.id IS NOT NULL
ORDER BY score DESC LIMIT %(k)s
"""


@dataclass
class Hit:
    doc: str
    version: str
    section: str
    title: str
    page: int
    content: str
    score: float
    vec_rank: int | None
    fts_rank: int | None


@lru_cache(maxsize=1)
def _embedder() -> SentenceTransformer:
    return SentenceTransformer(os.getenv("EMBEDDING_MODEL", "BAAI/bge-small-en-v1.5"))


@lru_cache(maxsize=1)
def latest_versions() -> tuple[str, ...]:
    """The manifest is the source of truth for what 'latest' means per doc."""
    entries = json.loads((RAW / "manifest.json").read_text(encoding="utf-8"))
    return tuple(f"{e['doc']}:{e['version']}" for e in entries)


# The FAQ is question-shaped, so it matches question-shaped queries on both the
# vector and the full-text side and crowds the authoritative documents out. On
# the Day 2 question set it took 26 of 40 slots and the chain answered from a
# stale FAQ threshold. These are floors, not caps: RRF still orders everything.
QUOTA = {"general_guideline": 2, "specific_guideline": 2}


def _apply_quota(rows: list[Hit], k: int) -> list[Hit]:
    picked: list[Hit] = []
    taken: set[int] = set()
    for doc, floor in QUOTA.items():
        for i, hit in enumerate(rows):
            if len(picked) >= k or sum(h.doc == doc for h in picked) >= floor:
                break
            if i not in taken and hit.doc == doc:
                picked.append(hit)
                taken.add(i)
    for i, hit in enumerate(rows):  # RRF order fills whatever is left
        if len(picked) >= k:
            break
        if i not in taken:
            picked.append(hit)
            taken.add(i)
    return sorted(picked, key=lambda h: -h.score)


def search(query: str, k: int = 5, versions: dict[str, str] | None = None) -> list[Hit]:
    """Top-k chunks for `query`. `versions` maps doc -> version; default is latest."""
    filt = [f"{d}:{v}" for d, v in versions.items()] if versions else list(latest_versions())
    qvec = _embedder().encode(QUERY_PREFIX + query, normalize_embeddings=True)
    fetch = max(k * 4, POOL)  # deep enough that the quota has candidates to draw on
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        register_vector(conn)
        rows = conn.execute(
            SQL,
            {"qvec": qvec, "qtext": query, "versions": filt,
             "pool": POOL, "rrf": RRF_K, "k": fetch},
        ).fetchall()
    return _apply_quota([Hit(*r) for r in rows], k)


# "Guideline v4.8 §1.6.1(e), p15" -> ("Guideline", "1.6.1(e)"). The rule engine
# writes its citations in the shape the answer must quote, so they parse the same
# way the auditor parses an answer.
REF = re.compile(r"([A-Za-z ]+?) v[^ ]+ §([^,]+)")
LONG = {v: k for k, v in
        {"general_guideline": "Guideline", "specific_guideline": "Specific Guideline",
         "general_faq": "FAQ"}.items()}

PINNED_SQL = """
SELECT doc, version, section, section_title, page, content, 1.0, NULL, NULL
FROM chunks
WHERE doc || ':' || version = ANY(%(versions)s)
  AND doc = %(doc)s
  AND (section = %(sec)s OR %(sec)s LIKE section || '%%')
ORDER BY
  -- The cited row first. Now that a table is one chunk per row, "§1.6.1(e)"
  -- must return the row holding RM3,000,000, not whichever row sorts first.
  (%(marker)s <> '' AND content LIKE '%%' || %(marker)s || '%%') DESC,
  -- Then the most specific section: "1.6.1(e)" prefix-matches the 1.6 heading
  -- too, and that heading is 38 characters of nothing.
  length(section) DESC,
  page
LIMIT %(limit)s
"""

# The row marker inside a citation: "§1.6.1(e)" -> "(e)".
MARKER = re.compile(r"(\([a-z]\))")


def search_sections(refs: list[str], versions: dict[str, str] | None = None,
                    limit: int = 2) -> list[Hit]:
    """Fetch the chunks of specific cited sections, by metadata rather than by text.

    The rule engine knows which section its answer rests on, so that section should
    not have to win a similarity contest to be retrieved. Appending the label to the
    query string -- which is what this used to do -- does not work: "§1.6.1(e)" as
    free text retrieves nothing, because the guideline body never writes its own
    section numbers. Measured on q01: with the label appended, the chunk holding
    RM3,000,000 was not in the top 20; the corpus had it the whole time.

    The section match is a prefix, both ways round: the engine cites §3.7.2 where
    the document numbers the section 3.7, and §1.6.1(e) where the chunk is 1.6.1.
    """
    filt = [f"{d}:{v}" for d, v in versions.items()] if versions else list(latest_versions())
    want: list[tuple[str, str]] = []
    for ref in refs:
        if (m := REF.match(ref.strip())) and (doc := LONG.get(m.group(1).strip())):
            sec = m.group(2).split(" Table")[0].strip()
            if (doc, sec) not in want:
                want.append((doc, sec))
    if not want:
        return []
    out: list[Hit] = []
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        register_vector(conn)
        for doc, sec in want:
            mark = m.group(1) if (m := MARKER.search(sec)) else ""
            out += [Hit(*r) for r in conn.execute(
                PINNED_SQL, {"versions": filt, "doc": doc, "sec": sec,
                             "marker": mark, "limit": limit}
            ).fetchall()]
    return out
