#!/bin/bash
# Wrapper script for WFO validation-only parallel execution
# Usage: wfo_wrapper.sh <strategy> <out_postfix>

STRATEGY=$1
OUT_POSTFIX=$2

export TRADING_FROZEN_RESEARCH_DATA='{"exchange":"binance","symbol":"BTC/USDT","timeframe":"1h","path":"/home/huythong/.qwenpaw/workspaces/trading/data/existing_research_audit_20261008/BTC_USDT__1h__pre_holdout.parquet","sha256":"45ba1b1bc6a91986eda445de8026cab9995fcabe011a735244a4c7cf0e0825ce","bytes":598799}'

.venv/bin/python scripts/run_wfo_parallel.py --strategy ${STRATEGY} --symbol BTC/USDT --timeframe 1h --cost 1x --workers 1 --train-months 12 --val-months 3 --test-months 3 --step-months 12 --validation-only --no-real-sensitivity --cell-timeout 180 --total-timeout 1650 --max-rss-mb 2048 --out ${OUT_POSTFIX}
