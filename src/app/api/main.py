"""FastAPI surface: /chat, /validate, /health, and the single-page frontend.

/validate and /health deliberately touch no LLM, so they keep working when the
daily token budget is spent.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import psycopg
from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from langchain_core.callbacks import UsageMetadataCallbackHandler
from pydantic import BaseModel, Field
from slowapi import Limiter
from slowapi.util import get_remote_address

from app import budget
from app.budget import QuotaExhausted
from app.graph.graph import SHORT, build_graph
from app.rag.chain import Busy
from app.rag.retriever import latest_versions
from app.tools.validate_fields import validate_fields

# Local dev convenience; in a container the vars come from the environment
# and load_dotenv is a no-op.
load_dotenv(Path(__file__).resolve().parents[3] / ".env")

# Handlers are sync on purpose: the graph, psycopg and sentence-transformers all
# block, and blocking inside an async handler stalls the whole event loop and
# serialises every other request. FastAPI runs sync handlers in a threadpool.
MAX_INPUT_CHARS = 2_000
STATIC = Path(__file__).resolve().parents[3] / "static"
CITE = re.compile(r"[\[【]([^\]】]+?) v([^ \]】]+) §([^,\]】]+), ?p(\d+)[\]】]")

@contextlib.asynccontextmanager
async def lifespan(_: FastAPI):
    with contextlib.suppress(Exception):  # health reports db: fail if this fails
        budget.init()
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
        for d, v, sec, pg in {m.groups() for m in CITE.finditer(answer)}
    ]
    return JSONResponse({
        "answer": answer,
        "citations": sorted(citations, key=lambda c: (c["doc"], c["page"])),
        "route": out.get("intent"),
        "thread_id": thread_id,
        "models": {m: u.get("total_tokens", 0)
                   for m, u in usage.usage_metadata.items()},
        "tokens": spent,
    })


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
        "status": "ok" if db == "ok" else "degraded",
        "guideline_versions": versions,
        "db": db,
        "budget": {"limit": budget.limit(), "used": used,
                   "remaining": budget.remaining() if db == "ok" else None},
    })


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")





__all__ = ["app", "SHORT"]
