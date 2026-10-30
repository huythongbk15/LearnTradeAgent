#!/usr/bin/env bash
# One Binance testnet execution cycle, started by Windows Task Scheduler
# (scripts/ops/windows/register_tasks.ps1). WSL has no systemd and its cron
# daemon does not start by itself, which silently stopped the runner from
# 2026-08-12 to 2026-10-29; Task Scheduler also wakes the WSL VM.
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"
LOG="$ROOT/logs/binance_testnet.log"
mkdir -p "$ROOT/logs"
echo "=== $(date -u +%FT%TZ) cycle start pid=$$" >> "$LOG"
# A closed console or a stopped task kills the cycle; leave a line saying so.
for sig in HUP INT TERM; do
    trap "echo \"=== \$(date -u +%FT%TZ) cycle killed by SIG$sig\" >> \"$LOG\"; exit 1" "$sig"
done
# -u: unbuffered, so the log shows progress while the run is still going.
TRADING_MODE=testnet "$ROOT/.venv/bin/python" -u scripts/live_enhanced_ma_binance.py \
    --testnet --profile testnet --execute \
    --state-file data/binance_testnet_risk_state_v2.json >> "$LOG" 2>&1
rc=$?
echo "=== $(date -u +%FT%TZ) cycle end rc=$rc" >> "$LOG"
exit "$rc"
