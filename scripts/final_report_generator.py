#!/usr/bin/env python3
"""
Final comprehensive report generator for the 30-stream research pipeline.
Produces all required tables from prompt sections 57-70.
"""
from __future__ import annotations

import json
from pathlib import Path
from datetime import datetime, timezone

ROOT = Path(".")
RUN_ID = "latest"
RUN_DIR = ROOT / "data" / "research_runs" / RUN_ID

SYMBOLS = [
    "BTC/USDT", "ETH/USDT", "SOL/USDT", "XRP/USDT", "BNB/USDT",
    "ZEC/USDT", "DOGE/USDT", "TRX/USDT", "ADA/USDT", "NEAR/USDT",
]
TIMEFRAMES = ["1h", "4h", "1d"]


def _load(name: str) -> Any:
    p = RUN_DIR / name
    if not p.exists():
        return [] if name.endswith(".json") else ""
    if name.endswith(".json"):
        return json.loads(p.read_text())
    return p.read_text()


def _fmt(v, fmt=".2f"):
    if v is None:
        return "N/A"
    if isinstance(v, str):
        return v
    try:
        return f"{v:{fmt}}"
    except Exception:
        return str(v)


def generate() -> str:
    wfo = _load("walk_forward_results.json")
    audit = _load("data_quality/audit.json")
    baselines = _load("baselines/results.json")

    lines = []
    lines.append("# FINAL RESEARCH REPORT")
    lines.append(f"\n**Generated:** {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    lines.append(f"**Run ID:** {RUN_ID}")
    lines.append(f"**Git SHA:** {__import__('subprocess').check_output(['git','rev-parse','HEAD'], cwd=ROOT).decode().strip()}")
    lines.append("\n---\n")

    # Section 1: Data Audit
    lines.append("## 1. DATA AUDIT (Section 3-5)")
    lines.append("\n| Pair | TF | Start | End | Bars | Missing | Duplicates | Quality |")
    lines.append("|---|---|---|---|---|---|---|---|")
    for a in audit:
        lines.append(
            f"| {a['symbol']} | {a['timeframe']} | {a.get('start','')} | {a.get('end','')} | {a.get('bars',0)} | {a.get('missing_bars',0)} | {a.get('duplicates',0)} | {a.get('quality','')} |"
        )
    lines.append("")

    # Section 2: 30-stream matrix
    lines.append("## 2. 30 PAIR-TIMEFRAME MATRIX (Section 45)")
    lines.append("\n| Pair | TF | Best Strategy | OOS Sharpe | Net Return | Max DD | DSR | PBO | Status |")
    lines.append("|---|---|---|---|---|---|---|---|---|")
    for sym in SYMBOLS:
        for tf in TIMEFRAMES:
            candidates = [r for r in wfo if r.get("symbol") == sym and r.get("timeframe") == tf]
            if not candidates:
                lines.append(f"| {sym} | {tf} | N/A | N/A | N/A | N/A | N/A | N/A | FAIL |")
                continue
            best = max(candidates, key=lambda x: x.get("net_return", -9999))
            lines.append(
                f"| {sym} | {tf} | {best.get('strategy','')} | {_fmt(best.get('oos_sharpe'))} | {_fmt(best.get('net_return'))}% | {_fmt(best.get('max_dd'))}% | {_fmt(best.get('dsr'))} | {_fmt(best.get('pbo', 1.0), '.2f')} | {best.get('status','')} |"
            )
    lines.append("")

    # Section 3: Best per pair
    lines.append("## 3. BEST MODEL PER PAIR (Section 43)")
    lines.append("\n| Pair | Best TF | Best Strategy | OOS Sharpe | Net Return | Max DD | Trades | Turnover | Cost Drag | PSR | DSR | PBO | Pos Fold% | 2x Cost | 3x Cost | Status |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for sym in SYMBOLS:
        best = None
        for tf in TIMEFRAMES:
            candidates = [r for r in wfo if r.get("symbol") == sym and r.get("timeframe") == tf]
            if not candidates:
                continue
            cand = max(candidates, key=lambda x: x.get("net_return", -9999))
            if best is None or cand.get("net_return", -9999) > best.get("net_return", -9999):
                best = cand
        if best:
            lines.append(
                f"| {sym} | {best.get('timeframe','')} | {best.get('strategy','')} | {_fmt(best.get('oos_sharpe'))} | {_fmt(best.get('net_return'))}% | {_fmt(best.get('max_dd'))}% | {best.get('trades',0)} | {_fmt(best.get('turnover'))} | {_fmt(best.get('cost_drag'))} | {_fmt(best.get('psr'))} | {_fmt(best.get('dsr'))} | {_fmt(best.get('pbo',1.0),'.2f')} | {_fmt(best.get('positive_folds',0)/max(1,best.get('folds',1))*100)}% | {_fmt(best.get('cost_2x_sharpe'))} | {_fmt(best.get('cost_3x_sharpe'))} | {best.get('status','')} |"
            )
    lines.append("")

    # Section 4: Cost stress summary
    lines.append("## 4. COST STRESS (Section 26-27)")
    lines.append("\n| Pair | TF | Strategy | 0.5x Sharpe | 1x Sharpe | 1.5x Sharpe | 2x Sharpe | 3x Sharpe |")
    lines.append("|---|---|---|---|---|---|---|---|")
    for r in wfo[:20]:  # sample
        lines.append(
            f"| {r.get('symbol','')} | {r.get('timeframe','')} | {r.get('strategy','')} | N/A | {_fmt(r.get('oos_sharpe'))} | N/A | {_fmt(r.get('cost_2x_sharpe'))} | {_fmt(r.get('cost_3x_sharpe'))} |"
        )
    lines.append("")

    # Section 5: Rejected / Status
    lines.append("## 5. STATUS & REJECTIONS (Section 46)")
    lines.append("\n| Pair | TF | Strategy | Status | Rejection Reason |")
    lines.append("|---|---|---|---|---|")
    for r in wfo:
        reason = "NO_EDGE" if r.get("oos_sharpe", 0) <= 0 else "COST_SENSITIVE" if r.get("cost_2x_sharpe", 0) < 0 else "PAPER_ELIGIBLE"
        lines.append(f"| {r.get('symbol','')} | {r.get('timeframe','')} | {r.get('strategy','')} | {r.get('status','')} | {reason} |")
    lines.append("")

    # Final
    lines.append("---\n")
    lines.append("## FINAL DECISION")
    lines.append("\n**MAINNET: NO-GO**\n")
    lines.append("Research correctness > coverage. Only candidates with positive net OOS edge after realistic costs, DSR>0, and stable parameters should advance to paper/testnet.")

    return "\n".join(lines)


if __name__ == "__main__":
    report = generate()
    out = RUN_DIR / "final_report.md"
    out.write_text(report)
    print(f"Final report written to {out}")
    print("\n--- REPORT PREVIEW (first 120 lines) ---")
    for i, line in enumerate(report.splitlines()[:120]):
        print(line)
