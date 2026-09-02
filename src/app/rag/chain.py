"""Day 2: the minimal retrieve -> generate chain. No router, no grading, no tools.

Returns both the answer and the hits it was grounded in, so a citation can be
checked against the chunk it claims to come from.
"""

from __future__ import annotations

import os
from operator import itemgetter

from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import RunnableParallel, RunnablePassthrough

from app.rag.retriever import Hit, search

# Short names used in citations. Keys are the `doc` values in the manifest.
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


def get_llm():
    """One place the provider is chosen. LLM_PROVIDER=groq|azure, groq by default."""
    provider = os.getenv("LLM_PROVIDER", "groq").lower()
    if provider == "groq":
        from langchain_groq import ChatGroq

        return ChatGroq(model=os.getenv("GROQ_MODEL", "openai/gpt-oss-120b"), temperature=0)
    if provider == "azure":
        from langchain_openai import AzureChatOpenAI

        return AzureChatOpenAI(
            azure_deployment=os.environ["AZURE_OPENAI_DEPLOYMENT"],
            api_version=os.getenv("AZURE_OPENAI_API_VERSION", "2024-10-21"),
            temperature=0,
        )
    raise ValueError(f"LLM_PROVIDER must be 'groq' or 'azure', got {provider!r}")


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
