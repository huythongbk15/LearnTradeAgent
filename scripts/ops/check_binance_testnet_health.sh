#!/usr/bin/env bash
# Exit 0 when execution runs keep arriving and finish cleanly; 2 otherwise.
# Called from Windows (scripts/ops/windows/check_health.ps1) so that a dead
# WSL or a dead schedule is still reported by something outside WSL.
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"
exec "$ROOT/.venv/bin/python" scripts/check_live_audit.py \
    --audit-log data/execution/binance_live_audit.jsonl "$@"
