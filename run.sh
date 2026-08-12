#!/usr/bin/env bash
# Starts aptitude-assessment natively on port 8090 (API_PORT in .env).
# Thin wrapper around scripts/run_api.sh, kept for consistency with every other
# project's top-level run.sh (dev.sh calls this). See scripts/run_worker.sh for
# the standalone signup consumer / key-pool CLI.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
exec ./scripts/run_api.sh
