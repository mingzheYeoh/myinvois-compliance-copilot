# Frontend

A mobile-first single-page app for business owners on phones. Vite, React, TypeScript —
zero UI libraries, zero state-management libraries. Summarised in the
[README](../README.md#frontend).

## Features

- **Ask** — multi-turn chat with an in-memory `thread_id`, so a profile-collection flow
  survives across turns. Shows the route badge (General / Applicability / Field Check),
  the citations as buttons that open the source text, and callout styling for
  "confirm with LHDN" notices.
- **Check Invoice** — deterministic validation against Appendix 1 via `/validate`. Spends
  no tokens and keeps working when the daily budget is exhausted. Offers a quick form for
  common fields and a raw JSON editor.
- **Header and health** — active guideline versions and the live token-budget meter from
  `/health`. On a cold start it shows a "~35s waking up" indicator and polls `/health`
  until `status == "ok"` before enabling the assistant.
- **Error handling** — a client-side character counter keeps input under the 2,000-char
  limit rather than discovering it as a 413; 429 quota exhaustion (with its reset time) is
  distinguished from rate-limit throttling; retry buttons never lose typed input.

## Build

```powershell
cd frontend
npm install
npm run dev     # Vite dev server, proxying /chat /chunk /feedback /validate /health to :8000
npm run build   # compiles into src/app/static/
```

FastAPI serves the compiled bundle from `src/app/static/` at `/`, with an SPA fallback for
client-side routes.

## Image size

`Dockerfile` is multi-stage: `node:22-slim` compiles the assets, which are copied into the
Python runtime image. Node and the build dependencies are discarded across stages.

- previous static asset: `static/index.html`, 5.0 KB
- frontend bundle: `src/app/static/`, ~175 KB uncompressed, 52 KB gzipped
- runtime image delta: **+174 KB**, about 0.01% of the image
