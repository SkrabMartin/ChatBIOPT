# BioResearch AI

Evidence-based research workspace for biology and bioinformatics: verifiable literature and data sources, grounded AI answers with citations, and reproducible analyses. Research and education use only, not for clinical decisions.

> **Status: M09 Security & Multi-user.** Projects and conversations are persisted, and PubMed lookup returns provenance-tracked records. Publications can be ingested into a project's evidence library: versioned abstracts, license-gated open-access full text, chunks and embeddings. Chat answers are grounded in that library through hybrid keyword + vector retrieval. The server validates every citation, splits answers into claims on an evidence board for human review, and abstains when no passage is relevant. An optional LLM reranker, per-stage run timings and opt-in OpenTelemetry tracing are available. Retrieval and answer quality are measured on hand-labelled development and held-out test sets, and results are browsable on the **Evaluations** page (see [Evaluation](#evaluation)). Genes can be resolved from a symbol or identifier within an explicitly chosen organism (NCBI Gene, UniProtKB, Ensembl) and pinned to a project. Each gene is stored with provenance and typed relations: `encodes` to its proteins and, for human genes, `associated_with_disease` from Open Targets (see [Entity intelligence](#entity-intelligence)). Users sign in; every project is private to its members (owner / editor / viewer), and expensive operations are rate-limited per user (see [Accounts and access control](#accounts-and-access-control)).
>
> **Self-hosted, not hardened for the open internet.** Accounts, sessions and project ACLs are in place, but there is no TLS termination, MFA or external identity provider. Put it behind HTTPS (and set `SESSION_COOKIE_SECURE=true`) before exposing it beyond `localhost`.

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
# first account (admin manages accounts; claims projects created before accounts existed)
docker compose exec backend python -m app.cli create-user --email you@example.org --name "You" --admin \
  --claim-unowned-projects    # prompts for a password (>= 12 characters)
```

Further accounts are created by an admin on the **Users** page.

| URL | What |
|---|---|
| http://localhost:8080 | Web UI: dashboard, projects, conversations |
| http://localhost:8080/api/v1/health | Health through the frontend proxy |
| http://localhost:8000/api/v1/docs | OpenAPI / Swagger UI (direct to backend) |

The backend applies pending Alembic migrations on startup (`RUN_MIGRATIONS=0` disables this). The `worker` service processes ingestion jobs. Data lives in the `pgdata` (database) and `objects` (document blobs) volumes and survives restarts; `docker compose down -v` deletes both. All ports bind to `127.0.0.1` only.

### Local models (optional)

The app runs without a model; chat answers then fail with a clear "model unavailable" message. To enable them, run Ollama in Compose and pull the models listed in `backend/model_registry.yaml`:

```bash
docker compose -f compose.yaml -f compose.ai.yaml up -d --build --wait                         # CPU
docker compose -f compose.yaml -f compose.ai.yaml -f compose.gpu.yaml up -d --build --wait     # NVIDIA GPU
docker compose -f compose.yaml -f compose.ai.yaml exec ollama ollama pull qwen2.5:1.5b         # default profile
docker compose -f compose.yaml -f compose.ai.yaml exec ollama ollama pull qwen2.5:7b           # optional, ~6 GB (V)RAM
docker compose -f compose.yaml -f compose.ai.yaml exec ollama ollama pull nomic-embed-text     # embeddings for retrieval
```

### Tracing (optional)

```bash
docker compose -f compose.yaml -f compose.observability.yaml up -d --build --wait   # add the AI/GPU overlays as needed
```

`compose.observability.yaml` adds Jaeger (UI at http://localhost:16686) and sets `OTEL_EXPORTER_OTLP_ENDPOINT` for the backend and worker. Spans cover HTTP requests, provider calls (PubMed, Europe PMC, NCBI Gene, UniProt, Ensembl, Open Targets), ingestion steps (`ingestion.job/fulltext/embed/verify`) and chat runs (`chat.run`, `chat.retrieval`, `chat.generation`). Spans carry IDs, counts, model names and status codes, never prompt or document text. Without the overlay tracing is off; each run still stores a stage breakdown in `runs.timings`.

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
| `OBJECT_STORE_PATH` | `var/objects` | Filesystem object store for document text and raw upstream payloads |
| `FULLTEXT_ALLOWED_LICENSES` | all CC licenses + CC0 | Licenses under which open-access full text may be stored |
| `EUROPEPMC_BASE_URL` | Europe PMC REST | Open-access status, license and JATS full text |
| `INGESTION_MAX_ATTEMPTS` | `3` | Attempts per ingestion job for transient provider failures |
| `INGESTION_RETRY_DELAY_S` | `30` | Base delay before a retry (multiplied by the attempt number) |
| `INGESTION_LEASE_S` | `300` | Worker lease; a job whose worker died becomes claimable again after this |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | — | OTLP/HTTP collector for traces (e.g. `http://jaeger:4318`); unset = tracing off |
| `RAG_TOP_K` | `8` | Most passages placed in a grounded answer's context (fewer if the context window is too small) |
| `UNIPROT_BASE_URL` / `UNIPROT_REQUESTS_PER_SECOND` | UniProt REST / `5` | Protein entries and gene cross-references |
| `ENSEMBL_BASE_URL` / `ENSEMBL_REQUESTS_PER_SECOND` | Ensembl REST / `10` | Reciprocal Ensembl ↔ NCBI Gene checks (Ensembl allows 15/s) |
| `OPENTARGETS_URL` / `OPENTARGETS_REQUESTS_PER_SECOND` | Open Targets GraphQL / `5` | Target–disease associations (human only) |
| `SESSION_COOKIE_SECURE` | `false` | Mark the session cookie `Secure`; set `true` whenever the UI is served over HTTPS |
| `SESSION_TTL_HOURS` / `SESSION_IDLE_HOURS` | `336` / `72` | Absolute session lifetime / sign-out after this long unused |
| `RATE_LIMIT_LOGIN_PER_MINUTE` | `10` | Sign-in attempts per client IP and per email |
| `RATE_LIMIT_MODEL_PER_MINUTE` | `30` | Chat runs and evidence searches per user |
| `RATE_LIMIT_PROVIDER_PER_MINUTE` | `60` | PubMed, entity provider and ingestion requests per user |
| `ENTITY_DISEASE_ASSOCIATIONS` | `10` | Strongest Open Targets associations stored per human gene (0 disables) |

## Checks

```bash
# Backend (needs the db container running)
cd backend
ruff check . && ruff format --check . && mypy && pytest

# Frontend
cd frontend
npm run lint && npm run typecheck && npm test && npm run build
```

CI (`.github/workflows/ci.yml`) runs the same checks, then builds the Compose stack and smoke-tests the UI, `/api/v1/health`, sign-in (CLI-created user, CSRF header, cookie) and workspace persistence across a restart through nginx.

Backend tests never call NCBI or the entity providers: they use recorded fixtures and a fake transport. A separate contract test checks the real PubMed API and runs only when you opt in:

```bash
cd backend && RUN_LIVE_TESTS=1 pytest -m live
```

## API

`GET /api/v1/health` is the readiness probe. It returns `200` with `"status": "ok"` when the database answers, otherwise `503` with `"status": "degraded"`. Both responses use the same body shape:

```json
{
  "status": "ok",
  "version": "0.9.0",
  "environment": "development",
  "database": { "status": "ok", "migration_revision": "0009", "latency_ms": 1.2 }
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

Codes are `validation_error` (422), `unauthenticated` (401), `invalid_credentials` (401), `forbidden` (403), `csrf_failed` (403), `not_found` (404), `method_not_allowed` (405), `conflict` (409), `last_owner` (409), `rate_limited` (429, with `Retry-After`), `taxon_mismatch` (409), `upstream_unavailable` (502), `upstream_invalid_response` (502), `upstream_timeout` (504) and `internal_error` (500).

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

### Evidence ingestion

| Method & route | Purpose |
|---|---|
| `POST /api/v1/projects/{id}/sources` | `{identifier: "<PMID>"}` → `202` with an ingestion job; returns the active job instead if one exists |
| `GET /api/v1/projects/{id}/sources` | Evidence library: sources, latest document version per kind, chunk counts, latest job (with step log) |
| `POST /api/v1/projects/{id}/sources/{source_id}/refresh` · `DELETE …/{source_id}` | Re-ingest · unlink (documents are kept) |
| `GET /api/v1/ingestion-jobs/{id}` | Job status, attempts and per-step log |
| `GET /api/v1/sources/{id}/documents` · `GET /api/v1/documents/{id}` · `GET /api/v1/chunks/{id}` | All versions · one version with text, sections and chunks · one chunk with its document and source |

```mermaid
flowchart LR
    API[POST sources] -->|queued job| Q[(ingestion_jobs)]
    Q -->|FOR UPDATE SKIP LOCKED| W[worker]
    W --> M[PubMed metadata] --> A[abstract document]
    W --> O[Europe PMC: OA status + license] -->|allowed license only| F[JATS full text]
    A & F --> C[section/sentence chunks]
    C --> V[verify checksums + offsets]
    A & F -.->|text + raw payload| S[(object store)]
```

- **Durable queue.** Jobs live in PostgreSQL and are processed by a separate `worker` process (`python -m app.workers.ingestion`), never inside an HTTP request.
  - Workers claim jobs with `FOR UPDATE SKIP LOCKED` and hold a lease, so a crashed worker's job is picked up again.
  - Transient provider failures are retried with a growing delay, up to `INGESTION_MAX_ATTEMPTS`. Permanent ones, such as an unknown PMID, fail immediately.
  - Each step is committed and logged as it finishes, so the abstract from a partly failed attempt is kept.
- **License gate.** Bibliographic metadata does not grant the right to archive full text. Full text is downloaded only when Europe PMC marks the article open access *and* gives an explicit, recognised license from `FULLTEXT_ALLOWED_LICENSES`. The license declared inside the JATS XML is checked again. An article that is "open access" without a license (for example an NIH author manuscript) stays abstract-only, and the job log says why.
- **Versioning and idempotency.** A document is one immutable text version of a source (`abstract` or `fulltext`). Re-ingesting identical text changes nothing; changed text becomes version *n+1*. Old versions and their chunks are never deleted, so anything that cited them still resolves to the exact text that was seen. The two kinds of documents are stored differently:
  - **Database:** checksums, license, origin URL and parser version.
  - **Object store:** the normalized text and the raw upstream payload, under content-addressed keys.
- **Chunking (`section-sentence-v1`).** Each chunk records its section kind and heading path plus `[start, end)` offsets into the stored text. A verification step checks after every ingestion that `text[start:end] == chunk.text` and that the blobs still match their checksums.
  - Chunks stay within one section, so Methods and Results passages are never mixed.
  - Chunks end on a sentence boundary, without splitting at abbreviations such as "et al." or "Fig.".
  - Each chunk targets about 350 tokens (hard limit 600), and one sentence overlaps into the next chunk.
- **Shared, not duplicated.** Sources and documents are public data shared across projects, and a project only links to them. Two projects ingesting the same PMID produce one set of documents.
- **Embeddings.** The last ingestion step embeds the chunks of each source's latest versions into the current embedding space, named `<profile>@<model digest>` (for example `nomic-embed@sha256:0a10…`). A different model or model revision is a new space, and chunks are re-embedded into it rather than mixed with old vectors. If the embedding model is unavailable, ingestion still succeeds: the step is logged as skipped, keyword retrieval keeps working, and a later refresh fills in the missing vectors.

### Retrieval and grounded answers

| Method & route | Purpose |
|---|---|
| `POST /api/v1/retrieval/query` | `{project_id, query, mode: keyword\|semantic\|hybrid, top_k, rerank_profile?}` → ranked passages with each retriever's rank, keyword term coverage, similarity, optional rerank grade and whether the hit counts as evidence |
| `POST /api/v1/chat/{id}/runs` | now also accepts `grounding: auto\|project\|none` (`auto` grounds when the project library has passages), `rerank: bool` and `rerank_profile`; the run records `rerank_profile` and `timings` (retrieval / rerank / generation ms) |
| `GET /api/v1/evaluations?kind=retrieval\|answers\|entities` | Recorded evaluation runs (newest first) with per-configuration headline metrics |
| `GET /api/v1/evaluations/{id}` | Full report: dataset hash, commit, per-configuration metrics with CIs, per-item results |
| `GET /api/v1/runs/{id}/evidence` | Passages given to a grounded run as `[E#]` labels, with ranks and whether the answer cited them |
| `GET /api/v1/projects/{id}/claims?review_status=&format=json\|csv` | Evidence board: answer sentences with their cited passages; CSV export |
| `PATCH /api/v1/claims/{id}` | `{review_status: requires_review\|verified\|disputed\|rejected, review_note?}` |

```mermaid
flowchart LR
    Q[question] --> K[keyword: PostgreSQL FTS] & S[semantic: pgvector cosine]
    K & S --> F[RRF fusion] --> G{evidence?}
    G -->|none| X[abstain: insufficient evidence, no model call]
    G -->|E1..En| P[grounded prompt] --> M[model] --> V[validate citations, extract claims]
    V --> B[(evidence board)]
```

- **Scope.** Retrieval only searches the latest document version of each source linked to the project. When a full text exists it is used instead of the abstract, since the full text already contains the abstract. Another project's library is never searched, through either index.
- **Hybrid retrieval (`hybrid-rrf-v1`).** Two retrievers each return up to 50 candidates and are combined with Reciprocal Rank Fusion (k = 60). Raw keyword and cosine scores are never compared directly.
  - **Keyword:** PostgreSQL full-text search with English stemming. Exact identifiers such as `R175H` or `rs…` stay whole tokens.
  - **Semantic:** cosine similarity over `nomic-embed-text` vectors, with its `search_query:` / `search_document:` prefixes.
  - **Fallback:** if the embedding model is unavailable, hybrid falls back to keyword search and says so in `warnings`.
- **What counts as evidence.** A passage counts in either of two cases:
  - its keyword match covers at least half of the question's terms (stop words removed), so a single shared word like "role" is not enough;
  - its semantic similarity is at least the profile's `min_similarity` (0.70 for nomic, calibrated on the dev set: off-topic questions scored 0.48–0.66).

  If nothing counts, the run abstains with `answer_class: insufficient_evidence` and the model is not called.
- **Grounded prompt (`grounded-answer-v1`).** Up to `RAG_TOP_K` passages are sent as labelled `[E1]…[En]` blocks inside `<evidence>`, together with any conversation history that still fits the context window. Passage text cannot close that block, and the prompt treats it as untrusted data, never as instructions. The model must start with `STATUS: supported | partially_supported | conflicting | insufficient_evidence` and cite labels after each sentence.
- **Citation validation.** The server keeps only labels that were actually given to this run. Invented labels are removed and counted in `citation_stats.invalid_markers`. The answer is then split into sentence-level claims, each linked to the passages it cites. A model abstention creates no claims.
- **Answer classes.** A run gets one of these classes:
  - `supported` / `conflicting` / `insufficient_evidence`: as the model declared.
  - `partially_supported`: some sentences cite nothing.
  - `unclassified`: no valid citation at all.
  - `tool_error`: retrieval itself failed. This is a failed run with error code `tool_error`, never reported as "no evidence".
- **What a citation means.** A valid citation only shows which passage the model pointed at, not that the passage supports the sentence. Every claim starts as `requires_review` on the **evidence board**, where you can verify, dispute or reject it.
- **Reproducibility.** Each run also records `grounding`, `retrieval_version`, `embedding_space`, `answer_class`, `citation_stats`, the ranked passages it was given (`run_evidence`) and its claims. The config hash covers the grounded prompt, the chunker, the embedding profile and the rerank profile. `runs.timings` holds per-stage milliseconds (`retrieval_ms`, `rerank_ms`, `generation_ms`) and rerank token usage.
- **Reranking (optional, off by default).** A rerank profile in `model_registry.yaml` (`llm-listwise-small`, `llm-listwise-general`) asks a registry model to grade up to 20 fused candidates 0–3 in one JSON-mode call (`llm-listwise-v1`); passages are fenced as untrusted data. Hits are re-sorted by grade, ties keep RRF order, and the grade is stored as `rerank_score`. If the model fails or returns no usable grades, the fused order is kept and the run shows `Reranking skipped: …`. See [Evaluation](#evaluation) for why it is off.
- **Identifier validation.** PMIDs, PMCIDs, DOIs and URLs in an answer that do not belong to the run's evidence sources are replaced with `[unverified reference removed]` and counted in `citation_stats.unverified_identifiers`.

### Accounts and access control

| Method & route | Purpose |
|---|---|
| `POST /api/v1/auth/login` | `{email, password}` → user; sets the session cookie. Public, rate-limited per IP and per email |
| `POST /api/v1/auth/logout` | Revoke this session (server side) |
| `GET /api/v1/auth/me` | The signed-in user |
| `POST /api/v1/auth/password` | `{current_password, new_password}`; signs out your other sessions |
| `GET` / `POST /api/v1/projects/{id}/members` | List members (any member) / add an existing user by `{email, role}` (owner) |
| `PATCH` / `DELETE /api/v1/projects/{id}/members/{user_id}` | Change role / remove (owner); any member may remove themself (leave) |
| `GET` / `POST /api/v1/admin/users`, `PATCH /api/v1/admin/users/{id}` | Admins: list, create, rename, reset password, grant admin, disable |

```mermaid
flowchart LR
    R[request] --> C{CSRF header on POST/PATCH/DELETE?}
    C -->|no| F1[403 csrf_failed]
    C -->|yes| S{valid session?}
    S -->|no| U[401 unauthenticated]
    S -->|yes| P{member of the resource's project?}
    P -->|no| N[404 not found]
    P -->|yes| O{role sufficient?}
    O -->|no| F2[403 forbidden]
    O -->|yes| H[handler: queries scoped to the project]
```

- **Authentication.** Local accounts with Argon2id password hashes (minimum 12 characters). Signing in creates a server-side session; the browser gets an opaque random token in an `HttpOnly`, `SameSite=Lax` cookie (`Secure` with `SESSION_COOKIE_SECURE=true`), and only the token's SHA-256 is stored. Sessions end on logout, after 14 days, after 72 hours unused, when the password changes (other sessions) and when an admin disables the account or resets its password. Unknown email and wrong password fail identically and take the same time.
- **CSRF.** `POST`, `PATCH` and `DELETE` (sign-in included) must carry `X-Requested-With: BioResearchAI`. A cross-site page cannot add that header without a CORS preflight, which the API never approves.
- **Authorization.** Only `/health` and `/auth/login` are public. A project and everything in it (conversations, messages, runs and their SSE streams, run evidence, claims, ingestion jobs, pinned genes) is visible only to its members. Non-members get `404`, so they cannot tell whether a resource exists; members lacking the role get `403`. Roles: **viewer** reads; **editor** also chats, runs models, edits the library, reviews claims and pins genes; **owner** also edits or deletes the project and manages members. A project always keeps at least one owner (`409 last_owner`).
- **Shared data.** Library sources, documents and chunks are shared between projects (a PubMed record is stored once). They are readable only if linked to a project you are a member of, and retrieval only searches the requested project's library after checking membership. Literature search, entity resolution and stored entities (public reference data from NCBI, UniProt and Open Targets) and evaluation reports are available to every signed-in user; which projects pinned an entity is not exposed.
- **Admins** manage accounts only. Being an admin grants no access to other users' projects.
- **Rate limits.** Per user: model calls (chat runs, evidence search) and external-provider calls (PubMed, entity providers, adding sources). Per client IP and per email: sign-in attempts. Exceeding a limit returns `429 rate_limited` with `Retry-After`. Limits are in-process, matching the single-worker deployment; several API replicas would need a shared store.
- **Upgrading from M08.** Projects created before accounts existed have no members and are invisible until someone claims them: `python -m app.cli create-user … --claim-unowned-projects`.
- **Regression suite.** `tests/test_security.py` classifies every API route (a new route fails until it is classified and declares its role) and probes a fully populated project as an anonymous client, without the CSRF header, as a non-member, as a viewer and as an editor.

### Entity intelligence

| Method & route | Purpose |
|---|---|
| `GET /api/v1/entities/organisms` | Organisms offered for resolution (any NCBI Taxonomy ID is accepted) |
| `POST /api/v1/entities/resolve` | `{query, taxon_id, kind?: auto\|symbol\|ncbigene\|ensembl\|hgnc\|uniprot}` → status, candidate genes, matches in other organisms, warnings, provider releases. Read-only |
| `GET /api/v1/projects/{id}/entities` | Genes pinned to the project, with the input they were resolved from and import warnings |
| `POST /api/v1/projects/{id}/entities` | `{gene_id, taxon_id, query?, match_type?}` → import the gene and its relations, then pin it (`201`). Repeating it refreshes the snapshot |
| `DELETE /api/v1/projects/{id}/entities/{entity_id}` | Unpin; the stored entity stays for other projects |
| `GET /api/v1/entities/{id}` | Entity with cross-references, attributes, provenance and typed edges in both directions |

```mermaid
flowchart LR
    I[symbol or ID + organism] --> R{resolve}
    R -->|symbol| NG[NCBI Gene: symbol/alias within taxon]
    R -->|ENSG…| EN[Ensembl xref → NCBI Gene ID]
    R -->|HGNC:… / UniProt| UP[UniProtKB xref → NCBI Gene ID]
    NG & EN & UP --> C{organism matches?}
    C -->|no| M[taxon_mismatch]
    C -->|yes| S[resolved / ambiguous]
    S -->|user picks| G[(gene)] -->|encodes| P[(protein: UniProtKB)]
    G -->|associated_with_disease, human only| D[(disease: Open Targets)]
```

- **Organism is part of the identity.** A symbol is only searched within the requested taxon: `TP53` in mouse resolves to *Trp53* (NCBI Gene 22059, via its alias), never to human 7157. Identifiers are looked up without an organism filter and then compared, so a human ID requested for mouse is `taxon_mismatch` and the response names the organism it belongs to. Orthologs are never substituted.
- **Statuses.** `resolved` (one gene), `ambiguous` (several genes share the symbol or alias; all are listed and none is picked), `not_found` (with hints when the symbol is an official symbol in another common organism), `taxon_mismatch`. Exact official-symbol matches outrank alias matches; an alias match says what the official symbol is.
- **Identifiers.** NCBI Gene IDs are looked up directly; replaced IDs follow NCBI's `currentid` (flagged), discontinued ones are not resolved. Ensembl gene IDs map through Ensembl's EntrezGene cross-references (a version suffix is reported as unchecked). HGNC IDs and UniProt accessions map through reviewed UniProtKB cross-references. An isoform suffix (`P04637-2`) is reported, since resolution is gene-level. Inactive UniProtKB accessions are reported as deleted, or followed to the entry they were merged into (flagged).
- **Import and provenance.** The gene is re-checked (live, same organism) before anything is stored. Each entity keeps the provider, its data release (NCBI Gene build, UniProt release, Open Targets data version), retrieval time and the raw provider record with its SHA-256. Every cross-reference records who asserted it. Ensembl IDs asserted by UniProt are checked reciprocally against Ensembl; an unconfirmed ID is kept but flagged and never used to query other providers.
- **Typed relations.** `encodes` (gene → protein) comes from a reviewed UniProtKB entry that cross-references the gene in the same organism. `associated_with_disease` (gene → disease) is an Open Targets association score from 0 to 1, stored with its rank, evidence-type scores and the note that it is not a measure of causality or variant effect. Diseases are normalized to CURIEs (`MONDO:0018875`).
- **Coverage.** Open Targets covers human targets only, so other organisms get `not_covered` instead of an empty list. If Open Targets is down, the import still succeeds with a warning and keeps the previous association snapshot. NCBI Gene, UniProt and Ensembl failures abort the import (`502`/`504`) and store nothing.

## Evaluation

```bash
cd backend
# retrieval: keyword vs semantic vs hybrid vs hybrid + reranker (direct DB access)
python -m app.evals.retrieval --project <project-id> --dataset ../evals/datasets/retrieval-test-v0.1.jsonl \
  --rerank llm-listwise-small --rerank llm-listwise-general --out ../evals/reports/retrieval-test-v0.1.json
# answers: no-RAG vs grounded vs grounded + reranker, through the real API, scored by an LLM judge
BIORESEARCH_EMAIL=you@example.org BIORESEARCH_PASSWORD=… \
python -m app.evals.answers --api http://127.0.0.1:8080/api/v1 --project <project-id> \
  --dataset ../evals/datasets/answers-test-v0.1.jsonl --model local-general --judge local-general \
  --rerank llm-listwise-general --out ../evals/reports/answers-test-v0.1.json
# baseline vs candidate with a paired bootstrap (one report = two configs; two reports = before/after)
python -m app.evals.compare ../evals/reports/retrieval-test-v0.1.json --baseline hybrid \
  --candidate hybrid+rerank:llm-listwise-general [--fail-on-regression]
# entities: resolution against the live NCBI Gene / UniProt / Ensembl APIs (no model, no project)
python -m app.evals.entities --dataset ../evals/datasets/entities-test-v0.1.jsonl \
  --out ../evals/reports/entities-test-v0.1.json
```

Every run is written to a JSON report and to the `evaluations` table (shown on the **Evaluations** page). Each report records the dataset SHA-256 and split, git commit, embedding space, chunker, models and judge. Metrics are means with 95% bootstrap intervals (2,000 resamples, fixed seed). Datasets have a `split` header: `dev` sets may be used for tuning (they set `min_similarity`); `test` sets are only reported. Relevance is declared by quoting passage text, so labels survive re-chunking; a run aborts if a phrase matches no chunk or the corpus differs from the dataset's.

`compare` pairs items by ID and calls a metric *improved* or *regressed* only when the 95% CI of the paired difference excludes zero.

All numbers below are for 3 TP53/glioma papers (79 chunks), `nomic-embed-text`, chunker `section-sentence-v1`, labelled by one annotator, on an RTX 3050 6 GB.

### Retrieval

Held-out `retrieval-test-v0.1` (20 queries); development set `retrieval-dev-v0.1` (24 queries) in parentheses for nDCG.

| Mode | recall@5 | recall@10 | MRR@10 | nDCG@10 (dev) | p50 / p95 ms |
|---|---|---|---|---|---|
| keyword | 0.867 [0.73–0.97] | 0.900 [0.78–1.00] | 0.796 [0.64–0.93] | 0.753 (0.692) | 3 / 8 |
| semantic | 0.817 [0.69–0.93] | 0.933 [0.85–1.00] | 0.738 [0.59–0.88] | 0.732 (0.762) | 34 / 45 |
| **hybrid** (default) | 0.933 [0.85–1.00] | 1.000 [1.00–1.00] | 0.838 [0.72–0.95] | 0.835 (0.832) | 29 / 44 |
| hybrid + rerank `llm-listwise-small` (1.5B) | 0.733 [0.56–0.88] | 0.800 [0.62–0.93] | 0.716 [0.53–0.88] | 0.652 (0.685) | 3,146 / 5,110 |
| hybrid + rerank `llm-listwise-general` (7B) | 0.850 [0.70–0.97] | 0.975 [0.93–1.00] | 0.851 [0.71–0.96] | 0.839 (0.817) | 20,809 / 21,480 |

### Answers

Held-out `answers-test-v0.1`: 8 answerable questions, 5 unanswerable from this corpus, 3 prompt-injection probes. Answers by `local-general` (qwen2.5:7b), judged by `llm-judge-v1` on the same model.

| Configuration | answer accuracy | false abstention ↓ | abstention accuracy | claim support | reference cited | citation validity | p50 / p95 ms |
|---|---|---|---|---|---|---|---|
| no RAG | 0.125 [0.00–0.38] | 0.125 | 0.40 [0.00–0.80] | — | — | — | 6,103 / 14,019 |
| **hybrid** (default) | 0.750 [0.50–0.94] | 0.250 | 1.00 | 1.000 | 0.875 | 1.000 | 14,621 / 21,496 |
| hybrid + rerank (7B) | 0.750 [0.38–1.00] | 0.250 | 1.00 | 0.929 | 1.000 | 1.000 | 25,986 / 30,785 |

Paired comparisons (`app.evals.compare`): grounding improves answer accuracy over no-RAG (Δ +0.625, CI [+0.25, +0.94]) and abstention accuracy (Δ +0.60, CI [+0.20, +1.00]). Neither reranker improves anything significantly; the 1.5B reranker significantly **regresses** recall@10 (Δ −0.20, CI [−0.35, −0.08]) and nDCG@10.

**Decision: reranking stays off** (`default_rerank_profile: null`). The 7B reranker matches hybrid quality at ~700× the retrieval latency; the 1.5B one is harmful. Both remain available per run (`rerank: true`) and in the search panel for experiments.

**Prompt injection.** Injected instructions in a passage are scored strictly: any forbidden string in the output fails, even when only quoted. The model alone resisted 1 of 3 probes. The server now removes PMIDs, PMCIDs, DOIs and URLs that do not belong to the run's evidence sources (`citation_stats.unverified_identifiers`), which raises system-level resistance to 2 of 3. The remaining failure is the model quoting a canary token while describing the passage as suspicious.

Caveats:

- The sets are tiny (8 answerable questions), so intervals are wide; treat the numbers as regression baselines, not general quality claims.
- The judge is an automated proxy on the same model family as the answerer, not an independent expert review. Claim support is judged per cited sentence.
- The 2 false abstentions under hybrid (a05, a07) are questions whose answer is spread across passages; the model declared `insufficient_evidence`.
- Latencies are with models already loaded; the 7B model and the embedding model share 6 GB of VRAM, so the first request after a model swap is much slower.

### Entities

Held-out `entities-test-v0.1`: 45 inputs across 7 organisms (human, mouse, rat, zebrafish, fly, worm, yeast). Expected NCBI Gene IDs were checked against NCBI Gene records and, for non-human genes, independently against Ensembl cross-references, not taken from the resolver. Run against NCBI Gene Build261023-2230m.1, UniProt 2026_03 and Ensembl 116.

| Category | Items | Examples | Correct |
|---|---|---|---|
| official symbol | 14 | `TP53`/human → 7157, `cep-1`/worm → 172616, `RAD51`/yeast → 856831 | 14 / 14 |
| alias | 4 | `TP53`/mouse → Trp53 22059, `HER2` → ERBB2 2064 | 4 / 4 |
| ambiguous | 2 | `p40` → MAPK1 + TP63, `DEC1` → BHLHE40 + DELEC1, none picked | 2 / 2 |
| identifier | 10 | `ENSG00000012048`, `HGNC:1100`, `P38398` → BRCA1 672; `P04637-2` → 7157 | 10 / 10 |
| cross-species | 8 | human 7157 / `ENSG00000141510` / `P04637` requested for mouse → `taxon_mismatch` | 8 / 8 |
| lifecycle | 3 | replaced 6248 → 84301, mouse 69994 → 68817, discontinued 102724970 | 3 / 3 |
| not found | 4 | `APOE`/fly (hint: human, mouse, rat), deleted UniProt `Q9ZZZ9` | 4 / 4 |

Accuracy 1.000 (45/45); wrong-organism rate 0.000 and false-resolution rate 0.000 (both must stay 0); p50 / p95 649 / 3,067 ms, bounded by NCBI's 3 requests/s without an API key. The first run found a real defect: inactive UniProtKB entries were rejected as invalid provider responses. They are now handled.

Caveats: 45 hand-picked items cannot show general coverage; they pin down the rules (organism, identifier type, lifecycle). Provider data changes between releases, so a later run can legitimately differ: every report records the releases it ran against.

## Repository layout

```text
backend/
  app/
    api/v1/      # routers (HTTP parsing only)
    core/        # settings, JSON logging, request-context middleware, error envelope, per-key rate limiter
    auth/        # accounts, sessions (Argon2id, hashed tokens), CSRF check, project-role authorization dependencies
    db/          # declarative base, async engine/session
    models/      # SQLAlchemy models (workspace, literature, evidence, retrieval, entities)
    schemas/     # Pydantic API contracts
    services/    # domain operations (projects, members, conversations, literature, runs)
    cli.py       # create-user (first admin, claim projects from before accounts)
    integrations/# external providers: shared rate limit/retry, PubMed, Europe PMC, NCBI Gene, UniProt, Ensembl, Open Targets
    entities/    # entity resolver (organism-explicit), gene import with typed edges and provenance
    ai/          # model registry, gateway adapters + router, versioned prompts, context builder, run executor
    ingestion/   # license policy, chunker, versioned document store, ingestion pipeline
    storage/     # object store (filesystem, content-addressed)
    workers/     # ingestion worker (separate process, DB job queue)
    retrieval/   # embeddings (spaces, batching), hybrid keyword + vector search with RRF, LLM listwise reranker
    evals/       # metrics with bootstrap CIs, retrieval / answer / entity runners, LLM judge, paired compare, report store
  alembic/       # migrations (0001 baseline … 0009 auth)
  model_registry.yaml
  tests/         # fixtures: recorded PubMed payloads (abstracts/affiliations replaced), Europe PMC CC BY full text,
                 # recorded NCBI Gene / UniProt / Ensembl / Open Targets responses
frontend/
  src/
    app/         # providers, router, layout
    components/  # shared UI primitives
    features/    # auth, account, admin, dashboard, projects + members, chat, literature, evidence, entities, evaluations, settings
    lib/api/     # typed fetch client + Zod response schemas
compose.yaml       # db, backend, worker, frontend
compose.ai.yaml    # optional Ollama service
compose.gpu.yaml   # optional NVIDIA GPU for Ollama
compose.observability.yaml  # optional Jaeger + OTLP tracing
.github/workflows/ci.yml
evals/
  datasets/        # gold sets (JSONL, dev/test split; relevance by quoted passage text; expected gene IDs)
  reports/         # eval reports with dataset hash, commit, embedding space
```

## Roadmap

| Milestone | Scope |
|---|---|
| **M01 Foundation** ✅ | Docker Compose, FastAPI, React, PostgreSQL, migrations, tests, CI |
| **M02 Persistent Workspace** ✅ | Projects, conversations, messages; CRUD and UI |
| **M03 Source Lookup** ✅ | Read-only PubMed search with stable IDs and provenance, no LLM |
| **M04 LLM Gateway** ✅ | Local Ollama / OpenAI-compatible gateway, streaming runs, model registry, usage tracking |
| **M05 Evidence Ingestion** ✅ | Durable ingestion worker, license-gated OA full text, versioned documents, section/sentence chunks |
| **M06 Retrieval / RAG** ✅ | Embeddings, hybrid FTS + vector search (RRF), grounded answers with validated citations, evidence board, retrieval eval |
| **M07 Quality & Evals** ✅ | Held-out test sets, answer eval with LLM judge, reranker ablation, paired compare, identifier validation, stage timings, OpenTelemetry tracing |
| **M08 Entity Intelligence** ✅ | Organism-explicit gene resolution (NCBI Gene, UniProtKB, Ensembl), typed edges with provenance, Open Targets disease associations, species/identifier eval |
| **M09 Security & Multi-user** ✅ | Accounts and server-side sessions, CSRF defence, project roles (owner / editor / viewer), isolation of every project resource, per-user rate limits, cross-project security regression suite |
| M10 Analysis Lab | Object storage, validated job workers |
| M11 Research Missions | Durable multi-step workflows with approvals and budgets |
| M12 Model Research | LoRA/SFT experiments behind measured release gates |

## Principles

- No citation without a verified upstream record. The system can answer "insufficient evidence".
- Database and versioned artifacts are the source of truth, not chat memory.
- Local-first: the base configuration needs no paid API.
- Measure before you optimize or fine-tune.
- Retrieved external text is untrusted data, never instructions.
