#!/usr/bin/env bash
# The new perp pair book and book LV on Binance USD-M Demo Trading, and the shadow ledger on
# mainnet prices, started every hour by Windows Task Scheduler
# (scripts/ops/windows/register_tasks.ps1). Rules: docs/operations/PERP_DEMO_RUNNER.md.
# Execution switches (TRADING_EXECUTION_ENABLED, TRADING_KILL_SWITCH,
# TRADING_ENTRY_KILL_SWITCH) and the demo key come from .env, as for the spot runner.
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"
LOG="$ROOT/logs/perp_demo.log"
mkdir -p "$ROOT/logs"
# The testnet cycle starts at :01 of every odd UTC hour: no run starts from :50
# of the even hour to :06 of the odd hour.
h=$((10#$(date -u +%H))); m=$((10#$(date -u +%M)))
if { [ $((h % 2)) -eq 0 ] && [ "$m" -ge 50 ]; } || { [ $((h % 2)) -eq 1 ] && [ "$m" -lt 6 ]; }; then
    exit 0
fi
# One run at a time: a run still going makes the next one skip.
exec 9>"$ROOT/logs/.perp_demo.lock"
flock -n 9 || exit 0
echo "=== $(date -u +%FT%TZ) perp_demo start pid=$$" >> "$LOG"
# No timeout: an --execute run is never cut between an order and its record;
# the lock keeps the next run out, and the next run reconciles first.
TRADING_MODE=testnet PYTHONPATH="$ROOT/src:$ROOT" nice -n 19 "$ROOT/.venv/bin/python" -u \
    scripts/live_perp_demo.py --execute --shadow --lv "$@" >> "$LOG" 2>&1
rc=$?
echo "=== $(date -u +%FT%TZ) perp_demo end rc=$rc" >> "$LOG"
exit "$rc"
