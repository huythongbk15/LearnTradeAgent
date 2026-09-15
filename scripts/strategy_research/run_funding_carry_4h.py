#!/usr/bin/env python3
"""Standalone runner for funding_carry BTC/4h WFO with error capture."""
import sys
import os
import subprocess
from pathlib import Path

root = "/home/huythong/.qwenpaw/workspaces/trading"
os.chdir(root)
env = os.environ.copy()
env["PYTHONPATH"] = f"{root}/src:{env.get('PYTHONPATH', '')}"

log_path = Path("/tmp/funding_carry_4h_run.log")

def log(msg):
    print(msg)
    with open(log_path, "a") as f:
        f.write(msg + "\n")

log("=== Funding Carry BTC/4h WFO Run ===")

try:
    result = subprocess.run(
        [
            sys.executable,
            f"{root}/scripts/run_wfo_parallel.py",
            "--strategy", "funding_carry",
            "--symbol", "BTC_USDT",
            "--timeframe", "4h",
            "--cost", "1x",
            "--workers", "4",
            "--out", "data/backtests/wfo_full",
            "--train-months", "12",
            "--val-months", "3",
            "--test-months", "3",
            "--step-months", "3",
            "--run-holdout",
            "--real-sensitivity",
        ],
        env=env,
        cwd=root,
        capture_output=True,
        text=True,
        timeout=1800,
    )
    
    log(f"Exit code: {result.returncode}")
    log(f"STDOUT tail:\n{result.stdout[-2000:]}")
    log(f"STDERR tail:\n{result.stderr[-2000:]}")
    
except subprocess.TimeoutExpired:
    log("TIMEOUT: Script exceeded 30 minutes")
except Exception as e:
    log(f"ERROR: {e}")

log("\n=== DONE ===")
