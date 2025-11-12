#!/usr/bin/env sh
set -euo pipefail

export PYTHONPATH="/app:${PYTHONPATH:-}"

# Run migrations
alembic upgrade head

# Start API server
UVICORN_HOST="${UVICORN_HOST:-0.0.0.0}"
UVICORN_PORT="${UVICORN_PORT:-8000}"
UVICORN_LOG_LEVEL="${UVICORN_LOG_LEVEL:-info}"
UVICORN_WORKERS="${UVICORN_WORKERS:-}"

if [ -n "$UVICORN_WORKERS" ]; then
  exec uvicorn app.main:app --host "$UVICORN_HOST" --port "$UVICORN_PORT" --log-level "$UVICORN_LOG_LEVEL" --workers "$UVICORN_WORKERS"
else
  exec uvicorn app.main:app --host "$UVICORN_HOST" --port "$UVICORN_PORT" --log-level "$UVICORN_LOG_LEVEL"
fi
