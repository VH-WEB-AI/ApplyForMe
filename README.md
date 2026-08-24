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
| `POST /leads/ingest` | Ingest researched job-seeker leads (master-sheet rows) — grades and dedupes |
| `POST /leads/validate` | Dry-run one lead against the SOP checklist without storing it |
| `GET /leads/duplicate` | Check a LinkedIn URL against the master sheet before researching |
| `GET /leads` | List leads, filterable by priority / state / contact status |
| `PATCH /leads/{lead_id}` | Update a lead's contact status and notes |
| `GET /leads/export` | Export the master sheet as CSV |
| `GET /leads/apollo/status` | Whether Apollo enrichment is usable on the current plan |
| `POST /leads/{lead_id}/enrich` | Fill blank contact fields from Apollo, after an identity check |

See [`docs/SCORING_LOGIC.md`](docs/SCORING_LOGIC.md) for exactly how every score,
badge, and threshold in the API responses is computed.

## Lead research (Web Research SOP)

`app/services/lead_qualification.py` encodes the Web Research SOP's rules for
sourcing U.S.-based recent graduates who are open to work: canonicalising
LinkedIn URLs into a dedup key, classifying Software/IT target roles, rejecting
recruiter and job-advert profiles, and grading each lead A+/A/B/C.

Two rules shape the data model. First, **signals are only ever read from what a
candidate publicly stated** — `extract_visa_signals()` parses the candidate's own
wording, and a researcher's checkbox cannot create an F-1/OPT/CPT flag on its
own. Nationality is likewise never inferred; `indian_identity` stays `"unknown"`
without an explicit public self-statement. Second, **re-submitting a lead fills
in blanks rather than overwriting** (`lead_ingest._merge_missing`), so an Apollo
or ContactOut enrichment pass adds a phone number without clobbering the
originally researched city.

### Apollo enrichment

`POST /leads/{id}/enrich` fills *blank* fields on a lead from Apollo's People
Enrichment API. Before writing anything it runs `verify_identity()`: Apollo's
returned LinkedIn URL must normalise to the same key as the stored lead, and if
Apollo has no URL, the surname must match plus one corroborating signal (state).
A failed check writes nothing at all rather than writing what it can — attaching
a same-name stranger's phone number is the specific failure the SOP's s15/s17
identity rules exist to prevent.

Two deliberate limits. Personal-email reveal is off (`reveal_personal_emails`
defaults to False) — the SOP asks for professional contact details and the
reveal costs extra credits. Phone enrichment is not wired up at all: Apollo
returns phone numbers asynchronously to a `webhook_url`, which this service
doesn't expose, so phone numbers stay a manual ContactOut/Apollo-UI step.

`scripts/apollo_capability_demo.py` (`python -m scripts.apollo_capability_demo`)
probes the configured key live and prints which endpoints it can reach, a real
extraction from each one that works, and a coverage table of which master-sheet
columns Apollo could fill. Its person probes use an unmatchable fake name, so it
never pulls a real individual's data or spends an enrichment credit. Re-run it
after any Apollo plan change.

`GET /leads/apollo/status` is worth calling before showing an enrich button.
Apollo's `auth/health` succeeds on every plan including Basic/Trial, but Trial
returns `403 API_INACCESSIBLE` for every *people* endpoint — so a working key
does not imply working enrichment, and the status endpoint probes an actual
people endpoint to tell the difference.

### Sourcing shortlists with People Data Labs

Apollo enriches people you have already found; it cannot *find* recent graduates,
because it carries no education history at any plan tier and a fresher who has
not started work is largely absent from a B2B contact database.
`app/services/pdl_client.py` covers that gap. PDL keeps an `education` array
(school, degrees, majors, start/end dates), which is what makes "passed out in
2025/2026" expressible as a query:

```sql
SELECT * FROM person WHERE location_country IN ('united states', 'india')
AND education.end_date >= '2025-01-01' AND education.end_date <= '2026-12-31'
AND education.degrees IN ('bachelors', 'masters')
AND education.majors IN ('computer science', 'data science', ...)
AND job_title_levels IN ('entry', 'training')
AND job_title_role = 'engineering'
AND linkedin_url IS NOT NULL
```

The last three clauses are the difference between a usable shortlist and a
useless one, and that was only visible by running it. Education filters alone
match **~1.2M people and are mostly noise**: a recent `education.end_date` also
catches executive-education and certificate courses, so the first sample the
query returned was a CTO who finished a Wharton course in 2025. Adding the
degree-type and seniority filters cuts it to **~36K**, and the samples become
junior software engineers and engineering interns. SOP s28 puts precision above
volume, so those filters default to on.

Two findings worth keeping: PDL's SQL parser rejects a long `OR ... LIKE` chain
with a 400 that reads like a plan restriction (use `IN`), and
`inferred_years_experience` comes back null on this dataset while null still
satisfies `<= 3`, so it silently filters nothing. `job_title_levels` is the
filter that actually works. The trade-off is that seniority keys off a *current
job title*, so a graduate with no job at all — precisely the SOP's target — has
none and drops out; pass `entry_level_only=False` to widen and expect to sift.

Note what the query does **not** contain. There is no visa filter, because no
commercial dataset carries work-authorization status. And there is no
nationality filter: SOP s13 records Indian identity only from a public
self-statement, so it can never be an *input* to sourcing.
`assert_no_identity_filter()` raises on any query mentioning nationality,
ethnicity, religion, caste or race, and it runs on every generated query and
again inside `search_people()`. That guard is deliberately an exception rather
than a warning — a query that quietly slipped such a filter through would be
discriminatory targeting, not a bad result set.

So PDL fills 12 of the 27 master-sheet columns — the sourcing half. The other 15,
including every SOP s10–s13 qualification signal, still come from a researcher
reading the profile. **Buying data does not remove the manual verification
step**; it only decides which profiles are worth opening.
`scripts/pdl_capability_demo.py` (`python -m scripts.pdl_capability_demo`) prints
that split as a live coverage table and reports whether the current plan includes
Person Search, which is a paid-tier endpoint. Run it before paying for anything —
the equivalent Apollo script is what established that an Apollo upgrade would not
have helped here.

One scope caveat: SOP s2/s22 target candidates **located in the US**. India is in
`SUPPORTED_COUNTRIES` because sourcing was extended to both, but an India-located
candidate is not on F-1/OPT by definition, so the visa-signal columns and the
A+/A scoring bands that depend on them do not apply to that segment. India-based
sourcing also brings India's DPDP Act into scope, which is consent-first and
applies extraterritorially.

Collection itself stays manual and out of this codebase — the SOP is written
around a researcher working within LinkedIn's terms, and nothing here scrapes
LinkedIn.

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
