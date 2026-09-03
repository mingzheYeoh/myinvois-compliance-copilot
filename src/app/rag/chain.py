"""Day 2: the minimal retrieve -> generate chain. No router, no grading, no tools.

Returns both the answer and the hits it was grounded in, so a citation can be
checked against the chunk it claims to come from.
"""

from __future__ import annotations

import contextlib
import os
from operator import itemgetter

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import RunnableParallel, RunnablePassthrough

from app.rag.retriever import Hit, search

# Short names used in citations. Keys are the `doc` values in the manifest.
# 8K TPM is the binding limit, so throttling is routine rather than exceptional.
MAX_RETRIES = 3

SHORT = {
    "general_guideline": "Guideline",
    "specific_guideline": "Specific Guideline",
    "general_faq": "FAQ",
}

SYSTEM = """You are a Malaysian e-Invoice (LHDN MyInvois) compliance assistant.

Answer ONLY from the numbered context below. Every factual sentence must carry a
citation in exactly this form, copied verbatim from the context block it came from:

    [<doc> v<version> §<section>, p<page>]

Rules:
- The first sentence must be the direct answer itself, under 20 words, with no preamble.
  Detail follows in subsequent paragraphs.
- Apply markdown bold (**...**) ONLY to the conclusion word and decisive figures (e.g.
  **No**, **RM12,000**, **exceeds RM10,000**, **1 January 2026**). Two or three per
  answer, never a whole sentence.
- Use only the doc, version, section and page shown in the context. Never invent,
  guess, adjust or reformat them, and never cite a section you were not given.
- If the context does not answer the question, reply exactly:
  "Not covered in the guidelines." — then stop. Do not answer from prior knowledge.
- Do not add disclaimers, caveats or advice beyond what the context states.

Context:
{context}"""

PROMPT = ChatPromptTemplate.from_messages([("system", SYSTEM), ("human", "{question}")])


def format_context(hits: list[Hit]) -> str:
    return "\n\n".join(
        f"[{SHORT.get(h.doc, h.doc)} v{h.version} §{h.section}, p{h.page}] {h.title}\n{h.content}"
        for h in hits
    )


class Busy(RuntimeError):
    """Groq throttled us past the retry budget. Surfaced to the user as a
    'busy, try again in a moment' message, never as a stack trace."""


class _Meter(BaseCallbackHandler):
    """Charge every LLM response to the shared daily budget.

    This used to live in the /chat handler, so only HTTP traffic was metered.
    On Day 7 the golden set spent Groq's whole 200,000 TPD while our own
    counter read 10,943/150,000: scripts went straight past the guard that
    exists so the user sees our quota message rather than the provider's 429.
    Tokens are produced here, so they are counted here, whatever the caller.
    """

    def on_llm_end(self, response, **kwargs) -> None:
        total = sum(
            (getattr(g.message, "usage_metadata", None) or {}).get("total_tokens", 0)
            for gen in response.generations for g in gen if hasattr(g, "message")
        )
        if not total:
            return
        from app.budget import spend
        # Metering is bookkeeping: a budget-table hiccup must not fail an answer
        # the user already paid for. /health surfaces a broken DB separately.
        with contextlib.suppress(Exception):
            spend(total)


METER = _Meter()


def get_llm(model: str | None = None, small: bool = False):
    """One place the provider is chosen. LLM_PROVIDER=groq|azure, groq by default.

    `small=True` picks the cheap model for classification-shaped nodes; the
    daily budget picks the fallback provider once gpt-oss is spent. max_retries
    gives every caller the Groq SDK's exponential backoff with jitter (it also
    honours Retry-After up to 60s), so nothing downstream has to retry itself.
    """
    provider = os.getenv("LLM_PROVIDER", "groq").lower()
    if provider == "groq":
        from langchain_groq import ChatGroq

        from app.budget import chosen_model, exhausted

        # The budget outranks every pin, including an explicit GROQ_MODEL or a
        # caller-supplied model. Otherwise the pin defeats the fallback and we
        # get Groq's 429 instead of our own degraded-but-working service, which
        # is the whole reason the budget exists.
        if exhausted():
            name = chosen_model(small)
        elif model:
            name = model
        elif small:
            name = os.getenv("GROQ_SMALL_MODEL") or chosen_model(small=True)
        else:
            name = os.getenv("GROQ_MODEL") or chosen_model()
        return ChatGroq(model=name, temperature=0, max_retries=MAX_RETRIES,
                        callbacks=[METER])
    if provider == "azure":
        from langchain_openai import AzureChatOpenAI

        # Only the DEPLOYMENT is named here. The model behind it is Azure's to
        # change -- pinning "gpt-5.4-mini" in code would mean a redeploy every
        # time the deployment is repointed, and would lie the moment it was.
        #
        # MEASURED against this endpoint: api-version 2024-10-21 (stable GA),
        # 2025-04-01-preview and the v1 route (base_url=<endpoint>/openai/v1,
        # no api-version) all serve the deployment and all accept temperature,
        # reasoning_effort and json_schema structured output. The GA version
        # wins on the tie -- a preview api-version is a moving target.
        #
        # MEASURED: reasoning_effort and temperature are mutually exclusive on
        # this deployment. Sending both returns 400 "Unsupported value:
        # 'temperature' does not support 0.0 with this model. Only the default
        # (1) value is supported." So it is a straight choice, and temperature
        # wins.
        #
        # reasoning_effort="minimal" is the cheaper setting, and nothing in this
        # graph is a reasoning problem -- the nodes classify, extract, grade and
        # quote, and the compliance decisions are the rule engine's. But it
        # forces temperature 1, and at temperature 1 the same question gave two
        # different compliance answers on consecutive runs. Specific Guideline
        # §11.1.2 exempts taxpayers not entitled to deduct under s108 "as well
        # as" taxpayers listed on Bursa Malaysia; one run rendered that as "not
        # entitled ... AND NOT listed on Bursa" -- an inverted condition, served
        # with a correct citation. That costs more than the tokens it saved.
        #
        # Set AZURE_REASONING_EFFORT to opt back in where determinism is not the
        # point (bulk classification, a scratch run).
        effort = os.getenv("AZURE_REASONING_EFFORT", "")
        sampling = {"reasoning_effort": effort} if effort else {"temperature": 0}
        return AzureChatOpenAI(
            azure_deployment=model or os.environ["AZURE_OPENAI_DEPLOYMENT"],
            api_version=os.getenv("AZURE_OPENAI_API_VERSION", "2024-10-21"),
            max_retries=MAX_RETRIES,
            callbacks=[METER],
            **sampling,
        )
    raise ValueError(f"LLM_PROVIDER must be 'groq' or 'azure', got {provider!r}")


def llm_name(llm) -> str:
    """What to print in a run header. Azure exposes a deployment, not a model:
    model_name would report langchain's "gpt-3.5-turbo" default, which is false.
    """
    return getattr(llm, "deployment_name", None) or getattr(llm, "model_name", "?")


def build_chain(k: int = 5):
    """question -> {answer, hits}. Hits are returned so citations can be audited."""
    return (
        RunnablePassthrough.assign(hits=lambda x: search(x["question"], k))
        | RunnablePassthrough.assign(context=lambda x: format_context(x["hits"]))
        | RunnableParallel(
            answer=PROMPT | get_llm() | StrOutputParser(),
            hits=itemgetter("hits"),
        )
    )
