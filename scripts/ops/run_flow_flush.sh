#!/usr/bin/env bash
# Book FLOW01 (H-FLOW-BTC-01) on Binance USD-M Demo Trading in its 5-minute mode, started
# every 5 minutes by Windows Task Scheduler (scripts/ops/windows/register_tasks.ps1 -Only FlowFlush).
# live_perp_demo.py --only flow01 first asks public mainnet data and FLOW01's record, without
# .env or any key, whether a signed run is needed: an exit due, an order left unsettled, or a
# fresh event. Most runs end there and sign nothing. A signed run reconciles, checks every
# book's legs against the venue (so every book's flag is passed, with the hourly run's sizes),
# then steps book FLOW01 alone. Rules: docs/operations/PERP_DEMO_RUNNER.md.
# Execution switches (TRADING_EXECUTION_ENABLED, TRADING_KILL_SWITCH,
# TRADING_ENTRY_KILL_SWITCH) and the demo key come from .env, as for the hourly run.
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"
LOG="$ROOT/logs/flow_flush.log"
mkdir -p "$ROOT/logs"
# The testnet cycle starts at :01 of every odd UTC hour: no run starts from :50
# of the even hour to :06 of the odd hour.
h=$((10#$(date -u +%H))); m=$((10#$(date -u +%M)))
if { [ $((h % 2)) -eq 0 ] && [ "$m" -ge 50 ]; } || { [ $((h % 2)) -eq 1 ] && [ "$m" -lt 6 ]; }; then
    exit 0
fi
# The hourly run's lock (scripts/ops/run_perp_demo.sh): while the hourly run or an
# earlier 5-minute run is going, this one skips.
exec 9>"$ROOT/logs/.perp_demo.lock"
flock -n 9 || exit 0
echo "=== $(date -u +%FT%TZ) flow_flush start pid=$$" >> "$LOG"
# No timeout, as for the hourly run: an --execute run is never cut between an order
# and its record; the lock keeps the next run out, and the next run reconciles first.
TRADING_MODE=testnet PYTHONPATH="$ROOT/src:$ROOT" OMP_NUM_THREADS=4 nice -n 19 "$ROOT/.venv/bin/python" -u \
    scripts/live_perp_demo.py --execute --only flow01 --lv --learn --flow02 --flow01 --pair-usd 500 "$@" >> "$LOG" 2>&1
rc=$?
echo "=== $(date -u +%FT%TZ) flow_flush end rc=$rc" >> "$LOG"
exit "$rc"
