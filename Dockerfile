# syntax=docker/dockerfile:1

# ---------- build ----------
# The builder carries uv, the compiler toolchain and the HF download cache; none
# of that ships. Only the resolved venv and the baked model cross the stage line.
FROM python:3.11-slim AS build

COPY --from=ghcr.io/astral-sh/uv:0.12.5 /uv /usr/local/bin/uv

WORKDIR /app
# The HF progress bar draws block glyphs, which crash `az acr build`'s log
# streamer on a Windows cp1252 console. Build logs want lines, not animation.
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy HF_HOME=/opt/hf \
    HF_HUB_DISABLE_PROGRESS_BARS=1

# Dependencies first so a code edit does not re-resolve (or re-download) torch.
# pyproject pins torch to download.pytorch.org/whl/cpu -- see the note there.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# Bake the embedding weights (~130MB) into the image. min-replicas 0 means every
# idle period ends in a cold start, and a model download on that path would put
# the user behind a Hugging Face round trip before the first token.
RUN /app/.venv/bin/python -c \
    "from sentence_transformers import SentenceTransformer; \
     SentenceTransformer('BAAI/bge-small-en-v1.5')"

# ---------- frontend build ----------
# Build the React + TypeScript frontend bundle with Vite.
FROM node:22-slim AS frontend

WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# ---------- runtime ----------
FROM python:3.11-slim

# HF_HUB_OFFLINE turns "the weights are baked in" from an intention into a
# guarantee: if anything ever tries to fetch at runtime it fails loudly here
# rather than quietly adding seconds to a cold start in production.
ENV PATH=/app/.venv/bin:$PATH \
    PYTHONPATH=/app/src \
    PYTHONUNBUFFERED=1 \
    HF_HOME=/opt/hf \
    HF_HUB_OFFLINE=1 \
    PORT=8000

RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin app

WORKDIR /app
COPY --from=build --chown=app:app /app/.venv /app/.venv
COPY --from=build --chown=app:app /opt/hf /opt/hf
# Runtime needs the rule tables, the manifest that pins document versions, and
# the compiled React frontend. The source PDFs and the golden set are
# ingestion- and test-time only, so they stay out of the image.
COPY --chown=app:app src/ src/
COPY --chown=app:app data/rules/ data/rules/
COPY --chown=app:app data/raw/manifest.json data/raw/
COPY --from=frontend --chown=app:app /app/src/app/static/ src/app/static/

USER app
EXPOSE 8000

# /health touches Postgres but never the LLM, so a failing check means the
# container is genuinely unwell -- it cannot be tripped by a spent token budget.
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD python -c "import urllib.request,sys; \
        sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', \
        timeout=4).status == 200 else 1)"

CMD ["uvicorn", "app.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
