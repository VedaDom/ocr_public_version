#!/usr/bin/env sh
set -eu

export PYTHONPATH="/app:${PYTHONPATH:-}"

# Wait for Postgres if DATABASE_URL is set
if [ -n "${DATABASE_URL:-}" ]; then
  echo "Waiting for database to be ready..."
  python - <<'PY'
import os, time, sys
from sqlalchemy import create_engine, text

url = os.environ.get('DATABASE_URL')
if not url:
    sys.exit(0)

deadline = time.time() + 120
while time.time() < deadline:
    try:
        eng = create_engine(url)
        with eng.connect() as conn:
            conn.execute(text("SELECT 1"))
        print("Database is ready", flush=True)
        sys.exit(0)
    except Exception:
        time.sleep(2)
print("ERROR: Database not ready in time", file=sys.stderr)
sys.exit(1)
PY
fi

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
