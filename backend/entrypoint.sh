#!/bin/sh
set -e

alembic upgrade head || echo "WARNING: alembic upgrade head failed - starting anyway, check logs above"

exec uvicorn app.main:app --host 0.0.0.0 --port 8000
