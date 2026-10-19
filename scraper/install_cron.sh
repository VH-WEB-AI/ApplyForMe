#!/bin/sh
# Installs (or refreshes) the two cron entries for the jobs automation. Safe to rerun.
D=/home/vhits/applyForMe/scraper
( crontab -l 2>/dev/null | grep -v "# applyforme-jobs"
  echo "0 6 * * * $D/run_daily.sh # applyforme-jobs daily scrape->post"
  echo "0 14 * * 0 $D/run_weekly_expire.sh # applyforme-jobs weekly expiry sweep"
) | crontab -
crontab -l | grep applyforme-jobs
