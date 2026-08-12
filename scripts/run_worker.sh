#!/usr/bin/env bash
# Usage: ./scripts/run_worker.sh [signup|keys ...args|import_question_bank ...args|seed_question_bank]
set -euo pipefail

case "${1:-}" in
  signup)
    exec uv run python -m app.workers.signup_consumer
    ;;
  keys)
    shift
    exec uv run python -m app.workers.api_keys "$@"
    ;;
  import_question_bank)
    shift
    exec uv run python -m app.workers.import_question_bank "$@"
    ;;
  seed_question_bank)
    # Manual-only — not called from docker-entrypoint.sh or app startup. See
    # app/workers/seed_question_bank.py's module docstring.
    exec uv run python -m app.workers.seed_question_bank
    ;;
  *)
    echo "Usage: $0 [signup|keys ...|import_question_bank ...|seed_question_bank]" >&2
    exit 2
    ;;
esac
