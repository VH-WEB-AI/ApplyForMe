#!/bin/sh
# Catch-up for a missed 06:00 cron (machine was off): run the daily pipeline only if
# last_success.json is older than yesterday. Cron: @reboot + hourly 07:00-22:00.
cd /home/vhits/applyForMe/scraper || exit 1
want=$(date -d yesterday +%F)
through=$(.venv/bin/python -c "import json;print(json.load(open('daily_runs/last_success.json')).get('through',''))" 2>/dev/null)
[ "$through" \< "$want" ] || exit 0
pgrep -f daily_pipeline.py >/dev/null && exit 0
exec ./run_daily.sh
