#!/usr/bin/env sh
set -eu

export PYTHONPATH="/app:${PYTHONPATH:-}"

python - <<'PY'
import os, sys, time
from urllib.parse import urlparse, urlunparse
try:
    import psycopg
    from psycopg import sql
except Exception:
    sys.exit(0)

db_url = os.environ.get("DATABASE_URL")
if not db_url:
    sys.exit(0)

url = db_url.replace("postgresql+psycopg://", "postgresql://")
p = urlparse(url)
target_db = (p.path[1:] if p.path else "") or "postgres"
admin_url = urlunparse((p.scheme, f"{p.username}:{p.password}@{p.hostname}:{p.port}", "/postgres", "", "", ""))

deadline = time.time() + 60
while True:
    try:
        with psycopg.connect(admin_url) as conn:
            conn.autocommit = True
            with conn.cursor() as cur:
                cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (target_db,))
                exists = cur.fetchone() is not None
            if not exists:
                with conn.cursor() as cur:
                    cur.execute(sql.SQL("CREATE DATABASE {}" ).format(sql.Identifier(target_db)))
        break
    except Exception as e:
        if time.time() > deadline:
            print(f"Database init failed: {e}", file=sys.stderr)
            sys.exit(1)
        time.sleep(2)
PY

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
