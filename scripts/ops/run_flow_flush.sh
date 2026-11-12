#!/usr/bin/env bash
# The 5-minute modes of book FLOW01 (H-FLOW-BTC-01) and book KRW (H-KRW-FADE-16) on Binance USD-M
# Demo Trading, started every 5 minutes by Windows Task Scheduler (scripts/ops/windows/register_tasks.ps1
# -Only FlowFlush). FLOW01 runs first, then KRW, one after the other under the same lock.
# live_perp_demo.py --only flow01 (or --only krw) first asks public data and that book's record, without
# .env or any key, whether a signed run is needed: for FLOW01 an exit due, an order left unsettled, or a
# fresh event; for KRW an event inside its entry window, an exit due, or an order left unsettled. Most
# runs end there and sign nothing. A signed run reconciles, checks every book's legs against the venue
# (so every book's flag is passed, with the hourly run's sizes), then steps that book alone.
# Rules: docs/operations/PERP_DEMO_RUNNER.md.
# Execution switches (TRADING_EXECUTION_ENABLED, TRADING_KILL_SWITCH,
# TRADING_ENTRY_KILL_SWITCH) and the demo key come from .env, as for the hourly run.
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"
LOG="$ROOT/logs/flow_flush.log"
mkdir -p "$ROOT/logs"
# The testnet cycle starts at :01 of every odd UTC hour: no run starts from :50
# of the even hour to :06 of the odd hour. Asked again before the second run.
testnet_window() {
    local h m
    h=$((10#$(date -u +%H))); m=$((10#$(date -u +%M)))
    { [ $((h % 2)) -eq 0 ] && [ "$m" -ge 50 ]; } || { [ $((h % 2)) -eq 1 ] && [ "$m" -lt 6 ]; }
}
if testnet_window; then
    exit 0
fi
# The hourly run's lock (scripts/ops/run_perp_demo.sh): while the hourly run or an
# earlier 5-minute run is going, this one skips. Both runs below hold it.
exec 9>"$ROOT/logs/.perp_demo.lock"
flock -n 9 || exit 0
echo "=== $(date -u +%FT%TZ) flow_flush start pid=$$" >> "$LOG"
# No timeout, as for the hourly run: an --execute run is never cut between an order
# and its record; the lock keeps the next run out, and the next run reconciles first.
TRADING_MODE=testnet PYTHONPATH="$ROOT/src:$ROOT" OMP_NUM_THREADS=4 nice -n 19 "$ROOT/.venv/bin/python" -u \
    scripts/live_perp_demo.py --execute --only flow01 --lv --learn --flow02 --flow01 --krw --pair-usd 500 \
    --krw-notice-share /var/tmp/perp_notice_share/upbit_page1.json "$@" >> "$LOG" 2>&1
rc=$?
# Book KRW's 5-minute mode, after FLOW01 and under the same lock: no new task.
if ! testnet_window; then
    TRADING_MODE=testnet PYTHONPATH="$ROOT/src:$ROOT" OMP_NUM_THREADS=4 nice -n 19 "$ROOT/.venv/bin/python" -u \
        scripts/live_perp_demo.py --execute --only krw --lv --learn --flow02 --flow01 --krw --pair-usd 500 \
        --krw-notice-share /var/tmp/perp_notice_share/upbit_page1.json "$@" >> "$LOG" 2>&1
    rc_krw=$?
    [ "$rc" -eq 0 ] && rc=$rc_krw
fi
echo "=== $(date -u +%FT%TZ) flow_flush end rc=$rc" >> "$LOG"
exit "$rc"
