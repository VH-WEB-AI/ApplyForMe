# Job Scraper (smart_scraper_jobs)

Multi-portal job-posting scraper, pulled in from
[VH-WEB-AI/smart_scraper_jobs](https://github.com/VH-WEB-AI/smart_scraper_jobs) and
adapted to push results to a backend API instead of (in addition to) a local CSV.

Scrapes job postings from 67 portals (`portals.csv`) and normalizes each into:

```
Portal, Job Title, Company, URL, About the job, Pay, Location, Summary,
Requirements, Responsibilities, Benefits, Scraped At
```

## Where this fits in ApplyForMe

This feeds the `job_postings` table that the **Job Match Engine** matches candidates
against (`../backend/app/db/models/jobs.py` — `JobPosting`; see the main
`../README.md`'s "No admin panel in Phase 1" note: job postings are currently
matched against, not authored, since there's no ingestion path yet). This scraper is
that missing ingestion path. It's intentionally decoupled from the FastAPI backend's
own runtime (Python scraping with BeautifulSoup/feedparser across 67 portals doesn't
belong inside a request/response API process) and instead runs standalone on a
schedule, pushing its output to a backend endpoint.

**Status: `BACKEND_API_URL` is not yet configured, and the target endpoint doesn't
exist yet either.** The Phase 1 backend (`../backend`) only exposes `GET /jobs` and
`POST /jobs/match` — there is no `POST /jobs` (or similar) to create `JobPosting`
rows. Someone needs to add that ingestion endpoint to the backend before this scraper
has anywhere real to send data. Until then it still runs and writes CSVs to
`./output/`, just skipping the upload step (logs `[skip] BACKEND_API_URL not set`).

**Schema note**: this scraper's row shape (`Portal, Job Title, Company, URL, About
the job, Pay, Location, Summary, Requirements, Responsibilities, Benefits, Scraped
At`) does not line up field-for-field with `JobPosting` (`title, company,
description, location, remote, seniority, salary_min, salary_max,
visa_sponsorship, required_skills, min_experience_years, is_active`). Whatever
ingestion endpoint gets built needs to map/parse one into the other (e.g. splitting
`Pay` into `salary_min`/`salary_max`, deriving `remote` from `Location`) — this
scraper intentionally doesn't guess at that mapping itself.

## What changed from the upstream repo

- Added `post_rows_to_backend()` in `job_scraper.py`: after a scrape run, batches all
  scraped rows and `POST`s them as JSON to `BACKEND_API_URL`.
- Split `requirements.txt`: the optional LLM-refinement path (translation + spam
  filtering via a local Hugging Face model) now lives in `requirements-llm.txt`,
  since `torch` is a ~2GB dependency not needed for the default scrape-and-post flow.
- Added `Dockerfile` + `run_loop.sh` + `docker-compose.yml` to run this on a schedule.

## Backend API contract

Once that endpoint exists, set it in `.env`. If it's added to this same backend and
you run this scraper's Docker Compose separately from `../docker-compose.yml`, use
the host-published port (`http://localhost:8010/...`), not the internal Docker
service name:

```
BACKEND_API_URL=http://localhost:8010/jobs/ingest
BACKEND_API_KEY=<if it needs auth>
```

Each batch is sent as:

```http
POST {BACKEND_API_URL}
Authorization: Bearer {BACKEND_API_KEY}   # omitted if no key set
Content-Type: application/json

{
  "source": "smart_scraper_jobs",
  "jobs": [
    {
      "Portal": "Remote OK",
      "Job Title": "VP of Sales",
      "Company": "Acme Inc",
      "URL": "https://...",
      "About the job": "...",
      "Pay": "$120k-$150k",
      "Location": "Remote",
      "Summary": "...",
      "Requirements": "...",
      "Responsibilities": "...",
      "Benefits": "...",
      "Scraped At": "2026-08-06T12:00:00+00:00"
    }
  ]
}
```

`BACKEND_API_BATCH_SIZE` (default 100) controls how many jobs go in each POST — tune
this down if your backend has a request-size limit.

## Running it

**Locally:**
```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill in BACKEND_API_URL once you have it
export $(grep -v '^#' .env | xargs)   # or use python-dotenv / direnv
python3 job_scraper.py --limit 25
```

**Via Docker (recommended for production — runs on a loop):**
```bash
cp .env.example .env   # fill in BACKEND_API_URL, SCRAPE_INTERVAL_SECONDS, etc.
docker compose up -d --build
docker compose logs -f
```

Each run writes a timestamped CSV to `./output/jobs_<timestamp>.csv` (kept as a local
audit trail / fallback even when the backend POST succeeds) and, if configured,
uploads to the backend.

## Options

- `--limit N` — max jobs per portal (default 25).
- `--only "Portal Name" "Other Portal"` — restrict to specific portals (see
  `portal_name` column in `portals.csv`).
- `--no-post-backend` — scrape and write CSV only, skip the backend upload for this
  run (useful for testing).
- `ADZUNA_APP_ID` / `ADZUNA_APP_KEY`, `USAJOBS_API_KEY` / `USAJOBS_EMAIL` — free API
  keys that unlock two more portals; both are skipped automatically if unset.

## Known limitations (from upstream)

Only `api` and `rss` methods (11 of 67 portals) are fully reliable out of the box.
The `generic` HTML-scrape fallback (~50 portals) works on plain server-rendered sites
and returns little/nothing on JS-heavy sites (React/Next.js) or bot-protected boards
— those would need a headless browser, which is out of scope here. Check each
portal's ToS/robots.txt before scraping at real volume, and keep `REQUEST_DELAY` (in
`job_scraper.py`) polite.

## Repair tool

`repair_csv.py` re-cleans an already-scraped CSV (fixes mojibake, drops
unrecoverable junk rows) without re-scraping:

```bash
python3 repair_csv.py jobs_output.csv jobs_output_fixed.csv
```
