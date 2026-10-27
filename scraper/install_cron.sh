#!/bin/sh
# Installs (or refreshes) the cron entries for the jobs automation. Safe to rerun.
D="$(cd "$(dirname "$0")" && pwd)"
chmod +x "$D"/run_daily.sh "$D"/run_catchup.sh "$D"/run_expire.sh "$D"/run_expire_catchup.sh
( crontab -l 2>/dev/null | grep -v "# applyforme-jobs"
  echo "0 6 * * * $D/run_daily.sh # applyforme-jobs daily scrape->post"
  echo "@reboot sleep 180 && $D/run_catchup.sh # applyforme-jobs catch-up"
  echo "30 7-22 * * * $D/run_catchup.sh # applyforme-jobs catch-up"
  echo "@reboot sleep 600 && $D/run_expire_catchup.sh # applyforme-jobs daily expiry sweep"
  echo "45 7-22 * * * $D/run_expire_catchup.sh # applyforme-jobs daily expiry sweep"
) | crontab -
crontab -l | grep applyforme-jobs
