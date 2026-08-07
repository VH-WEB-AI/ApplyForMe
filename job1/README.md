# ApplyForMe — AI Engine Architecture (Phase 1)

Production-ready backend for an AI-powered career platform: JWT auth, resume
upload/parsing, ATS scoring, semantic job matching, career health analytics,
and a RAG-based AI Career Copilot — orchestrated through a centralized AI
Orchestrator that routes to four specialized engines.

## Architecture

```
Client (Web/Mobile)
   -> API Gateway (FastAPI + JWT auth)
      -> AI Orchestrator
           - Request Classifier   -> which engine handles this request
           - Context Manager      -> loads candidate profile/resume/app history
           - Engine Router        -> dispatches to the right engine
           - Error Handler/Retry  -> bounded retries on transient LLM/JSON failures
           - Result Aggregator    -> standard response envelope
           - Audit Logger         -> Postgres + structured logs
        -> Engine 1: Resume Intelligence   (parsing, ATS score, skills, suggestions)
        -> Engine 2: Job Match Engine      (embeddings + LLM explanation, hard constraints)
        -> Engine 3: Career Health Engine  (aggregate scoring, trends, benchmarks)
        -> Engine 4: Career Copilot        (RAG chat over conversation history)
   -> Shared AI Services (resume parser, embeddings, prompt builder,
      response validator, JSON formatter, cache, audit logger, LLM client)
   -> Postgres (pgvector) + Redis + Celery workers
```

## Project layout

```
app/
  core/            settings, security (JWT/bcrypt), logging, exceptions, DI
  db/               async SQLAlchemy session, declarative base, Alembic migrations
  models/           User, CandidateProfile, Resume, JobDescription, Application,
                     Conversation, ConversationMessage, AuditLog
  schemas/          Pydantic request/response models
  api/v1/endpoints/ auth, users, resumes, jobs, career_health, copilot, applications
  orchestrator/     ai_orchestrator, request_classifier, context_manager, error_handler
  engines/          resume_intelligence, job_match, career_health, career_copilot
  shared_services/  llm_client, resume_parser, embedding_service, prompt_builder,
                     response_validator, json_formatter, cache_service, audit_logger
  workers/          celery_app + background tasks (resume processing, embeddings)
  tests/            pytest smoke tests
```

## Quickstart (Docker)

```bash
cp .env.example .env
# then edit .env and set a real JWT_SECRET_KEY plus the key for your selected AI provider

docker compose up --build
```

This starts:
- `postgres` (pgvector/pgvector:pg16) on :5432
- `redis` on :6379
- `api` (FastAPI + Uvicorn, auto-runs `alembic upgrade head`) on :8000
- `celery_worker` for background resume processing / embedding jobs
- `flower` (Celery monitoring UI) on :5555

API docs: http://localhost:8000/api/docs
Metrics (Prometheus format): http://localhost:8000/metrics
Health check: http://localhost:8000/health

## Local (non-Docker) setup

```bash
python -m venv venv && source venv/bin/activate
pip install -r requirements.txtm

# Start Postgres (with pgvector) and Redis yourself, then:
cp .env.example .env   # point DATABASE_URL / REDIS_URL at localhost

alembic upgrade head
uvicorn app.main:app --reload

# In a separate terminal:
celery -A app.workers.celery_app worker --loglevel=info
```

## AI provider selection

The application supports OpenAI and the Gemini Developer API through the same
`shared_services/llm_client.py` interface. Select exactly one provider in
`.env`; the other provider's key can remain blank.

```dotenv
# OpenAI
AI_PROVIDER=openai
OPENAI_API_KEY=your-openai-key

# Or Gemini (including a Google AI Studio free-tier key)
AI_PROVIDER=gemini
GEMINI_API_KEY=your-gemini-key
GEMINI_CHAT_MODEL=gemini-2.5-flash
GEMINI_EMBEDDING_MODEL=gemini-embedding-001
```

Install dependencies again after pulling this change so `google-genai` is
available. `EMBEDDING_DIM` remains `3072`, which matches the default models
and the pgvector schema; keep it unchanged unless you also migrate the vector
columns.

## Database migrations

The first two migrations enable the `vector` extension and create the initial
application schema. After changing models, generate the next migration:
After changing models, generate the next migration:

```bash
alembic revision --autogenerate -m "describe your change"
alembic upgrade head
```

## Core API flow

1. `POST /api/v1/auth/register` / `/login` → JWT access + refresh tokens
2. `PUT /api/v1/users/me/profile` → set headline, skills, preferences
3. `POST /api/v1/resumes/upload` → saves file, queues Celery job that parses
   the resume and runs it through **Engine 1** (ATS score, skills, suggestions,
   embedding) — poll `GET /api/v1/resumes/{id}` for status `scored`
4. `POST /api/v1/jobs/match` → **Engine 2**: embeds resume + JD, computes
   cosine similarity, checks hard constraints (visa/salary), LLM explains the match
5. `GET /api/v1/career-health` → **Engine 3**: aggregates resume/match history
   into a career health score, trend, weak areas, recommendations
6. `POST /api/v1/copilot/message` → **Engine 4**: RAG over past conversation
   messages (pgvector cosine search) + candidate context, LLM reply

Every one of these AI calls goes through `ai_orchestrator.dispatch(...)`,
so classification, context loading, retries, and audit logging are
consistent across all four engines.

## Design notes

- **One LLM seam**: all OpenAI calls go through `shared_services/llm_client.py`
  — swapping providers or adding a local model later touches one file.
- **Structured outputs**: every engine prompt requests strict JSON
  (`response_format={"type": "json_object"}` where supported), then
  `json_formatter` + `response_validator` (Pydantic) guarantee the shape
  before it reaches the API layer or gets persisted.
- **Retries live in the orchestrator**, not in each engine — engines stay
  simple; `error_handler.run_with_retry` retries on transient LLM/JSON
  failures and converts anything else into a clean `EngineError`.
- **Context caching**: `ContextManager` caches a candidate's profile/resume/
  application summary in Redis for 5 minutes so multiple engine calls in a
  session don't repeatedly hit Postgres; profile updates invalidate it.
- **Heavy work is async**: resume parsing + scoring + embedding runs in a
  Celery task so upload requests return immediately.
- **Observability**: `/metrics` (Prometheus format via
  `prometheus-fastapi-instrumentator`), structured JSON logs (`structlog`),
  and a Postgres `audit_logs` table recording model, prompt version, token
  usage, and latency per engine call — the basis for Grafana dashboards and
  cost/token tracking.

## What's intentionally left for you to extend

- Frontend (React/Next.js) — not included; this is the backend/AI-engine layer.
- OAuth/social login, email verification flow.
- Rate limiting middleware (cache_service.incr is ready to back one).
- OpenTelemetry trace exporter wiring (`OTEL_EXPORTER_OTLP_ENDPOINT` is
  already in config — add `opentelemetry-instrumentation-*` auto-instrumentation
  in `main.py` for your collector of choice).
- LinkedIn/job-board ingestion pipeline that populates `JobDescription`
  (the `embed_job_description_task` Celery task is ready to consume it).
- Production secrets management (don't ship `.env` — use your platform's
  secret store) and TLS termination in front of the API.
