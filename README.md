# BioResearch AI

Evidence-based research workspace for biology and bioinformatics: verifiable literature and data sources, grounded AI answers with citations, and reproducible analyses. Research and education use only, not for clinical decisions.

> **Status: M04 LLM Gateway.** Projects and conversations are persisted, PubMed lookup returns provenance-tracked records, and conversations get streamed answers from a local model (Ollama), with every answer traced to a recorded run. Answers are **not yet grounded in retrieved sources**; retrieval-augmented answers with citations arrive in M05–M06 (see the [roadmap](#roadmap)).
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

### Local models (optional)

The app runs without a model; chat answers then fail with a clear "model unavailable" message. To enable them, run Ollama in Compose and pull the models listed in `backend/model_registry.yaml`:

```bash
docker compose -f compose.yaml -f compose.ai.yaml up -d --build --wait                         # CPU
docker compose -f compose.yaml -f compose.ai.yaml -f compose.gpu.yaml up -d --build --wait     # NVIDIA GPU
docker compose -f compose.yaml -f compose.ai.yaml exec ollama ollama pull qwen2.5:1.5b         # default profile
docker compose -f compose.yaml -f compose.ai.yaml exec ollama ollama pull qwen2.5:7b           # optional, ~6 GB (V)RAM
```

An Ollama already running on the host works too: plain `compose.yaml` points the backend at `host.docker.internal:11434`. **Settings** in the UI shows which models are pulled and lets each browser pick one.

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
| `MODEL_REGISTRY_PATH` | `backend/model_registry.yaml` | Model profiles: provider, model, context window, license, data policy, prices |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Ollama endpoint for `provider: ollama` profiles |
| `ALLOW_EXTERNAL_MODELS` | `false` | Must be `true` before any `data_policy: external` profile (hosted API) can be used |
| `MODEL_READ_TIMEOUT_S` | `120` | Max silence between streamed chunks (includes model load on first use) |
| `RUN_MAX_DURATION_S` | `300` | Hard limit for one answer |

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
  "version": "0.4.0",
  "environment": "development",
  "database": { "status": "ok", "migration_revision": "0004", "latency_ms": 1.2 }
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

### AI runs (model gateway)

| Method & route | Purpose |
|---|---|
| `GET /api/v1/models` | Registry profiles with policy gate, live availability and resolved revision (Ollama digest) |
| `POST /api/v1/chat/{conversation_id}/runs` | `{model_profile?}` → `202` with a queued run that answers the latest user message |
| `GET /api/v1/runs/{id}/events` | Server-sent events: `status`, `delta {text}`, then one of `completed {run, message}`, `failed {error_code, message, run}`, `cancelled {run}` |
| `GET /api/v1/runs/{id}` · `GET /api/v1/conversations/{id}/runs` | Run records |
| `POST /api/v1/runs/{id}/cancel` | Stop an active run |

How it works:

- **Provider-neutral gateway.** Adapters exist for Ollama (`/api/chat` NDJSON) and for OpenAI-compatible servers (`/v1/chat/completions` SSE), which covers vLLM and hosted APIs. Models are declared in `model_registry.yaml`. Each profile records the exact model, context window, output limit, license, deployment, data policy and price. Ollama always receives `num_ctx`, because otherwise it silently truncates long prompts.
- **Context.** Each run sends the versioned system prompt (`research-assistant-v1`) plus the newest messages that fit the context window (estimated at 3 characters per token). The prompt tells the model it has no retrieval and must not invent citations. If the latest question alone does not fit, the run fails with `context_too_long` instead of sending a truncated question.
- **Traceability.** Every run records:
  - the profile, model, resolved revision, prompt version and config hash;
  - how many messages of context it used;
  - input/output tokens, estimated cost, time to first token, total latency, and the finish reason or error;
  - the trace ID.

  The assistant message links to its run through `run_id`, and the UI labels it as model output that is not grounded in sources.
- **No fake answers.** If the model is unavailable, times out, errors mid-stream, returns nothing or is cancelled, the run ends as `failed` or `cancelled` with an error code. In those cases no assistant message is stored, not even partial text. Failures stay visible after a reload and can be retried.
- **Concurrency.** A partial unique index allows only one queued or running run per conversation (a second request returns `409`).
- **Restarts.** Runs execute in an in-process task, separate from the HTTP request, so closing the browser does not stop them. Events can be resumed with `Last-Event-ID`. After a restart, finished runs replay their outcome from the database, and runs a crash left active are closed as `interrupted`.
- **Data policy.** Profiles with `data_policy: external` cannot be used unless `ALLOW_EXTERNAL_MODELS=true`, and they also need their API-key variable set. The shipped registry contains only local and self-hosted profiles. To add a hosted model, copy the vLLM entry and set `deployment: cloud`, `data_policy: external`, `base_url`, `model`, `api_key_env` and the provider's prices.
- **Rendering.** Model Markdown is rendered without raw HTML or images. Links appear as inert text, so an unverified URL never looks like a citation.

## Repository layout

```text
backend/
  app/
    api/v1/      # routers (HTTP parsing only)
    core/        # settings, JSON logging, request-context middleware, error envelope
    db/          # declarative base, async engine/session
    models/      # SQLAlchemy models (workspace, literature sources)
    schemas/     # Pydantic API contracts
    services/    # domain operations (projects, conversations, literature, runs)
    integrations/# external providers: shared rate limit/retry, PubMed client + parser
    ai/          # model registry, gateway adapters + router, versioned prompts, context builder, run executor
  alembic/       # migrations (0001 baseline, 0002 workspace, 0003 literature, 0004 runs)
  model_registry.yaml
  tests/         # fixtures/pubmed: recorded PubMed payloads (abstracts/affiliations replaced)
frontend/
  src/
    app/         # providers, router, layout
    components/  # shared UI primitives
    features/    # dashboard, projects, chat, literature, settings
    lib/api/     # typed fetch client + Zod response schemas
compose.yaml       # db, backend, frontend
compose.ai.yaml    # optional Ollama service
compose.gpu.yaml   # optional NVIDIA GPU for Ollama
.github/workflows/ci.yml
```

## Roadmap

| Milestone | Scope |
|---|---|
| **M01 Foundation** ✅ | Docker Compose, FastAPI, React, PostgreSQL, migrations, tests, CI |
| **M02 Persistent Workspace** ✅ | Projects, conversations, messages; CRUD and UI |
| **M03 Source Lookup** ✅ | Read-only PubMed search with stable IDs and provenance, no LLM |
| **M04 LLM Gateway** ✅ | Local Ollama / OpenAI-compatible gateway, streaming runs, model registry, usage tracking |
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
