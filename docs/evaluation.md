# Evaluation: method, and one unresolved finding

Scores and the summary table are in the [README](../README.md#evaluation-ragas). This page
carries the method, the case-mix reasoning, and the finding that has not been settled.

## Method

Metric definitions are RAGAS's; the implementation is in-repo because every published
`ragas` release pins `langchain-core<1.0` and this app runs on 1.6.1.

Judge: `chat-small`, the same Azure deployment the app answers with, in both runs. The
scored set is identical across the two — the same 2 rag, 13 deterministic and 2
field_check cases, by id. Day 11 added one case (q21, an abstention); abstentions are
excluded from every metric in both runs, so the extra case moves the excluded count and
nothing that was measured.

Excluded from every metric: **clarifying** (asked for a missing input rather than
answering, so there is no claim set to ground) and **abstention** (correct refusal, so
there are no claims and no ground truth to recall). Retrieval metrics are reported
separately for rule-engine cases because the answer there came from `params.json`, not
from the retrieved chunks — averaging the two together would describe neither.

Re-run with `uv run python scripts/ragas_eval.py`; results land in
`data/eval/ragas-<date>-<judge>.json` so runs stay comparable.

## The two instruments disagree

Day 11 chunked guideline tables one row per chunk and began fetching cited sections by
metadata instead of by similarity. The golden set went **20/20 → 21/21**, and citations
got more precise: `§1.6.1(e)` now resolves to the row that actually carries RM3,000,000
rather than to a 38-character heading. Over that same change, RAGAS context recall on the
rule-engine cases went **0.551 → 0.436** and precision **0.882 → 0.700**.

Day 10 ranked row-level chunking as the top fix and predicted recall would reach 0.85+.
It did not. Both numbers are measured, not estimated.

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

## Pending: a second judge

The same model family that writes the answers also grades them. The intended answer to
that is a cross-judge run on Groq's `gpt-oss-120b`. **It is not done.** The chat-small pass
alone cost 149,601 scoring tokens against a 150,000 daily ceiling, and a half-run would
have produced no comparison at all. A full pass is ~263,000 tokens (113,363 answering +
149,601 scoring).

## Cost of a run

The Day 11 golden set cost 112,923 tokens over 21 cases. The increase over Day 10 is a
flat tax on every case that answers, not a retry effect: the abstention cases, which are
the only ones that run the corrective-RAG loop, got slightly *cheaper*, while the 15 cases
that answer each gained about 1,150 tokens from the structured-output schema, the longer
prompt and the two appended pinned chunks.

| Class | n | mean tokens | Day 10 | Δ | retried |
|---|---|---|---|---|---|
| rag | 2 | 4,718 | 3,614 | +1,104 | 0/2 |
| deterministic | 13 | 5,514 | 4,287 | +1,227 | 0/13 |
| field_check | 2 | 3,080 | 2,802 | +279 | 0/2 |
| clarifying | 1 | 697 | 697 | +0 | 0/1 |
| abstention | 3 | 8,316 | 8,756 | −440 | 3/3 |
