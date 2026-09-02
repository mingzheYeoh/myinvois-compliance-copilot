# MyInvois Compliance Copilot

An agentic RAG assistant for Malaysia's mandatory e-Invoicing (LHDN / MyInvois), grounded
in the official IRBM guidelines. Every answer cites the guideline **version and section
number** so a user can verify it against the source PDF. Compliance determinations — which
implementation phase a business falls into, from what date, under which relaxation — are
made by a **deterministic Python rule engine, never by the LLM**; the model's job is to
classify, retrieve, and explain with citations, not to decide outcomes.

See [PLAN.md](PLAN.md) for the full architecture and two-week build plan.

## Status

Day 1 of 14 — ingestion only. No LangChain/LangGraph code yet.

## How to run locally

Requires [uv](https://docs.astral.sh/uv/) and Docker Desktop. In PowerShell:

```powershell
# 1. Dependencies (uv fetches Python 3.11 itself; nothing global changes)
uv sync

# 2. Postgres 16 + pgvector
docker compose up -d

# 3. Config
Copy-Item .env.example .env

# 4. Source PDFs -> data/raw/  (skip if they are already there)
$ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
@{
  "irbm-e-invoice-guideline.pdf"          = "https://www.hasil.gov.my/wp-content/uploads/IRBM-e-Invoice-Guideline.pdf"
  "irbm-e-invoice-specific-guideline.pdf" = "https://www.hasil.gov.my/wp-content/uploads/IRBM-e-Invoice-Specific-Guideline.pdf"
  "lhdnm-e-invoice-general-faqs.pdf"      = "https://www.hasil.gov.my/media/0xqitc2t/lhdnm-e-invoice-general-faqs.pdf"
}.GetEnumerator() | ForEach-Object {
  Invoke-WebRequest -Uri $_.Value -OutFile "data/raw/$($_.Key)" -UserAgent $ua
}

# 5. Ingest (first run downloads the ~130MB embedding model)
uv run python scripts/ingest.py

# Checks
uv run pytest -q
uv run ruff check .
```

`--dry-run` parses and reports without touching the database.

## Ingestion design

`scripts/ingest.py` turns the LHDN PDFs into citable, searchable chunks.

**Chunking is per-section, and the heading pattern is per-document.** LHDN uses three
different numbering conventions across the three PDFs, so each one declares its own
heading regex in `data/raw/manifest.json` alongside its version and effective date. Three
regexes in a data file beat one unmaintainable regex in code, and adding a new guideline
version is a manifest entry plus a re-run.

**A number that looks like a section usually isn't one.** The parser only accepts a
heading if its number *continues the document's numbering* (`follows()` in `ingest.py`).
Without that check, real ingests of these PDFs produced phantom sections from a date
(`1.7.2021`), a glossary reference (`Universal Business Language Version 2.1`), and the
numbered sub-bullets inside an FAQ answer — and Specific Guideline §16, *e-Invoice
treatment during interim relaxation period*, went missing entirely because its heading is
Title Case where every other heading is uppercase. Each of those is a regression test in
`tests/test_chunking.py`.

**The Specific Guideline splits again at second-level numbering.** Its top-level sections
run 20+ pages, so `sub_heading` in the manifest breaks §14 into `14.1`, `14.4`, `14.5` and
citations become `[Specific Guideline v4.8 §14.4]`. Third-level numbering (`14.4.5`) is
deliberately left intact — those are numbered *paragraphs*, and splitting there strands
them of context. `MAX_CHARS` is the fallback only when a sub-section is still too long.
Each chunk carries the page its text actually came from, not the section's first page.

**Running headers and footers are detected, not configured** — any first or last line
repeating on more than half the pages is page chrome and is dropped, so
`E-INVOICE GUIDELINE (VERSION 4.8)` does not end up embedded in all 71 chunks.

**Re-ingesting a version replaces it.** Idempotency is keyed on `(doc, version)`, deleted
and reinserted in one transaction. Not a per-chunk upsert: sections move between
revisions, so stale chunks would otherwise survive a re-ingest.

**Hybrid search is Postgres-only.** `chunks.embedding` is a `vector(384)` with an HNSW
index; `chunks.tsv` is a generated `tsvector` column with a GIN index that Postgres
maintains itself. No second service, no application-side sync.

### Current corpus

| Document | Version | Sections | Chunks | Avg chars |
|---|---|---|---|---|
| e-Invoice Guideline (General) | 4.8 (30 Aug 2026) | 51 | 71 | 877 |
| e-Invoice Specific Guideline | 4.8 (7 Jul 2026) | 17 | 133 | 1038 |
| e-Invoice General FAQs | updated 5 May 2026 | 127 | 139 | 642 |

343 chunks total. Superseded versions live in `data/raw/archive/` as fixtures for the
version-parameterised rule engine.

> **The URL slug is case-sensitive.** `wp-content/uploads/irbm-e-invoice-guideline.pdf`
> serves a stale **v4.6**; `wp-content/uploads/IRBM-e-Invoice-Guideline.pdf` serves the
> current **v4.8**. Search-engine `/media/<slug>/` links redirect to the stale file.
> Always confirm the version string on page 1 after fetching — v4.8 contains
> `RM3,000,000` and no `RM1,000,000`.

## Known limitations (Day 1)

- Six General Guideline headings have almost no extractable text because their content is
  a figure or table; they are flagged as `THIN SECTIONS` on every run. Table extraction is
  not implemented.
- Source PDFs are gitignored (large, re-downloadable); `manifest.json` is tracked.

## Disclaimer

Informational only. Not tax or legal advice. Verify against the official LHDN documents.
