---
title: ClaimFlow API
emoji: 🩻
colorFrom: blue
colorTo: indigo
sdk: docker
app_port: 8000
pinned: false
---

# ClaimFlow API (demo backend)

FastAPI backend for the ClaimFlow demo: 3-stage human-in-the-loop medical claims
processing with trained imaging CNNs, LLM-assisted analysis, retrieval, and a
hash-chained audit log. The demo database reseeds on every boot (ephemeral by
design). Interactive API docs at `/docs`.

Frontend lives on Vercel and proxies `/api/*` here. Built from
https://github.com/Minifigures/claimflow (see its README for the full system).

Space variables expected: `MODEL_BACKEND=real`, `APP_ORIGIN=<vercel url>`,
`COOKIE_SECURE=true`; secrets: `JWT_SECRET`, `GEMINI_API_KEY` (free-tier live
LLM lane; deterministic fallbacks when absent).
