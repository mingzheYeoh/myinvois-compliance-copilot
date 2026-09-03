"""Golden-set regression harness. One command, one table, non-zero exit on failure.

    uv run python scripts/eval.py                  # all 20 (~2500 tok each)
    uv run python scripts/eval.py --subset q01,q09 # a few, without spending the day
    uv run python scripts/eval.py --limit 5

Groq gives this key 8,000 TPM, which one field_check question can spend on its
own, so the harness paces itself against the refilling token bucket using the
providers' own usage metadata. get_llm() already carries the SDK's exponential
backoff with jitter; the retry here is the outer net for a 429 that survives it.
"""

from __future__ import annotations

import argparse
import os
import random
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import yaml  # noqa: E402
from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

from ask import CITE, norm  # noqa: E402

GOLDEN = ROOT / "data" / "golden" / "questions.yaml"

# MEASURED: a raw call returns x-ratelimit-limit-tokens 8000 with
# x-ratelimit-reset-tokens in *milliseconds*, so the TPM ceiling is a bucket
# refilling continuously at TPM/60 tokens per second, not a 60-second window.
# The honest model is therefore a debt: spend N tokens, wait N/refill seconds.
# One field_check case measured 11,431 tokens, more than the whole bucket, so a
# 429 mid-case is possible whatever the pacing -- that is what the retry is for.
#
# This pacing does NOT protect against the daily cap. The first full run's seven
# consecutive 429s looked like TPM and were not: the error text (truncated at the
# time) said "tokens per day (TPD): Limit 200000". TPD is handled by aborting,
# below, because no backoff short of hours will clear it.
TPM = int(os.getenv("GROQ_TPM", "8000"))
REFILL = TPM / 60.0
_debt = 0.0


def charge(tokens: int) -> None:
    global _debt
    _debt += tokens


def pace() -> None:
    """Wait for the token bucket to give back what the last turn took."""
    global _debt
    if _debt <= 0:
        return
    wait = _debt / REFILL
    print(f"    [tpm] spent {_debt:.0f} tokens; waiting {wait:.0f}s for the bucket",
          flush=True)
    time.sleep(wait)
    _debt = 0.0


@dataclass
class Result:
    id: str
    question: str
    expected_route: str
    actual_route: str = "-"
    citations: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    answer: str = ""
    tokens: int = 0

    @property
    def ok(self) -> bool:
        return not self.problems


def load_cases(path: Path = GOLDEN) -> list[dict]:
    cases = yaml.safe_load(path.read_text(encoding="utf-8"))
    for c in cases:  # `turns` and `question` are two spellings of the same thing
        if "turns" not in c:
            c["turns"] = [c["question"]]
        c.setdefault("question", c["turns"][0])
    return cases


def citations(answer: str) -> list[str]:
    seen = {f"{d} v{v} §{s}, p{p}" for d, v, s, p in
            (m.groups() for m in CITE.finditer(norm(answer)))}
    return sorted(seen)


# "RM 3 million" and "RM3,000,000" are the same threshold; a suite that failed
# the first would be testing the model's prose style, not its compliance answer.
# This also stops a banned figure sneaking past in words ("RM1 million").
MONEY = re.compile(r"RM\s*([\d,]+(?:\.\d+)?)\s*(million|mil\b|m\b)?", re.I)


def money(text: str) -> str:
    def expand(m: re.Match) -> str:
        n = float(m.group(1).replace(",", ""))
        if m.group(2):
            n *= 1_000_000
        return f"RM{n:,.0f}" if n == int(n) else f"RM{n:,.2f}"

    return MONEY.sub(expand, text)


# Models write "Supplier's Name" with a typographic apostrophe; Appendix 1 uses
# a straight one. Same field name, so fold them before comparing.
QUOTES = str.maketrans({"’": "'", "‘": "'", "“": '"', "”": '"'})


def flat(text: str) -> str:
    """Everything cosmetic folded away: spaces, hyphens, quotes, money wording."""
    return money(norm(text).translate(QUOTES)).lower()


def check(case: dict, answer: str, route: str, cites: list[str]) -> list[str]:
    body = flat(answer)
    problems = []
    if route != case["expected_route"]:
        problems.append(f"routed to {route}, expected {case['expected_route']}")
    for want in case.get("expected_sections") or []:
        if not any(flat(want) in flat(c) for c in cites):
            problems.append(f"no citation matching {want!r}")
    for want in case.get("expected_facts") or []:
        if flat(want) not in body:
            problems.append(f"answer omits {want!r}")
    for banned in case.get("must_not_contain") or []:
        if flat(banned) in body:
            problems.append(f"answer contains banned {banned!r}")
    return problems


class DailyQuotaGone(RuntimeError):
    """Groq's 200,000 TPD is spent. Backing off will not help, so the run stops
    rather than burning 90 seconds per remaining case to learn that 17 times."""


def _invoke(graph, question: str, thread: str, usage):
    """One turn, with an outer backoff for a 429 the SDK's own retries lost."""
    for attempt in range(3):
        try:
            return graph.invoke(
                {"question": question},
                config={"configurable": {"thread_id": thread}, "callbacks": [usage]})
        except Exception as exc:
            text = str(exc).lower()
            if "tokens per day" in text or "tpd" in text:
                raise DailyQuotaGone(str(exc)[:220]) from exc
            transient = "429" in text or "rate_limit" in text or "busy" in text
            if attempt == 2 or not transient:
                raise
            # The bucket refills at ~133 tok/s, so a run that emptied it needs
            # most of a minute back, not the 20s the first version waited.
            delay = 45 * (attempt + 1) + random.uniform(0, 5)
            print(f"    [429] throttled; backing off {delay:.0f}s")
            time.sleep(delay)
            globals()["_debt"] = 0.0  # we just paid it back with interest
    raise AssertionError("unreachable")


def run_case(graph, case: dict) -> Result:
    from langchain_core.callbacks import UsageMetadataCallbackHandler

    res = Result(case["id"], case["question"], case["expected_route"])
    usage = UsageMetadataCallbackHandler()
    out = {}
    try:
        for turn in case["turns"]:
            pace()
            out = _invoke(graph, turn, f"golden-{case['id']}", usage)
            # The handler accumulates across turns, so charge the bucket the delta.
            total = sum(u.get("total_tokens", 0) for u in usage.usage_metadata.values())
            charge(total - res.tokens)
            res.tokens = total
    except DailyQuotaGone:
        raise
    except Exception as exc:
        res.problems.append(f"raised {type(exc).__name__}: {str(exc)[:300]}")
        return res
    res.answer = out.get("answer", "")
    res.actual_route = out.get("intent", "-")
    res.citations = citations(res.answer)
    res.problems = check(case, res.answer, res.actual_route, res.citations)
    return res


def run_all(cases: list[dict], graph=None) -> list[Result]:
    from app.graph.graph import build_graph

    graph = graph or build_graph()
    results = []
    for i, case in enumerate(cases):
        try:
            res = run_case(graph, case)
        except DailyQuotaGone as exc:
            print(f"\n  daily token quota gone: {exc}\n"
                  f"  {len(cases) - i} case(s) not run.", flush=True)
            for skipped in cases[i:]:
                results.append(Result(skipped["id"], skipped["question"],
                                      skipped["expected_route"],
                                      problems=["not run: daily token quota gone"]))
            return results
        # flush: stdout is block-buffered when redirected to a file, and a run
        # this slow is unwatchable if the table only appears at the end.
        print(f"  {res.id} {'PASS' if res.ok else 'FAIL'}  ({res.tokens} tok)", flush=True)
        results.append(res)
    return results


def _short(cites: list[str]) -> str:
    out = "; ".join(c.split(" §")[-1].replace(", p", " p") for c in cites)
    return (out[:31] + "...") if len(out) > 34 else out or "-"


def table(results: list[Result]) -> None:
    head = f"{'id':<5}{'question':<44}{'expected':<15}{'actual':<15}{'citations':<36}result"
    print(f"\n{head}\n{'-' * len(head)}")
    for r in results:
        q = r.question.replace("\n", " ")
        q = (q[:41] + "...") if len(q) > 44 else q
        print(f"{r.id:<5}{q:<44}{r.expected_route:<15}{r.actual_route:<15}"
              f"{_short(r.citations):<36}{'PASS' if r.ok else 'FAIL'}")

    failed = [r for r in results if not r.ok]
    if failed:
        print(f"\n{len(failed)} of {len(results)} failed:\n")
        for r in failed:
            print(f"{r.id}  {r.question[:90]}")
            for p in r.problems:
                print(f"    - {p}")
            if r.answer:
                print(f"    answer: {' '.join(r.answer.split())[:300]}")
            print()
    print(f"{len(results) - len(failed)}/{len(results)} passed, "
          f"{sum(r.tokens for r in results)} tokens spent")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--subset", help="comma-separated ids, e.g. q01,q09")
    ap.add_argument("--limit", type=int, help="run only the first N cases")
    args = ap.parse_args()

    cases = load_cases()
    if args.subset:
        wanted = {s.strip() for s in args.subset.split(",")}
        cases = [c for c in cases if c["id"] in wanted]
        if not cases:
            print(f"no case matches {args.subset!r}")
            return 2
    if args.limit:
        cases = cases[: args.limit]

    from app.budget import limit, used
    from app.rag.chain import get_llm

    print(f"golden set: {len(cases)} case(s)  generate={get_llm().model_name}  "
          f"small={get_llm(small=True).model_name}  budget={used()}/{limit()}  TPM={TPM}")
    results = run_all(cases)
    table(results)
    return 1 if any(not r.ok for r in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
