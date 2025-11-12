#!/usr/bin/env sh
set -euo pipefail

export PYTHONPATH="/app:${PYTHONPATH:-}"

# Wait for the database to be ready if DATABASE_URL is set
python - <<'PY'
import os, time, sys
from sqlalchemy import create_engine, text
url = os.environ.get('DATABASE_URL') or os.environ.get('database_url') or os.environ.get('DATABASE_URI')
if not url:
    print('No database URL set, skipping DB wait')
    sys.exit(0)
engine = create_engine(url, pool_pre_ping=True)
for i in range(60):
    try:
        with engine.connect() as conn:
            conn.execute(text('SELECT 1'))
        print('Database is ready')
        sys.exit(0)
    except Exception as e:
        print(f'Waiting for database... ({i+1}/60)')
        time.sleep(1)
print('Database is not ready after waiting 60s', file=sys.stderr)
sys.exit(1)
PY

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
