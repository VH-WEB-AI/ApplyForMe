# Scoring & Decision Logic

What actually computes every number and decision in the AI Engine, and what the LLM
is (and isn't) responsible for. Source of truth is the code — file/line references
below point at it so this doc can be checked against reality, not trusted blindly.

## The one rule that shapes everything

**The LLM never computes a score.** Every number (`ats_score`, `match_score`,
`careerHealthScore`, component scores, badges, priority levels) comes from plain
Python in an `analysis.py` module — regex, arithmetic, taxonomy lookups, cosine
similarity. The LLM's only job is to *explain* numbers it's handed and to produce
qualitative output (recommendations, advice, free-text answers) that must stay
consistent with them. This is enforced at the prompt level too — each engine's
`BUSINESS_RULES` explicitly tells the model not to contradict or invent scores.

Why it matters practically: re-running the same resume/job pair produces the exact
same score every time (and skips the LLM call entirely — see **Caching** below),
and a wrong or missing OpenAI key still gives you accurate scores, just no prose.

---

## 1. Resume Intelligence — ATS Compatibility Score

`backend/app/engines/resume_intelligence/analysis.py` → `compute_ats_score()`

Weighted, 100 points total, each component independently deterministic:

| Component | Points | Logic |
|---|---|---|
| **Contact parseability** | 15 | 8 pts if an email regex matches the header/summary text, 7 pts if a phone regex matches |
| **Section structure** | 25 | 5 pts per required heading found (Summary, Experience, Education, Skills = 20 max) + 5 bonus pts if a Projects or Certifications section exists |
| **Keyword/skill match** | 30 | If a target role/industry was given: fraction of that text's top keywords also found in the resume (`keyword_overlap_score`), × 30. If no target role: `min(1, taxonomy_skills_found_anywhere / 10) × 30` — a real signal instead of a flat placeholder |
| **Formatting/parseability** | 20 | Word-count sanity check (10 pts, see below) + 5 pts if the Experience section uses bullet glyphs (`•`/`-`/`*`) instead of dense paragraphs + up to 5 pts for recognizable date ranges (`Jan 2020 – Present`, `01/2020-12/2022`, etc.) |
| **Skills section quality** | 10 | `min(1, taxonomy_skills_found_in_Skills_section / 8) × 10` — rewards an explicit, recognizable skills list specifically, not skills mentioned incidentally elsewhere |

**Word-count sanity check** (`length_score`): <120 words → 0 (usually means the PDF
was scanned/image-based and barely any text was actually extracted — a real ATS
parsing failure, not just a short resume); 120–250 → half credit; 250–1200 → full
credit; 1200–1800 → 0.7; beyond that → 0.4 (too long to be fully indexed/skimmed).

Section headings are recognized by `resume_parser.py`'s alias list (e.g. "Work
Experience", "Professional Experience", "Employment History" all map to
`experience`) — a heading only counts if it's on its own line and matches one of
those aliases; unusual/creative headings won't be recognized.

### Section scores (per-section quality, separate from the ATS score)

`compute_section_scores()` — used for `weak_sections` (anything scoring below 60):
- **Summary**: `40 + 2 × word_count`, capped at 100; 0 if the section is missing.
- **Experience**: `30 + 40 × action_verb_ratio + 30 × achievement_ratio` — the
  fraction of bullets that start with a verb from a curated action-verb list
  (`led`, `built`, `optimized`, …), and the fraction that contain a digit
  (treated as a proxy for quantified impact, e.g. "reduced latency by 30%").
- **Education**: 80 if present, 0 if not (presence-only, no quality check).
- **Skills**: `20 + comma_count`, capped at 100 — more comma-separated entries score higher.

### Resume Score (the headline number)

`compute_resume_score()`: `round(avg(section_scores) × 0.7 + ats_score × 0.3)`.

### Other deterministic extractions
- **Total experience years** (shared with Job Match): earliest year to latest
  year/"Present" found in a `YYYY - YYYY|Present` style range in the Experience
  text (`experience_estimator.py`).
- **Education/Certifications**: lines containing a degree keyword (`bachelor`,
  `mba`, `phd`, …) or a certification keyword (`pmp`, `aws certified`, …).

---

## 2. Job Match — Match Score

`backend/app/engines/job_match/analysis.py` + `engine.py`. Five weighted components
sum to a 0–100 `match_score`. There is no semantic/embedding component — keyword
overlap is the sole text-matching signal (deliberately, to stay deterministic,
free of OpenAI API cost/latency, and easy to reason about):

| Component | Weight | Logic |
|---|---|---|
| **Keyword overlap** | 45% | Fraction of the job description's top keywords also present in the job-relevant resume text |
| **Experience** | 20% | `candidate_years / min_required_years`, capped at 1.0; full credit if the job has no stated minimum |
| **Location** | 15% | See tiered logic below |
| **Visa** | 10% | 1.0 if the candidate doesn't need sponsorship (citizen/PR/"authorized"/etc.); otherwise 1.0 if the job offers sponsorship, 0.0 if not |
| **Salary** | 10% | 1.0 if the job's max offer ≥ candidate's minimum ask; 0.5 if either side is unknown; otherwise `job_max / candidate_min` |

`match_score = round(100 × Σ(component_score × weight))`. Weights live in
`WEIGHTS` at the top of `analysis.py` — flagged in the code as admin-configurable
defaults, not yet tuned against real outcome data.

**"Job-relevant resume text"**: every resume section *except* the header
(name/email/phone/links) — pure contact-info noise that dilutes the keyword
overlap without carrying any job-fit signal.

**Note**: `job_matches.semantic_score` still exists as a DB column (always `0.0`
for new rows going forward) rather than being dropped via migration — historical
rows keep their real value for anyone auditing past matches.

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
