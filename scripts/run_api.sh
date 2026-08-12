#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

# API_PORT lives in .env, which Python's pydantic-settings loads on its own —
# bash never sources it, so read it here explicitly (a plain env var still
# wins if one is already exported).
if [ -z "${API_PORT:-}" ] && [ -f .env ]; then
  # grep exits 1 when .env has no API_PORT= line (falling back to uvicorn's
  # own default below) — under set -e that would otherwise kill the script
  # silently, so don't let a no-match abort here.
  API_PORT="$(grep -m1 '^API_PORT=' .env | cut -d= -f2- || true)"
fi

if [ "${RUN_MIGRATIONS:-true}" = "true" ]; then
  uv run alembic upgrade head
fi

exec uv run uvicorn app.main:app --host 0.0.0.0 --port "${API_PORT:-8090}" --reload
