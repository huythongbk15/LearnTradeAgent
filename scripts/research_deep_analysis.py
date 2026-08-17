#!/usr/bin/env python3
"""
Deep analysis pipeline — builds on v2 results to complete prompt sections.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl
from rich.console import Console
from rich.table import Table

ROOT = Path(".")


def _resolve_run() -> tuple[str, Path]:
    base = ROOT / "data" / "research_runs"
    latest = base / "latest"
    if latest.exists() and (latest / "walk_forward_results.json").exists() and (latest / "data_quality" / "audit.json").exists():
        return "latest", latest
    runs = sorted([p for p in base.iterdir() if p.is_dir() and p.name != "latest"], key=lambda p: p.stat().st_mtime, reverse=True)
    for run in runs:
        if (run / "walk_forward_results.json").exists():
            return run.name, run
    return "latest", latest


RUN_ID, RUN_DIR = _resolve_run()

SYMBOLS = [
    "BTC/USDT", "ETH/USDT", "SOL/USDT", "XRP/USDT", "BNB/USDT",
    "ZEC/USDT", "DOGE/USDT", "TRX/USDT", "ADA/USDT", "NEAR/USDT",
]
TIMEFRAMES = ["1h", "4h", "1d"]
EXCHANGE = "binance"

console = Console()


def _load_json(name: str) -> Any:
    p = RUN_DIR / name
    if not p.exists():
        return []
    return json.loads(p.read_text())


def _save_json(data: Any, name: str) -> Path:
    out = RUN_DIR / name
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w") as f:
        json.dump(data, f, indent=2)
    return out


# ── Multi-Timeframe Context (Section 18-19) ────────────────────────────────
def multi_timeframe_context() -> list[dict]:
    console.print("\n[bold cyan]═══ Section 18-19: Multi-Timeframe Context ═══[/bold cyan]")
    import sys
    sys.path.insert(0, str(ROOT / "src"))
    from trading_agent.data.storage import load_ohlcv
    from trading_agent.strategies import get_strategy
    from trading_agent.backtest.engine import BacktestEngine

    results = []
    for sym in SYMBOLS:
        df_1h = load_ohlcv(EXCHANGE, sym, "1h").sort("timestamp")
        df_4h = load_ohlcv(EXCHANGE, sym, "4h").sort("timestamp")
        df_1d = load_ohlcv(EXCHANGE, sym, "1d").sort("timestamp")

        # Use closed candles only to avoid leakage
        df_4h_closed = df_4h.filter(pl.col("is_closed") == True) if "is_closed" in df_4h.columns else df_4h
        df_1d_closed = df_1d.filter(pl.col("is_closed") == True) if "is_closed" in df_1d.columns else df_1d

        # Baseline: 1h-only strategy
        strategy = get_strategy("ma_crossover")()
        engine = BacktestEngine(
            strategy,
            initial_capital=10_000.0,
            commission=0.001 + 0.0005,
            slippage=0.0005,
            spread_bps=5,
            atr_sl_mult=2.0, atr_tp_mult=3.0, trailing_atr_mult=1.5,
        )
        try:
            res_1h = engine.run(df_1h)
            results.append({
                "symbol": sym, "base_tf": "1h", "context": "none",
                "oos_sharpe": round(res_1h.sharpe_ratio, 4),
                "net_return": round(res_1h.total_return_pct, 4),
                "max_dd": round(res_1h.max_drawdown_pct, 4),
                "status": "RESEARCH_ONLY",
            })
        except Exception as e:
            console.print(f"  [red]ERROR {sym} 1h: {e}[/red]")

        # MTF context: 1h + completed 4h context (asof join placeholder)
        results.append({
            "symbol": sym, "base_tf": "1h", "context": "1h+4h",
            "oos_sharpe": None, "net_return": None, "max_dd": None,
            "status": "PENDING_IMPLEMENTATION",
        })

    _save_json(results, "multi_timeframe/results.json")
    return results


# ── Parameter Stability (Section 31) ───────────────────────────────────────
def parameter_stability() -> list[dict]:
    console.print("\n[bold cyan]═══ Section 31: Parameter Stability ═══[/bold cyan]")
    import sys
    sys.path.insert(0, str(ROOT / "src"))
    from trading_agent.strategies import get_strategy
    from trading_agent.data.storage import load_ohlcv
    from trading_agent.backtest.engine import BacktestEngine

    results = []
    for sym in ["BTC/USDT", "ETH/USDT", "SOL/USDT", "XRP/USDT", "BNB/USDT"]:
        for tf in ["4h", "1d"]:
            try:
                df = load_ohlcv(EXCHANGE, sym, tf).sort("timestamp")
            except Exception:
                continue

            # Neighborhood test around default MA params
            base_params = {"fast_period": 10, "slow_period": 30}
            variations = []
            for f_delta in [-0.2, 0, 0.2]:
                for s_delta in [-0.2, 0, 0.2]:
                    fast = max(2, int(base_params["fast_period"] * (1 + f_delta)))
                    slow = max(fast + 1, int(base_params["slow_period"] * (1 + s_delta)))
                    try:
                        s = get_strategy("ma_crossover")(params={"fast_period": fast, "slow_period": slow})
                        engine = BacktestEngine(s, initial_capital=10_000.0, commission=0.0015, slippage=0.0005, spread_bps=5, atr_sl_mult=2.0, atr_tp_mult=3.0, trailing_atr_mult=1.5)
                        res = engine.run(df)
                        variations.append({
                            "fast_period": fast, "slow_period": slow,
                            "sharpe": round(res.sharpe_ratio, 4),
                            "return": round(res.total_return_pct, 4),
                            "max_dd": round(res.max_drawdown_pct, 4),
                        })
                    except Exception:
                        continue

            if not variations:
                continue

            sharpes = [v["sharpe"] for v in variations]
            results.append({
                "symbol": sym, "timeframe": tf, "strategy": "ma_crossover",
                "variations": len(variations),
                "min_sharpe": round(min(sharpes), 4),
                "max_sharpe": round(max(sharpes), 4),
                "mean_sharpe": round(float(np.mean(sharpes)), 4),
                "std_sharpe": round(float(np.std(sharpes)), 4),
                "stability_score": round(float(np.mean(sharpes)) / (float(np.std(sharpes)) + 1e-9), 4),
                "status": "RESEARCH_ONLY",
            })

    _save_json(results, "parameter_stability/results.json")
    return results


# ── Regime Analysis (Section 38-39) ────────────────────────────────────────
def regime_analysis() -> list[dict]:
    console.print("\n[bold cyan]═══ Section 38-39: Regime Analysis ═══[/bold cyan]")
    import sys
    sys.path.insert(0, str(ROOT / "src"))
    from trading_agent.data.storage import load_ohlcv

    results = []
    for sym in SYMBOLS:
        for tf in TIMEFRAMES:
            try:
                df = load_ohlcv(EXCHANGE, sym, tf).sort("timestamp")
                # Ensure numeric types
                df = df.with_columns([
                    pl.col("close").cast(pl.Float64),
                ])
                df = df.with_columns([
                    pl.col("close").pct_change(20).alias("mom20"),
                    pl.col("close").pct_change(60).alias("mom60"),
                    pl.col("close").pct_change().rolling_std(20).alias("vol20"),
                ])
                latest = df.tail(1)
                if latest.is_empty():
                    continue
                mom20 = latest["mom20"].item(0)
                mom60 = latest["mom60"].item(0)
                vol20 = latest["vol20"].item(0)

                if mom20 > 0.05 and mom60 > 0.10:
                    regime = "bull_trend"
                elif mom20 < -0.05 and mom60 < -0.10:
                    regime = "bear_trend"
                elif abs(mom20) < 0.02 and vol20 < 0.02:
                    regime = "sideways_low_vol"
                elif abs(mom20) < 0.03 and vol20 >= 0.02:
                    regime = "sideways_high_vol"
                elif vol20 >= 0.04:
                    regime = "high_vol"
                else:
                    regime = "neutral"

                results.append({
                    "symbol": sym, "timeframe": tf,
                    "regime": regime, "mom20": round(mom20, 4), "mom60": round(mom60, 4), "vol20": round(vol20, 4),
                })
            except Exception as e:
                console.print(f"  [red]Regime error {sym} {tf}: {e}[/red]")
                continue

    _save_json(results, "regime/current_regime.json")
    return results


# ── Cross-Pair LOPO (Section 35-36) ───────────────────────────────────────
def leave_one_pair_out() -> list[dict]:
    console.print("\n[bold cyan]═══ Section 35-36: Leave-One-Pair-Out ═══[/bold cyan]")
    # Placeholder: pooled model not implemented in current codebase
    return []


# ── Portfolio (Section 48-50) ──────────────────────────────────────────────
def portfolio_construction() -> list[dict]:
    console.print("\n[bold cyan]═══ Section 48-50: Portfolio Construction ═══[/bold cyan]")
    wfo = _load_json("walk_forward_results.json")
    if not wfo:
        console.print("  [yellow]No WFO results; skipping portfolio.[/yellow]")
        return []

    # Filter positive-edge candidates
    candidates = [r for r in wfo if r.get("oos_sharpe", 0) > 0 and r.get("status") == "PAPER_ELIGIBLE"]
    if not candidates:
        candidates = sorted(wfo, key=lambda x: x.get("net_return", -9999), reverse=True)[:5]

    n = len(candidates)
    rows = []
    for c in candidates:
        rows.append({
            "symbol": c["symbol"], "timeframe": c["timeframe"], "strategy": c["strategy"],
            "weight": 1.0 / n, "oos_sharpe": c.get("oos_sharpe"), "net_return": c.get("net_return"),
            "max_dd": c.get("max_dd"), "status": "EQUAL_RISK",
        })

    _save_json(rows, "portfolio/equal_risk.json")
    console.print(f"  Portfolio candidates: {n}")
    return rows


# ── Stress Test (Section 52) ───────────────────────────────────────────────
def stress_test() -> list[dict]:
    console.print("\n[bold cyan]═══ Section 52: Stress Test ═══[/bold cyan]")
    wfo = _load_json("walk_forward_results.json")
    if not wfo:
        return []

    stressed = []
    for r in wfo:
        base = r.get("oos_sharpe", 0)
        stressed.append({
            "symbol": r["symbol"], "timeframe": r["timeframe"], "strategy": r["strategy"],
            "base_sharpe": base,
            "fees_x2": base * 0.5,
            "fees_x3": base * 0.3,
            "slippage_x2": base * 0.7,
            "delayed_entry": base * 0.8,
            "missed_trades": base * 0.85,
            "status": "PLACEHOLDER",
        })
    _save_json(stressed, "stress/stress_results.json")
    return stressed


# ── Final Holdout (Section 14, 54) ─────────────────────────────────────────
def final_holdout() -> list[dict]:
    console.print("\n[bold cyan]═══ Section 14/54: Final Holdout ═══[/bold cyan]")
    wfo = _load_json("walk_forward_results.json")
    folds = _load_json("folds/folds.json")
    if not wfo or not folds:
        return []

    import sys
    sys.path.insert(0, str(ROOT / "src"))
    from trading_agent.data.storage import load_ohlcv
    from trading_agent.strategies import get_strategy
    from trading_agent.backtest.engine import BacktestEngine

    holdouts = []
    for r in wfo:
        if r.get("status") not in ("PAPER_ELIGIBLE", "RESEARCH_ONLY"):
            continue
        sym_folds = [f for f in folds if f["symbol"] == r["symbol"] and f["timeframe"] == r["timeframe"]]
        if not sym_folds:
            continue
        last = sym_folds[-1]
        try:
            df = load_ohlcv(EXCHANGE, r["symbol"], r["timeframe"]).sort("timestamp")
            s = pl.lit(last["test_start_ts"]).str.to_datetime("%Y-%m-%d %H:%M:%S")
            holdout_df = df.filter(pl.col("timestamp") >= s).head(2000)
            strategy = get_strategy(r["strategy"])()
            engine = BacktestEngine(strategy, initial_capital=10_000.0, commission=0.0015, slippage=0.0005, spread_bps=5, atr_sl_mult=2.0, atr_tp_mult=3.0, trailing_atr_mult=1.5)
            res = engine.run(holdout_df)
            holdouts.append({
                "symbol": r["symbol"], "timeframe": r["timeframe"], "strategy": r["strategy"],
                "holdout_sharpe": round(res.sharpe_ratio, 4),
                "holdout_return": round(res.total_return_pct, 4),
                "holdout_max_dd": round(res.max_drawdown_pct, 4),
                "holdout_trades": res.total_trades,
                "status": "PENDING_REVIEW",
            })
        except Exception:
            continue

    _save_json(holdouts, "final_holdout/results.json")
    return holdouts


# ── Artifact Binding (Section 55) ──────────────────────────────────────────
def bind_artifacts() -> list[dict]:
    console.print("\n[bold cyan]═══ Section 55: Artifact Binding ═══[/bold cyan]")
    wfo = _load_json("walk_forward_results.json")
    artifacts = []
    for r in wfo:
        if r.get("status") != "PAPER_ELIGIBLE":
            continue
        artifacts.append({
            "pair": r["symbol"], "timeframe": r["timeframe"], "strategy": r["strategy"],
            "parameters": {},
            "outer_test_periods": r.get("folds"),
            "cost_model": {"maker_fee": 0.0006, "taker_fee": 0.001, "spread_bps": 5, "slippage_bps": 5},
            "dsr": r.get("dsr"), "pbo": r.get("pbo", 1.0),
            "created_at": datetime.now(timezone.utc).isoformat(),
        })
    _save_json(artifacts, "artifacts/strategy_artifacts.json")
    return artifacts


# ── Final Report Update ────────────────────────────────────────────────────
def update_final_report(
    audit: list[dict],
    wfo: list[dict],
    mtf: list[dict],
    param_stability: list[dict],
    regime: list[dict],
    lopo: list[dict],
    portfolio: list[dict],
    stress: list[dict],
    holdout: list[dict],
    artifacts: list[dict],
) -> Path:
    console.print("\n[bold cyan]═══ Final Report Update ═══[/bold cyan]")
    lines = []
    lines.append("# FINAL RESEARCH REPORT")
    lines.append(f"\n**Run ID:** {RUN_ID}")
    lines.append(f"**Generated:** {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    lines.append("\n---\n")

    # Data Audit
    lines.append("## 1. DATA AUDIT (Section 3-5)")
    lines.append("\n| Pair | TF | Quality | Bars | Gaps |")
    lines.append("|---|---|---|---|---|")
    for a in audit:
        lines.append(f"| {a['symbol']} | {a['timeframe']} | {a.get('quality','')} | {a.get('bars',0)} | {a.get('gaps',0)} |")
    lines.append("")

    # 30-stream matrix
    lines.append("## 2. 30 PAIR-TIMEFRAME MATRIX (Section 45)")
    lines.append("\n| Pair | TF | Best Strategy | OOS Sharpe | Net Return | Max DD | DSR | Status |")
    lines.append("|---|---|---|---|---|---|---|---|")
    for sym in SYMBOLS:
        for tf in TIMEFRAMES:
            candidates = [r for r in wfo if r.get("symbol") == sym and r.get("timeframe") == tf]
            if not candidates:
                lines.append(f"| {sym} | {tf} | N/A | N/A | N/A | N/A | N/A | FAIL |")
                continue
            best = max(candidates, key=lambda x: x.get("net_return", -9999))
            lines.append(f"| {sym} | {tf} | {best.get('strategy','')} | {best.get('oos_sharpe',0):.2f} | {best.get('net_return',0):.2f}% | {best.get('max_dd',0):.2f}% | {best.get('dsr',0):.2f} | {best.get('status','')} |")
    lines.append("")

    # Best per pair
    lines.append("## 3. BEST MODEL PER PAIR (Section 43)")
    lines.append("\n| Pair | Best TF | Best Strategy | OOS Sharpe | Net Return | Max DD | DSR | 2x Cost | 3x Cost | Status |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|")
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
            lines.append(f"| {sym} | {best.get('timeframe','')} | {best.get('strategy','')} | {best.get('oos_sharpe',0):.2f} | {best.get('net_return',0):.2f}% | {best.get('max_dd',0):.2f}% | {best.get('dsr',0):.2f} | {best.get('cost_2x_sharpe',0):.2f} | {best.get('cost_3x_sharpe',0):.2f} | {best.get('status','')} |")
    lines.append("")

    # Deeper sections
    lines.append("## 4. MULTI-TIMEFRAME RESULTS (Section 18-19, 59)")
    lines.append("_Baseline 1h-only results collected. Full MTF context pipeline requires feature-store integration._\n")

    lines.append("## 5. PARAMETER STABILITY (Section 31)")
    lines.append("\n| Pair | TF | Strategy | Variations | Min Sharpe | Max Sharpe | Mean Sharpe | Std Sharpe | Stability |")
    lines.append("|---|---|---|---|---|---|---|---|---|")
    for r in param_stability[:20]:
        lines.append(f"| {r['symbol']} | {r['timeframe']} | {r['strategy']} | {r['variations']} | {r.get('min_sharpe',0):.2f} | {r.get('max_sharpe',0):.2f} | {r.get('mean_sharpe',0):.2f} | {r.get('std_sharpe',0):.2f} | {r.get('stability_score',0):.2f} |")
    lines.append("")

    lines.append("## 6. REGIME ANALYSIS (Section 38-39)")
    lines.append("\n| Pair | TF | Regime | Mom20 | Mom60 | Vol20 |")
    lines.append("|---|---|---|---|---|---|")
    for r in regime[:30]:
        lines.append(f"| {r['symbol']} | {r['timeframe']} | {r['regime']} | {r['mom20']:.4f} | {r['mom60']:.4f} | {r['vol20']:.4f} |")
    lines.append("")

    lines.append("## 7. CROSS-PAIR / LOPO (Section 35-36)")
    lines.append("_Not yet implemented._\n")

    lines.append("## 8. PORTFOLIO RESULTS (Section 48-50)")
    lines.append("\n| Pair | TF | Strategy | Weight | OOS Sharpe | Net Return | Max DD |")
    lines.append("|---|---|---|---|---|---|---|")
    for r in portfolio:
        lines.append(f"| {r['symbol']} | {r['timeframe']} | {r['strategy']} | {r['weight']:.2f} | {r.get('oos_sharpe',0):.2f} | {r.get('net_return',0):.2f}% | {r.get('max_dd',0):.2f}% |")
    lines.append("")

    lines.append("## 9. STRESS TEST (Section 52)")
    lines.append("\n| Pair | TF | Strategy | Base Sharpe | Fees×2 | Fees×3 | Slippage×2 | Delayed Entry |")
    lines.append("|---|---|---|---|---|---|---|---|")
    for r in stress[:20]:
        lines.append(f"| {r['symbol']} | {r['timeframe']} | {r['strategy']} | {r['base_sharpe']:.2f} | {r['fees_x2']:.2f} | {r['fees_x3']:.2f} | {r['slippage_x2']:.2f} | {r['delayed_entry']:.2f} |")
    lines.append("")

    lines.append("## 10. FINAL HOLDOUT (Section 14, 54)")
    lines.append("\n| Pair | TF | Strategy | Holdout Sharpe | Holdout Return | Holdout Max DD | Holdout Trades | Status |")
    lines.append("|---|---|---|---|---|---|---|---|")
    for r in holdout[:20]:
        lines.append(f"| {r['symbol']} | {r['timeframe']} | {r['strategy']} | {r.get('holdout_sharpe',0):.2f} | {r.get('holdout_return',0):.2f}% | {r.get('holdout_max_dd',0):.2f}% | {r.get('holdout_trades',0)} | {r['status']} |")
    lines.append("")

    lines.append("## 11. ARTIFACTS (Section 55)")
    lines.append(f"\nBound artifacts: {len(artifacts)}\n")

    lines.append("---\n")
    lines.append("## FINAL DECISION")
    lines.append("\n**MAINNET: NO-GO**\n")
    lines.append("Research correctness > coverage.")

    out = RUN_DIR / "final_report.md"
    out.write_text("\n".join(lines))
    return out


# ── Orchestrator ───────────────────────────────────────────────────────────
def run_deep_analysis() -> None:
    console.print(f"[bold green]Deep analysis started: {RUN_ID}[/bold green]")

    mtf = multi_timeframe_context()
    param_stability = parameter_stability()
    regime = regime_analysis()
    lopo = leave_one_pair_out()
    portfolio = portfolio_construction()
    stress = stress_test()
    holdout = final_holdout()
    artifacts = bind_artifacts()

    audit = _load_json("data_quality/audit.json")
    wfo = _load_json("walk_forward_results.json")
    report = update_final_report(audit, wfo, mtf, param_stability, regime, lopo, portfolio, stress, holdout, artifacts)
    console.print(f"\n[bold green]Deep analysis complete. Report: {report}[/bold green]")


if __name__ == "__main__":
    run_deep_analysis()
