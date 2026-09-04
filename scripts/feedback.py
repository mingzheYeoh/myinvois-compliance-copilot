"""List recent "this answer is wrong" reports, each with its LangSmith trace.

    uv run python scripts/feedback.py            # 20 most recent
    uv run python scripts/feedback.py --limit 5
    uv run python scripts/feedback.py --full     # whole answer, not the first line

The trace URL is the reason this exists. A report tells you an answer was wrong;
the trace tells you which of retrieval, the rule engine or generation made it
wrong, which is the difference between a complaint and a golden case.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

from app import feedback  # noqa: E402


def trace_url(run_id: str | None) -> str:
    """Resolved through the SDK rather than assembled from a string.

    A LangSmith run URL carries the workspace and project ids, neither of which is
    in the row -- guessing the format would produce a link that looks right and
    404s. If the lookup fails (tracing was off, the run aged out, no API key) the
    id is still printed, which is enough to find it by hand.
    """
    if not run_id:
        return "no trace (tracing was off for this answer)"
    try:
        from langsmith import Client

        return Client().get_run_url(run=Client().read_run(run_id))
    except Exception as exc:
        return f"run {run_id} (URL lookup failed: {type(exc).__name__})"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=20)
    ap.add_argument("--full", action="store_true", help="print the whole answer")
    args = ap.parse_args()

    rows = feedback.recent(args.limit)
    if not rows:
        print("No reports yet.")
        return 0

    for r in rows:
        answer = r["answer"] if args.full else r["answer"].split("\n")[0][:160]
        cites = ", ".join(f"{c['doc']} §{c['section']} p{c['page']}"
                          for c in r["citations"]) or "-"
        print(f"\n{r['created_at']:%Y-%m-%d %H:%M}  {r['route'] or '-'}  {r['model'] or '-'}")
        print(f"  Q: {r['question'][:160]}")
        print(f"  A: {answer}")
        print(f"  cited: {cites}")
        print(f"  trace: {trace_url(r['run_id'])}")
        print(f"  thread: {r['thread_id']}")
    print(f"\n{len(rows)} report(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
