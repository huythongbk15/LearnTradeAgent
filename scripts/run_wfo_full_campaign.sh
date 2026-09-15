#!/usr/bin/env bash
# WFO rerun campaign: 3 fixed strategies x 6 pairs = 18 canonical WFO jobs.
# Concurrency-limited; per-job logs + hard timeout guards; result-file manifest.
#
# Each job runs the user's exact command:
#   python scripts/run_wfo_parallel.py --strategy S --symbol SYM --timeframe TF \
#       --cost 1x --out data/backtests/wfo_full/
#
# Usage:
#   MAX_CONCURRENT=3 PER_JOB_TIMEOUT=2400 bash scripts/run_wfo_full_campaign.sh
set -uo pipefail

WS=/home/huythong/.qwenpaw/workspaces/trading
cd "$WS"

OUT=data/backtests/wfo_full
LOG=data/backtests/wfo_full_logs
mkdir -p "$LOG" "$OUT"

PY="$WS/.venv/bin/python"
RUN="$WS/scripts/run_wfo_parallel.py"

# Capture the orchestrator's own stdout+stderr to a report file (per-job logs
# are already written separately). controlled_exec also pipes this stdout.
exec > >(tee "$LOG/campaign_report.txt") 2>&1

MAX_CONCURRENT="${MAX_CONCURRENT:-3}"
PER_JOB_TIMEOUT="${PER_JOB_TIMEOUT:-2400}"   # per single WFO run, seconds

# strategy|symbol|timeframe
JOBS=(
"range_mean_reversion|BTC/USDT|1h"
"range_mean_reversion|BTC/USDT|4h"
"range_mean_reversion|ETH/USDT|4h"
"range_mean_reversion|SOL/USDT|4h"
"range_mean_reversion|AVAX/USDT|4h"
"range_mean_reversion|BNB/USDT|4h"
"volatility_breakout|BTC/USDT|1h"
"volatility_breakout|BTC/USDT|4h"
"volatility_breakout|ETH/USDT|4h"
"volatility_breakout|SOL/USDT|4h"
"volatility_breakout|AVAX/USDT|4h"
"volatility_breakout|BNB/USDT|4h"
"funding_carry|BTC/USDT|1h"
"funding_carry|BTC/USDT|4h"
"funding_carry|ETH/USDT|4h"
"funding_carry|SOL/USDT|4h"
"funding_carry|AVAX/USDT|4h"
"funding_carry|BNB/USDT|4h"
)

TOTAL=${#JOBS[@]}
echo "[CAMPAIGN START $(date -u +%FT%TZ)] total=$TOTAL max_concurrent=$MAX_CONCURRENT per_job_timeout=${PER_JOB_TIMEOUT}s"

manifest="$LOG/manifest.tsv"
: > "$manifest"

run_one() {
  local strat="$1" sym="$2" tf="$3"
  local safe="${strat}__$(echo "$sym" | tr -d '/')__${tf}"
  local logfile="$LOG/${safe}.log"
  {
    echo "[START $(date -u +%H:%M:%S)] $strat $sym $tf  (cmd: $PY $RUN --strategy $strat --symbol $sym --timeframe $tf --cost 1x --out $OUT)"
  } > "$logfile"
  # Hard timeout guard per job: TERM at PER_JOB_TIMEOUT, KILL 60s later.
  timeout -k 60 "${PER_JOB_TIMEOUT}" \
    $PY "$RUN" --strategy "$strat" --symbol "$sym" --timeframe "$tf" \
        --cost 1x --out "$OUT" \
    >> "$logfile" 2>&1
  local rc=$?
  echo "[END rc=$rc $(date -u +%H:%M:%S)] $strat $sym $tf" >> "$logfile"
  printf '%s\t%s\t%s\t%s\n' "$strat" "$sym" "$tf" "$rc" >> "$manifest"
  return 0
}

for job in "${JOBS[@]}"; do
  IFS='|' read -r strat sym tf <<< "$job"
  run_one "$strat" "$sym" "$tf" &
  # throttle to MAX_CONCURRENT running jobs
  while [ "$(jobs -rp | wc -l)" -ge "$MAX_CONCURRENT" ]; do
    wait -n 2>/dev/null || sleep 5
  done
done

wait  # drain remaining
echo "[CAMPAIGN END $(date -u +%FT%TZ)] all jobs finished."

echo
echo "=== MANIFEST (rc per job) ==="
awk -F'\t' '{printf "%-22s %-12s %-4s -> rc=%s\n", $1, $2, $3, $4}' "$manifest" | sort

echo
echo "=== PER-JOB VERDICT (from run_wfo_parallel stdout) ==="
for job in "${JOBS[@]}"; do
  IFS='|' read -r strat sym tf <<< "$job"
  local safe="${strat}__$(echo "$sym" | tr -d '/')__${tf}"
  local logfile="$LOG/${safe}.log"
  if [ -f "$logfile" ]; then
    status=$(grep -oE 'Status: (PASS|FAIL)' "$logfile" | tail -1)
    verdict=$(grep -oE 'Verdict: .*' "$logfile" | tail -1)
    sharpe=$(grep -oE 'Median Sharpe: .*$' "$logfile" | tail -1)
    elapsed=$(grep -oE 'Elapsed: .*' "$logfile" | tail -1)
    printf '%-22s %-12s %-4s | %s | %s | %s | %s\n' "$strat" "$sym" "$tf" "${status:-NO_STATUS}" "${verdict:-?}" "${sharpe:-?}" "${elapsed:-?}"
  else
    printf '%-22s %-12s %-4s | NO LOG\n' "$strat" "$sym" "$tf"
  fi
done

echo
echo "=== RC summary ==="
awk -F'\t' '{print "rc="$4}' "$manifest" | sort | uniq -c
echo "[CAMPAIGN DONE]"
