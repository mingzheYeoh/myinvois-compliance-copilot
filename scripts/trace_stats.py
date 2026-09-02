"""Aggregate LangSmith runs by node: latency and tokens. Reads only, no LLM spend.

    uv run python scripts/trace_stats.py [project] [hours]
"""

from __future__ import annotations

import statistics
import sys
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
from dotenv import load_dotenv  # noqa: E402

load_dotenv(Path(__file__).resolve().parents[1] / ".env")
from langsmith import Client  # noqa: E402

NODES = {"router", "retrieve", "grade_docs", "rewrite_query", "profile_extract",
         "rule_engine", "retrieve_for_rules", "validate_fields", "generate"}


def main() -> int:
    project = sys.argv[1] if len(sys.argv) > 1 else "myinvois-copilot"
    hours = int(sys.argv[2]) if len(sys.argv) > 2 else 48
    after = datetime.now(UTC) - timedelta(hours=hours)
    client = Client()

    per_node: dict[str, list[tuple[float, int]]] = defaultdict(list)
    roots: list[tuple[float, int]] = []
    n = 0
    for r in client.list_runs(project_name=project, start_time=after):
        n += 1
        if not (r.start_time and r.end_time):
            continue
        secs = (r.end_time - r.start_time).total_seconds()
        tok = (r.total_tokens or 0)
        if r.parent_run_id is None:
            roots.append((secs, tok))
        if r.name in NODES:
            per_node[r.name].append((secs, tok))

    print(f"project={project}  runs seen={n}  graph invocations={len(roots)}\n")
    print(f"{'node':<20}{'calls':>6}{'p50 s':>9}{'mean s':>9}{'mean tok':>10}{'tot tok':>10}")
    print("-" * 64)
    for name in sorted(per_node, key=lambda k: -sum(t for _, t in per_node[k])):
        rows = per_node[name]
        secs = [s for s, _ in rows]
        toks = [t for _, t in rows]
        print(f"{name:<20}{len(rows):>6}{statistics.median(secs):>9.2f}"
              f"{statistics.mean(secs):>9.2f}{statistics.mean(toks):>10.0f}{sum(toks):>10}")
    if roots:
        secs = [s for s, _ in roots]
        toks = [t for _, t in roots]
        print("-" * 64)
        print(f"{'PER GRAPH RUN':<20}{len(roots):>6}{statistics.median(secs):>9.2f}"
              f"{statistics.mean(secs):>9.2f}{statistics.mean(toks):>10.0f}{sum(toks):>10}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
