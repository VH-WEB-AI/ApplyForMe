#!/bin/sh
# Cron entry point: everything (skip-if-done, retry, alerts) lives in daily_pipeline.py.
cd "$(dirname "$0")" || exit 1
mkdir -p daily_runs
exec .venv/bin/python -u daily_pipeline.py "$@" >> daily_runs/cron.log 2>&1
