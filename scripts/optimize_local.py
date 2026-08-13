#!/usr/bin/env python3
"""
Local parameter optimization — chạy trên máy bạn.
Chạy param_sweep.py trong container, lưu kết quả local.
"""

import subprocess
import sys
from datetime import datetime
from pathlib import Path

# === CONFIG ===
COMPOSE_FILE = "docker-compose.yml"
CONFIG_PATH = "./config/credentials.yaml"
STRATEGIES = ["ma_crossover", "rsi_mean_reversion", "bbands_breakout"]
SYMBOLS = ["BTC/USDT", "ETH/USDT", "SOL/USDT"]
TIMEFRAMES = ["1h", "4h"]
TRAIN_DAYS = 730
TEST_DAYS = 365
OUTPUT_DIR = Path("./data/param_sweep")
# ==============


def run(cmd, check=True):
    print(f"  $ {cmd}")
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    if check and result.returncode != 0:
        print(f"  ✗ FAILED: {result.stderr}")
        return False
    if result.stdout:
        for line in result.stdout.strip().split("\n"):
            print(f"  {line}")
    return True


def main():
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_file = OUTPUT_DIR / f"param_sweep_{timestamp}.json"
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    print(f"\n=== Parameter Sweep Local {timestamp} ===")
    print(f"Strategies: {', '.join(STRATEGIES)}")
    print(f"Symbols: {', '.join(SYMBOLS)}")
    print(f"Timeframes: {', '.join(TIMEFRAMES)}")
    print(f"Train: {TRAIN_DAYS}d | Test: {TEST_DAYS}d")
    print(f"Output: {output_file}")

    if not Path(CONFIG_PATH).exists():
        print(f"  ✗ Config not found: {CONFIG_PATH}")
        sys.exit(1)

    # Pull latest
    print("\n1. Pulling latest image...")
    run(f"docker compose -f {COMPOSE_FILE} pull trading-agent", check=False)

    # Run param sweep
    print("\n2. Running parameter sweep...")
    strategies_str = " ".join(STRATEGIES)
    symbols_str = " ".join(SYMBOLS)
    timeframes_str = " ".join(TIMEFRAMES)

    cmd = (
        f"docker compose -f {COMPOSE_FILE} run --rm "
        f"-e TRADING_CONFIG_PATH=/app/config/credentials.yaml "
        f"-v {OUTPUT_DIR.absolute()}:/app/data/param_sweep "
        f"trading-agent python param_sweep.py "
        f"--strategies {strategies_str} "
        f"--symbols {symbols_str} "
        f"--timeframes {timeframes_str} "
        f"--train-days {TRAIN_DAYS} --test-days {TEST_DAYS} "
        f"--output /app/data/param_sweep/param_sweep_{timestamp}.json"
    )

    ok = run(cmd, check=False)

    if ok:
        print(f"\n✅ Done — results: {output_file}")
        print(f"   View: cat {output_file} | jq .")
    else:
        print("\n❌ Failed — check logs above")
        sys.exit(1)


if __name__ == "__main__":
    main()
