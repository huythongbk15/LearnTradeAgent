#!/usr/bin/env bash
# The forced selling paper log, started every 5 minutes by Windows Task Scheduler
# (scripts/ops/windows/register_tasks.ps1). It only writes records under
# data/flowbid: no order, no key, no private call, nothing the runner reads.
# Rules: docs/operations/FLOWBID_PAPER_RUN.md. Arguments reach tools.flowbid.run (--now).
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"
LOG="$ROOT/logs/flowbid.log"
[ -n "${FLOWBID_ROOT:-}" ] && LOG="$ROOT/logs/flowbid_rehearsal.log"
mkdir -p "$ROOT/logs"
# The testnet cycle starts at :01 of every odd UTC hour. From :50 of the even hour
# to :06 of the odd hour no run starts, so a run cut at its 4 minute limit is over
# by :54; the next run judges the skipped minutes, since a run covers every closed
# minute since the last covered one.
if [ -z "${FLOWBID_ROOT:-}" ]; then
    h=$((10#$(date -u +%H))); m=$((10#$(date -u +%M)))
    if { [ $((h % 2)) -eq 0 ] && [ "$m" -ge 50 ]; } || { [ $((h % 2)) -eq 1 ] && [ "$m" -lt 6 ]; }; then
        exit 0
    fi
fi
# One run at a time: a run still going when the next is due makes the next one skip.
exec 9>"$ROOT/logs/.flowbid.lock"
flock -n 9 || exit 0
echo "=== $(date -u +%FT%TZ) flowbid start pid=$$" >> "$LOG"
# nice 19 for the testnet cycle on this machine. timeout 4 minutes, inside the
# 5 minute step. rc 124 or 137 means it was stopped here.
PYTHONPATH="$ROOT/src:$ROOT" timeout -k 20 240 nice -n 19 "$ROOT/.venv/bin/python" -u \
    -m tools.flowbid.run "$@" >> "$LOG" 2>&1
rc=$?
echo "=== $(date -u +%FT%TZ) flowbid end rc=$rc" >> "$LOG"
exit "$rc"
