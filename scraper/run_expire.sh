#!/bin/sh
# Daily expiry sweep on both panels (primary first; the mirror reuses its verdicts), then
# repair_panel.py fixes what verify_panel.py would flag (taxonomy, <7 tags, duplicates) and
# verify_panel.py records the result. Repair and verify run even if the sweep failed.
# Waits for the daily run's lock so the two never overlap. --recheck-alive-days 1 lets an
# interrupted sweep resume without rechecking what it already covered today.
# last_expire_success records the date both panels were swept (run_expire_catchup.sh skips
# the day once it is set).
cd "$(dirname "$0")" || exit 1
mkdir -p daily_runs
exec flock -w 14400 daily_runs/.lock sh -c '
  .venv/bin/python -u expire_jobs.py --fetch --check --browser --recheck-alive-days 1 --apply &&
  .venv/bin/python -u expire_jobs.py --panel mirror --fetch --check --browser --recheck-alive-days 1 --apply &&
  date +%F > daily_runs/last_expire_success
  .venv/bin/python -u repair_panel.py --apply
  .venv/bin/python -u repair_panel.py --panel mirror --apply
  .venv/bin/python verify_panel.py > daily_runs/verify_primary_latest.log 2>&1
  .venv/bin/python verify_panel.py --panel mirror > daily_runs/verify_mirror_latest.log 2>&1
' >> daily_runs/expire.log 2>&1
