# MyInvois Compliance Copilot — Project Plan

**Owner:** Yeoh Ming Zhe (FakerYeoh)
**Goal:** A deployable, publicly accessible AI Engineer portfolio project that solves a real problem and demonstrates LangChain, LangGraph, agentic RAG, evaluation, and cloud deployment on Azure.
**Timeline:** 2 weeks (Day 1 = start date)
**Budget:** Zero out-of-pocket cost (Azure for Students credit + free tiers)

---

## 1. Problem Statement

Malaysia's mandatory e-Invoicing (LHDN / MyInvois) is rolling out in phases through 2026. The rules change frequently — Guideline v4.8 (released 30 Aug 2026) raised the exemption threshold from RM1M to RM3M and changed the rules for new businesses. Most online explainers are already outdated, and MSME owners are asking the same questions repeatedly:

- Do I need to issue e-Invoices, and from when?
- When can I use consolidated e-Invoices vs. individual ones?
- What are the mandatory fields, and is my invoice missing any?
- When is self-billed e-Invoice required?

**Solution:** An agentic RAG assistant grounded in official LHDN documents. Every answer cites the guideline **version and section number**, so users can verify it against the source. Compliance determinations (which phase, which date) are made by a **deterministic rule engine**, not by the LLM.

**Target user:** Malaysian MSME owners, bookkeepers, and accounting staff.

---

## 2. Scope

### In scope (v1)
- Q&A over LHDN e-Invoice General Guideline, Specific Guideline, and FAQs
- Applicability check: user describes their business → phase, implementation date, relaxation period, RM10,000 individual-invoice rule
- Mandatory field validation: user pastes an invoice summary (JSON) → missing/invalid fields
- Version-aware citations on every answer
- Public URL with abuse protection (rate limits, daily token budget)
- Offline evaluation with RAGAS on a golden test set
- CI/CD to Azure

### Out of scope (v1)
- Submitting to the MyInvois API
- Multi-language UI (English only; Malay/Chinese later)
- User accounts, chat history persistence
- Legal advice disclaimer is shown; the tool is informational only

---

## 3. Knowledge Base

All sources are official, public LHDN PDFs. Versions below were confirmed on
**2 Sep 2026** by reading page 1 of each PDF, not by trusting the URL:

| Document | Version | Published | Ingested | Purpose |
|---|---|---|---|---|
| e-Invoice Guideline (General) | **4.8** | 30 Aug 2026 | 71 chunks / 51 sections | Core rules, phases, thresholds |
| e-Invoice Specific Guideline | **4.8** | 7 Jul 2026 | 133 chunks / 17 sections | Industry-specific and edge cases (self-billed, consolidated, cross-border) |
| e-Invoice General FAQs | — | updated 5 May 2026 | 139 chunks / 127 questions | Plain-language Q&A, high retrieval value |
| SDK mandatory fields table | not yet ingested | — | — | Source of truth for the field validator (Day 5) |

> **Fetch trap — the URL slug is case-sensitive.** `hasil.gov.my` serves a
> *stale* v4.6 from `wp-content/uploads/irbm-e-invoice-guideline.pdf` and the
> current v4.8 from `wp-content/uploads/IRBM-e-Invoice-Guideline.pdf`. The
> `/media/<slug>/` links returned by search engines redirect to the lowercase
> (stale) file. Always verify the version string on page 1 after fetching.
> v4.8 is confirmed by `RM3,000,000` appearing 8 times and `RM1,000,000` zero
> times — the threshold change is the version's defining feature.

Superseded versions are kept in `data/raw/archive/` (currently General v4.6 and
Specific v4.7). They are test fixtures for the version-parameterised rule
engine: thresholds are keyed by guideline version, so the old thresholds must
stay reproducible.

**Chunking strategy:** Split by numbered section (the guidelines are already
section-numbered). Each chunk carries metadata: `{doc, version, section, page,
effective_date}`. This makes citations precise and makes version upgrades a
re-ingest, not a rewrite.

Each document declares its own heading regex in `data/raw/manifest.json` — LHDN
uses a different numbering convention in each PDF. The Specific Guideline
additionally splits at **second-level** numbering (`3.1`, `14.4`) because its
top-level sections run 20+ pages; third-level numbering (`14.4.5`) is left
intact, as those are numbered paragraphs rather than headings.

---

## 4. Architecture

### 4.1 LangGraph Design

```
State: {
  query, intent, business_profile, retrieved_docs,
  grade, answer, citations, retry_count
}

router
 ├─ [general_qa]     → retrieve → grade_docs ─┬─ (pass) → generate → END
 │                                  (retry ≤ 2) └─ (fail) → rewrite_query → retrieve
 ├─ [applicability]  → profile_extract → rule_engine → retrieve → generate → END
 └─ [field_check]    → validate_fields (tool) → generate → END
```

| Node | Type | Responsibility |
|---|---|---|
| `router` | LLM, structured output | Classify intent into `general_qa` / `applicability` / `field_check` |
| `retrieve` | Retriever | Hybrid search (pgvector + Postgres full-text), top-k with version filter |
| `grade_docs` | LLM, structured output | Score relevance of retrieved chunks; decide pass / rewrite |
| `rewrite_query` | LLM | Reformulate query for retry (Corrective RAG loop) |
| `profile_extract` | LLM, structured output | Extract `{annual_turnover, commencement_year, industry, transaction_types}` |
| `rule_engine` | **Pure Python** | Deterministic phase / date / threshold logic. Unit-tested. No LLM. |
| `validate_fields` | `@tool` | Check invoice JSON against mandatory field table |
| `generate` | LLM | Produce answer with inline citations `[Guideline v4.8 §2.3]` |

**Design principle:** The LLM explains and cites; it never decides compliance outcomes. This separation is the main talking point in interviews.

**Decisions from the Day 2 audit** (implement on Day 4):

1. **Source conflicts are resolved by precedence, not by ranking.** The FAQ
   (updated 5 May 2026) still states the old RM1,000,000 exemption threshold,
   while Guideline v4.8 (30 Aug 2026) §1.6.1(e) states RM3,000,000. On Day 2 the
   FAQ took 26 of 40 retrieval slots and the chain answered RM1 million — a
   correctly-cited, verifiable, *outdated* answer. Fix is twofold:
   - a **per-doc retrieval quota** in `retrieve`, so the FAQ cannot crowd out the
     Guideline (the FAQ is question-shaped and therefore matches question-shaped
     queries on both the vector and full-text side);
   - a **fixed precedence stated in the `generate` prompt**:
     **Guideline > Specific Guideline > FAQ**. Where sources disagree, the
     Guideline governs and the answer says so.

2. **Quantitative facts do not come from prose reading.** Relaxation periods,
   thresholds, phase dates and effective dates come from `rule_engine`, never
   from the model reading a chunk. Day 2 Q7 is the worked example: §16.1's lead
   sentence says "six (6)-month interim relaxation period ... of each
   implementation phase", but Table 16.1 row 4 grants phase 4 until
   **31 December 2027**. The model cited the correct section and stated a false
   number, because it read the prose and ignored the table in the same chunk.
   No citation check catches that; only moving the number out of prose does.

### 4.2 Tech Stack

| Layer | Choice | Reason |
|---|---|---|
| Orchestration | LangGraph + LangChain | Stateful graph, conditional edges, cycles |
| LLM | Azure AI Foundry (student credit) → fallback Groq free tier | Provider swap is one line in LangChain |
| Embeddings | `bge-small-en-v1.5`, run in-container (CPU) | Free forever, no API dependency |
| Vector store | pgvector on Azure Database for PostgreSQL Flexible (free tier) | Hybrid search with `tsvector`, no extra service |
| API | FastAPI | `/chat`, `/validate`, `/health` |
| Frontend | Single HTML page served by FastAPI | Simplest thing that works on Container Apps |
| Hosting | Azure Container Apps (free grant) | Scale-to-zero, HTTPS out of the box |
| CI/CD | GitHub Actions → Azure Container Registry → Container Apps | Test → build → push → deploy |
| Observability | LangSmith (free tier) | Trace every graph run |
| Evaluation | RAGAS | Faithfulness, answer relevancy, context precision |
| Container | Docker | Reproducible builds |

### 4.3 Abuse & Cost Protection
- Per-IP rate limit (`slowapi`): e.g. 10 requests / minute
- Global daily token budget; return a friendly "quota exhausted" message when exceeded
- Input length cap: 2,000 characters
- No API keys in the image; all secrets via Container Apps environment variables

---

## 5. Two-Week Plan

### Week 1 — Make it work

| Day | Deliverable |
|---|---|
| 1 | Download PDFs. Ingestion script: pdfplumber → section-based chunks → metadata → pgvector. Local Postgres via Docker Compose. |
| 2 | Minimal retrieve → generate chain. LangSmith wired up; inspect traces. |
| 3 | `rule_engine.py`: phase, implementation date, relaxation period, RM10k rule, new-business rules. 10+ unit tests. **No LLM work today.** |
| 4 | Build the LangGraph: router, three paths, grade/rewrite loop with retry cap. |
| 5 | `validate_fields` tool + `profile_extract` structured output. |
| 6 | FastAPI endpoints, single-page frontend, rate limiting, token budget. |
| 7 | Golden test set: 20 questions with expected section citations. Buffer. |

### Week 2 — Ship, measure, package

| Day | Deliverable |
|---|---|
| 8 | Dockerfile. Deploy to Azure Container Apps + Azure PostgreSQL. Secrets via env vars. Public URL live. |
| 9 | GitHub Actions pipeline: pytest → docker build → push ACR → deploy. |
| 10 | RAGAS evaluation on golden set. Record scores. |
| 11 | Fix what evaluation exposes (typically chunking and rewrite prompt). Re-run RAGAS. |
| 12 | README: architecture diagram, "why the rule engine is not an LLM", evaluation table, cost/abuse design, known limitations. |
| 13 | Get 2 real users (MSME owner / bookkeeper) to try it. Log feedback. |
| 14 | Buffer. Draft LinkedIn post. |

---

## 6. Evaluation

**Golden set:** 20 questions across the three intents, each with the expected guideline section(s).

**Metrics (RAGAS):**
- Faithfulness — answer is grounded in retrieved context
- Answer relevancy — answer addresses the question
- Context precision — retrieved chunks are the right ones

**Rule engine:** 100% unit-test coverage on phase/date logic; tests double as documentation of the rules.

Scores go in the README. Regressions are visible per commit.

---

## 7. Definition of Done

- [ ] Public HTTPS URL, works on mobile
- [ ] All three intents demoable end-to-end
- [ ] Every answer shows version + section citations
- [ ] Rule engine unit tests pass in CI
- [ ] RAGAS scores published in README
- [ ] Deployment is fully automated from `main`
- [ ] Zero cost incurred beyond Azure student credit
- [ ] README explains architecture and design decisions in under 5 minutes of reading

---

## 8. Risks

| Risk | Mitigation |
|---|---|
| Azure student credit runs out | Groq free tier as LLM fallback; embeddings are already local |
| PDF section parsing is messy | Day 1 is dedicated to ingestion; fall back to page-based chunks with section regex |
| Guideline updates again mid-project | Version is metadata; re-ingest is one script run |
| Public URL gets abused | Rate limit + daily budget + input cap from Day 6 |
| Scope creep | v1 scope is fixed above; extras go to a "Future work" section |

---

## 9. Interview Talking Points

1. Why compliance decisions live in a deterministic rule engine, not the LLM
2. Corrective RAG loop: how `grade_docs` + `rewrite_query` reduce hallucination
3. Version-aware citations and why metadata design matters more than the embedding model
4. Hybrid retrieval with Postgres only — no extra vector DB service
5. Measured quality: RAGAS numbers, not vibes
6. Production concerns on a free tier: rate limits, token budget, scale-to-zero, secrets
7. What broke during real-user testing and what changed

---

## 10. Future Work (not for v1)
- Malay and Chinese language support
- MCP server exposing `rule_engine` and `validate_fields` as tools for other agents
- Scheduled ingestion that detects new guideline versions automatically
- Streaming responses