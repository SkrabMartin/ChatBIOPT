# BioResearch AI

Evidence-based research workspace for biology and bioinformatics: verifiable literature and data sources, grounded AI answers with citations, and reproducible analyses. Research and education use only, not for clinical decisions.

> **Status: M03 Source Lookup.** Projects, conversations and messages are persisted, and a read-only PubMed literature search returns normalized, provenance-tracked records with links to the originals. There is no LLM yet: messages are saved, not answered (see the [roadmap](#roadmap)).
>
> **Single-user, local mode.** There is no authentication until M09. Never expose the API or UI beyond `localhost`.

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
| http://localhost:8080 | Web UI: dashboard, projects, conversations |
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
| `NCBI_EMAIL` | — | Contact sent to NCBI with every request (requested by NCBI usage policy) |
| `NCBI_API_KEY` | — | Optional; raises the PubMed rate limit from 3 to 10 requests/s |
| `NCBI_TIMEOUT_S` | `10` | Per-request timeout for E-utilities |
| `NCBI_MAX_ATTEMPTS` | `3` | Attempts per request for timeouts, connection errors, 429 and 5xx |

## Checks

```bash
# Backend (needs the db container running)
cd backend
ruff check . && ruff format --check . && mypy && pytest

# Frontend
cd frontend
npm run lint && npm run typecheck && npm test && npm run build
```

CI (`.github/workflows/ci.yml`) runs the same checks, then builds the Compose stack and smoke-tests the UI, `/api/v1/health` and workspace persistence across a restart through nginx.

Backend tests never call NCBI: they use recorded fixtures and a fake transport. A separate contract test checks the real PubMed API and runs only when you opt in:

```bash
cd backend && RUN_LIVE_TESTS=1 pytest -m live
```

## API

`GET /api/v1/health` is the readiness probe. It returns `200` with `"status": "ok"` when the database answers, otherwise `503` with `"status": "degraded"`. Both responses use the same body shape:

```json
{
  "status": "ok",
  "version": "0.3.0",
  "environment": "development",
  "database": { "status": "ok", "migration_revision": "0003", "latency_ms": 1.2 }
}
```

Every response carries an `X-Request-ID` header. A valid client-supplied ID is reused, otherwise the server generates one. The same ID appears as `trace_id` in the JSON logs and in error bodies.

### Workspace

| Method & route | Purpose |
|---|---|
| `GET /api/v1/projects?limit=&offset=` | List projects, most recently updated first |
| `POST /api/v1/projects` | Create `{title, description?}` |
| `GET` / `PATCH` / `DELETE /api/v1/projects/{id}` | Read / partial update / delete (cascades to conversations and messages) |
| `GET /api/v1/projects/{id}/conversations?limit=&offset=` | List a project's conversations |
| `POST /api/v1/projects/{id}/conversations` | Create `{title}` |
| `GET` / `PATCH` / `DELETE /api/v1/conversations/{id}` | Read / rename / delete (cascades to messages) |
| `GET /api/v1/conversations/{id}/messages?after_sequence=&limit=` | Messages in `sequence` order; cursor pagination with `has_more` |
| `POST /api/v1/conversations/{id}/messages` | Append `{content}` as a `user` message |

Contract rules:

- **Titles:** 1–200 characters after trimming. Descriptions are at most 5000 characters, and a blank description is stored as `null`. Message content is 1–20000 characters, must not be blank, and is stored verbatim.
- **Unknown fields** in request bodies are rejected with `422`.
- **`PATCH` is partial:** omitted fields stay unchanged. `"description": null` clears the description; `"title": null` is rejected.
- **Message order:** each message gets a per-conversation `sequence` (1, 2, 3, …). Sequences are unique and have no gaps, even when clients post concurrently. Clients can only author `user` messages; `assistant` messages will be written server-side by AI runs (M04).
- **List pages:** `{items, total, limit, offset}`, with `limit` 1–200. Message pages are `{items, has_more}`, with `limit` 1–500.
- **Errors:** every error uses one envelope:

```json
{ "error": { "code": "validation_error", "message": "Request validation failed", "trace_id": "…",
             "details": [{ "loc": ["body", "title"], "msg": "String should have at least 1 character", "type": "string_too_short" }] } }
```

Codes are `validation_error` (422), `not_found` (404), `method_not_allowed` (405), `upstream_unavailable` (502), `upstream_invalid_response` (502), `upstream_timeout` (504) and `internal_error` (500).

### Literature (PubMed, read-only)

| Method & route | Purpose |
|---|---|
| `POST /api/v1/literature/search` | `{query, limit?, offset?}` returns normalized PubMed records in PubMed's relevance order |
| `GET /api/v1/literature/pubmed/{pmid}?refresh=` | Stored snapshot of a record; fetched from PubMed if unknown or when `refresh=true` |

How a search runs:

1. ESearch returns the matching PMIDs.
2. EFetch returns the full records as XML.
3. The XML is parsed safely with `defusedxml` and normalized (`normalizer_version: pubmed-v1`).
4. Each record is upserted into `sources`, together with its canonical raw XML and a SHA-256 checksum, and its identifiers go into `source_identifiers`.
5. The search itself is logged in `literature_searches`: query, PubMed's query translation, total count, ranked result IDs and retrieval time.

Guarantees:

- **Links come only from validated identifiers.** A PMID must be digits, a DOI must match `10.x/…` and a PMCID must be `PMC…`. A malformed identifier is dropped, never linked.
- **A provider failure is an error, never an empty result.** Timeouts, outages and malformed payloads return `502`/`504`, and nothing from that request is stored.
- **Rate limits are respected.** Requests are spaced to NCBI's limit (3/s, or 10/s with an API key). Timeouts, connection errors, 429 and 5xx are retried with exponential backoff and jitter; other 4xx are not retried. The limiter is per process, which is correct for the single-worker deployment.
- **PubMed's paging limit is enforced.** ESearch only pages through the first 10,000 hits, so `offset + limit` must not exceed 10,000.
- **Every record carries its provenance:** `retrieved_at`, `raw_payload_sha256` and `normalizer_version`.

## Repository layout

```text
backend/
  app/
    api/v1/      # routers (HTTP parsing only)
    core/        # settings, JSON logging, request-context middleware, error envelope
    db/          # declarative base, async engine/session
    models/      # SQLAlchemy models (workspace, literature sources)
    schemas/     # Pydantic API contracts
    services/    # domain operations (projects, conversations, literature)
    integrations/# external providers: shared rate limit/retry, PubMed client + parser
  alembic/       # migrations (0001 baseline, 0002 workspace, 0003 literature)
  tests/         # fixtures/pubmed: recorded PubMed payloads (abstracts/affiliations replaced)
frontend/
  src/
    app/         # providers, router, layout
    components/  # shared UI primitives
    features/    # dashboard, projects, chat, literature
    lib/api/     # typed fetch client + Zod response schemas
compose.yaml
.github/workflows/ci.yml
```

## Roadmap

| Milestone | Scope |
|---|---|
| **M01 Foundation** ✅ | Docker Compose, FastAPI, React, PostgreSQL, migrations, tests, CI |
| **M02 Persistent Workspace** ✅ | Projects, conversations, messages; CRUD and UI |
| **M03 Source Lookup** ✅ | Read-only PubMed search with stable IDs and provenance, no LLM |
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
