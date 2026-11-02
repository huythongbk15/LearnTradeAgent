#!/usr/bin/env bash
# The weekly paper selection run, started by Windows Task Scheduler on Monday at
# 00:07 UTC (scripts/ops/windows/register_tasks.ps1). It only writes records under
# data/selection: no order, no key, no private call, nothing the runner reads.
# Rules and operation: docs/operations/SELECTION_PAPER_RUN.md. Arguments reach
# tools.selection.run_weekly (--as-of, --no-fetch, --seed-only).
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"
LOG="$ROOT/logs/selection_weekly.log"
mkdir -p "$ROOT/logs"
echo "=== $(date -u +%FT%TZ) selection start pid=$$" >> "$LOG"
# A signal to this script leaves a line saying so. Stopping the Windows task sends
# none: it ends conhost and leaves the Linux job running.
for sig in HUP INT TERM; do
    trap "echo \"=== \$(date -u +%FT%TZ) selection killed by SIG$sig\" >> \"$LOG\"; exit 1" "$sig"
done
# nice 19: this machine also runs the testnet cycle, and heavy work has failed it
# through its latency gate. -u: unbuffered, so the log shows progress. timeout is
# the real limit of the run, 50 minutes, inside the task's hour: a stuck job would
# hold the lock and cost next Monday. rc 124 or 137 means it was stopped here.
PYTHONPATH="$ROOT/src:$ROOT" timeout -k 60 3000 nice -n 19 "$ROOT/.venv/bin/python" -u \
    -m tools.selection.run_weekly "$@" >> "$LOG" 2>&1
rc=$?
echo "=== $(date -u +%FT%TZ) selection end rc=$rc" >> "$LOG"
exit "$rc"
