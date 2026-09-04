"""Run the Day 2 questions through the chain and print answer + citations + trace.

    uv run python scripts/ask.py                # all 8 Day 2 questions
    uv run python scripts/ask.py "my question"  # one ad-hoc question
    uv run python scripts/ask.py --retrieval-only   # no LLM key needed
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

# Windows consoles default to cp1252, which cannot encode "§" or the model's
# narrow no-break spaces.
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

from app.rag.chain import SHORT, build_chain  # noqa: E402
from app.rag.citations import CITE, normalise_pages  # noqa: E402
from app.rag.retriever import search  # noqa: E402

QUESTIONS = [
    "What is the exemption threshold for e-Invoice implementation?",
    "My business started in 2024 with RM2M turnover. When must I implement e-Invoice?",
    "Can I issue a consolidated e-Invoice for a RM12,000 sale?",
    "What is the Special Voluntary Disclosure for e-Invoice?",
    "When is a self-billed e-Invoice required?",
    "What are the mandatory fields in an e-Invoice?",
    "How long is the relaxation period for Phase 4?",
    "What is the penalty for not issuing an e-Invoice?",
]



# Models emit narrow / non-breaking spaces inside citations ("Table 3.6"),
# which is a rendering choice, not a different section. Fold them before
# comparing or the auditor reports fabrication that did not happen.
SPACES = str.maketrans(dict.fromkeys(
    # narrow/no-break spaces and the typographic hyphens models like to
    # substitute inside citations: cosmetic, not a different section.
    [" ", " ", " ", " "], " ")
    | dict.fromkeys(
    ["‐", "‑", "‒", "–", "—", "−"], "-"))


def norm(text: str) -> str:
    """Fold everything cosmetic before a citation is compared or extracted.

    Page ranges are folded here rather than at each call site, so eval.py and
    ask_graph.py -- which both parse through norm() -- get the fix for free.
    """
    return normalise_pages(text.translate(SPACES))


def cited(answer: str) -> set[tuple[str, str, str, str]]:
    return {m.groups() for m in CITE.finditer(norm(answer))}


def retrieved(hits) -> set[tuple[str, str, str, str]]:
    return {(SHORT.get(h.doc, h.doc), h.version, h.section, str(h.page)) for h in hits}


def show(i: int, q: str, answer: str, hits, url: str | None) -> None:
    print(f"\n{'=' * 78}\nQ{i}. {q}\n{'=' * 78}")
    print(answer.strip())
    print("\n  retrieved:")
    for h in hits:
        print(f"    [{SHORT.get(h.doc, h.doc)} v{h.version} §{h.section}, p{h.page}]"
              f"  score={h.score:.4f} vec={h.vec_rank} fts={h.fts_rank}")
    ghosts = cited(answer) - retrieved(hits)
    if ghosts:
        print("  !! CITED BUT NOT RETRIEVED (fabricated citation):")
        for g in sorted(ghosts):
            print(f"    [{g[0]} v{g[1]} §{g[2]}, p{g[3]}]")
    elif cited(answer):
        print("  citations all trace to retrieved chunks")
    if url:
        print(f"  trace: {url}")


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    questions = args or QUESTIONS

    if "--retrieval-only" in sys.argv:
        for i, q in enumerate(questions, 1):
            hits = search(q, k=5)
            print(f"\n{'=' * 78}\nQ{i}. {q}\n{'=' * 78}")
            for h in hits:
                print(f"  [{SHORT.get(h.doc, h.doc)} v{h.version} §{h.section}, p{h.page}]"
                      f"  score={h.score:.4f} vec={h.vec_rank} fts={h.fts_rank}")
                print(f"      {h.content[:160].replace(chr(10), ' ')}")
        return 0

    from langchain_core.tracers.context import tracing_v2_enabled

    chain = build_chain(k=5)
    for i, q in enumerate(questions, 1):
        with tracing_v2_enabled() as cb:
            out = chain.invoke({"question": q})
            try:
                url = cb.get_run_url()
            except Exception as exc:  # tracing off or key missing
                url = f"(no trace: {exc})"
        show(i, q, out["answer"], out["hits"], url)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
