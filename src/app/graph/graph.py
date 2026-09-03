"""The LangGraph from PLAN.md §4.1. Three intents, one corrective-RAG loop.

    router
     ├─ general_qa    -> retrieve -> grade_docs -┬ pass -> generate -> END
     │                                           └ fail -> rewrite_query -> retrieve
     ├─ applicability -> profile_extract -> rule_engine -> retrieve -> generate -> END
     └─ field_check   -> validate_fields -> generate -> END

The LLM classifies, extracts, grades, rewrites and explains. It never decides a
compliance outcome: that is `rule_engine`, which is pure Python.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Annotated, Any, Literal, TypedDict

from langchain_core.prompts import ChatPromptTemplate
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, StateGraph
from pydantic import BaseModel, Field

from app.rag.chain import SHORT, format_context, get_llm
from app.rag.retriever import Hit, search
from app.rules.engine import (
    BusinessProfile,
    Determination,
    Transaction,
    determine,
    reference_facts,
)
from app.tools.validate_fields import field_list, validate_fields

MAX_RETRIES = 2

# Fields that mean "the user is asking about their own business". Anything else
# on the profile (industry, or fy2022_period_months, which defaults to 12 and so
# is never None) must not turn a generic question into a request for details.
DECISION_FIELDS = (
    "annual_turnover", "commencement_year", "commencement_date", "fy2022_turnover",
    "expected_first_year_turnover", "has_related_company_over_threshold",
    "owned_by_exempt_person", "year_turnover_reached_threshold",
)

# A year at or before 2025 in the message means the sale may predate the
# 1 Jan 2026 RM10,000 rule, which flips the answer. Assuming "today" there
# would silently answer about a different transaction than the user asked about.
PRE_RULE_YEAR = re.compile(r"\b(?:19\d\d|20[0-1]\d|202[0-5])\b")


# Classifying and grading do not need the big model; generating a cited answer
# does. Splitting them is the cheapest latency and token win available.
def structured(model, small: bool = False):
    """Groq's gpt-oss models fail forced tool-use ("model did not call a tool")
    when there is nothing to extract. json_schema is reliable for both cases."""
    return get_llm(small=small).with_structured_output(model, method="json_schema")


class State(TypedDict, total=False):
    question: str
    query: str  # what retrieve actually searches for; rewrite_query changes it
    intent: str
    profile: dict[str, Any]
    transaction: dict[str, Any] | None
    determination: dict[str, Any]
    invoice: dict[str, Any]
    field_report: dict[str, Any]
    hits: Annotated[list[Hit], lambda _, new: new]
    grade: str
    answer: str
    retry_count: int


# --- structured outputs -----------------------------------------------------

class Route(BaseModel):
    intent: Literal["general_qa", "applicability", "field_check"] = Field(
        description="Which path handles this question")


class Grade(BaseModel):
    sufficient: bool = Field(description="True if the chunks answer the question")
    missing: str = Field(default="", description="What is absent, if not sufficient")


class Rewrite(BaseModel):
    query: str = Field(description="A reformulated retrieval query")


class InvoiceExtract(BaseModel):
    """Invoice the user pasted. `is_invoice_data` is False when they wrote prose
    about invoices rather than supplying one -- we ask instead of inventing."""

    is_invoice_data: bool = Field(
        description="True only if the message contains actual invoice field "
                    "values (JSON, key/value pairs or a filled-in list)")
    asks_for_field_list: bool = Field(
        default=False,
        description="True if the user is asking WHICH fields are required, rather "
                    "than supplying an invoice to check")
    fields: dict[str, str] = Field(
        default_factory=dict,
        description="Field name -> value, copied verbatim. Never invent a value.")


class Extraction(BaseModel):
    """Profile and transaction in one call; a second LLM round trip buys nothing."""

    profile: BusinessProfile = Field(default_factory=BusinessProfile)
    transaction_amount: float | None = Field(
        default=None, description="Value of a single sale the user asks about, if any")
    transaction_date: date | None = Field(
        default=None, description="Date of that sale, only if the user states one")


ROUTER_PROMPT = ChatPromptTemplate.from_messages([("system", """\
Classify a Malaysian e-Invoice question into one path.

applicability - ANY question that asks for a date, a threshold, a phase, a
  relaxation period, an exemption, or whether someone must do something
  ("do I need to", "when must I", "how long is", "what is the threshold",
  "can I issue X for RM Y"). This holds even when the question is phrased
  generally rather than about a specific business. These are decided by a
  deterministic rule engine, never by reading prose.
field_check - the user supplies invoice data (JSON or a field list) to validate.
general_qa - everything else: definitions, processes, concepts, penalties.

Examples:
  "How long is the relaxation period for Phase 4?" -> applicability
  "What is the exemption threshold?" -> applicability
  "Can I issue a consolidated e-Invoice for a RM12,000 sale?" -> applicability
  "What is an e-Invoice?" -> general_qa
  "What is the penalty for not issuing one?" -> general_qa
  "What are the mandatory fields in an e-Invoice?" -> field_check
  "Check this invoice: {{...}}" -> field_check"""),
    ("human", "{question}")])

EXTRACT_PROMPT = ChatPromptTemplate.from_messages([("system", """\
Extract the business profile from the user's message. Leave a field null when
the user did not state it - never infer, never default. Notes:
- annual_turnover: current annual turnover or revenue in ringgit, as a number.
- commencement_year: the year the business started operating.
- has_related_company_over_threshold: true only if the user says they have a
  non-individual shareholder, a holding company, a related company or a joint
  venture with turnover of at least RM3,000,000. "related company" is the
  section 2 Promotion of Investments Act 1986 meaning, not the everyday one.
Already known (keep unless the user contradicts it): {known}"""),
    ("human", "{question}")])

GRADE_PROMPT = ChatPromptTemplate.from_messages([("system", """\
Judge whether the retrieved chunks, taken together, contain enough to answer
the question. Answer about the whole set in one go. Say what is missing if not.

Question: {question}

Chunks:
{context}""")])

REWRITE_PROMPT = ChatPromptTemplate.from_messages([("system", """\
The previous retrieval missed. Rewrite the search query using the vocabulary of
Malaysian e-Invoice guidelines (e.g. "consolidated e-Invoice", "self-billed",
"interim relaxation period", "annual turnover or revenue threshold"). Return the
query only.

Original question: {question}
Previous query: {query}
What was missing: {missing}""")])

PRECEDENCE = """\
Source precedence, highest first: Guideline > Specific Guideline > FAQ.
If two chunks disagree on a number or a date, you MUST cite both, follow the
higher-precedence one, and state plainly that the lower-precedence document has
not yet been updated."""

GENERATE_PROMPT = ChatPromptTemplate.from_messages([("system", """\
You are a Malaysian e-Invoice (LHDN MyInvois) compliance assistant.

Answer ONLY from the context below. Every factual sentence carries a citation
copied verbatim from the context block it came from:

    [<doc> v<version> §<section>, p<page>]

{precedence}

Never invent, guess or reformat a citation, and never cite a section you were
not given. If the context does not answer the question, reply exactly
"Not covered in the guidelines." and stop.
{determination}
Context:
{context}"""),
    ("human", "{question}")])


# --- nodes ------------------------------------------------------------------

# Cheap pre-router. These phrasings are unambiguous, so spending an LLM call to
# classify them buys nothing. Anything not matched still goes to the model.
APPLICABILITY_KW = re.compile(
    r"\b(?:threshold|relaxation|exempt\w*|deadline|implementation date|"
    r"phase\s*\d|when (?:must|do|does|should|is|are)|how long|"
    r"do i (?:need|have to)|am i (?:required|exempt)|consolidat\w+|self-billed|"
    r"turnover|RM\s?[\d,]{3,})\b",
    re.I)
# A JSON-ish object with at least one quoted key is invoice data, not prose.
INVOICE_BLOCK = re.compile(r"\{[^{}]*[\"'][^\"']+[\"']\s*:", re.S)
# Day 7 q08: the 20b router sent "what are the mandatory fields" to general_qa,
# which answered from §4.0 prose instead of the deterministic Appendix 1 table.
# The phrasing is unambiguous, so it does not need a model to classify it.
FIELD_LIST_KW = re.compile(r"\b(?:mandatory|which|required) fields\b", re.I)


def pre_route(question: str) -> str | None:
    """Route without an LLM where the wording is decisive. None = ask the model."""
    if INVOICE_BLOCK.search(question):
        return "field_check"
    if FIELD_LIST_KW.search(question):
        return "field_check"
    if APPLICABILITY_KW.search(question):
        return "applicability"
    return None


def router(state: State) -> State:
    if state.get("determination", {}).get("blocking"):
        # Mid-collection: the user is answering our question, not asking a new
        # one. "It started in 2024" classifies as general_qa on its own.
        return {"intent": "applicability", "query": state["question"], "retry_count": 0}
    if (hit := pre_route(state["question"])) is not None:
        return {"intent": hit, "query": state["question"], "retry_count": 0}
    route = structured(Route, small=True).invoke(
        ROUTER_PROMPT.format_messages(question=state["question"]))
    return {"intent": route.intent, "query": state["question"], "retry_count": 0}


def retrieve(state: State) -> State:
    return {"hits": search(state.get("query") or state["question"], k=6)}


def grade_docs(state: State) -> State:
    g = structured(Grade, small=True).invoke(
        GRADE_PROMPT.format_messages(
            question=state["question"], context=format_context(state["hits"])))
    return {"grade": "pass" if g.sufficient else "fail", "determination":
            {**state.get("determination", {}), "_missing_ctx": g.missing}}


def rewrite_query(state: State) -> State:
    r = structured(Rewrite, small=True).invoke(
        REWRITE_PROMPT.format_messages(
            question=state["question"], query=state.get("query", ""),
            missing=state.get("determination", {}).get("_missing_ctx", "")))
    return {"query": r.query, "retry_count": state.get("retry_count", 0) + 1}


def profile_extract(state: State) -> State:
    known = state.get("profile") or {}
    got = structured(Extraction, small=True).invoke(
        EXTRACT_PROMPT.format_messages(question=state["question"], known=known or "nothing"))
    # Multi-turn collection: a later turn adds fields, it never clears them.
    merged = {**known, **{k: v for k, v in got.profile.model_dump().items() if v is not None}}
    txn = None
    if got.transaction_amount is not None:
        needs_date = (got.transaction_date is None
                      and bool(PRE_RULE_YEAR.search(state["question"])))
        txn = {"amount": got.transaction_amount,
               "on": (got.transaction_date or date.today()).isoformat(),
               "date_assumed": got.transaction_date is None,
               "needs_date": needs_date}
    return {"profile": merged, "transaction": txn or state.get("transaction")}


def rule_engine(state: State) -> State:
    profile = state.get("profile") or {}
    raw = state.get("transaction")
    txn = Transaction(amount=raw["amount"], on=date.fromisoformat(raw["on"])) if raw else None
    d: Determination = determine(BusinessProfile(**profile), transaction=txn)
    out = d.model_dump(mode="json")
    # The tables are authoritative for everyone, profile or not: "what is the
    # threshold" and "how long is the Phase 4 relaxation" need no profile.
    out["facts"] = [r.model_dump() for r in reference_facts()]
    # Only demand profile fields when the user is actually asking about their
    # own business. A bare "what is the exemption threshold?" is not that.
    out["blocking"] = bool(d.missing) and any(
        profile.get(f) is not None for f in DECISION_FIELDS)
    if raw and raw.get("needs_date"):
        # Do not assume today when the message mentions a pre-2026 year.
        out["missing"] = [*out.get("missing", []), "transaction_date"]
        out["blocking"] = True
        out["consolidated_allowed"] = None
        out["individual_over_10k_required"] = None
    elif raw and raw.get("date_assumed"):
        out["date_assumed"] = raw["on"]
    return {"determination": out}


INVOICE_PROMPT = ChatPromptTemplate.from_messages([("system", """\
Read the user's message and decide which of three things it is.

- It contains an actual invoice (JSON, key/value pairs, a filled-in list):
  set is_invoice_data true and copy every field name and value verbatim.
  Never invent, complete or normalise a value.
- It asks which fields an e-Invoice needs ("what are the mandatory fields",
  "which fields are required"): set asks_for_field_list true.
- Anything else: leave both false."""),
    ("human", "{question}")])


def validate_fields_node(state: State) -> State:
    got = structured(InvoiceExtract, small=True).invoke(
        INVOICE_PROMPT.format_messages(question=state["question"]))
    if got.is_invoice_data and got.fields:
        return {"field_report": validate_fields(got.fields).model_dump(),
                "invoice": got.fields}
    if got.asks_for_field_list:
        return {"field_report": {
            "list_request": True,
            "mandatory": [f.model_dump() for f in field_list("mandatory")],
            "conditional": [f.model_dump() for f in field_list("conditional")],
            "optional_count": len(field_list("optional")),
        }}
    return {"field_report": {"no_invoice": True}}


def _determination_block(d: dict[str, Any]) -> tuple[str, str]:
    """Render the rule-engine result for the prompt, and the query to retrieve."""
    if not d or "facts" not in d:
        return "", ""
    lines = ["", "RULE ENGINE OUTPUT. These values are computed deterministically from",
             "the guideline tables and OVERRIDE anything the context prose seems to say.",
             "Where a fact below and a context chunk disagree on a number or a date, the",
             "fact below is correct - §16.1's opening sentence says 'six months' while its",
             "own Table 16.1 says otherwise, and the table wins."]
    if d.get("blocking"):
        lines.append(f"  MISSING INPUT: {', '.join(d['missing'])}. Ask the user for exactly")
        lines.append("  these and nothing else. Do not answer the question yet.")
    elif d.get("required") is not None or d.get("consolidated_allowed") is not None:
        for key in ("required", "implementation_date", "relaxation_until",
                    "consolidated_allowed", "individual_over_10k_required"):
            if d.get(key) is not None:
                lines.append(f"  {key}: {d[key]}")
    if d.get("date_assumed"):
        lines.append(f"  NOTE: no transaction date was given, so {d['date_assumed']} "
                     f"(today) was assumed. Say so in the answer.")
    for r in d.get("reasons", []):
        lines.append(f"  - [{r['section']}] ({r['basis']}) {r['text']}")
    if d.get("facts"):
        lines.append("  Reference facts from the same tables:")
        for r in d["facts"]:
            lines.append(f"  - [{r['section']}] ({r['basis']}) {r['text']}")
    if any(r["basis"] == "faq_gap_fill" for r in d.get("reasons", [])):
        lines.append("  One rule above is a faq_gap_fill: the Guideline is silent and the")
        lines.append("  FAQ filled the gap. End your answer with exactly one line:")
        lines.append('  "Please confirm this with LHDN - the Guideline does not state it '
                     'directly."')
    query = " ".join(dict.fromkeys(
        r["section"] for r in [*d.get("reasons", []), *d.get("facts", [])]))
    return "\n".join(lines) + "\n", query


ASK_FOR = {
    "annual_turnover": "your current annual turnover or revenue, in ringgit",
    "commencement_year": "the year your business started operating",
    "commencement_date": "the exact date your business started operating",
    "transaction_date": "the date of that sale (the RM10,000 rule only applies "
                        "from 1 January 2026)",
    "has_related_company_over_threshold": (
        "whether you have a non-individual shareholder, holding company, related "
        "company or joint venture with annual turnover of at least RM3,000,000 "
        '("related company" has the section 2 Promotion of Investments Act 1986 '
        "meaning, not the everyday one)"),
}


def _field_block(rep: dict[str, Any]) -> str:
    if rep.get("list_request"):
        lines = ["", "APPENDIX 1 FIELD LIST (deterministic, read from the published",
                 "table -- reproduce it, do not add or drop entries).",
                 f"Mandatory ({len(rep['mandatory'])}):"]
        for f in rep["mandatory"]:
            lines.append(f"  {f['no']}. {f['name']} - {f['category']} [{f['section']}]")
        lines.append(f"Conditional ({len(rep['conditional'])}), required only when the "
                     f"stated condition applies:")
        for f in rep["conditional"]:
            lines.append(f"  {f['no']}. {f['name']} - {f['condition']} [{f['section']}]")
        lines.append(f"There are also {rep['optional_count']} optional fields, which "
                     f"need not be listed unless asked.")
        return "\n".join(lines) + "\n"
    if rep.get("no_invoice"):
        return ("\nThe user did not supply invoice data. Ask them to paste the "
                "invoice as JSON or as field/value pairs. Do NOT list the fields "
                "from memory and do NOT guess what their invoice contains.\n")
    lines = ["", "FIELD VALIDATION RESULT (deterministic, from Appendix 1 -- report it,",
             f"do not recompute it). {rep['checked']} fields checked; "
             f"{len(rep['present'])} supplied.",
             f"  valid: {rep['valid']}"]
    if rep["missing_mandatory"]:
        lines.append("  MISSING MANDATORY:")
        for f in rep["missing_mandatory"]:
            lines.append(f"    - {f['name']} (no. {f['no']}, {f['category']}) "
                         f"[{f['section']}]")
    if rep["check_conditional"]:
        lines.append("  CONDITIONAL, required only if the condition applies:")
        for f in rep["check_conditional"]:
            lines.append(f"    - {f['name']} (no. {f['no']}) - {f['condition']} "
                         f"[{f['section']}]")
    if rep["unknown_keys"]:
        lines.append(f"  NOT IN APPENDIX 1 (ignored): {', '.join(rep['unknown_keys'])}")
    return "\n".join(lines) + "\n"


def generate(state: State) -> State:
    if state.get("intent") == "field_check":
        answer = (GENERATE_PROMPT | get_llm()).invoke({
            "question": state["question"],
            "context": format_context(state.get("hits", [])),
            "precedence": PRECEDENCE,
            "determination": _field_block(state.get("field_report", {})),
        })
        return {"answer": answer.content}

    d0 = state.get("determination", {})
    missing = d0.get("missing") or []
    if missing and d0.get("blocking"):
        # Deterministic: on the first run the model was handed this list and
        # answered "1 January 2026" from the retrieved context anyway. A rule
        # engine that says "I cannot decide" must not be talked over.
        asks = "\n".join(f"  - {ASK_FOR.get(m, m)}" for m in missing)
        return {"answer": "I need a little more before I can answer that:\n" + asks}
    block, _ = _determination_block(state.get("determination", {}))
    answer = (GENERATE_PROMPT | get_llm()).invoke({
        "question": state["question"],
        "context": format_context(state.get("hits", [])),
        "precedence": PRECEDENCE,
        "determination": block,
    })
    return {"answer": answer.content}


def retrieve_for_rules(state: State) -> State:
    """Retrieve the guideline text behind the rule engine's own citations, so the
    answer can quote the sections the determination rests on."""
    _, refs = _determination_block(state.get("determination", {}))
    return {"hits": search(f"{state['question']} {refs}".strip(), k=6)}


# --- wiring -----------------------------------------------------------------

def _after_router(state: State) -> str:
    return state["intent"]


def _after_grade(state: State) -> str:
    if state.get("grade") == "pass" or state.get("retry_count", 0) >= MAX_RETRIES:
        return "generate"
    return "rewrite_query"


def build_graph(checkpointer: MemorySaver | None = None):
    g = StateGraph(State)
    for name, fn in [("router", router), ("retrieve", retrieve), ("grade_docs", grade_docs),
                     ("rewrite_query", rewrite_query), ("profile_extract", profile_extract),
                     ("rule_engine", rule_engine), ("retrieve_for_rules", retrieve_for_rules),
                     ("validate_fields", validate_fields_node), ("generate", generate)]:
        g.add_node(name, fn)

    g.set_entry_point("router")
    g.add_conditional_edges("router", _after_router, {
        "general_qa": "retrieve",
        "applicability": "profile_extract",
        "field_check": "validate_fields",
    })
    g.add_edge("retrieve", "grade_docs")
    g.add_conditional_edges("grade_docs", _after_grade,
                            {"generate": "generate", "rewrite_query": "rewrite_query"})
    g.add_edge("rewrite_query", "retrieve")
    g.add_edge("profile_extract", "rule_engine")
    g.add_edge("rule_engine", "retrieve_for_rules")
    g.add_edge("retrieve_for_rules", "generate")
    g.add_edge("validate_fields", "generate")
    g.add_edge("generate", END)
    return g.compile(checkpointer=checkpointer or MemorySaver())


__all__ = ["State", "build_graph", "SHORT"]
