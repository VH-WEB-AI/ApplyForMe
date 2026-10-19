#!/bin/sh
# Weekly expiry sweep on both panels (primary first; the mirror reuses its verdicts).
# Waits for the daily run's lock so the two never overlap. --recheck-alive-days 1 lets an
# interrupted sweep resume without rechecking what it already covered today.
cd /home/vhits/applyForMe/scraper || exit 1
mkdir -p daily_runs
exec flock -w 14400 daily_runs/.lock sh -c '
  .venv/bin/python -u expire_jobs.py --fetch --check --browser --recheck-alive-days 1 --apply &&
  .venv/bin/python -u expire_jobs.py --panel mirror --fetch --check --browser --recheck-alive-days 1 --apply &&
  .venv/bin/python verify_panel.py > daily_runs/verify_primary_latest.log 2>&1 &&
  .venv/bin/python verify_panel.py --panel mirror > daily_runs/verify_mirror_latest.log 2>&1
' >> daily_runs/expire_weekly.log 2>&1
