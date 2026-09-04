"""Ingest LHDN e-Invoice PDFs from data/raw into pgvector.

Chunks are split on numbered sections (each document declares its own heading
regex in data/raw/manifest.json, because LHDN uses a different convention per
document). Re-running with the same (doc, version) replaces those rows rather
than duplicating them.

    uv run python scripts/ingest.py [--dry-run]
"""

from __future__ import annotations

import json
import os
import re
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import pdfplumber
import psycopg
from dotenv import load_dotenv
from pgvector.psycopg import register_vector
from sentence_transformers import SentenceTransformer

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"

# bge-small-en-v1.5 has a 512-token window; ~1800 chars keeps whole sections
# intact where possible and stays clear of truncation where it can't.
MAX_CHARS = 1800
MIN_CHARS = 120

DOT_LEADER = re.compile(r"\.{4,}")  # table-of-contents lines
# pdfplumber emits Symbol/private-use glyphs for bullets and smart punctuation.
GLYPHS = {"‘": "'", "’": "'", "“": '"', "”": '"', "–": "-", "—": "-", " ": " "}


@dataclass
class Chunk:
    """A citable unit. `lines` carries each line's page, so that splitting a
    30-page section still cites the page the text actually came from."""

    section: str
    title: str
    lines: list[tuple[int, str]]
    # A table's preamble, repeated onto each row chunk so a lone row still says
    # what it is a row OF. Deliberately not part of `lines`: page comes from
    # lines[0], and a row on p33 of a table that opens on p32 must cite p33.
    header: str = ""

    @property
    def page(self) -> int:
        return self.lines[0][0] if self.lines else 0

    @property
    def text(self) -> str:
        body = "\n".join(t for _, t in self.lines).strip()
        return f"{self.header}\n{body}".strip() if self.header else body


def clean(line: str) -> str:
    for bad, good in GLYPHS.items():
        line = line.replace(bad, good)
    # Private-use area (Symbol-font bullets etc.) and stray replacement chars.
    line = re.sub(r"[-�]", "-", line)
    return line.strip()


def read_pages(path: Path, first_page: int) -> list[tuple[int, list[str]]]:
    """Extract text per page, stripping the running header/footer.

    Headers and footers are detected rather than configured: any first or last
    line repeating on more than half the pages is chrome, not content.
    """
    with pdfplumber.open(path) as pdf:
        raw = [
            (i, [clean(x) for x in (p.extract_text() or "").split("\n") if clean(x)])
            for i, p in enumerate(pdf.pages, 1)
        ]

    body = [(i, lines) for i, lines in raw if i >= first_page and lines]
    threshold = len(body) / 2
    heads = Counter(lines[0] for _, lines in body)
    tails = Counter(lines[-1] for _, lines in body)
    chrome = {t for t, n in (*heads.items(), *tails.items()) if n > threshold}
    # Bare page numbers are chrome too, however few pages share the exact string.
    return [
        (i, [x for x in lines if x not in chrome and not re.fullmatch(r"\d{1,3}", x)])
        for i, lines in body
    ]


def _num(section: str) -> tuple[int, ...]:
    """'2.0' -> (2,), '2.4.1' -> (2, 4, 1). LHDN writes top-level as 'N.0'."""
    parts = [int(x) for x in section.split(".")]
    while len(parts) > 1 and parts[-1] == 0:
        parts.pop()
    return tuple(parts)


def follows(prev: tuple[int, ...] | None, cur: tuple[int, ...]) -> bool:
    """Does `cur` continue the numbering after `prev`?

    This is what separates a real heading from a number that merely looks like
    one. '1.7.2021' (a date) cannot follow 1.5; 'Version 2.1' in the glossary
    cannot follow 4.0; an answer's sub-bullet '1.' cannot follow question 126.
    """
    if prev is None:
        return True
    if len(cur) > len(prev):  # descending a level: 1.6 -> 1.6.1
        return len(cur) == len(prev) + 1 and cur[:-1] == prev and cur[-1] <= 1
    # same or shallower level: siblings must share a parent and increase.
    return cur[:-1] == prev[: len(cur) - 1] and cur[-1] > prev[len(cur) - 1]


def split_sections(
    pages, heading: re.Pattern, part: re.Pattern | None
) -> tuple[list[Chunk], list[str]]:
    """Walk the document top to bottom, starting a new section at each heading."""
    sections: list[Chunk] = []
    rejected: list[str] = []
    prefix = ""
    last: tuple[int, ...] | None = None
    label, title = "preamble", ""
    buf: list[tuple[int, str]] = []

    def flush() -> None:
        if any(t.strip() for _, t in buf):
            sections.append(Chunk(label, title, list(buf)))

    for page_no, lines in pages:
        for line in lines:
            if DOT_LEADER.search(line):  # table of contents
                continue
            if part and (m := part.match(line)):
                prefix = m.group(1).strip()
                continue
            if m := heading.match(line):
                num = m.group(1)
                if follows(last, _num(num)):
                    flush()
                    last = _num(num)
                    label = f"{prefix} Q{num}" if prefix else num
                    title = m.group(2).strip()
                    buf = [(page_no, title)]
                    continue
                # Looked like a heading, isn't one — keep it as body text.
                rejected.append(f"p{page_no}: {line[:70]}")
            buf.append((page_no, line))
    flush()
    return sections, rejected


def subsplit(s: Chunk, sub: re.Pattern | None) -> list[Chunk]:
    """Break a long section at its second-level numbering: 3 -> 3.1, 3.2, 14.4.

    The same `follows` continuation check applies, seeded with the section's own
    number, so a stray '3.1' quoted inside section 14 is not mistaken for a
    sub-heading. Third-level numbering (14.4.5) is deliberately left alone --
    those are numbered paragraphs, and splitting there strands them of context.
    """
    if sub is None or not s.section[:1].isdigit():
        return [s]
    parts: list[Chunk] = []
    label, last = s.section, _num(s.section)
    buf: list[tuple[int, str]] = []

    def flush() -> None:
        if any(t.strip() for _, t in buf):
            parts.append(Chunk(label, s.title, list(buf)))

    for page_no, line in s.lines:
        if (m := sub.match(line)) and follows(last, _num(m.group(1))):
            flush()
            label, last = m.group(1), _num(m.group(1))
            buf = []
        buf.append((page_no, line))
    flush()
    # If the text before the first sub-heading is just the section title, it is
    # context for the first sub-section, not a chunk of its own.
    if len(parts) > 1 and len(parts[0].text) < MIN_CHARS:
        parts[1].lines[:0] = parts[0].lines
        parts.pop(0)
    return parts


def by_size(s: Chunk) -> list[Chunk]:
    """Last resort: split a still-oversized chunk at line boundaries."""
    if len(s.text) <= MAX_CHARS:
        return [s]
    out: list[Chunk] = []
    piece: list[tuple[int, str]] = []
    size = 0
    for page_no, line in s.lines:
        if size + len(line) > MAX_CHARS and piece:
            out.append(Chunk(s.section, s.title, piece, s.header))
            piece, size = [], 0
        piece.append((page_no, line))
        size += len(line) + 1
    if piece:
        # A trailing scrap is more useful glued to the previous piece.
        if len("\n".join(t for _, t in piece)) < MIN_CHARS and out:
            out[-1].lines.extend(piece)
        else:
            out.append(Chunk(s.section, s.title, piece, s.header))
    return out


# A table row opens with its own marker: a lettered item ("(e) Taxpayer with an
# annual turnover..."), or a bare row number ("6 Payment to agents"). Section
# numbering is not a row -- "3.7.1 For the purposes" has a dot after the digits,
# never a space -- so the lookahead for whitespace is what keeps them apart.
LETTER_ROW = re.compile(r"^\(([a-z])\)\s+\S")
NUMBER_ROW = re.compile(r"^(\d{1,2})\s+[A-Za-z]")


def _step(prev: str, cur: str) -> int:
    if prev.isalpha() and cur.isalpha():
        return ord(cur) - ord(prev)
    return int(cur) - int(prev) if prev.isdigit() and cur.isdigit() else 0


def _chain(marks: list[tuple[int, str]]) -> list[tuple[int, str]]:
    """Keep only the markers that continue the sequence: a, b, c, d, e.

    Two jobs at once. It rejects prose, because the marker patterns alone fire on
    any line opening with a small number -- "2 January 2026" among them -- and
    only a real table numbers its rows consecutively. And it ignores NESTED
    markers: Guideline 1.6.1 runs (a)(b)(c)(d)(e) with its own (i)(ii) beneath
    (c), and cutting at that (i) both orphans it and truncates (c) of the detail
    that belongs to it.
    """
    kept = marks[:1]
    for i, k in marks[1:]:
        if _step(kept[-1][1], k) == 1:
            kept.append((i, k))
    return kept


def table_rows(s: Chunk) -> list[Chunk] | None:
    """One chunk per table row, or None if this section is not a table.

    Day 10 measured context recall at 0.00 on five cases whose answer turns on a
    single table cell -- the RM3,000,000 in Guideline 1.6.1(e), the activities in
    Specific Guideline Table 3.6. The figures were in the corpus the whole time,
    buried in an 1,800-character block that ranked below question-shaped FAQ
    entries. A row is short, says one thing, and embeds close to the question that
    asks for it; the preamble rides along as a header so the row still reads as a
    row of something, and the page stays the row's own so the citation gets more
    precise rather than less.
    """
    marks = _chain([(i, m.group(1))
                    for i, (_, line) in enumerate(s.lines)
                    if (m := LETTER_ROW.match(line) or NUMBER_ROW.match(line))])
    if len(marks) < 3:
        return None
    header = "\n".join(t for _, t in s.lines[:marks[0][0]]).strip()
    out: list[Chunk] = []
    for (start, _), (end, _) in zip(marks, [*marks[1:], (len(s.lines), "")], strict=True):
        out.extend(by_size(Chunk(s.section, s.title, s.lines[start:end], header)))
    # Anything before the first row is context, not a chunk of its own; it is
    # already carried on every row above.
    return out


def pack(sections: list[Chunk], sub: re.Pattern | None = None) -> list[Chunk]:
    """Sections -> chunks: split at sub-headings, then table rows, then on size."""
    out: list[Chunk] = []
    for s in sections:
        for part in subsplit(s, sub):
            out.extend(table_rows(part) or by_size(part))
    return out


SCHEMA = """
CREATE EXTENSION IF NOT EXISTS vector;
CREATE TABLE IF NOT EXISTS chunks (
    id             bigserial PRIMARY KEY,
    doc            text NOT NULL,
    version        text NOT NULL,
    section        text NOT NULL,
    section_title  text NOT NULL DEFAULT '',
    page           int  NOT NULL,
    effective_date date,
    content        text NOT NULL,
    embedding      vector(384) NOT NULL,
    -- Postgres maintains the full-text column itself; the hybrid retriever in
    -- src/app/rag just reads it. No application code, no drift.
    tsv tsvector GENERATED ALWAYS AS (to_tsvector('english', content)) STORED
);
CREATE INDEX IF NOT EXISTS chunks_embedding_idx
    ON chunks USING hnsw (embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS chunks_tsv_idx ON chunks USING gin (tsv);
CREATE INDEX IF NOT EXISTS chunks_doc_version_idx ON chunks (doc, version);
"""


def report(entry: dict, sections: list[Chunk], chunks: list[Chunk], rejected: list[str]) -> None:
    lengths = [len(c.text) for c in chunks]
    preamble = [s for s in sections if s.section == "preamble"]
    thin = [s for s in sections if s.section != "preamble" and len(s.text) < MIN_CHARS]
    print(f"\n{entry['title']} v{entry['version']}  ({entry['file']})")
    print(f"  sections parsed : {len({s.section for s in sections if s.section != 'preamble'})}")
    print(f"  chunks          : {len(chunks)}")
    print(
        f"  avg chunk length: {sum(lengths) // len(lengths) if lengths else 0} chars"
        f"  (min {min(lengths, default=0)}, max {max(lengths, default=0)})"
    )
    if preamble:
        pages = sorted({s.page for s in preamble})
        chars = sum(len(s.text) for s in preamble)
        print(
            f"  unparsed front  : {chars} chars before the first heading "
            f"(p{pages[0]}-{pages[-1]}) -> kept as section 'preamble'"
        )
    if thin:
        # A heading with almost no text means the body is a figure or table that
        # the PDF text layer does not carry. Kept, but flagged as low-value.
        print(
            f"  THIN SECTIONS   : {len(thin)} heading(s) with <{MIN_CHARS} chars of text "
            f"-> {', '.join(f'{s.section} p{s.page}' for s in thin[:6])}"
        )
    if rejected:
        print(
            f"  rejected as non-heading ({len(rejected)}): {rejected[0]}"
            + (f" (+{len(rejected) - 1} more)" if len(rejected) > 1 else "")
        )
    if not (preamble or thin or rejected):
        print("  parse issues    : none")


def main() -> int:
    load_dotenv()
    dry = "--dry-run" in sys.argv
    manifest = json.loads((RAW / "manifest.json").read_text(encoding="utf-8"))

    model = SentenceTransformer(os.getenv("EMBEDDING_MODEL", "BAAI/bge-small-en-v1.5"))
    conn = None
    if not dry:
        conn = psycopg.connect(os.environ["DATABASE_URL"])
        conn.execute(SCHEMA)
        conn.commit()
        register_vector(conn)

    all_chunks: dict[str, list[Chunk]] = {}
    for entry in manifest:
        path = RAW / entry["file"]
        if not path.exists():
            print(f"!! missing {path.name} - skipped")
            continue

        pages = read_pages(path, entry.get("first_page", 1))
        heading = re.compile(entry["heading"])
        part = re.compile(entry["part_heading"]) if entry.get("part_heading") else None
        sub = re.compile(entry["sub_heading"]) if entry.get("sub_heading") else None
        sections, rejected = split_sections(pages, heading, part)
        chunks = pack(sections, sub)
        all_chunks[entry["doc"]] = chunks
        report(entry, sections, chunks, rejected)

        if dry:
            continue

        vectors = model.encode(
            [c.text for c in chunks],
            batch_size=32,
            normalize_embeddings=True,
            show_progress_bar=False,
        )
        with conn.transaction():
            # Idempotency: sections move between revisions, so replace the whole
            # (doc, version) rather than upserting chunk by chunk.
            conn.execute(
                "DELETE FROM chunks WHERE doc = %s AND version = %s",
                (entry["doc"], entry["version"]),
            )
            conn.cursor().executemany(
                """INSERT INTO chunks
                   (doc, version, section, section_title, page, effective_date,
                    content, embedding)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
                [
                    (
                        entry["doc"],
                        entry["version"],
                        c.section,
                        c.title,
                        c.page,
                        entry["effective_date"],
                        c.text,
                        v,
                    )
                    for c, v in zip(chunks, vectors, strict=True)
                ],
            )

    print("\n--- section-level chunk distribution (top-level section) ---")
    for doc, chunks in all_chunks.items():
        dist = Counter(_toplevel(c.section) for c in chunks)
        secs = Counter(_toplevel(s) for s in {c.section for c in chunks})
        print(f"\n{doc}:")
        for sec, n in sorted(dist.items(), key=lambda kv: _sortkey(kv[0])):
            print(f"  {sec:<12} {'#' * min(n, 40):<40} {n:>3} chunks / {secs[sec]:>3} sections")

    if conn:
        total = conn.execute("SELECT count(*) FROM chunks").fetchone()[0]
        conn.close()
        print(f"\nrows in chunks table: {total}")
    return 0


def _toplevel(section: str) -> str:
    """Group for the distribution report: '2.4.1' -> '2', 'PART 3 Q94' -> 'PART 3'."""
    return section.split(" Q")[0] if section.startswith("PART") else section.split(".")[0]


def _sortkey(sec: str):
    m = re.search(r"\d+", sec)
    return (0, int(m.group())) if m else (1, sec)


if __name__ == "__main__":
    raise SystemExit(main())
