#!/usr/bin/env bash
# Run remaining WFO strategies across 6 pairs with cost=1x
# 4 strategies × 6 pairs = 24 combos
# Runs 3 at a time (12 CPUs / 4 workers per run = 3 parallel)
set -euo pipefail

cd /home/huythong/.qwenpaw/workspaces/trading

STRATEGIES=(
    "cross_sectional_momentum_lo"
    "cross_sectional_momentum_ls"
    "stat_arbitrage_lo"
    "stat_arbitrage_ls"
)

# pairs: symbol|timeframe|symboldir
PAIRS=(
    "BTC/USDT|1h|BTCUSDT__1h"
    "BTC/USDT|4h|BTCUSDT__4h"
    "ETH/USDT|4h|ETHUSDT__4h"
    "SOL/USDT|4h|SOLUSDT__4h"
    "AVAX/USDT|4h|AVAXUSDT__4h"
    "BNB/USDT|4h|BNBUSDT__4h"
)

MAX_PARALLEL=3
SEMAPHORE_COUNT=0

run_combo() {
    local strategy="$1"
    local symbol="$2"
    local timeframe="$3"
    local symboldir="$4"
    local outdir="data/backtests/wfo_full/${strategy}__${symboldir}"
    mkdir -p "$outdir"
    echo "[START] ${strategy} ${symbol} ${timeframe} -> ${outdir}"
    python scripts/run_wfo_parallel.py \
        --strategy "$strategy" \
        --symbol "$symbol" \
        --timeframe "$timeframe" \
        --cost 1x \
        --out "$outdir" \
        --workers 4 \
        --cell-timeout 1800 \
        --run-holdout \
        --real-sensitivity
    echo "[DONE] ${strategy} ${symbol} ${timeframe}"
}

pids=()
for strategy in "${STRATEGIES[@]}"; do
    for pair in "${PAIRS[@]}"; do
        IFS='|' read -r symbol timeframe symboldir <<< "$pair"
        run_combo "$strategy" "$symbol" "$timeframe" "$symboldir" &
        pids+=($!)
        SEMAPHORE_COUNT=$((SEMAPHORE_COUNT + 1))
        if [ "$SEMAPHORE_COUNT" -ge "$MAX_PARALLEL" ]; then
            wait -n "${pids[@]}"
            SEMAPHORE_COUNT=$((SEMAPHORE_COUNT - 1))
        fi
    done
done

# Wait for all remaining
wait
echo "=== All 24 WFO runs complete ==="
