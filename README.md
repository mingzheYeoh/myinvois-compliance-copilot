# MyInvois Compliance Copilot

An agentic RAG assistant for Malaysia's mandatory e-Invoicing (LHDN / MyInvois), grounded
in the official IRBM guidelines. Every answer cites the guideline **version and section
number** so a user can verify it against the source PDF. Compliance determinations — which
implementation phase a business falls into, from what date, under which relaxation — are
made by a **deterministic Python rule engine, never by the LLM**; the model's job is to
classify, retrieve, and explain with citations, not to decide outcomes.

See [PLAN.md](PLAN.md) for the full architecture and two-week build plan.

## Status

**Day 11 of 14 — live.** <https://myinvois-api.ambitiousbush-8ab23d9e.southeastasia.azurecontainerapps.io>

| | |
|---|---|
| Corpus | 420 chunks — 86 Guideline v4.8, 194 Specific Guideline v4.8, 140 FAQ 2026-05-05 |
| Graph | 3 intents (general QA / applicability / field check) with a corrective-RAG retry loop |
| Rule engine | Deterministic; decides every date, threshold and phase. The LLM never decides an outcome |
| Golden set | 21/21 on Azure (`uv run python scripts/eval.py`) |
| RAGAS | Faithfulness 0.950, answer relevancy 0.803 (n=17 of 21). Retrieval scores fell over the same change — see [Evaluation](#evaluation-ragas) |
| Cost | ≈5,400 tokens per answer (112,923 over the 21-case run); P50 5.7s warm, ~34s cold start at min-replicas 0 |
| Serving | Azure Container Apps + Azure PostgreSQL 16 (pgvector 0.8.2), 565MB image, scale-to-zero |
| Models | Azure OpenAI primary, Groq fallback; bge-small embeddings baked into the image |
| CI | GitHub Actions: 120 tests + ruff → ACR build → deploy. The test job needs no secrets |

Remaining: Day 12 documentation, 13 user testing, 14 buffer.

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

| Document | Version | Sections | Chunks |
|---|---|---|---|
| e-Invoice Guideline (General) | 4.8 (30 Aug 2026) | 51 | 86 |
| e-Invoice Specific Guideline | 4.8 (7 Jul 2026) | 17 | 194 |
| e-Invoice General FAQs | updated 5 May 2026 | 127 | 140 |

420 chunks total, up from 343 before Day 11 split tables into one chunk per row. The
section counts are unchanged: a row chunk keeps its section's label, which is what keeps
citations stable across the change. Superseded versions live in `data/raw/archive/` as
fixtures for the version-parameterised rule engine.

> **The URL slug is case-sensitive.** `wp-content/uploads/irbm-e-invoice-guideline.pdf`
> serves a stale **v4.6**; `wp-content/uploads/IRBM-e-Invoice-Guideline.pdf` serves the
> current **v4.8**. Search-engine `/media/<slug>/` links redirect to the stale file.
> Always confirm the version string on page 1 after fetching — v4.8 contains
> `RM3,000,000` and no `RM1,000,000`.

## Known limitations

- **The golden set and RAGAS disagree about Day 11, and the disagreement is unresolved.**
  Row-level table chunking and section-pinned retrieval took the golden set from 20/20 to
  21/21 and made citations land on the right table row — while RAGAS context recall on the
  same 13 rule-engine cases fell 0.551 → 0.436 and precision 0.882 → 0.700. Both numbers
  are measured, with the same judge on the same cases. See [Evaluation](#evaluation-ragas)
  for the two candidate mechanisms and the one experiment that would settle it.
- **The daily token budget is one counter shared by development and production.** On
  2026-09-04 a day of evaluation work spent 1,032,879 tokens against a raised *local*
  ceiling. Production's own limit stayed at its 150,000 default — the deploy script
  unsets the override — but the counter is global, so the live app reported
  `remaining: 0` and refused new questions for the rest of the day. Nobody had asked it
  anything; the outage was real, just unwitnessed. A per-environment or per-key ceiling
  is the obvious shape. It is not built.
- Production runs a 150,000-token daily ceiling; development ran at 1,200,000 for a single
  day of evaluation. The two differ because they buy different things — one caps what a
  day of public traffic can cost, the other buys a scoring run, and one RAGAS pass alone
  is ~263,000 tokens.
- Tables that extract as text are chunked one row per chunk since Day 11. A heading whose
  content is a *figure* still yields nothing to chunk; those are flagged `THIN SECTIONS`
  on every ingest run.
- `§Appendix 1` citations are served from `data/rules/invoice_fields.json`, the same table
  the field checker reads — Appendix 1 was never ingested as chunks. So the citation opens,
  but the appendix is not retrievable: no answer can reach it through search.
- Source PDFs are gitignored (large, re-downloadable); `manifest.json` is tracked.

## Frontend

A mobile-first Single Page Application designed for business owners on mobile devices, built with **Vite**, **React**, and **TypeScript** (zero UI libraries, zero state management libraries).

### Features
- **Ask Assistant**: Multi-turn chat session with in-memory `thread_id` preservation (supports profile collection flows like Day 4 Q2). Displays route badges (**General**, **Applicability**, **Field Check**), structured guideline citations (`doc`, `version`, `section`, `page`), and callout styling for "confirm with LHDN" notices.
- **Check Invoice**: Deterministic validation against official IRBM Appendix 1 specifications via `/validate`. Zero LLM token consumption; remains 100% operational when daily token budget is exhausted. Offers both a Quick Form (common fields) and raw JSON editor.
- **Header & System Health**: Displays active guideline document versions from `/health` and live token budget meter. Handles cold-starts by displaying a "~35s waking up" indicator and polling `/health` until `status == "ok"` before activating the assistant.
- **Defensive Error Handling**: Client-side character counter prevents exceeding the 2,000 character limit (guarding against HTTP 413); gracefully distinguishes 429 quota exhaustion (displaying exact reset time) from rate-limit throttling; and provides retry buttons without losing typed input on network disruptions.

### Development & Build
```powershell
# Run Vite dev server with proxy to FastAPI (port 8000)
cd frontend
npm install
npm run dev

# Compile production bundle into src/app/static/
npm run build
```

FastAPI serves the compiled bundle from `src/app/static/` at `/` with an SPA fallback for client routing.

### Docker Multi-Stage Build & Image Size Delta
`Dockerfile` uses a multi-stage build with `node:22-slim` to compile the frontend assets, which are copied into the Python runtime container:
- **Previous static asset**: `static/index.html` (5,069 bytes, ~5.0 KB)
- **New frontend bundle**: `src/app/static/` (179,417 bytes, ~175.2 KB uncompressed; 51.9 KB gzipped)
- **Runtime image delta**: Net increase of **+174.3 KB** (~0.01% of the total ~1.5 GB image). Node.js and build dependencies are completely discarded across stages.

## Evaluation (RAGAS)

| Metric | Day 10 baseline | After Day 11 | Cases |
|---|---|---|---|
| Faithfulness | 0.922 | **0.950** | n=17 |
| Answer Relevancy | 0.806 | 0.803 | n=17 |
| Context Precision (rag only) | 0.750 | 0.750 | n=2 |
| Context Recall (rag only) | 1.000 | 1.000 | n=2 |
| Context Precision (rule-engine cases) | 0.882 | **0.700** | n=13 |
| Context Recall (rule-engine cases) | 0.551 | **0.436** | n=13 |

Judge: `chat-small`, the same Azure deployment the app answers with, in both runs. The
scored set is identical across the two — the same 2 rag, 13 deterministic and 2
field_check cases, by id. Day 11 added one case (q21, an abstention); abstentions are
excluded from every metric in both runs, so the extra case moves the excluded count and
nothing that was measured.

Excluded: clarifying (asked for a missing input rather than answering, so there is no
claim set to ground) and abstention (correct refusal, so there are no claims and no
ground truth to recall). Retrieval metrics are shown separately for rule-engine cases
because the answer there came from `params.json`, not from the retrieved chunks —
averaging the two together would describe neither.

### The two instruments disagree

Day 11 chunked guideline tables one row per chunk and began fetching cited sections by
metadata instead of by similarity. The golden set went **20/20 → 21/21**, and citations
got more precise: `§1.6.1(e)` now resolves to the row that actually carries RM3,000,000
rather than to a 38-character heading. Over that same change, RAGAS context recall on the
rule-engine cases went **0.551 → 0.436** and precision **0.882 → 0.700**.

Day 10 ranked row-level chunking as the top fix and predicted recall would reach 0.85+.
It did not. Both numbers above are measured, not estimated.

Two candidate mechanisms, one of them untested:

1. **Fragmentation** (recall). q07 and q11 each fell 1.00 → 0.00 while their context grew
   from 6 chunks to 8. A reference sentence that used to sit inside one prose block now
   spans several rows, so no single retrieved chunk clearly entails it — even though every
   word of it was retrieved. Not a volume effect: total context grew 6,876 → 9,108
   characters (+32%).
2. **Rank position** (precision) — **untested hypothesis.** Context precision here is mean
   average precision, which is rank-sensitive, and pinned sections are *appended* after the
   hybrid results, at ranks 7–8. A relevant chunk at rank 7 contributes k/7, pulling the
   mean down even when it is the most authoritative chunk present.
   **The experiment that settles it:** re-score the same run with pinned chunks ordered
   first and nothing else changed. If precision returns toward 0.882, the drop was an
   artifact of rank position rather than a loss of retrieval quality. Not yet run — it
   costs a full scoring pass.

No winner is claimed between the two instruments. They measure different things: the
golden set asks whether the answer carried the right facts and citations; RAGAS asks
whether each retrieved chunk is relevant, in order, and entails the reference. A change
can genuinely improve one and depress the other. What exists here is a named experiment
that would resolve which happened.

### Pending: a second judge

The same model family that writes the answers also grades them. The intended answer to
that is a cross-judge run on Groq's `gpt-oss-120b`. **It is not done.** The chat-small
pass alone cost 149,601 scoring tokens against a 150,000 daily ceiling, and a half-run
would have produced no comparison at all. A full pass is ~263,000 tokens (113,363
answering + 149,601 scoring).

Re-run with `uv run python scripts/ragas_eval.py`; results land in
`data/eval/ragas-<date>-<judge>.json` so runs stay comparable. Metric definitions are
RAGAS's; the implementation is in-repo because every published `ragas` release pins
`langchain-core<1.0` and this app runs on 1.6.1.

## Disclaimer

Informational only. Not tax or legal advice. Verify against the official LHDN documents.
