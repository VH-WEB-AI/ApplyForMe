# ApplyForMe AI Engine

Phase 1 implementation of the ApplyForMe AI Orchestrator and its four engines:
Resume Intelligence, Job Match, Career Health, and Career Copilot.

## Architecture

```
Frontend --> API Gateway (FastAPI routes) --> AI Orchestrator --> Engine --> LLM Gateway (OpenAI)
                                                    |
                                    Shared AI Services (parsing, skills, embeddings,
                                    prompt building, validation, cache, audit log)
                                                    |
                                     PostgreSQL (+ pgvector) / Redis
```

Every engine implements the same `Engine` contract
(`app/orchestrator/engine_base.py`): `gather_context` (deterministic — parsing,
scoring, retrieval; never calls the LLM) -> `build_prompt_spec` (assembles the
reasoning/explanation prompt) -> the orchestrator invokes the LLM and validates
the response against a pydantic schema, retrying on failure -> `postprocess`
(merges deterministic + LLM output, persists rows, returns JSON). See
`app/orchestrator/orchestrator.py` for the full lifecycle.

- **Resume Intelligence** (`app/engines/resume_intelligence`): parses PDF/DOCX,
  scores resume/ATS quality deterministically, and asks the LLM only for
  missing-skills, recommendations, and rewrite suggestions.
- **Job Match** (`app/engines/job_match`): matches a candidate's resume against
  a job posting using a deterministic weighted business-rule engine (semantic
  similarity, keywords, experience, location, visa, salary), then asks the LLM
  to explain the score. `app/engines/job_match/service.py` fans this out across
  every active `JobPosting` row so a candidate can see all their matches
  ranked — there's no admin authoring UI in this phase; job postings are
  assumed to already exist in the table.
- **Career Health** (`app/engines/career_health`): rolls up resume/ATS/profile/
  skill/application/interview/market signals into one weighted score
  (`Settings.career_health_weights`), then asks the LLM for personalized advice.
- **Career Copilot** (`app/engines/career_copilot`): a RAG-grounded assistant —
  chunks and embeds the candidate's resume into pgvector, retrieves the most
  relevant chunks for each question, and answers using only that candidate's
  actual data (never fabricates), with conversation history persisted per
  `Conversation`.

## Prerequisites

- Docker + Docker Compose
- Python 3.12+ (only needed for running things outside Docker, e.g. Alembic/tests)
- An OpenAI API key

## Setup

1. Copy the env file and fill in your real OpenAI key:

   ```
   cp .env.example .env
   cp .env.example backend/.env   # used by Alembic/pytest running outside Docker
   ```

   Edit both `.env` files and set `OPENAI_API_KEY` to a real key. The default
   ports (`5440` Postgres, `6390` Redis, `8010` API) were chosen to avoid
   clashing with other local services — adjust in `docker-compose.yml` and the
   `.env` files together if you need different ports.

2. Start Postgres and Redis:

   ```
   docker compose up -d postgres redis
   ```

3. Apply database migrations (creates the schema + enables the pgvector extension):

   ```
   cd backend
   python3 -m venv .venv && source .venv/bin/activate
   pip install -r requirements.txt
   alembic upgrade head
   ```

4. Start the API (either via Docker, or locally with the venv from step 3):

   ```
   docker compose up -d backend
   # or, locally:
   uvicorn app.main:app --reload --port 8000
   ```

5. Check it's alive: `curl http://localhost:8010/health`

## Running tests

```
cd backend
source .venv/bin/activate
python -m pytest
```

Tests run against the real Postgres container (savepoint-per-test, always
rolled back — see `tests/conftest.py`) and never call the real OpenAI API
(all LLM/embedding calls are mocked), so they're safe to run without an API key.

## API overview

| Endpoint | Purpose |
|---|---|
| `POST /candidates` | Bootstrap a `User` + `CandidateProfile` (minimal — no auth in this phase) |
| `POST /resume/analyze` | Multipart resume upload -> Resume Intelligence Engine |
| `GET /jobs` | List active job postings |
| `POST /jobs/match` | Match one candidate against one job posting |
| `GET /jobs/matches/{candidate_id}` | Match a candidate against every active job posting, ranked |
| `GET /career-health/{candidate_id}` | Career Health Engine |
| `POST /copilot/ask` | Career Copilot Engine (pass `conversation_id` to continue a thread) |

See [`docs/SCORING_LOGIC.md`](docs/SCORING_LOGIC.md) for exactly how every score,
badge, and threshold in the API responses is computed.

## Notes on design choices

- **Model-agnostic**: every LLM/embedding call goes through
  `app/services/llm_gateway.py`. Swapping providers/models is an env-var change,
  not a code change.
- **Caching**: engines that implement `Engine.cache_key()` (Resume Intelligence,
  Job Match) skip the LLM call entirely when the underlying content hasn't
  changed (see `app/services/cache.py`, Redis-backed).
- **Audit log**: every LLM call (prompt/response/tokens/latency/errors) is
  recorded in `ai_response_logs` via `app/services/audit_logger.py`.
- **PII**: resume/job text is redacted (`app/services/pii_redaction.py`) before
  being included in prompts sent to the LLM.
- **No admin panel in Phase 1**: job postings are matched against, not
  authored, in this build — see Job Match section above.
# ApplyForMe
