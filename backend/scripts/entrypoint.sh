#!/usr/bin/env bash
# Container entrypoint:
# 1. Wait briefly for Postgres (compose healthcheck should already gate us).
# 2. Run pending Alembic migrations.
# 3. Hand off to the command passed by docker (uvicorn or celery).

set -euo pipefail

echo "[entrypoint] running alembic migrations…"
alembic upgrade head

echo "[entrypoint] launching: $*"
exec "$@"
