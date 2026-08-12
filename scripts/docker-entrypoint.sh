#!/usr/bin/env bash
# Container entrypoint: apply pending migrations, then exec the service command.
# Set RUN_MIGRATIONS=false on containers that should not migrate.
set -euo pipefail

if [ "${RUN_MIGRATIONS:-true}" = "true" ]; then
  echo "[entrypoint] alembic upgrade head"
  alembic upgrade head
  echo "[entrypoint] schema up to date"
fi

exec "$@"
