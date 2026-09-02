# Windows has no make; the uv commands on the right work as-is in PowerShell.
.PHONY: test test-all eval eval-subset lint api

test:            ## unit tests only, no LLM, no quota
	uv run pytest

test-all: test eval   ## unit tests plus the golden set (spends quota)

eval:            ## golden set: 20 questions, table, non-zero exit on failure
	uv run python scripts/eval.py

eval-subset:     ## make eval-subset IDS=q01,q09
	uv run python scripts/eval.py --subset $(IDS)

lint:
	uv run ruff check .

api:
	uv run uvicorn app.api.main:app --reload --port 8000
