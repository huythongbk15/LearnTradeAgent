#!/usr/bin/env bash
# The weekly NL-P paper log, started by Windows Task Scheduler on Monday at 04:07
# UTC (scripts/ops/windows/register_tasks.ps1), seven minutes after the paper
# entry bar opens. It only writes records under data/newperp: no order, no key, no
# private call, nothing the runner reads. Rules: docs/operations/NEWPERP_PAPER_RUN.md.
# Arguments reach tools.newperp.run (--as-of).
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"
LOG="$ROOT/logs/newperp_weekly.log"
[ -n "${NEWPERP_ROOT:-}" ] && LOG="$ROOT/logs/newperp_rehearsal.log"
mkdir -p "$ROOT/logs"
echo "=== $(date -u +%FT%TZ) newperp start pid=$$" >> "$LOG"
# nice 19: this machine also runs the testnet cycle. timeout is the real limit, 25
# minutes, inside the task's hour. rc 124 or 137 means it was stopped here.
PYTHONPATH="$ROOT/src:$ROOT" timeout -k 60 1500 nice -n 19 "$ROOT/.venv/bin/python" -u \
    -m tools.newperp.run "$@" >> "$LOG" 2>&1
rc=$?
echo "=== $(date -u +%FT%TZ) newperp end rc=$rc" >> "$LOG"
exit "$rc"
