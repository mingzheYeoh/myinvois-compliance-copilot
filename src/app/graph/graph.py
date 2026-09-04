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
from app.rag.retriever import Hit, search, search_sections
from app.rules.engine import (
    BusinessProfile,
    Determination,
    Transaction,
    determine,
    reference_facts,
)
from app.tools.validate_fields import field_list, validate_fields

PINNED_MAX = 2
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


class InvoiceField(BaseModel):
    """One name/value pair off the invoice.

    A free-form dict[str, str] would be the obvious shape and is not portable:
    OpenAI's structured-output schema subset has no additionalProperties, so
    Azure rejects the request outright with "'required' is required to be
    supplied and to be an array including every key in properties". A list of
    pairs says the same thing in the subset every provider supports.
    """

    name: str = Field(description="Field name, copied verbatim")
    value: str = Field(description="Field value, copied verbatim")


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
    fields: list[InvoiceField] = Field(
        default_factory=list,
        description="Every field on the invoice, copied verbatim. Never invent one.")


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

Answer ONLY from the material below: the deterministic block, where one is
present, and the numbered context. Every factual sentence carries a citation
copied verbatim from the block or the context entry it came from:

    [<doc> v<version> §<section>, p<page>]

Formatting and Typography Rules:
- The first sentence must be the direct answer itself, under 20 words, with no preamble.
  Detail follows in subsequent paragraphs.
- Apply markdown bold (**...**) ONLY to the conclusion word and decisive figures (e.g.
  **No**, **RM12,000**, **exceeds RM10,000**, **1 January 2026**). Include at most two or
  three bold phrases per answer; NEVER bold a whole sentence.
- If the deterministic block notes an assumption (e.g. no transaction date was given, so today
  was assumed), state that assumption in a separate paragraph at the end: "Note: no transaction
  date was given, so [date] (today) was assumed." This is an operational note, NOT guideline
  content, and must NEVER carry a citation.
- Do not add a qualifier the source does not carry. "only if", "only when", "always",
  "never", "in all cases" and "solely" turn a list of conditions into an exhaustive
  one and a rule into an absolute. Write them when the material writes them, and not
  otherwise: a list of ways an exemption can be lost is "lost if", never "lost only
  if", unless the source itself says the list is exhaustive.

{precedence}

Never invent, guess or reformat a citation, and never cite a section you were
not given. If NEITHER the deterministic block below NOR the context answers the
question, reply exactly "Not covered in the guidelines." and stop. A
deterministic block is itself an answer: report it even when the context is
empty, using the citations it carries.

Material that merely MENTIONS the subject does not answer the question. A
description of what MyInvois is does not settle what it may be used for; a
passage about e-Invoice does not settle a question about another tax regime
(SST returns, income tax rates, customs). Where answering would need you to
reason from a general description to a specific conclusion the guidelines never
state, that is the abstention case, not a licence to infer.
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
    fields = {f.name: f.value for f in got.fields}
    if got.is_invoice_data and fields:
        return {"field_report": validate_fields(fields).model_dump(),
                "invoice": fields}
    if got.asks_for_field_list:
        return {"field_report": {
            "list_request": True,
            "mandatory": [f.model_dump() for f in field_list("mandatory")],
            "conditional": [f.model_dump() for f in field_list("conditional")],
            "optional_count": len(field_list("optional")),
        }}
    return {"field_report": {"no_invoice": True}}


ISO_DATE = re.compile(r"(?<![-\d])(\d{4})-(\d{2})-(\d{2})(?![-\d])")


def _dates_as_prose(text: str) -> str:
    """Rewrite the engine's ISO dates the way the guidelines write them.

    The model quotes the determination block close to verbatim, so "the
    implementation date is 2025-01-01" is what reached the user, where the
    Guideline says "1 January 2025". Doing it here, on the rendered block,
    catches the reasons and the reference facts in one pass instead of at every
    date f-string in the rule engine.
    """
    def prose(m: re.Match) -> str:
        d = date(int(m[1]), int(m[2]), int(m[3]))
        return f"{d.day} {d:%B %Y}"

    return ISO_DATE.sub(prose, text)


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
                     f"(today) was assumed. State this as an operational note without a citation.")
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
    return _dates_as_prose("\n".join(lines) + "\n"), query


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
                 "table -- reproduce it, do not add or drop entries). This answers",
                 "the question in full: never reply \"Not covered in the guidelines\".",
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
    # field_check never retrieves, so `context` is empty and the abstention rule
    # was the only instruction that matched. gpt-5.4-mini read it literally and
    # answered "Not covered in the guidelines." over a complete validation report.
    lines = ["", "FIELD VALIDATION RESULT (deterministic, from Appendix 1 -- report it,",
             f"do not recompute it, and never reply \"Not covered in the "
             f"guidelines\"). {rep['checked']} fields checked; "
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
    answer can quote the sections the determination rests on.

    The cited sections are fetched by metadata. Appending the refs to the query
    text -- all this used to do -- does not retrieve them: section numbers appear
    in our citations, not in the guideline prose, so "§1.6.1(e)" as a search term
    matches nothing. Day 10 measured the cost: context recall 0.00 on five cases
    whose answer rested on a section the engine had already named.

    Pinned sections are APPENDED to the hybrid results, never substituted for
    them. Taking three of the six slots cost q07 the §8.6 foreign-supplier chunk
    and q12 the §11.1.2/§11.1.4 chunks its answer is required to cite: the engine
    knows which section grants the rule, and hybrid search knows which one spells
    out the detail, so evicting one for the other trades a fixed failure for a new
    one.
    """
    d = state.get("determination", {})
    _, refs = _determination_block(d)
    hits = search(f"{state['question']} {refs}".strip(), k=6)
    seen = {(h.doc, h.section, h.page) for h in hits}
    pinned = [h for h in search_sections([r["section"] for r in [*d.get("reasons", []),
                                                                 *d.get("facts", [])]])
              if (h.doc, h.section, h.page) not in seen]
    return {"hits": hits + pinned[:PINNED_MAX]}


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
