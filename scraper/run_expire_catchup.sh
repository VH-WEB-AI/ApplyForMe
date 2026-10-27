#!/bin/sh
# Daily expiry sweep, run at most once a day: skip if both panels were already swept today
# (last_expire_success) or a sweep is in progress. Cron: @reboot + hourly 07:45-22:45, so a
# day the machine was off is picked up on the next boot.
cd "$(dirname "$0")" || exit 1
[ "$(cat daily_runs/last_expire_success 2>/dev/null)" = "$(date +%F)" ] && exit 0
pgrep -f expire_jobs.py >/dev/null && exit 0
exec ./run_expire.sh
