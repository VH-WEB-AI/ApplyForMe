# Scoring & Decision Logic

What actually computes every number and decision in the AI Engine, and what the LLM
is (and isn't) responsible for. Source of truth is the code — file/line references
below point at it so this doc can be checked against reality, not trusted blindly.

## The one rule that shapes everything (Job Match and Career Health)

**The LLM never computes a score.** Every number (`match_score`,
`careerHealthScore`, component scores, badges, priority levels) comes from plain
Python in an `analysis.py` module — regex, arithmetic, taxonomy lookups, cosine
similarity. The LLM's only job is to *explain* numbers it's handed and to produce
qualitative output (recommendations, advice, free-text answers) that must stay
consistent with them. This is enforced at the prompt level too — each engine's
`BUSINESS_RULES` explicitly tells the model not to contradict or invent scores.

Why it matters practically: re-running the same job match produces the exact same
score every time (and skips the LLM call entirely — see **Caching** below), and a
wrong or missing OpenAI key still gives you accurate scores, just no prose.

**Resume Intelligence is the deliberate exception** — see section 1 below. Every
one of its fields (`resumeScore`, `atsScore`, `sectionScores`, `tags`, `education`,
`certifications`, `totalExperienceYears`) is produced by a single structured LLM
call, not by `analysis.py` regex/arithmetic (that module has been deleted). This
was a deliberate tradeoff, made after the regex/keyword-heuristic approach kept
producing real accuracy bugs in production (section headers going unrecognized,
degree/certification keyword matching picking up unrelated sentences, agency/
staffing-firm boilerplate PDFs polluting every field) that were faster and more
robust to fix by having the LLM read the whole resume holistically than by adding
ever more regex special-cases. The tradeoff accepted: `resumeScore`/`atsScore` are
no longer reproducible bit-for-bit across calls, and a missing/failing OpenAI key
now fails the whole `/resume/analyze` and `/resume/ats-check` requests instead of
degrading gracefully to scores-without-prose.

---

## 1. Resume Intelligence — fully LLM-driven

`backend/app/engines/resume_intelligence/engine.py` — `ResumeIntelligenceEngine`.
There is no `analysis.py` for this engine anymore. A single structured LLM call
(`ResumeLLMOutput` in `schemas.py`) produces every field in one pass, briefed via
`SYSTEM_PROMPT`/`BUSINESS_RULES`/`build_prompt_spec()` to act as three experts at
once (recruiter, ATS parsing engine, career coach):

- **`resumeScore`, `atsScore`** (0-100 each): judged by the model, not computed by
  a weighted formula — the prompt describes what each should weigh (contact info
  parseability, section structure, keyword/skill match against the target role,
  formatting clarity, explicit skills section for `atsScore`; overall clarity/
  impact for `resumeScore`) but the number itself is the model's judgment call.
- **`sectionScores`** (`summary`/`experience`/`education`/`skills`, 0-100 each) and
  **`weakSections`** (keys below 60): also the model's judgment, instructed to read
  for section *content* under any heading wording/decoration rather than pattern-
  matching a fixed alias list — this is what fixed real bugs where an atypical
  heading (e.g. "Experience Summary") caused a section to score 0 despite having
  content.
- **`tags`** (up to 50): built in three explicit passes the model is instructed to
  follow — (a) exhaustively list every literal skill/technology/tool named,
  prioritizing an explicit Skills section as ground truth; (b) every distinct job
  title/role literally held (e.g. "technical lead"), for job-posting matchability;
  (c) up to 5 broader domain keywords. A pydantic validator (`_clean_tags` in
  `schemas.py`) is a safety net, not a second extraction pass: it splits any
  comma-joined string the model returns as one tag, strips whitespace/control-char
  artifacts, deduplicates case-insensitively, and caps at 50.
- **`education`, `certifications`**: every distinct entry literally stated;
  explicitly instructed to stop at the first sign of unrelated content (a new
  project/experience block) even without a recognized heading, and to return an
  empty list rather than stretch an unrelated sentence that merely mentions the
  word "certification".
- **`totalExperienceYears`**: summed from explicit date ranges in the text (using
  a `current_date` passed in `candidate_context`), falling back to a stated
  approximate total in prose (e.g. "8+ years of experience") if no date range
  exists anywhere, and only 0 if neither is present.
- **`missingSkills`, `recommendations`, `rewriteSuggestions`**: as before — the
  model's qualitative output, still required to stay grounded in the resume text.

The system prompt explicitly instructs the model to detect and disregard
staffing-agency cover/marketing pages that get PDF-merged into a candidate's
resume (a real case: a multi-page PDF with an agency sales cover page and
"About Us"/"Why Choose Us" trailing pages, which previously polluted `education`
with sentences from the agency's own marketing copy).

`resume_parser.py`'s `identify_sections`/`SECTION_HEADERS` still exists and is
still used — but only to populate `ResumeVersion.sections` (which Job Match's
`total_experience_years` calculation reads independently at match time), not for
Resume Intelligence's own scoring anymore.

---

## 2. Job Match — Match Score

`backend/app/engines/job_match/analysis.py` + `engine.py`. Five weighted components
sum to a 0–100 `match_score`. There is no semantic/embedding component and no live
keyword extraction at match time — **tag overlap** (precomputed keyphrases, see
below) is the sole text-matching signal and carries the single highest weight:

| Component | Weight | Logic |
|---|---|---|
| **Tag overlap** | 45% | Fraction of the job's tags also present in the resume's tags (`tag_extractor.tag_overlap_score`) |
| **Experience** | 20% | `candidate_years / min_required_years`, capped at 1.0; full credit if the job has no stated minimum |
| **Location** | 15% | See tiered logic below |
| **Visa** | 10% | 1.0 if the candidate doesn't need sponsorship (citizen/PR/"authorized"/etc.); otherwise 1.0 if the job offers sponsorship, 0.0 if not |
| **Salary** | 10% | 1.0 if the job's max offer ≥ candidate's minimum ask; 0.5 if either side is unknown; otherwise `job_max / candidate_min` |

`match_score = round(100 × Σ(component_score × weight))`. Weights live in
`WEIGHTS` at the top of `analysis.py` — flagged in the code as admin-configurable
defaults, not yet tuned against real outcome data.

### Tags (`app/services/tag_extractor.py`)

Both `resume_versions.tags` and `job_postings.tags` are JSONB columns holding a
list of keyphrases, extracted **once, at creation time** (resume upload / job
ingest) via [YAKE](https://github.com/LIAAD/yake) — a lightweight, open-source,
purely statistical keyphrase extractor (no model download, no torch, CPU-only).
Job Match reads these precomputed lists at match time; it never re-extracts from
raw text, so matching stays cheap even at high volume.

- **Resume tags**: extracted from every section *except* the header
  (name/email/phone/links — pure contact-info noise, same reasoning as the old
  job-relevant-text concept it replaces).
- **Job tags**: extracted from the job's `description` field.
- Existing rows created before this shipped are backfilled once via
  `backend/scripts/backfill_tags.py` (idempotent — only fills rows where `tags`
  is still empty, safe to re-run).

**Note**: `job_matches.semantic_score` still exists as a DB column (always `0.0`
for new rows going forward) rather than being dropped via migration — historical
rows keep their real value for anyone auditing past matches. `keyword_extractor.py`
(the deterministic keyword-frequency module that used to power Resume
Intelligence's `ats_score` keyword-match component) has been deleted along with
the rest of that engine's `analysis.py` — see section 1 above.

**Location scoring** (`location_score`) — tiered, not exact-string:
- Job is remote → **1.0**, unconditionally.
- Either side's location is unknown → **0.5** (neutral, not penalized).
- Same city (state compared only if both sides have one; "TX" and "Texas" are
  normalized to the same value) → **1.0**.
- Same state, different city → **0.6** (same commute region, partial credit).
- Otherwise → **0.2**.

**Missing skills**: taxonomy-normalized set difference between the job's
`required_skills` and skills found anywhere in the candidate's resume
(`skill_extractor.skill_gap`).

**Priority badge**: high (score ≥ 80) / normal (≥ 55) / low (below).

**Interview readiness**: `ready` if `(match_score + resume_score) / 2 ≥ 75`,
`needs_prep` if ≥ 50, else `not_ready`.

**"Match against all active jobs"** (`GET /jobs/matches/{candidate_id}`) simply
runs the same single-job match for every active `JobPosting` row and sorts by
`match_score` descending — no separate ranking logic.

---

## 3. Career Health — Overall Score

`backend/app/engines/career_health/analysis.py` + `engine.py`. Eight components,
each 0–100, combined via `Settings.career_health_weights` (env/config-driven,
current defaults below):

| Component | Weight | Logic |
|---|---|---|
| **Resume quality** | 20% | The candidate's latest `resume_score` (0 if no resume uploaded yet) |
| **ATS compatibility** | 15% | The candidate's latest `ats_score` (0 if none) |
| **Profile completeness** | 10% | `filled_profile_fields / 10 × 100` across role, industry, experience level, visa status, location, salary range, LinkedIn/GitHub/portfolio URLs |
| **Skill relevance** | 20% | `max(0, 100 - 15 × missing_skills_count)` from the latest resume analysis (10 assumed missing, i.e. 0 score, if no resume exists yet) |
| **Application activity** | 10% | `min(100, applications_count × 10)` — 10+ logged applications maxes this out |
| **Interview progress** | 10% | `min(100, interviews_count × 20)` — 5+ interviews maxes this out |
| **Market alignment** | 10% | Average `match_score` across every job match ever computed for this candidate; 50 (neutral) if none exist yet |
| **Professional presence** | 5% | `present_urls / 3 × 100` across LinkedIn/GitHub/portfolio |

`overall_score = round(Σ(component × weight))`. `weak_areas` is every component
scoring below 60. `trend_delta` is the difference from the candidate's previous
snapshot (0 if this is their first one).

---

## 4. Career Copilot — RAG-Grounded Answers

`backend/app/engines/career_copilot/engine.py` + `analysis.py`. No score to
compute here — the deterministic logic instead decides *what context the LLM is
allowed to use*:

1. **Intent detection** (`detect_intent`): plain keyword matching against the
   question (e.g. "ats score" → `explain_resume_score`, "interview" →
   `interview_prep`) — cheap, deterministic, and avoids spending an LLM call just
   to classify the question.
2. **Resume chunking + retrieval**: the resume is split into ~300-word
   overlapping chunks (`document_chunking.chunk_text`, 50-word overlap), each
   embedded and cached (`owner_type="resume_chunk"`). The question is embedded
   and the top 3 chunks by pgvector cosine distance are retrieved
   (`embedding_generator.top_k_similar`) and handed to the LLM as grounding text.
3. **Availability flags**: `has_resume` / `has_career_health` / `has_job_matches`
   / `has_applications` booleans are computed from real DB rows and passed to the
   LLM with an explicit instruction to say "I don't have that yet" rather than
   guess when a flag is false.
4. **Conversation history**: last 10 messages of the given (or newly created)
   conversation, replayed in order.

The LLM answers using only this assembled context — the `BUSINESS_RULES` in
`engine.py` explicitly forbid inventing resume content, applications, or scores
not present in it.

---

## LLM Gateway — model behavior

`backend/app/services/llm_gateway.py`, config in `backend/app/config.py` /
`.env`:

- **Model-agnostic by design**: every engine calls the same `chat_completion()` /
  `create_embedding()`; swapping `OPENAI_CHAT_MODEL` or pointing
  `OPENAI_BASE_URL` at a different OpenAI-compatible provider requires no code
  changes.
- **Reasoning-tier models** (`o1`/`o3`/`o4`/`gpt-5` prefixes, current default
  `gpt-5-mini`): these reject any custom `temperature` (API-default only), so the
  gateway omits that param for them and instead passes `reasoning_effort` (via
  `extra_body`, since the installed SDK version has no first-class param for it
  yet) — default `low`, chosen for latency on these structured
  extraction/explanation tasks; the older explicit-`temperature=0.3` path is kept
  for non-reasoning models (e.g. `gpt-4o-mini`).
- **Retries/timeouts**: each call gets 15s connect/read timeout via the OpenAI
  client (`max_retries=0` there — retries are handled by `tenacity` instead: 3
  attempts, exponential backoff 1s→8s). This exists specifically so a stuck
  provider call fails in seconds rather than hanging for the SDK's 600s default
  and starving FastAPI's worker thread pool for every other request.
- **Response validation**: `response_validator.validate_with_retry` parses the
  model's JSON output against the engine's pydantic schema; on failure it retries
  (up to `Settings.response_validation_max_retries`, default 2) by feeding the
  validation error back to the model as corrective feedback.

## Caching

Resume Intelligence and Job Match implement `Engine.cache_key()` — same resume
content-hash + same target role (Resume Intelligence), or same resume version +
same job content-hash (Job Match) — and skip the LLM call entirely on a cache
hit (Redis-backed, `services/cache.py`), returning the previously computed
response verbatim. Career Health and Career Copilot don't cache (health changes
whenever activity does; copilot answers are conversational, not idempotent).

## Audit trail

Every LLM call — success or failure, whatever the cause — is logged to
`ai_response_logs` via `services/audit_logger.py`: engine name, model, prompt/
response, token counts, latency, retry count, and any error message. Useful for
answering "why did this candidate get this score/explanation" after the fact.
