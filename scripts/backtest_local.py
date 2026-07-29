#!/usr/bin/env python3
"""
Local backtest runner — chạy trên máy bạn.
Không cần SSH, không cần GitHub Actions, không cần VPS.
"""
import subprocess
import sys
from datetime import datetime
from pathlib import Path

# === CONFIG (sửa cho máy bạn) ===
COMPOSE_FILE = "docker-compose.yml"           # hoặc docker-compose.local.yml
CONFIG_PATH = "./config/credentials.yaml"     # config local
STRATEGIES = ["ma_crossover", "rsi_mean_reversion", "bbands_breakout"]
SYMBOL = "BTC/USDT"
TIMEFRAME = "1h"
DAYS = 730
# =================================

def run(cmd, check=True):
    print(f"  $ {cmd}")
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    if check and result.returncode != 0:
        print(f"  ✗ FAILED: {result.stderr}")
        return False
    if result.stdout:
        for line in result.stdout.strip().split('\n'):
            print(f"  {line}")
    return True

def main():
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    print(f"\n=== Backtest Local {timestamp} ===")
    print(f"Symbol: {SYMBOL} | Timeframe: {TIMEFRAME} | Days: {DAYS}")
    print(f"Strategies: {', '.join(STRATEGIES)}")

    # Ensure config exists
    if not Path(CONFIG_PATH).exists():
        print(f"  ✗ Config not found: {CONFIG_PATH}")
        sys.exit(1)

    # Pull latest image (optional)
    print("\n1. Pulling latest image...")
    run(f"docker compose -f {COMPOSE_FILE} pull trading-agent", check=False)

    # Run each strategy
    all_ok = True
    for strategy in STRATEGIES:
        print(f"\n2. Running {strategy}...")
        cmd = (
            f"docker compose -f {COMPOSE_FILE} run --rm "
            f"-e TRADING_CONFIG_PATH=/app/config/credentials.yaml "
            f"trading-agent backtest run {strategy} {SYMBOL} "
            f"--timeframe {TIMEFRAME} --days {DAYS}"
        )
        ok = run(cmd, check=False)
        if not ok:
            all_ok = False
            print(f"  ⚠ {strategy} failed, continuing...")

    # Show metrics summary
    print("\n3. Extracting metrics...")
    run(f"""
        docker compose -f {COMPOSE_FILE} run --rm \
        -e TRADING_CONFIG_PATH=/app/config/credentials.yaml \
        trading-agent python3 -c "
import sqlite3
conn = sqlite3.connect('data/trading.db')
cur = conn.cursor()
cur.execute('''
  SELECT strategy, MAX(total_return_pct) as best_return, MAX(sharpe) as best_sharpe
  FROM backtest_results
  WHERE created_at > datetime('now', '-2 days')
  GROUP BY strategy
''')
for row in cur.fetchall():
    print(f'{row[0]}: return={row[1]:.2f}%, sharpe={row[2]:.2f}')
" 2>/dev/null || echo 'Metrics table not yet available'
    """, check=False)

    print(f"\n{'✅ All done' if all_ok else '⚠ Some strategies failed'} — {timestamp}")

if __name__ == "__main__":
    main()