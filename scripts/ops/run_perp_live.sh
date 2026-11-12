#!/usr/bin/env bash
# Perp tier R0: book KRW alone on Binance USD-M mainnet (docs/operations/PERP_LIVE_R0.md), started every
# 5 minutes by Windows Task Scheduler (scripts/ops/windows/register_tasks.ps1 -Only PerpLive), as the
# separate WSL user that owns the live key (D10), from that user's own checkout pinned to a tag.
# Most runs only read public data and sign nothing; scripts/live_perp_r0.py decides.
# The env file (PERP_LIVE_ENV_FILE, default ~/.perp_live.env, mode 600) holds every switch, the key's
# names, the size and LIVE_SAFETY_HMAC_KEY. This wrapper takes no arguments: nothing typed by hand
# (--resume) ever rides on the schedule.
# Unlike the Demo wrappers it does not keep out of the testnet cycle's window: book KRW's entry window
# is 15 minutes after E, and this run is one light process.
set -uo pipefail
if [ "$#" -ne 0 ]; then
    echo "run_perp_live.sh takes no arguments" >&2
    exit 2
fi
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"
ENV_FILE="${PERP_LIVE_ENV_FILE:-$HOME/.perp_live.env}"
LOG="$ROOT/logs/perp_live.log"
mkdir -p "$ROOT/logs"
# One run at a time: a run still going makes the next one skip.
exec 9>"$ROOT/logs/.perp_live.lock"
flock -n 9 || exit 0
echo "=== $(date -u +%FT%TZ) perp_live start pid=$$" >> "$LOG"
# No timeout: a signed run is never cut between an order and its record; the next run reconciles first.
PYTHONPATH="$ROOT/src:$ROOT" "$ROOT/.venv/bin/python" -u scripts/live_perp_r0.py \
    --execute --confirm-live LIVE_TRADING_WITH_REAL_MONEY --env-file "$ENV_FILE" >> "$LOG" 2>&1
rc=$?
echo "=== $(date -u +%FT%TZ) perp_live end rc=$rc" >> "$LOG"
exit "$rc"
