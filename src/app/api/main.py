"""FastAPI surface: /chat, /chunk, /feedback, /validate, /health, and the frontend.

/chunk, /validate and /health deliberately touch no LLM, so they keep working when
the daily token budget is spent -- which matters most for /chunk: the moment the
quota runs out is exactly when someone is left holding an answer they want to check.
"""

from __future__ import annotations

import contextlib
import json
import os
from collections import OrderedDict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import psycopg
from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from langchain_core.callbacks import UsageMetadataCallbackHandler
from langchain_core.tracers.context import collect_runs
from pydantic import BaseModel, Field
from slowapi import Limiter
from slowapi.util import get_remote_address
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.staticfiles import StaticFiles

from app import budget, feedback
from app.budget import QuotaExhausted
from app.graph.graph import SHORT, build_graph
from app.rag.chain import Busy
from app.rag.citations import parse as parse_citations
from app.rag.retriever import LONG, latest_versions, search_sections
from app.tools.validate_fields import validate_fields

# Local dev convenience; in a container the vars come from the environment
# and load_dotenv is a no-op.
load_dotenv(Path(__file__).resolve().parents[3] / ".env")

# Handlers are sync on purpose: the graph, psycopg and sentence-transformers all
# block, and blocking inside an async handler stalls the whole event loop and
# serialises every other request. FastAPI runs sync handlers in a threadpool.
MAX_INPUT_CHARS = 2_000
# What a "Report a problem" click needs, held until someone clicks. Bounded and
# in-process on purpose: nothing about an answer is written down unless a user
# actually reports it. It shares the fate of the conversation memory beside it --
# MemorySaver is in-process too -- so an answer stays reportable while the replica
# that produced it is alive, which is the window a reader clicks in. After a
# scale-to-zero the thread is gone anyway, and /feedback says so rather than
# silently accepting a report it cannot store.
RECENT_MAX = 200
_recent: OrderedDict[str, dict[str, Any]] = OrderedDict()
STATIC = Path(__file__).resolve().parents[1] / "static"
STATIC.mkdir(parents=True, exist_ok=True)

@contextlib.asynccontextmanager
async def lifespan(_: FastAPI):
    with contextlib.suppress(Exception):  # health reports db: fail if this fails
        budget.init()
        feedback.init()
    yield


limiter = Limiter(key_func=get_remote_address)
app = FastAPI(title="MyInvois Compliance Copilot", docs_url="/docs", lifespan=lifespan)
app.state.limiter = limiter
_graph = None


def graph():
    global _graph
    if _graph is None:
        _graph = build_graph()
    return _graph


class ChatIn(BaseModel):
    message: str
    thread_id: str | None = None


class FeedbackIn(BaseModel):
    thread_id: str
    message_id: str


class ValidateIn(BaseModel):
    invoice: dict[str, Any] = Field(default_factory=dict)


def error(status: int, message: str, **extra: Any) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": message, **extra})


@app.exception_handler(Exception)
async def unhandled(_: Request, exc: Exception) -> JSONResponse:
    """Never leak a stack trace to a public URL."""
    return error(500, "Something went wrong handling that request.",
                 type=type(exc).__name__)


@app.exception_handler(429)
async def too_many(_: Request, exc: Exception) -> JSONResponse:
    return error(429, "Too many requests. Please wait a moment and try again.")


def _midnight_utc() -> str:
    tomorrow = datetime.now(UTC).date() + timedelta(days=1)
    return datetime.combine(tomorrow, datetime.min.time(), UTC).isoformat()


@app.post("/chat")
@limiter.limit("10/minute")
def chat(request: Request, body: ChatIn) -> JSONResponse:
    if len(body.message) > MAX_INPUT_CHARS:
        return error(413, f"Message is {len(body.message)} characters; the limit is "
                          f"{MAX_INPUT_CHARS}. Please shorten it.",
                     limit=MAX_INPUT_CHARS)
    if not body.message.strip():
        return error(400, "Message cannot be empty.")
    if budget.exhausted():
        return error(429, "The daily question quota is used up, so I cannot answer "
                          "new questions until it resets. Invoice validation still "
                          "works and needs no quota.",
                     resets_at=_midnight_utc(), budget=budget.limit())

    thread_id = body.thread_id or os.urandom(8).hex()
    usage = UsageMetadataCallbackHandler()
    try:
        # collect_runs captures the LangSmith run id, which is the whole point of
        # the feedback table: a report without it is an anecdote, with it it is an
        # execution tree. Empty when tracing is off, and that is not an error.
        with collect_runs() as runs:
            out = graph().invoke(
                {"question": body.message},
                config={"configurable": {"thread_id": thread_id}, "callbacks": [usage]})
    except QuotaExhausted:
        return error(429, "The daily question quota is used up. Invoice validation "
                          "still works and needs no quota.",
                     resets_at=_midnight_utc(), budget=budget.limit())
    except Busy:
        return error(503, "The model is busy right now. Please try again in a moment.")
    except Exception as exc:  # provider errors must not surface as tracebacks
        text = str(exc).lower()
        if "tpd" in text or "per day" in text:
            # The provider's own daily cap, reached before ours. Retrying in a
            # moment will not help, so do not tell the user it will.
            return error(429, "The daily question quota is used up. Invoice "
                              "validation still works and needs no quota.",
                         resets_at=_midnight_utc(), budget=budget.limit())
        if "rate_limit" in text or "429" in text:
            return error(503, "The model is busy right now. Please try again in "
                              "a moment.")
        raise

    # get_llm() meters every call at the client, so nothing is charged here.
    # This handler only reports what the run cost, per model.
    spent = sum(u.get("total_tokens", 0) for u in usage.usage_metadata.values())

    answer = out.get("answer", "")
    citations = [
        {"doc": d, "version": v, "section": sec, "page": int(pg)}
        for d, v, sec, pg in parse_citations(answer)
    ]
    sorted_cites = sorted(citations, key=lambda c: (c["doc"], c["page"]))
    message_id = os.urandom(8).hex()
    _recent[message_id] = {
        "thread_id": thread_id, "message_id": message_id, "question": body.message,
        "answer": answer, "citations": sorted_cites, "route": out.get("intent"),
        "model": next(iter(usage.usage_metadata), None),
        "run_id": str(runs.traced_runs[0].id) if runs.traced_runs else None,
    }
    while len(_recent) > RECENT_MAX:
        _recent.popitem(last=False)

    return JSONResponse({
        "answer": answer,
        "citations": sorted_cites,
        "route": out.get("intent"),
        "thread_id": thread_id,
        "message_id": message_id,
        "models": {m: u.get("total_tokens", 0)
                   for m, u in usage.usage_metadata.items()},
        "tokens": spent,
    })


@app.post("/feedback")
@limiter.limit("20/minute")
def report_problem(request: Request, body: FeedbackIn) -> JSONResponse:
    """One click, no form. The body names an answer; the server already knows the
    rest, so there is no free text to moderate and no way to submit a complaint."""
    entry = _recent.get(body.message_id)
    if not entry or entry["thread_id"] != body.thread_id:
        return error(404, "That answer is no longer available to report. Reports "
                          "can only be filed while the conversation is still open.")
    try:
        feedback.record(entry)  # a second click is the same report, not a new one
    except Exception:
        return error(503, "Could not log that just now. Please try again shortly.")
    return JSONResponse({"status": "logged"})


@app.get("/chunk")
@limiter.limit("60/minute")
def chunk(request: Request, ref: str) -> JSONResponse:
    """The source text behind one citation. Read-only, no LLM, no quota.

    The project's claim is that an answer can be checked. Until now checking meant
    opening a 200-page PDF and finding §1.6.1(e) by hand, which nobody does, so in
    practice the citations were decoration. This returns the chunk the retriever
    actually used, so the check is a click.

    The ref is parsed here rather than in the browser because the browser would
    need its own copy of the citation regex, and a second copy of that regex is the
    exact thing src/app/rag/citations.py exists to prevent. It also means page
    ranges the model writes ("p44-p50") normalise on the way in for free.
    """
    found = parse_citations(ref)
    if len(found) != 1:
        return error(400, "That is not a single citation reference.")
    doc, version, section, page = next(iter(found))
    if not (short := LONG.get(doc)):
        return error(404, "No source text is available for that citation.")
    hits = search_sections([f"{doc} v{version} §{section}"],
                           versions={short: version}, limit=4)
    if not hits:
        return error(404, "No source text is available for that citation.")
    # Prefer the page the citation names, for a section split across chunks;
    # otherwise the row search_sections ranked first, which is the one carrying
    # the cited row marker.
    hit = next((h for h in hits if h.page == int(page)), hits[0])
    return JSONResponse({"doc": doc, "version": hit.version, "section": hit.section,
                         "title": hit.title, "page": hit.page, "content": hit.content})


@app.post("/validate")
@limiter.limit("30/minute")
def validate(request: Request, body: ValidateIn) -> JSONResponse:
    """Deterministic: no LLM, so no budget check and no quota consumed."""
    if len(json.dumps(body.invoice)) > MAX_INPUT_CHARS * 5:
        return error(413, "Invoice payload is too large.")
    return JSONResponse(validate_fields(body.invoice).model_dump())


@app.get("/health")
def health() -> JSONResponse:
    db = "fail"
    try:
        with psycopg.connect(os.environ["DATABASE_URL"], connect_timeout=3) as conn:
            conn.execute("SELECT 1")
        db = "ok"
    except Exception:
        db = "fail"
    versions = {}
    with contextlib.suppress(Exception):
        versions = dict(s.split(":", 1) for s in latest_versions())
    used = budget.used() if db == "ok" else None
    return JSONResponse({
        "status": "ok" if db == "ok" and bool(versions) else "degraded",
        "guideline_versions": versions,
        "db": db,
        "budget": {"limit": budget.limit(), "used": used,
                   "remaining": budget.remaining() if db == "ok" else None},
    })


class SPAStaticFiles(StaticFiles):
    async def get_response(self, path: str, scope: Any) -> Any:
        try:
            return await super().get_response(path, scope)
        except StarletteHTTPException as exc:
            if exc.status_code == 404:
                return await super().get_response("index.html", scope)
            raise


app.mount("/", SPAStaticFiles(directory=STATIC, html=True), name="static")





__all__ = ["app", "SHORT"]
