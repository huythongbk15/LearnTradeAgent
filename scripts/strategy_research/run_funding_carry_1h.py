#!/usr/bin/env python3
"""Standalone WFO runner for funding_carry BTC/USDT 1h.

Uses a dedicated output directory so it does NOT collide with the parallel
canonical WFO outputs in data/backtests/wfo_full/.

Key differences from the earlier full-campaign run:
  • Uses the updated param grid (5 entry thresholds × 2 exit × 1 hold × 1 vol = 10 combos)
  • Dedicated output directory: data/backtests/wfo_funding_carry_only
  • 6 workers for faster completion
  • 4h subprocess timeout
  • Explicit holdout + real sensitivity enabled
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("funding_carry_wfo")

ROOT = Path(__file__).resolve().parent.parent.parent
OUT_DIR = "data/backtests/wfo_funding_carry_only"
LOG_FILE = Path("/tmp/funding_carry_1h_wfo.log")


def log(msg: str) -> None:
    logger.info(msg)
    with open(LOG_FILE, "a") as f:
        f.write(msg + "\n")


def main() -> None:
    log("=" * 70)
    log("Funding Carry BTC/USDT 1h - Standalone WFO Re-run")
    log("=" * 70)

    env = os.environ.copy()
    env["PYTHONPATH"] = f"{ROOT}/src:{env.get('PYTHONPATH', '')}"

    cmd = [
        sys.executable,
        str(ROOT / "scripts" / "run_wfo_parallel.py"),
        "--strategy", "funding_carry",
        "--symbol", "BTC/USDT",
        "--timeframe", "1h",
        "--cost", "1x",
        "--out", OUT_DIR,
        "--workers", "6",
        "--cell-timeout", "1800",
        "--train-months", "12",
        "--val-months", "3",
        "--test-months", "3",
        "--step-months", "3",
        "--run-holdout",
        "--real-sensitivity",
    ]

    log(f"Command: {' '.join(cmd)}")
    log(f"Output dir: {OUT_DIR}")
    log(f"Timeout: 4h (14400s)")
    log("")

    start = time.time()
    try:
        result = subprocess.run(
            cmd,
            env=env,
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=14400,
        )

        elapsed = time.time() - start
        log(f"Exit code: {result.returncode}")
        log(f"Elapsed: {elapsed:.0f}s")
        log(f"STDOUT tail:\n{result.stdout[-3000:]}")
        if result.stderr:
            log(f"STDERR tail:\n{result.stderr[-2000:]}")

        # Collect summary
        out_root = ROOT / OUT_DIR
        reports = list(out_root.rglob("report.json"))
        fc_reports = [r for r in reports if "funding_carry" in str(r)]
        log(f"Total reports found: {len(reports)}")
        log(f"Funding carry reports: {len(fc_reports)}")

        # Summarize funding_carry results
        if fc_reports:
            summary = []
            for r in sorted(fc_reports):
                try:
                    data = json.loads(r.read_text())
                    params = data.get("active_config", {}).get("strategy", {}).get("parameters", {})
                    metrics = {
                        "return_pct": data.get("total_return_pct", 0),
                        "sharpe": data.get("sharpe", 0),
                        "trades": data.get("total_trades", 0),
                        "max_dd": data.get("max_drawdown_pct", 0),
                    }
                    summary.append({
                        "path": str(r.relative_to(out_root)),
                        "params": params,
                        "metrics": metrics,
                    })
                except Exception as e:
                    log(f"Error reading {r}: {e}")

            summary_file = out_root / "funding_carry_summary.json"
            summary_file.write_text(json.dumps(summary, indent=2, default=str))
            log(f"Summary saved: {summary_file}")

            # Print top results
            summary.sort(key=lambda x: x["metrics"]["return_pct"], reverse=True)
            log("\nTop 5 cells by return:")
            for s in summary[:5]:
                p = s["params"]
                m = s["metrics"]
                log(f"  entry={p.get('funding_entry_threshold')} exit={p.get('funding_exit_threshold')} "
                    f"max_hold={p.get('max_hold_periods')} -> "
                    f"return={m['return_pct']:.2f}% sharpe={m['sharpe']:.2f} trades={m['trades']}")

    except subprocess.TimeoutExpired:
        log("TIMEOUT: Script exceeded 4h limit")
    except Exception as e:
        log(f"ERROR: {type(e).__name__}: {e}")

    log("\n=== DONE ===")


if __name__ == "__main__":
    main()
