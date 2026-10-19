#!/bin/sh
# Installs (or refreshes) the cron entries for the jobs automation. Safe to rerun.
D="$(cd "$(dirname "$0")" && pwd)"
chmod +x "$D"/run_daily.sh "$D"/run_catchup.sh "$D"/run_weekly_expire.sh
( crontab -l 2>/dev/null | grep -v "# applyforme-jobs"
  echo "0 6 * * * $D/run_daily.sh # applyforme-jobs daily scrape->post"
  echo "0 14 * * 0 $D/run_weekly_expire.sh # applyforme-jobs weekly expiry sweep"
  echo "@reboot sleep 180 && $D/run_catchup.sh # applyforme-jobs catch-up"
  echo "30 7-22 * * * $D/run_catchup.sh # applyforme-jobs catch-up"
) | crontab -
crontab -l | grep applyforme-jobs
