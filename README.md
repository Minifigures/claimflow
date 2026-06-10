# ClaimFlow

A human-in-the-loop medical insurance claims prototype: three review portals, three ML-assisted analysis stages, one auditable workflow. Claimants submit imaging claims (X-ray / CT / MRI); an imaging specialist, a medical specialist, and an insurance agent each review machine-generated analysis before any decision is made. The final decision dispatches the claimant's notification email atomically.

## How it works

```
Claimant upload ──> [Stage 1] modality classification + authenticity forensics
                              + drafted diagnostic report ──> Imaging specialist
                              (forward / return to claimant)
                ──> [Stage 2] LLM recommendation note over all case documents
                              ──> Medical specialist (send to insurer / further testing)
                ──> [Stage 3] adjudication summary: claimant history (SQL) +
                              anonymized similar-case precedents (vector retrieval)
                              ──> Insurance agent (approve / reject + email, atomic)
```

Every workflow transition, model call, retrieval, and email is recorded in a hash-chained, actor-aware audit log; tampering with any record (including *who* acted) breaks the chain.

## Quickstart

```bash
cp .env.example .env
docker compose up --build      # or: make demo
# frontend http://localhost:3000 — backend API http://localhost:8000
```

Runs fully **keyless** by default: every ML/LLM stage degrades to deterministic, schema-identical fallbacks, and email is logged to the console provider and surfaced in the UI. Set `ANTHROPIC_API_KEY` in `.env` for live Claude analysis (stage routing in `backend/app/llm/routing.py`).

| Portal | Login | Password |
|---|---|---|
| Claimant | `claimant@demo.ca` | `demo1234` |
| Imaging specialist | `imaging@demo.ca` | `demo1234` |
| Medical specialist | `specialist@demo.ca` | `demo1234` |
| Insurance agent | `agent@demo.ca` | `demo1234` |

Demo data is pre-seeded with a claim parked at every workflow stage — including a tampered X-ray with per-signal forensic findings, and a French-language claimant so the decision modal drafts a bilingual notification.

Without Docker: `make install && make seed && make dev-api` and `make dev-web` (Node 20+, Python 3.11+, uv).

## What's real

- **Modality classifier**: EfficientNet-B0 fine-tuned on 15,000 ROCOv2 radiology images (5,000/class, CUI-derived labels), temperature-calibrated. `MODEL_BACKEND=real` serves it; `stub` (default) keeps the demo dependency-free.
- **Authenticity layer**: deterministic forensics (metadata/DICOM consistency, ELA, frequency, copy-move) fused with a CNN trained on generated tampering — capped so non-ML evidence can always override the model. Honesty caveats in [docs/model-choices.md](docs/model-choices.md).
- **LLM stages**: Claude (Sonnet vision / Opus reasoning / Haiku drafting) with structured outputs, stop-reason guardrails, per-call cost audit, and prompt-injection defenses for claimant-uploaded documents.
- **Retrieval**: per-claimant document search with isolation enforced inside the retriever, plus cross-claimant precedent retrieval through an allowlist anonymizer.

## Docs

- [docs/design.md](docs/design.md) — architecture, state machine, sequence diagrams, production path
- [docs/model-choices.md](docs/model-choices.md) — per-stage model selections and rationale, governance mapping, cost table
- Training pipeline: `backend/ml_training/` (`build_datasets`, `train_modality`, `train_authenticity`, `evaluate`, Colab notebook)

## Tests

```bash
make test   # backend: pytest (state machine matrix, audit tamper, PII guards,
            # injection cases, full lifecycle E2E) — 220+ tests
make lint
cd frontend && npx tsc --noEmit && npm run build
```

## Stack

FastAPI · SQLAlchemy 2 / SQLite (Postgres-ready) · Next.js App Router / TypeScript strict · PyTorch + timm · ChromaDB + sentence-transformers · Anthropic API (Bedrock as the documented production path)
