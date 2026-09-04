"""get_llm() meters every LLM call, whatever the caller.

Day 7: the golden set spent Groq's whole 200,000 TPD while our own counter read
10,943/150,000, because budget.spend() was wired into the /chat handler only.
This exercises the path that had no meter -- a graph invocation with no API in
sight -- and it must never touch a real provider or a real budget table.
"""

from __future__ import annotations

import pytest
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.runnables import RunnableLambda

from app.graph.graph import Answer, InvoiceExtract, InvoiceField, build_graph

TOKENS_PER_CALL = 100
INVOICE = 'Check this invoice: {"Supplier' + chr(39) + 's Name": "ACME Sdn Bhd"}'


class FakeChat(BaseChatModel):
    """Never leaves the process, but reports usage the way a real client does."""

    @property
    def _llm_type(self) -> str:
        return "fake"

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        msg = AIMessage(
            content="A field is missing [Guideline v4.8 §Appendix 1, p44].",
            usage_metadata={"input_tokens": 70, "output_tokens": 30,
                            "total_tokens": TOKENS_PER_CALL})
        return ChatResult(generations=[ChatGeneration(message=msg)])

    def with_structured_output(self, schema, **kwargs):
        """The call still happens -- only the parsing is faked -- so the tokens
        a structured node spends are still there to be metered.

        Honours the schema it is handed. generate() became structured when
        coverage turned into a field, and a fake that always returned
        InvoiceExtract would hand it an object with no `coverage`.
        """
        got = {
            "InvoiceExtract": lambda: InvoiceExtract(
                is_invoice_data=True,
                fields=[InvoiceField(name="Supplier's Name", value="ACME")]),
            "Answer": lambda: Answer(
                coverage="addressed",
                answer="A field is missing [Guideline v4.8 §Appendix 1, p44]."),
        }[schema.__name__]()
        return RunnableLambda(lambda x: (self.invoke(x), got)[1])


@pytest.fixture
def charges(monkeypatch):
    spent: list[int] = []
    monkeypatch.setenv("LLM_PROVIDER", "groq")
    monkeypatch.setenv("GROQ_API_KEY", "not-a-real-key")
    monkeypatch.setattr("app.budget.exhausted", lambda: False)
    monkeypatch.setattr("app.budget.spend", lambda n: spent.append(n))
    monkeypatch.setattr("langchain_groq.ChatGroq",
                        lambda **kw: FakeChat(callbacks=kw.get("callbacks")))
    return spent


def test_a_direct_graph_invocation_is_metered(charges):
    """No FastAPI, no /chat handler: the meter has to live under the caller."""
    out = build_graph().invoke({"question": INVOICE},
                               config={"configurable": {"thread_id": "t-meter"}})
    assert out["intent"] == "field_check"
    # validate_fields (structured) and generate, charged separately.
    assert charges == [TOKENS_PER_CALL, TOKENS_PER_CALL]


def test_a_response_without_usage_metadata_charges_nothing(charges):
    """Some providers omit usage on an empty completion; that is 0, not a crash."""
    from app.rag.chain import METER

    METER.on_llm_end(ChatResult(generations=[ChatGeneration(
        message=AIMessage(content="hi"))]))
    assert charges == []
