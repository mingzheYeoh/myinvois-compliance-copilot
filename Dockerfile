FROM python:3.11-slim

# uv gives us the same locked resolution locally and in CI.
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy HF_HOME=/opt/hf

# Dependencies first so code edits don't invalidate the (slow) torch layer.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

# Bake the embedding model into the image: Container Apps scales to zero, and
# a 130MB download on every cold start is not something a user should wait for.
RUN uv run python -c "\
from sentence_transformers import SentenceTransformer; \
SentenceTransformer('BAAI/bge-small-en-v1.5')"

COPY src/ src/
COPY scripts/ scripts/

# Day 1 has one runnable entrypoint. Day 8 swaps this for uvicorn.
CMD ["uv", "run", "python", "scripts/ingest.py"]
