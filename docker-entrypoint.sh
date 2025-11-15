#!/usr/bin/env sh
set -eu

export PYTHONPATH="/app:${PYTHONPATH:-}"

# Init database schema
if [ "${USE_ALEMBIC:-false}" = "true" ]; then
  alembic upgrade head
else
  python -c 'from app.infrastructure.db import create_all; create_all()'
fi

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
