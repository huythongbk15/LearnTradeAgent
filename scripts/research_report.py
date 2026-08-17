#!/usr/bin/env python3
"""
Generate final report tables from walk-forward results + baselines.
"""
from __future__ import annotations

import json
from pathlib import Path
from collections import defaultdict

ROOT = Path(".")
RUN_DIR = ROOT / "data" / "research_runs" / "latest"

SYMBOLS = [
    "BTC/USDT", "ETH/USDT", "SOL/USDT", "XRP/USDT", "BNB/USDT",
    "ZEC/USDT", "DOGE/USDT", "TRX/USDT", "ADA/USDT", "NEAR/USDT",
]
TIMEFRAMES = ["1h", "4h", "1d"]


def _load_json(name: str) -> Any:
    p = RUN_DIR / name
    if not p.exists():
        return []
    return json.loads(p.read_text())


def _best_per_pair_table(wfo: list[dict]) -> str:
    """Section 43: Best candidate per pair."""
    rows = []
    for sym in SYMBOLS:
        best = None
        for tf in TIMEFRAMES:
            candidates = [r for r in wfo if r["symbol"] == sym and r["timeframe"] == tf]
            if not candidates:
                continue
            # pick highest net_return among positive dsr
            pos = [c for c in candidates if c["dsr"] > 0]
            if not pos:
                pos = candidates
            cand = max(pos, key=lambda x: x["net_return"])
            if best is None or cand["net_return"] > best["net_return"]:
                best = cand
        if best:
            rows.append(
                f"| {best['symbol']} | {best['timeframe']} | {best['strategy']} | {best['oos_sharpe']:.2f} | {best['net_return']:.2f}% | {best['max_dd']:.2f}% | {best.get('cost_2x_sharpe',0):.2f} | {best.get('cost_3x_sharpe',0):.2f} | {best['status']} |"
            )
    header = "| Pair | Best TF | Best Strategy | OOS Sharpe | Net Return | Max DD | 2x Cost Sharpe | 3x Cost Sharpe | Status |"
    sep = "|---|---|---|---|---|---|---|---|---|"
    return "\n".join([header, sep] + rows)


def _pair_tf_matrix(wfo: list[dict]) -> str:
    """Section 45: 30-stream matrix."""
    rows = []
    for sym in SYMBOLS:
        for tf in TIMEFRAMES:
            candidates = [r for r in wfo if r["symbol"] == sym and r["timeframe"] == tf]
            if not candidates:
                rows.append(f"| {sym} | {tf} | N/A | N/A | N/A | N/A | N/A | N/A | FAIL |")
                continue
            best = max(candidates, key=lambda x: x["net_return"])
            rows.append(
                f"| {sym} | {tf} | {best['strategy']} | {best['oos_sharpe']:.2f} | {best['net_return']:.2f}% | {best['max_dd']:.2f} | {best['dsr']:.2f} | {best.get('pbo',1.0):.2f} | {best['status']} |"
            )
    header = "| Pair | TF | Best Strategy | OOS Sharpe | Net Return | Max DD | DSR | PBO | Status |"
    sep = "|---|---|---|---|---|---|---|---|---|"
    return "\n".join([header, sep] + rows)


def generate_report() -> None:
    wfo = _load_json("walk_forward_results.json")
    baselines = _load_json("baselines/results.json")
    audit = _load_json("data_quality/audit.json")

    report = []
    report.append("# FINAL RESEARCH REPORT")
    report.append(f"\nRun directory: {RUN_DIR}")
    report.append(f"Walk-forward records: {len(wfo)}")
    report.append(f"Baseline records: {len(baselines)}")

    report.append("\n## 1. Data Audit (Section 3-5)")
    for a in audit:
        report.append(f"- {a['symbol']} {a['timeframe']}: {a['quality']} | bars={a['bars']} | gaps={a['gaps']}")

    report.append("\n## 2. Best Per Pair (Section 43)")
    report.append(_best_per_pair_table(wfo))

    report.append("\n## 3. Pair × TF Matrix (Section 45)")
    report.append(_pair_tf_matrix(wfo))

    report.append("\n## 4. Status")
    report.append("MAINNET: NO-GO")

    out = RUN_DIR / "final_report.md"
    out.write_text("\n".join(report))
    print(f"Report written to {out}")


if __name__ == "__main__":
    generate_report()
