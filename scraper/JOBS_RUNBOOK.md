# Jobs pipeline runbook

Every command runs from `/home/vhits/applyForMe/scraper`. Credentials come from
`scraper/.env` (`ADMIN_API_URL`, `ADMIN_API_TOKEN` for intake; `ADMIN_EMAIL`,
`ADMIN_PASSWORD` for the admin token the expiry sweep needs).

## 0. Two panels

Every post goes to **both** panels: the primary (`ADMIN_API_URL`,
admin.applyforme.us) and the mirror (`ADMIN_API_MIRROR_URL`, admin.yorrp.com).
Same backend, same `x-jobs-key`, but different databases, so each has its own
dedup state: `admin_sent_jobs.json` (primary) and `admin_sent_jobs.mirror.json`
(mirror). A row already on one panel still goes to the other if it's missing there.

- `--target primary` / `--target mirror` posts to one panel only.
- `python3 seed_mirror_state.py` rebuilds the mirror state from the jobs live
  on the mirror (read-only, merges; safe to rerun). Run it if the state file is lost.
- `job_scraper.py` posts to both automatically when `ADMIN_API_MIRROR_URL` is set.

## 1. Post a new jobs CSV to the panels

The input must be payload-shaped: `company,role,platform,location,remote,salaryMin,
salaryMax,seniority,visaSponsorship,employmentType,description,requiredSkills,tags,
sourceLink,active`.

```bash
# a. normalise the data (never edits the source file)
python3 clean_jobs_csv.py --csv NEW_JOBS.csv \
                          --out NEW_JOBS_clean.csv \
                          --dropped-out NEW_JOBS_dropped.csv

# b. add industry / category / subCategory (~4 min for 3k rows)
python3 categorize_jobs.py --csv NEW_JOBS_clean.csv --out NEW_JOBS_ready.csv

# c. dry run: builds every payload, calls nothing
python3 send_csv_to_admin_api.py --csv NEW_JOBS_ready.csv --dry-run

# d. back up the dedup state, then post for real (~1.1s/job: 3k rows ≈ 55 min)
cp admin_sent_jobs.json admin_sent_jobs.json.bak-$(date +%Y%m%d-%H%M)
cp admin_sent_jobs.mirror.json admin_sent_jobs.mirror.json.bak-$(date +%Y%m%d-%H%M)
nohup python3 -u send_csv_to_admin_api.py --csv NEW_JOBS_ready.csv > post.log 2>&1 &
tail -f post.log

# e. progress at any time (baseline = key count before the run)
python3 -c "import json;print(len(json.load(open('admin_sent_jobs.json'))))"
```

Expect one `sent=N skipped_dup=0 failed=0` line per panel, and `invalid=0`. `invalid` counts rows the DTO
would reject (missing company or role); `skipped_dup` counts sourceLinks already
in `admin_sent_jobs.json`.

## 2. Verify what landed

```bash
python3 verify_panel.py                  # re-reads the live panel, checks the 5 invariants
python3 verify_panel.py --panel mirror   # same, on admin.yorrp.com
```

## 3. Expire dead postings

```bash
# a. pull the panel's active jobs (resumable, checkpoints every page)
python3 expire_jobs.py --fetch

# b. check the links — nothing is modified by this phase
python3 expire_jobs.py --check --workers 8                     # everything
python3 expire_jobs.py --check --host linkedin.com             # one platform
python3 expire_jobs.py --check --created-before 2026-08-27     # one cohort
python3 expire_jobs.py --check --browser                       # + render Indeed in Chromium
python3 expire_jobs.py --check --browser --max-age-days 30     # + retire unverifiable old ones

# c. see exactly what would be deactivated, and on what evidence
python3 expire_jobs.py --report --limit 50

# d. apply: PATCH active=false, resumable, never repeats an id
python3 expire_jobs.py --apply
python3 expire_jobs.py --apply --limit 20    # try a handful first
```

Useful flags: `--limit N` (sample size), `--workers N` (parallel HTTP checks),
`--proxy URL` or `EXPIRE_PROXY_URL` in `.env` (needed for Indeed — it answers
datacentre IPs with 401/403 even in a real browser), `--include-inactive`.

State lives in `expire_jobs_state/`:

| file | holds |
| --- | --- |
| `jobs.json` | the fetched panel snapshot |
| `checks.jsonl` | one line per concluded check, with its evidence string |
| `deactivated.json` | ids already PATCHed, so a rerun never repeats one |
| `cursor.json` | last fetch cursor |

`unknown` verdicts are deliberately not cached — the next run retries them.
Every run checks **every active job**, whenever it was posted or last checked:
`alive` verdicts are not reused (`--recheck-alive-days`, default 0). Before
2026-09-28 they were cached forever, and jobs that closed later stayed visible.
`expired` is final. The mirror reuses only primary verdicts from the last
`--reuse-primary-hours` (default 6), i.e. from the primary sweep just run.
Always `--fetch` first so newly posted jobs are included.
Delete `checks.jsonl` to force a full re-check.

### Both panels

`expire_jobs.py` and `verify_panel.py` take `--panel primary|mirror` (default
primary). The mirror keeps its sweep state in `expire_jobs_state_mirror/` and logs
in with `ADMIN_MIRROR_EMAIL` / `ADMIN_MIRROR_PASSWORD`. Sweep the primary first:
the mirror's `--check` reuses the primary's verdict for any link it has already
visited (evidence reads `same link on primary panel: ...`), so only
mirror-only links get visited.

```bash
python3 expire_jobs.py --panel mirror --fetch --check --browser --apply
python3 verify_panel.py --panel mirror
```

## 4. Daily automation

`./install_cron.sh` installs everything (safe to rerun):

```cron
@reboot sleep 180 && run_catchup.sh          # daily scrape -> post, if yesterday not done yet
30 7-22 * * * run_catchup.sh
@reboot sleep 600 && run_expire_catchup.sh   # daily expiry sweep, once per day
45 7-22 * * * run_expire_catchup.sh
```

`run_expire.sh` sweeps the primary then the mirror, writes today's date to
`daily_runs/last_expire_success`, runs `repair_panel.py --apply` on both (fixes missing or
invalid taxonomy, tops tags up to 7, deactivates duplicate active copies), then runs
`verify_panel.py` on both. `python3 repair_panel.py` without `--apply` only prints the plan. It waits on the
daily run's lock, so a scrape and a sweep never overlap. Log: `daily_runs/expire.log`.
The sweep runs daily because roughly 6% of a fresh batch is already closed at the
source on the day it is posted.
