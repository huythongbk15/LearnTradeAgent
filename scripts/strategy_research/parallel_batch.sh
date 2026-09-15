#!/bin/bash
# Run all remaining strategies in parallel batches
# Each strategy runs on all 6 pairs concurrently
set -e
cd /home/huythong/.qwenpaw/workspaces/trading

export PYTHONPATH="${PYTHONPATH:-}"

# Strategies to run: "strategy_id:cli_name:overwrite_existing"
STRATEGIES=(
    "cross_sectional_momentum_lo:cross_sectional_momentum_lo:0"
    "cross_sectional_momentum_ls:cross_sectional_momentum_ls:0"
    "stat_arbitrage_lo:stat_arbitrage_lo:0"
    "stat_arbitrage_ls:stat_arbitrage_ls:0"
    "range_mean_reversion:range_mean_reversion:1"
    "volatility_breakout:volatility_breakout:1"
    "funding_carry:funding_carry:1"
)

PAIRS=("BTCUSDT/1h" "BTCUSDT/4h" "ETHUSDT/4h" "SOLUSDT/4h" "AVAXUSDT/4h" "BNBUSDT/4h")

echo "=== Parallel batch WFO launcher ==="
echo "Strategies: ${#STRATEGIES[@]}"
echo "Pairs: ${#PAIRS[@]}"
echo ""

pids=()

for strat in "${STRATEGIES[@]}"; do
    IFS=':' read -r strat_id cli_name overwrite <<< "$strat"
    for pair in "${PAIRS[@]}"; do
        IFS='/' read -r pair_name tf <<< "$pair"
        pair_fmt="${pair_name/USDT/\/USDT}"
        out_dir="data/backtests/wfo_full/${strat_id}__${pair_name}__${tf}"

        # Skip if overwrite=0 and reports already exist
        if [ "$overwrite" = "0" ] && ls "${out_dir}"/**/report.json >/dev/null 2>&1; then
            echo "[SKIP] ${strat_id} ${pair_name} ${tf} - already complete"
            continue
        fi

        echo "[START] ${strat_id} ${pair_fmt} ${tf}"
        python scripts/run_wfo_parallel.py \
            --strategy "$cli_name" \
            --symbol "$pair_fmt" \
            --timeframe "$tf" \
            --cost "1x" \
            --workers 8 \
            --cell-timeout 600 \
            --train-months 12 \
            --val-months 3 \
            --test-months 3 \
            --step-months 3 \
            --out "$out_dir" \
            > "logs/${strat_id}_${pair_name}_${tf}.log" 2>&1 &

        pids+=($!)
        # Wait a moment before launching next task to avoid resource contention
        sleep 2
    done
done

echo ""
echo "Launched ${#pids[@]} tasks"
echo "Waiting for completion..."

# Wait for all background tasks
for pid in "${pids[@]}"; do
    wait "$pid" 2>/dev/null
done

echo "All tasks completed!"
