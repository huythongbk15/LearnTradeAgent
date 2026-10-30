#!/usr/bin/env bash
# The daily check, started by Windows Task Scheduler
# (scripts/ops/windows/register_tasks.ps1). Each day's full report is kept in
# logs/daily_check/; the one line printed here becomes the notification.
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"
exec "$ROOT/.venv/bin/python" scripts/ops/daily_check.py \
    --report "logs/daily_check/$(date -u +%F).txt" "$@"
