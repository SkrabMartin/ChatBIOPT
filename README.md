# BioResearch AI

Evidence-based research workspace for biology and bioinformatics: verifiable literature and data sources, grounded AI answers with citations, and reproducible analyses. Research and education use only, not for clinical decisions.

> **Status: M01 Foundation.** Only the platform skeleton exists so far: API, database, migrations, web UI shell, tests and CI. There is no literature search, LLM or analysis code yet; see the [roadmap](#roadmap).

## Stack

| Layer | Technology |
|---|---|
| Frontend | React 19, TypeScript, Vite, Tailwind CSS 4, React Router, TanStack Query, Zod |
| Backend | Python 3.12, FastAPI, Pydantic v2, SQLAlchemy 2 (async, psycopg 3), Alembic |
| Database | PostgreSQL 17 with pgvector (`pgvector/pgvector:pg17`) |
| Runtime | Docker Compose; nginx serves the SPA and proxies `/api` to the backend |
| Quality | pytest, Ruff, mypy (strict), ESLint, Vitest + Testing Library, GitHub Actions |

```mermaid
flowchart LR
    B[Browser] --> N[nginx :8080<br/>SPA + /api proxy]
    N --> A[FastAPI :8000<br/>/api/v1]
    A --> P[(PostgreSQL + pgvector)]
```

## Quick start (Docker)

Requirements: Docker with Compose v2.

```bash
cp .env.example .env        # optional: defaults work for local development
docker compose up -d --build --wait
```

| URL | What |
|---|---|
| http://localhost:8080 | Web UI (dashboard with live system status) |
| http://localhost:8080/api/v1/health | Health through the frontend proxy |
| http://localhost:8000/api/v1/docs | OpenAPI / Swagger UI (direct to backend) |

The backend applies pending Alembic migrations on startup (`RUN_MIGRATIONS=0` disables this). Data lives in the `pgdata` volume and survives restarts; `docker compose down -v` deletes it. All ports bind to `127.0.0.1` only.

## Local development

Start only the database in Docker, run the backend and frontend natively:

```bash
docker compose up -d db

# Backend (http://localhost:8000)
cd backend
python3 -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
alembic upgrade head
uvicorn app.main:create_app --factory --reload

# Frontend (http://localhost:5173, proxies /api to :8000)
cd frontend
npm install
npm run dev
```

The backend reads configuration from environment variables or `backend/.env`:

| Variable | Default | Purpose |
|---|---|---|
| `DATABASE_URL` | `postgresql+psycopg://bioresearch:bioresearch@localhost:5432/bioresearch` | Application database |
| `APP_ENV` | `development` | `development`, `test` or `production` |
| `LOG_LEVEL` | `INFO` | JSON logs to stdout |
| `DATABASE_CONNECT_TIMEOUT_S` | `3` | DB connect timeout |
| `HEALTH_CHECK_TIMEOUT_S` | `2.0` | Upper bound for the readiness probe |
| `TEST_DATABASE_URL` | `…/bioresearch_test` | Used by pytest; the database is created automatically |

## Checks

```bash
# Backend (needs the db container running)
cd backend
ruff check . && ruff format --check . && mypy && pytest

# Frontend
cd frontend
npm run lint && npm run typecheck && npm test && npm run build
```

CI (`.github/workflows/ci.yml`) runs the same checks, then builds the Compose stack and smoke-tests the UI and `/api/v1/health` through nginx.

## API

`GET /api/v1/health` is the readiness probe. It returns `200` with `"status": "ok"` when the database answers, otherwise `503` with `"status": "degraded"`. Both responses use the same body shape:

```json
{
  "status": "ok",
  "version": "0.1.0",
  "environment": "development",
  "database": { "status": "ok", "migration_revision": "0001", "latency_ms": 1.2 }
}
```

Every response carries an `X-Request-ID` header. A valid client-supplied ID is reused, otherwise the server generates one. The same ID appears as `trace_id` in the JSON logs and in error bodies.

## Repository layout

```text
backend/
  app/
    api/v1/      # routers (HTTP parsing only)
    core/        # settings, JSON logging, request-context middleware
    db/          # declarative base, async engine/session
    schemas/     # Pydantic API contracts
  alembic/       # migrations (0001 = baseline)
  tests/
frontend/
  src/
    app/         # providers, router, layout
    features/    # feature modules (dashboard)
    lib/api/     # typed fetch client + Zod response schemas
compose.yaml
.github/workflows/ci.yml
```

## Roadmap

| Milestone | Scope |
|---|---|
| **M01 Foundation** ✅ | Docker Compose, FastAPI, React, PostgreSQL, migrations, tests, CI |
| M02 Persistent Workspace | Projects, conversations, messages; CRUD and UI |
| M03 Source Lookup | Read-only PubMed / Europe PMC search with stable IDs and provenance, no LLM |
| M04 LLM Gateway | Local Ollama or optional cloud model, streaming, usage tracking |
| M05 Evidence Ingestion | Metadata, open-access documents, chunking, versioning |
| M06 Retrieval / RAG | Embeddings, hybrid FTS + vector search, validated citations |
| M07 Quality & Evals | Gold dataset, reranking, citation support metrics, tracing |
| M08 Entity Intelligence | UniProt / NCBI / Open Targets, entity resolution |
| M09 Security & Multi-user | Auth, ACLs, data isolation, rate limits |
| M10 Analysis Lab | Object storage, validated job workers |
| M11 Research Missions | Durable multi-step workflows with approvals and budgets |
| M12 Model Research | LoRA/SFT experiments behind measured release gates |

## Principles

- No citation without a verified upstream record. The system can answer "insufficient evidence".
- Database and versioned artifacts are the source of truth, not chat memory.
- Local-first: the base configuration needs no paid API.
- Measure before you optimize or fine-tune.
- Retrieved external text is untrusted data, never instructions.
