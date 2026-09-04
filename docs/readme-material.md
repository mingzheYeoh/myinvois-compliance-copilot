# README material — staged, not yet written

Everything below is destined for the README, which is being written by hand
tomorrow. It lives here so the numbers and the reasoning are captured while they
are still fresh and verifiable, and so the README session is about prose rather
than about re-deriving figures.

Every number here is reproducible from `data/eval/` and from the commits named.

---

## For the "Verifiable citations" section

**The claim and the gap.** The project's claim is that an answer can be checked.
Before this change, checking meant opening a 200-page PDF and finding §1.6.1(e)
by hand — which nobody does, so in practice the citations were decoration.

**What it does now.** Every citation, inline in the answer and in the list beneath
it, is a button. Clicking one expands the stored source chunk in place, with its
document, version, section and page. Read-only: `GET /chunk?ref=<citation>` re-reads
a row the ingest already wrote, so it costs no LLM call and works after the daily
quota is spent — which is exactly when someone is left holding an answer they want
to check.

Two design points worth stating:

- The ref is parsed **server-side**. The browser sends the bracketed citation text
  verbatim and never learns what a citation looks like, so there is no second copy
  of the regex that `src/app/rag/citations.py` exists to prevent. Page ranges the
  model writes on its own ("p44-p50") normalise on the way in for free.
- The panel shows the section and page of the row that was **actually stored**, not
  the ones the answer wrote. A citation pointing at the wrong page shows the right
  one, so the mismatch is visible rather than hidden.

**Known gap:** `§Appendix 1` citations do not resolve. The field validator reads
its own params table, and Appendix 1 was never ingested as chunks, so field-check
answers cite something the corpus does not hold. Belongs in Known limitations.

---

## For the "Feedback" section

There is a "Report a problem" link and there is no rating widget, and the absence
is the deliberate part.

A user asking whether they must issue an e-Invoice cannot judge whether the answer
is correct — that is *why* they are asking. A satisfaction or accuracy rating would
therefore measure how plausible an answer *feels*, which is precisely the failure
mode Days 2 to 4 were spent removing: a fluent, confident, wrong answer would score
well, and a correct "the guidelines do not address this" would score badly.

So the only feedback channel is one click that says "this is wrong", which resolves
to a LangSmith trace, which becomes a golden case. One click, no free text, no PII:
nothing to moderate and nothing to leak.

---

## Finding: the two instruments disagree

This is a finding, not a footnote.

Day 11 changed retrieval in two ways: guideline tables are chunked as rows rather
than prose blocks, and sections the rule engine cites are pinned into the context
by metadata instead of having to win a similarity contest.

**The golden set improved.** 20/20 → 21/21, and citations became more precise: a
reader clicking through to §1.6.1(e) now lands on the row that actually carries
RM3,000,000.

**The RAGAS retrieval metrics moved down.**

| Metric | Day 10 | After | Δ |
|---|---|---|---|
| Faithfulness | 0.922 | 0.950 | +0.028 |
| Answer relevancy | 0.806 | 0.803 | −0.003 |
| Context precision (RAG, n=2) | 0.750 | 0.750 | 0.000 |
| Context recall (RAG, n=2) | 1.000 | 1.000 | 0.000 |
| Context precision (rule-engine, n=13) | 0.882 | **0.700** | **−0.182** |
| Context recall (rule-engine, n=13) | 0.551 | **0.436** | **−0.115** |

Judge: the same Azure `chat-small` deployment as the Day 10 baseline, so the
comparison holds the judge fixed. Baseline preserved at
`data/eval/ragas-2026-09-04-day10-baseline.json`.

State plainly that the Day 10 prediction was wrong: row-level chunking was ranked
the top fix with an expected recall of 0.551 → 0.85+. It went to 0.436.

**Two candidate mechanisms.**

1. **Fragmentation (recall).** q07 and q11 both fell from 1.00 to 0.00 while their
   context grew from 6 chunks to 8. A reference sentence that used to sit inside one
   prose block now spans several rows, so no single retrieved chunk clearly entails
   it, even though every word of it was retrieved. Ruled out as a volume effect:
   context grew 6,876 → 9,108 characters (+32%), so this is not "smaller chunks
   carry less".

2. **Ordering (precision) — UNTESTED HYPOTHESIS.** Context precision here is mean
   average precision, which is rank-sensitive. Pinned sections are *appended* after
   the hybrid results, at ranks 7–8. A relevant chunk at rank 7 contributes k/7 to
   MAP, dragging the mean down even when it is the most authoritative chunk present.
   Label this as a hypothesis, not a finding.

   **The experiment that settles it:** re-score the same run with pinned chunks
   ordered *first* instead of appended, changing nothing else. If precision returns
   towards 0.882, the drop was an artifact of rank position rather than a loss of
   retrieval quality. Not yet run — it costs a full scoring pass.

**Do not pick a winner between the instruments.** The honest position is that they
measure different things: the golden set asks "did the answer contain the right
facts and citations", RAGAS asks "is each retrieved chunk relevant, in order, and
does it entail the reference". A change can genuinely improve one and depress the
other. What we have is a named experiment that resolves which is happening here.

---

## Pending: the second judge

The chat-small pass is complete. The **Groq `gpt-oss-120b` cross-judge comparison is
pending**, and the README should say so with the reason rather than omit it: the
chat-small scoring pass cost **149,601 tokens**, and after steps 1 and 2 only
**167,121** remained of the day's ceiling. A second pass needing ~150k against a
~17k margin would have produced a half-run and no comparison, so it was stopped
rather than rushed.

This leaves the self-grading caveat open: the same model family that produces the
answers also grades them. Say that the cross-judge run is the intended answer to it
and that it is scheduled, not skipped.

---

## For "Known limitations": the shared-counter incident

Record it as it happened, not as a hypothetical.

The daily token budget is one counter in Postgres, shared by the development loop
and the deployed app. On Day 11 the ceiling was raised to 1,200,000 for one day's
evaluation work, with the override deliberately confined to the local `.env` — the
deploy script unsets `DAILY_TOKEN_BUDGET`, so production stayed on the 150,000
default in code, and `/health` confirmed `"limit":150000`.

That part worked. The part that did not: **the counter is shared, so development
spend drained production's allowance.** After the evaluation runs, live `/health`
read `used: 1,032,879, remaining: 0` against a 150,000 limit, and the deployed app
refused new questions for the rest of the day. No user had asked it anything. The
outage was real; it was invisible only because nobody was watching.

The fix is known and named in Future work below. The point of recording it is that
the isolation was tested and held at the *configuration* layer while failing at the
*data* layer — a budget that is per-environment in its value but global in its
counter is not per-environment at all.

Also state, in the same section: **production runs on a 150,000-token daily
ceiling; development ran at a higher one (1,200,000 for a single day of evaluation
work).** They differ because they buy different things — production caps what a day
of public traffic can cost, while an evaluation pass scores 21 cases across four
metrics and two judges in one sitting. It is an honest detail, not one to hide.

---

## For "Future work"

- **A per-environment or per-key budget ceiling.** One counter serving both the dev
  loop and production is why any of this blocked. The obvious shape is a counter
  keyed by environment (or by API key), so exhausting a development allowance cannot
  take the deployed app down. Naming it beats pretending the current design is
  finished.
- **Run the ordering experiment** above and settle the precision question.
- **Serve `§Appendix 1` citations.** They come from the validator's params table
  rather than the chunk corpus, so today they are the one citation class that cannot
  be expanded.
- **Day 10 fix 3, and how it was framed.** It was proposed on the premise that
  RM3,000,000 was absent from the corpus. It was not — it was at §1.6.1(e), p15 the
  whole time, and the defect was that retrieval ranked it below the top 20. Worth
  keeping because the wrong framing (missing data) and the right one (bad ranking)
  lead to completely different fixes: re-ingest versus pinned retrieval.

---

## Day 11 numbers worth quoting

**Token cost per class** (21 cases, 112,923 tokens total). The question was whether
the Day 11 increase came from abstention cases running the corrective-RAG retry
loop, or from structured output and a longer prompt taxing every case.

| Class | n | mean tokens | Day 10 | Δ | retried |
|---|---|---|---|---|---|
| rag | 2 | 4,718 | 3,614 | +1,104 | 0/2 |
| deterministic | 13 | 5,514 | 4,287 | +1,227 | 0/13 |
| field_check | 2 | 3,080 | 2,802 | +279 | 0/2 |
| clarifying | 1 | 697 | 697 | +0 | 0/1 |
| abstention | 3 | 8,316 | 8,756 | −440 | 3/3 |

It is a **flat tax**, not retries. The only cases that retry are the abstentions,
and they got slightly *cheaper*. The whole increase sits on the 15 cases that
answer, about +1,150 each (~20–25%), from the structured output schema, the longer
prompt and the two appended pinned chunks. Retries remain expensive per case
(8,316 vs 4,888 for a first-time answer) but their count did not move.
