| Metric | Score | Cases |
|---|---|---|
| Faithfulness | 0.922 | n=17 |
| Answer Relevancy | 0.806 | n=17 |
| Context Precision (rag only) | 0.750 | n=2 |
| Context Precision (rule-engine cases) | 0.882 | n=13 |
| Context Recall (rag only) | 1.000 | n=2 |
| Context Recall (rule-engine cases) | 0.551 | n=13 |

Judge: `chat-small`, the same Azure deployment the app answers with.

Case mix: 2× rag, 13× deterministic, 2× field_check, 1× clarifying, 2× abstention. 3 excluded from every metric (clarifying: asked for a missing input rather than answering; no claim set to ground, abstention: correct refusal; no claims, no ground truth to recall).

Retrieval metrics are shown separately for rule-engine cases because the answer there came from params.json, not from the retrieved chunks -- averaging the two together would describe neither.
