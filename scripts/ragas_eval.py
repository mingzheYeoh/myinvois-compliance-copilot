"""RAGAS-style evaluation of the 20-case golden set.

    uv run python scripts/ragas_eval.py --calibrate   # one case, measure cost
    uv run python scripts/ragas_eval.py               # full run
    uv run python scripts/ragas_eval.py --metrics-only # re-score the cached run

The four metric DEFINITIONS are RAGAS's (faithfulness, answer relevancy,
context precision, context recall); the implementation is ours. The `ragas`
package cannot be installed here: every published version hard-imports
langchain_community.chat_models.vertexai, which langchain-community 0.4 removed
on its way to being sunset, and the versions that still ship it pin
langchain-core<1.0 while this app runs on 1.6.1 with LangGraph 1.2. Downgrading
the application in order to measure it is not a trade worth making. Reimplementing
also buys the thing the library would have fought us on: every judge call goes
through get_llm(), so it is metered against the same daily budget and carries the
same retry and pacing behaviour as the app.

NOT EVERY CASE IS A RAG CASE. Faithfulness against retrieved context is the
wrong instrument for an answer the deterministic rule engine produced from
params.json, and answer relevancy is the wrong instrument for a correct
abstention. Cases are classified from the run's own state -- never a hardcoded
id list, which would silently rot as the graph changes -- and each metric is
averaged only over the classes it applies to. Everything excluded is named in
the output with its reason.
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
import time
from datetime import date
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

from eval import (  # noqa: E402
    DailyQuotaGone,
    _invoke,
    charge,
    classify,
    load_cases,
    pace,
)
from langchain_core.callbacks import UsageMetadataCallbackHandler  # noqa: E402
from pydantic import BaseModel, Field  # noqa: E402

from app.graph.graph import (  # noqa: E402
    _determination_block,
    _field_block,
    build_graph,
    structured,
)
from app.rag.chain import format_context, get_llm, llm_name  # noqa: E402
from app.rag.retriever import _embedder  # noqa: E402

OUT_DIR = ROOT / "data" / "eval"
RUN_CACHE = OUT_DIR / "run-cache.json"
REFERENCES = OUT_DIR / "references.json"


# --- classification ---------------------------------------------------------
#
# What produced the answer decides which metrics mean anything. Derived from the
# graph's final state so it stays true as the graph changes.
#
#   rag            retrieved chunks were the source        all four metrics
#   deterministic  the rule engine's cited facts were      faithfulness +
#                  the source (params.json, not context)   relevancy only
#   field_check    Appendix 1 table, never retrieves       faithfulness +
#                                                          relevancy only
#   clarifying     asked for a missing input instead of    none
#                  answering; correct behaviour, but
#                  there is no claim set to ground
#   abstention     "Not covered in the guidelines."        none
#
# The golden set already checks that clarifying and abstention cases are CORRECT.
# RAGAS would score them near zero for doing exactly the right thing, so they are
# excluded here rather than dragging an average down for good behaviour.
CLASSES = {
    "rag": {"faithfulness", "answer_relevancy", "context_precision", "context_recall"},
    "deterministic": {"faithfulness", "answer_relevancy"},
    "field_check": {"faithfulness", "answer_relevancy"},
    "clarifying": set(),
    "abstention": set(),
}
WHY_EXCLUDED = {
    "clarifying": "asked for a missing input rather than answering; no claim set to ground",
    "abstention": "correct refusal; no claims, no ground truth to recall",
}
WHY_PARTIAL = {
    "deterministic": "answer came from the rule engine (params.json), not from "
                     "retrieved context, so retrieval metrics do not describe it",
    "field_check": "never retrieves; grounded in the Appendix 1 field table",
}


def grounding(state: dict[str, Any], contexts: list[str]) -> list[str]:
    """Exactly the material generate() put in front of the model.

    Built by calling the graph's own block builders rather than re-deriving them.
    The first version of this walked determination["reasons"], and scored q01's
    faithfulness at 0.00: every claim was judged unsupported because RM3,000,000
    lives in determination["facts"] -- reference_facts() out of params.json, which
    _determination_block injects into the prompt and "reasons" never contains. The
    answer was right and the instrument was wrong. Reconstructing the prompt by
    hand is how a measurement quietly stops measuring the thing it names."""
    if state.get("intent") == "field_check":
        block = _field_block(state.get("field_report") or {})
    else:
        block, _ = _determination_block(state.get("determination") or {})
    return ([block] if block.strip() else []) + contexts


# --- judge schemas ----------------------------------------------------------
class Statements(BaseModel):
    statements: list[str] = Field(description="Atomic factual claims, one per item")


class Verdict(BaseModel):
    statement: str
    supported: bool = Field(description="True if the context entails this statement")


class Verdicts(BaseModel):
    verdicts: list[Verdict]


class Questions(BaseModel):
    questions: list[str] = Field(description="Questions this answer fully answers")
    noncommittal: bool = Field(description="True if the answer evades or refuses")


class Relevance(BaseModel):
    relevant: list[bool] = Field(description="One boolean per numbered context, in order")


class Reference(BaseModel):
    reference: str = Field(description="The correct answer, guideline facts only")


def judge(schema, prompt: str):
    return structured(schema).invoke(prompt)


# --- metrics ----------------------------------------------------------------
def faithfulness(answer: str, contexts: list[str]) -> tuple[float | None, dict]:
    """Share of the answer's atomic claims that the given material entails."""
    st = judge(Statements, "Break the ANSWER into atomic factual statements. Each must "
               "stand alone and assert exactly one fact. Ignore hedges and pleasantries.\n\n"
               f"ANSWER:\n{answer}")
    if not st.statements:
        return None, {"reason": "no factual statements in the answer"}
    ctx = "\n\n".join(contexts)
    vs = judge(Verdicts, "For each STATEMENT decide whether the CONTEXT entails it. "
               "Supported means the context states it or it follows directly. General "
               "knowledge is not support. Return one verdict per statement, in order.\n\n"
               f"CONTEXT:\n{ctx}\n\nSTATEMENTS:\n" +
               "\n".join(f"{i}. {s}" for i, s in enumerate(st.statements, 1)))
    if not vs.verdicts:
        return None, {"reason": "judge returned no verdicts"}
    ok = [v.supported for v in vs.verdicts]
    return sum(ok) / len(ok), {"statements": len(ok), "unsupported":
                               [v.statement for v in vs.verdicts if not v.supported]}


def answer_relevancy(question: str, answer: str) -> tuple[float | None, dict]:
    """Cosine similarity between the real question and questions reverse-generated
    from the answer. Embeddings are the app's own bge-small, run locally: it costs
    no quota and keeps the measurement in the same vector space as retrieval."""
    q = judge(Questions, "Generate 3 questions that the ANSWER below completely answers. "
              "Set noncommittal if the answer evades, refuses or says it does not know.\n\n"
              f"ANSWER:\n{answer}")
    if q.noncommittal or not q.questions:
        return 0.0, {"noncommittal": True}
    emb = _embedder().encode([question] + q.questions, normalize_embeddings=True)
    sims = [float(emb[0] @ v) for v in emb[1:]]
    return sum(sims) / len(sims), {"generated": q.questions,
                                   "sims": [round(s, 3) for s in sims]}


def context_precision(question: str, reference: str,
                      contexts: list[str]) -> tuple[float | None, dict]:
    """Mean average precision: are the useful chunks ranked at the top?"""
    if not contexts:
        return None, {"reason": "nothing retrieved"}
    rel = judge(Relevance, "For each numbered CONTEXT entry decide whether it was useful "
                "in arriving at the REFERENCE answer for the QUESTION. Return one boolean "
                f"per entry, in order.\n\nQUESTION: {question}\n\nREFERENCE: {reference}\n\n"
                "CONTEXT:\n" + "\n\n".join(f"[{i}] {c}" for i, c in enumerate(contexts, 1)))
    flags = (rel.relevant + [False] * len(contexts))[:len(contexts)]
    if not any(flags):
        return 0.0, {"relevant_ranks": []}
    hits = 0
    total = 0.0
    for i, f in enumerate(flags, 1):
        if f:
            hits += 1
            total += hits / i
    return total / hits, {"relevant_ranks": [i for i, f in enumerate(flags, 1) if f]}


SENTENCE = re.compile(r"(?<=[.;])\s+(?=[A-Z§])")


def context_recall(reference: str, contexts: list[str]) -> tuple[float | None, dict]:
    """Share of the reference answer's sentences that the retrieved context supports."""
    if not contexts:
        return None, {"reason": "nothing retrieved"}
    sents = [s.strip() for s in SENTENCE.split(reference) if len(s.strip()) > 15]
    if not sents:
        return None, {"reason": "reference too short to split"}
    vs = judge(Verdicts, "For each STATEMENT from the reference answer, decide whether the "
               "CONTEXT supports it. Return one verdict per statement, in order.\n\n"
               f"CONTEXT:\n{chr(10).join(contexts)}\n\nSTATEMENTS:\n" +
               "\n".join(f"{i}. {s}" for i, s in enumerate(sents, 1)))
    if not vs.verdicts:
        return None, {"reason": "judge returned no verdicts"}
    ok = [v.supported for v in vs.verdicts]
    return sum(ok) / len(ok), {"sentences": len(ok)}


# --- reference answers ------------------------------------------------------
def build_references(cases: list[dict]) -> dict[str, str]:
    """The golden set's `why` field mixes the guideline ground truth with notes to
    the reviewer ("this is the Day 2 Q1 failure"). Context recall would count that
    commentary as unsupported and understate every score, so the guideline half is
    extracted once and cached. The cache is committed and meant to be read: if a
    reference is wrong, fix the file rather than trusting a one-off LLM pass."""
    # Merged, not all-or-nothing: a --calibrate run writes one entry, and the full
    # run afterwards must fill in the other nineteen rather than find a file that
    # "exists" and silently score them against an empty reference.
    out = json.loads(REFERENCES.read_text(encoding="utf-8")) if REFERENCES.exists() else {}
    for c in cases:
        if c["id"] in out:
            continue
        r = judge(Reference, "Rewrite the NOTE as a direct answer to the QUESTION, keeping "
                  "only what the guidelines state (sections, figures, dates, conditions). "
                  "Drop all commentary about test suites, regressions or past failures.\n\n"
                  f"QUESTION: {c['question']}\n\nNOTE: {c.get('why', '')}")
        out[c["id"]] = r.reference.strip()
        print(f"  reference {c['id']}: {out[c['id']][:70]}...", flush=True)
        REFERENCES.parent.mkdir(parents=True, exist_ok=True)
        REFERENCES.write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
    return out


# --- run phase --------------------------------------------------------------
def run_cases(cases: list[dict]) -> dict[str, dict]:
    """Answer every case once and cache question/answer/contexts/state. Cached so a
    metrics retry never pays for the graph again -- which is most of the headroom
    a re-score needs."""
    graph = build_graph()
    rows: dict[str, dict] = {}
    if RUN_CACHE.exists():
        rows = json.loads(RUN_CACHE.read_text(encoding="utf-8"))
    for c in cases:
        if c["id"] in rows:
            print(f"  {c['id']} cached", flush=True)
            continue
        usage = UsageMetadataCallbackHandler()
        out: dict[str, Any] = {}
        started = time.perf_counter()
        seen = 0
        for turn in c["turns"]:
            pace()
            out = _invoke(graph, turn, f"ragas-{c['id']}", usage)
            # The handler accumulates across turns; the bucket wants the delta.
            total = sum(u.get("total_tokens", 0) for u in usage.usage_metadata.values())
            charge(total - seen)
            seen = total
        hits = out.get("hits") or []
        rows[c["id"]] = {
            "question": c["turns"][-1],
            "answer": out.get("answer", ""),
            "contexts": [format_context([h]) for h in hits],
            "intent": out.get("intent", "-"),
            "determination": out.get("determination") or {},
            "field_report": out.get("field_report") or {},
            "secs": round(time.perf_counter() - started, 2),
            "tokens": sum(u.get("total_tokens", 0) for u in usage.usage_metadata.values()),
        }
        print(f"  {c['id']} {rows[c['id']]['intent']:13} "
              f"{len(hits)} chunks  {rows[c['id']]['tokens']} tok", flush=True)
        RUN_CACHE.parent.mkdir(parents=True, exist_ok=True)
        RUN_CACHE.write_text(
            json.dumps(rows, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    return rows


def score_case(cid: str, row: dict, reference: str) -> dict:
    kind = classify(row, row["answer"])
    applies = CLASSES[kind]
    ctx = row["contexts"]
    res: dict[str, Any] = {"id": cid, "class": kind, "intent": row["intent"],
                           "chunks": len(ctx), "scores": {}, "detail": {}}
    if "faithfulness" in applies:
        s, d = faithfulness(row["answer"], grounding(row, ctx))
        res["scores"]["faithfulness"], res["detail"]["faithfulness"] = s, d
    if "answer_relevancy" in applies:
        s, d = answer_relevancy(row["question"], row["answer"])
        res["scores"]["answer_relevancy"], res["detail"]["answer_relevancy"] = s, d
    # Retrieval metrics are computed wherever there IS retrieval, but only the rag
    # class feeds the headline average; deterministic ones are reported apart.
    if ctx and kind in ("rag", "deterministic"):
        s, d = context_precision(row["question"], reference, ctx)
        res["scores"]["context_precision"], res["detail"]["context_precision"] = s, d
        s, d = context_recall(reference, ctx)
        res["scores"]["context_recall"], res["detail"]["context_recall"] = s, d
    return res


METRICS = ["faithfulness", "answer_relevancy", "context_precision", "context_recall"]


def aggregate(rows: list[dict]) -> dict:
    """Headline averages count only the cases a metric is valid for."""
    head = {}
    for m in METRICS:
        vals = [r["scores"][m] for r in rows
                if m in CLASSES[r["class"]] and r["scores"].get(m) is not None]
        head[m] = {"score": round(statistics.mean(vals), 3) if vals else None,
                   "n": len(vals)}
    aside = {}
    for m in ("context_precision", "context_recall"):
        vals = [r["scores"][m] for r in rows
                if r["class"] == "deterministic" and r["scores"].get(m) is not None]
        aside[m] = {"score": round(statistics.mean(vals), 3) if vals else None,
                    "n": len(vals)}
    return {"headline": head, "retrieval_on_deterministic": aside}


def markdown(report: dict) -> str:
    h, aside, counts = report["headline"], report["retrieval_on_deterministic"], report["classes"]

    def row(name, cell):
        s = cell["score"]
        return (f"| {name} | {'n/a' if s is None else f'{s:.3f}'} | n={cell['n']} |")

    lines = ["| Metric | Score | Cases |", "|---|---|---|"]
    for m in ("faithfulness", "answer_relevancy"):
        lines.append(row(m.replace("_", " ").title(), h[m]))
    # Reported apart, and n is shown on every row, because only two of twenty
    # cases are standard RAG: a headline "context precision 0.75" over n=2 would
    # read as a property of the system rather than of two questions.
    for m in ("context_precision", "context_recall"):
        lines.append(row(m.replace("_", " ").title() + " (rag only)", h[m]))
        lines.append(row(m.replace("_", " ").title() + " (rule-engine cases)", aside[m]))
    lines += ["", f"Judge: `{report['judge']}`, the same Azure deployment the app answers with.",
              "", "Case mix: " + ", ".join(f"{n}× {k}" for k, n in counts.items() if n)
              + f". {report['excluded']} excluded from every metric ("
              + ", ".join(f"{k}: {WHY_EXCLUDED[k]}" for k, n in counts.items()
                          if n and not CLASSES[k]) + ").",
              "", "Retrieval metrics are shown separately for rule-engine cases because the "
              "answer there came from params.json, not from the retrieved chunks -- averaging "
              "the two together would describe neither."]
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--calibrate", action="store_true",
                    help="one case only, then print measured cost and stop")
    ap.add_argument("--subset", help="comma-separated case ids")
    ap.add_argument("--metrics-only", action="store_true",
                    help="re-score the cached run without re-answering")
    args = ap.parse_args()

    cases = load_cases()
    if args.subset:
        want = set(args.subset.split(","))
        cases = [c for c in cases if c["id"] in want]
    if args.calibrate:
        cases = cases[:1]

    from app import budget
    start_used = budget.used()
    llm = get_llm()
    print(f"judge={llm_name(llm)}  cases={len(cases)}  "
          f"budget={start_used}/{budget.limit()}", flush=True)

    print("\nanswering:", flush=True)
    rows = run_cases(cases) if not args.metrics_only else json.loads(
        RUN_CACHE.read_text(encoding="utf-8"))
    after_run = budget.used()

    print("\nreferences:", flush=True)
    refs = build_references(cases)

    print("\nscoring:", flush=True)
    scored = []
    for c in cases:
        pace()
        r = score_case(c["id"], rows[c["id"]], refs.get(c["id"], c.get("why", "")))
        scored.append(r)
        got = "  ".join(f"{k[:4]} {v:.2f}" for k, v in r["scores"].items()
                        if v is not None) or "excluded"
        print(f"  {r['id']} [{r['class']}] {got}", flush=True)

    used = budget.used()
    report = {
        "date": date.today().isoformat(),
        "judge": llm_name(llm),
        "note": "RAGAS metric definitions, own implementation; see the module docstring.",
        "cases": len(cases),
        "scored": sum(1 for r in scored if r["scores"]),
        "excluded": sum(1 for r in scored if not CLASSES[r["class"]]),
        "classes": {k: sum(1 for r in scored if r["class"] == k) for k in CLASSES},
        "applicability": {"scored_by_class": {k: sorted(v) for k, v in CLASSES.items()},
                          "excluded_reasons": {**WHY_EXCLUDED, **WHY_PARTIAL}},
        "tokens": {"answering": after_run - start_used, "scoring": used - after_run,
                   "total": used - start_used},
        **aggregate(scored),
        "per_case": scored,
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    stem = OUT_DIR / f"ragas-{report['date']}"
    stem.with_suffix(".json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    md = markdown(report)
    stem.with_suffix(".md").write_text(md + "\n", encoding="utf-8")

    print("\n" + md)
    print(f"\ntokens: answering {report['tokens']['answering']}, "
          f"scoring {report['tokens']['scoring']}, total {report['tokens']['total']}")
    if args.calibrate:
        n = len(load_cases())
        per = report["tokens"]["total"]
        print(f"\nCALIBRATION: {per} tokens for 1 case -> ~{per * n:,} for {n}; "
              f"a metrics-only retry ~{report['tokens']['scoring'] * n:,}.")
    print(f"wrote {stem.with_suffix('.json').name} and {stem.with_suffix('.md').name}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except DailyQuotaGone as exc:
        print(f"\nstopped: {exc}", file=sys.stderr)
        sys.exit(2)
