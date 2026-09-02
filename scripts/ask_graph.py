"""Run the Day 2 question set through the Day 4 graph.

    uv run python scripts/ask_graph.py           # the 8 questions
    uv run python scripts/ask_graph.py --twoturn # Q2 as a two-turn conversation
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from dotenv import load_dotenv  # noqa: E402

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

from ask import CITE, QUESTIONS, norm  # noqa: E402

from app.graph.graph import build_graph  # noqa: E402
from app.rag.chain import SHORT  # noqa: E402


def audit(answer: str, hits, determination=None) -> str:
    """Legitimate citations come from two places: a retrieved chunk, or a
    section the rule engine itself cited. Only the rest is fabrication."""
    given = {(SHORT.get(h.doc, h.doc), h.version, h.section, str(h.page)) for h in hits}
    d = determination or {}
    for r in [*d.get("reasons", []), *d.get("facts", [])]:
        m = CITE.search(norm("[" + r["section"] + "]"))
        if m:
            given.add(m.groups())
    used = {m.groups() for m in CITE.finditer(norm(answer))}
    ghosts = used - given
    if ghosts:
        return "FABRICATED: " + ", ".join(f"[{g[0]} v{g[1]} §{g[2]}, p{g[3]}]" for g in ghosts)
    return f"all {len(used)} citation(s) trace to retrieved chunks" if used else "no citations"


def run_one(graph, question: str, thread: str) -> dict:
    return graph.invoke({"question": question}, config={"configurable": {"thread_id": thread}})


def main() -> int:
    import os

    from app.budget import limit, used
    from app.rag.chain import get_llm

    print(f"models: generate={get_llm().model_name}  structured={get_llm(small=True).model_name}"
          f"  budget={used()}/{limit()}  DAILY_TOKEN_BUDGET={os.getenv('DAILY_TOKEN_BUDGET')}")
    graph = build_graph()

    if "--twoturn" in sys.argv:
        thread = "q2-multiturn"
        turns = [
            "My business has RM2M turnover. When must I implement e-Invoice?",
            "It started in 2024, and no, I have no corporate shareholder, holding "
            "company or related company of any size.",
        ]
        for n, q in enumerate(turns, 1):
            out = run_one(graph, q, thread)
            print(f"\n{'=' * 78}\nTURN {n}: {q}\n{'=' * 78}")
            print(f"  route      : {out.get('intent')}")
            print(f"  profile    : {out.get('profile')}")
            d = out.get("determination", {})
            print(f"  missing    : {d.get('missing')}")
            print(f"  required   : {d.get('required')}  date={d.get('implementation_date')}")
            print(f"\n{out['answer'].strip()}")
        return 0

    for i, q in enumerate(QUESTIONS, 1):
        out = run_one(graph, q, f"day4-q{i}")
        hits = out.get("hits", [])
        print(f"\n{'=' * 78}\nQ{i}. {q}\n{'=' * 78}")
        print(f"  route: {out.get('intent')}   retries: {out.get('retry_count', 0)}"
              f"   grade: {out.get('grade', '-')}")
        d = out.get("determination", {})
        if "required" in d:
            print(f"  rule engine: required={d.get('required')} "
                  f"date={d.get('implementation_date')} "
                  f"relaxed_until={d.get('relaxation_until')} "
                  f"consolidated_allowed={d.get('consolidated_allowed')} "
                  f"missing={d.get('missing')}")
        print(f"\n{out['answer'].strip()}\n")
        print("  retrieved:")
        for h in hits:
            print(f"    [{SHORT.get(h.doc, h.doc)} v{h.version} §{h.section}, p{h.page}]"
                  f"  score={h.score:.4f}")
        print(f"  audit: {audit(out['answer'], hits, d)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
