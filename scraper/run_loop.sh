#!/bin/sh
# Runs job_scraper.py on a fixed interval, writing a timestamped CSV to
# ./output and (if BACKEND_API_URL is set) POSTing every scraped row there.
set -eu

INTERVAL="${SCRAPE_INTERVAL_SECONDS:-86400}"
LIMIT="${LIMIT_PER_PORTAL:-25}"
mkdir -p /app/output

while true; do
  stamp=$(date -u +%Y%m%dT%H%M%SZ)
  echo "[run_loop] starting scrape at ${stamp}"
  python3 job_scraper.py --out "/app/output/jobs_${stamp}.csv" --limit "$LIMIT"
  echo "[run_loop] done, sleeping ${INTERVAL}s"
  sleep "$INTERVAL"
done
