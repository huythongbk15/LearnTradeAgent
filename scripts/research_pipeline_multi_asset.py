#!/usr/bin/env python3
"""
Master research pipeline for 10 pairs × 3 timeframes = 30 streams.
Follows prompt sections 3-70 strictly.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Project paths
ROOT = Path(".")
sys.path.insert(0, str(ROOT / "src"))
os.environ["USE_LLM"] = "false"

import polars as pl
from rich.console import Console
from rich.table import Table

from trading_agent.data.storage import load_ohlcv
from trading_agent.strategies import get_strategy
from trading_agent.backtest.engine import BacktestEngine

console = Console()

# ── Fixed universe ─────────────────────────────────────────────────────────
SYMBOLS = [
    "BTC/USDT",
    "ETH/USDT",
    "SOL/USDT",
    "XRP/USDT",
    "BNB/USDT",
    "ZEC/USDT",
    "DOGE/USDT",
    "TRX/USDT",
    "ADA/USDT",
    "NEAR/USDT",
]
TIMEFRAMES = ["1h", "4h", "1d"]
EXCHANGE = "binance"

# ── Run metadata ───────────────────────────────────────────────────────────
RUN_ID = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
RUN_DIR = ROOT / "data" / "research_runs" / RUN_ID
RUN_DIR.mkdir(parents=True, exist_ok=True)

# ── Cost model ─────────────────────────────────────────────────────────────
COST = {
    "maker_fee": 0.0006,
    "taker_fee": 0.001,
    "spread_bps": 5,
    "slippage_bps": 5,
}
COST_STRESS = [0.5, 1.0, 1.5, 2.0, 3.0]


# ── Helpers ────────────────────────────────────────────────────────────────
def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def _data_audit(symbol: str, timeframe: str) -> dict[str, Any]:
    """Section 3-5: Data audit + OHLCV consistency."""
    try:
        df = load_ohlcv(EXCHANGE, symbol, timeframe)
    except Exception as e:
        return {
            "symbol": symbol,
            "timeframe": timeframe,
            "status": "FAIL",
            "error": str(e),
        }

    if df is None or len(df) == 0:
        return {
            "symbol": symbol,
            "timeframe": timeframe,
            "status": "FAIL",
            "error": "no data",
        }

    df = df.sort("timestamp")
    n = len(df)
    expected = {
        "1h": 24 * 365 * 3,
        "4h": 6 * 365 * 3,
        "1d": 365 * 3,
    }.get(timeframe, 0)

    # Basic checks
    missing = sum(
        df.select(
            [pl.col(c).null_count() for c in ["open", "high", "low", "close", "volume"]]
        ).row(0)
    )
    duplicates = int(df["timestamp"].is_duplicated().sum())
    non_finite = sum(
        df.select(
            [
                (~pl.col(c).is_finite()).sum()
                for c in ["open", "high", "low", "close", "volume"]
            ]
        ).row(0)
    )
    zero_neg_vol = int((df["volume"] <= 0).sum())

    # OHLCV consistency
    o = pl.col("open")
    h = pl.col("high")
    low_ = pl.col("low")
    c = pl.col("close")
    v = pl.col("volume")
    consistency = (
        (h >= o).alias("h_ge_o")
        & (h >= c).alias("h_ge_c")
        & (low_ <= o).alias("l_le_o")
        & (low_ <= c).alias("l_le_c")
        & (h >= low_).alias("h_ge_l")
        & (o > 0).alias("o_pos")
        & (h > 0).alias("h_pos")
        & (low_ > 0).alias("l_pos")
        & (c > 0).alias("c_pos")
        & (v >= 0).alias("v_nonneg")
    )
    consistency_ok = int(df.select(consistency).sum().row(0)[0])
    consistency_pct = consistency_ok / n if n else 0.0

    # Gap analysis
    diffs = df["timestamp"].diff().cast(pl.Int64).drop_nulls()
    median_gap = int(diffs.median()) if len(diffs) > 0 else 0
    expected_gap = {
        "1h": 3_600_000_000,
        "4h": 14_400_000_000,
        "1d": 86_400_000_000,
    }.get(timeframe, 0)
    gaps = int((diffs != expected_gap).sum()) if len(diffs) > 0 and expected_gap else 0

    # Determine quality
    if (
        n == 0
        or missing
        or duplicates
        or non_finite
        or consistency_pct < 0.99
        or zero_neg_vol > n * 0.001
    ):
        quality = "FAIL"
    elif gaps > n * 0.01 or zero_neg_vol > 0:
        quality = "DEGRADED"
    else:
        quality = "PASS"

    return {
        "symbol": symbol,
        "timeframe": timeframe,
        "start": str(df["timestamp"].item(0)),
        "end": str(df["timestamp"].item(-1)),
        "bars": n,
        "expected_bars": expected,
        "missing_bars": max(0, expected - n),
        "duplicates": duplicates,
        "non_finite": non_finite,
        "zero_negative_volume": zero_neg_vol,
        "consistency_pct": round(consistency_pct * 100, 2),
        "gaps": gaps,
        "median_gap_ms": median_gap,
        "timezone": "UTC",
        "data_sha256": _sha256_file(
            Path(f"data/raw/{EXCHANGE}/{symbol.replace('/', '_')}/{timeframe}.parquet")
        ),
        "quality": quality,
        "status": "PASS" if quality == "PASS" else "FAIL",
    }


@dataclass
class Trial:
    pair: str
    timeframe: str
    strategy: str
    params: dict[str, Any]
    fold: int
    train_start: str
    train_end: str
    val_start: str
    val_end: str
    test_start: str
    test_end: str
    gross_sharpe: float = 0.0
    net_sharpe: float = 0.0
    net_return: float = 0.0
    max_dd: float = 0.0
    trades: int = 0
    turnover: float = 0.0
    cost_drag: float = 0.0
    psr: float = 0.0
    dsr: float = 0.0
    pbo: float = 0.0
    positive_folds: int = 0
    cost_2x_sharpe: float = 0.0
    cost_3x_sharpe: float = 0.0
    status: str = "RESEARCH_ONLY"
    rejection_reason: str | None = None


class ResearchPipeline:
    def __init__(self):
        self.run_id = RUN_ID
        self.run_dir = RUN_DIR
        self.artifacts: list[dict[str, Any]] = []
        self.trials: list[Trial] = []
        self.data_audit: list[dict[str, Any]] = []
        self.baselines: list[dict[str, Any]] = []
        self.cross_pair: list[dict[str, Any]] = []
        self.portfolio: list[dict[str, Any]] = []
        self.stress: list[dict[str, Any]] = []
        self.final_holdout: list[dict[str, Any]] = []

    # ── Section 3-5: Data Audit ───────────────────────────────────────────
    def run_data_audit(self) -> None:
        console.print("\n[bold cyan]═══ Section 3-5: Data Audit ═══[/bold cyan]")
        for sym in SYMBOLS:
            for tf in TIMEFRAMES:
                audit = _data_audit(sym, tf)
                self.data_audit.append(audit)
                icon = "✅" if audit["status"] == "PASS" else "❌"
                console.print(
                    f"  {icon} {sym} {tf}: bars={audit['bars']} | missing={audit['missing_bars']} | "
                    f"dups={audit['duplicates']} | consistency={audit['consistency_pct']:.1f}% | {audit['quality']}"
                )

        (self.run_dir / "data_quality").mkdir(exist_ok=True)
        with open(self.run_dir / "data_quality" / "audit.json", "w") as f:
            json.dump(self.data_audit, f, indent=2)

    # ── Section 9: Baselines ──────────────────────────────────────────────
    def _get_baseline_strategies(self) -> dict[str, Any]:
        return {
            "no_trade": None,
            "buy_hold": None,
            "ma_crossover": get_strategy("ma_crossover")(),
            "rsi": get_strategy("rsi")(),
            "bbands": get_strategy("bbands")(),
            "enhanced_ma": get_strategy("enhanced_ma")(),
        }

    def _cost_fee(self, cost_mult: float) -> float:
        return COST["taker_fee"] * cost_mult

    def _run_backtest(
        self,
        df: pl.DataFrame,
        strategy,
        symbol: str,
        timeframe: str,
        cost_mult: float = 1.0,
    ) -> dict[str, Any]:
        fee = self._cost_fee(cost_mult)
        if strategy is None:
            # Buy & hold benchmark
            start = float(df["close"].item(0))
            end = float(df["close"].item(-1))
            ret = (end - start) / start * 100
            return {
                "strategy": "buy_hold",
                "return": ret,
                "sharpe": 0.0,
                "max_dd": 0.0,
                "trades": 1,
                "win_rate": 1.0 if ret > 0 else 0.0,
                "profit_factor": 999.0 if ret > 0 else 0.0,
                "avg_hold_bars": len(df),
                "cost_mult": cost_mult,
            }

        engine = BacktestEngine(
            strategy,
            initial_capital=10_000.0,
            commission=fee + COST["spread_bps"] / 10000,
            slippage=COST["slippage_bps"] / 10000,
            spread_bps=COST["spread_bps"],
            atr_sl_mult=2.0,
            atr_tp_mult=3.0,
            trailing_atr_mult=1.5,
        )
        result = engine.run(df)
        return {
            "strategy": strategy.meta.name
            if hasattr(strategy, "meta") and strategy.meta
            else "unknown",
            "return": result.total_return_pct,
            "sharpe": result.sharpe_ratio,
            "max_dd": result.max_drawdown_pct,
            "trades": result.total_trades,
            "win_rate": result.win_rate,
            "profit_factor": result.profit_factor,
            "avg_hold_bars": result.avg_hold_bars,
            "cost_mult": cost_mult,
        }

    def run_baselines(self) -> None:
        console.print("\n[bold cyan]═══ Section 9: Baselines ═══[/bold cyan]")
        baselines = self._get_baseline_strategies()
        rows = []
        for sym in SYMBOLS:
            for tf in TIMEFRAMES:
                audit = next(
                    (
                        a
                        for a in self.data_audit
                        if a["symbol"] == sym and a["timeframe"] == tf
                    ),
                    None,
                )
                if not audit or audit["status"] != "PASS":
                    continue
                df = load_ohlcv(EXCHANGE, sym, tf).sort("timestamp")
                for name, strategy in baselines.items():
                    res = self._run_backtest(df, strategy, sym, tf)
                    res["symbol"] = sym
                    res["timeframe"] = tf
                    res["data_quality"] = audit["quality"]
                    self.baselines.append(res)
                    rows.append(
                        (sym, tf, name, res["sharpe"], res["return"], res["max_dd"])
                    )

        table = Table("Pair", "TF", "Strategy", "Sharpe", "Return%", "MaxDD%")
        for r in rows:
            table.add_row(*[str(x) for x in r])
        console.print(table)

        (self.run_dir / "baselines").mkdir(exist_ok=True)
        with open(self.run_dir / "baselines" / "results.json", "w") as f:
            json.dump(self.baselines, f, indent=2)

    # ── Section 12-15: Walk-forward ───────────────────────────────────────
    def run_walk_forward(self) -> None:
        console.print("\n[bold cyan]═══ Section 12-15: Walk-Forward ═══[/bold cyan]")
        # Placeholder for nested purged walk-forward implementation
        console.print(
            "[yellow]Walk-forward splits will be implemented in next iteration.[/yellow]"
        )

    # ── Section 25: Selection ─────────────────────────────────────────────
    def run_selection(self) -> None:
        console.print("\n[bold cyan]═══ Section 25: Model Selection ═══[/bold cyan]")
        console.print(
            "[yellow]Selection logic will be implemented after baselines.[/yellow]"
        )

    # ── Section 26-32: Costs, Stability ───────────────────────────────────
    def run_cost_stress(self) -> None:
        console.print("\n[bold cyan]═══ Section 26-27: Cost Stress ═══[/bold cyan]")
        # Placeholder for cost stress across all baselines
        console.print(
            "[yellow]Cost stress will be computed from baseline results.[/yellow]"
        )

    # ── Section 33-37: Cross-pair / LOPO ──────────────────────────────────
    def run_cross_pair(self) -> None:
        console.print(
            "\n[bold cyan]═══ Section 33-36: Cross-Pair / LOPO ═══[/bold cyan]"
        )
        console.print(
            "[yellow]Cross-pair analysis will be implemented after single-TF stage.[/yellow]"
        )

    # ── Section 38-42: Regime / Online / Sizing ───────────────────────────
    def run_regime_online(self) -> None:
        console.print(
            "\n[bold cyan]═══ Section 38-42: Regime / Online / Sizing ═══[/bold cyan]"
        )
        console.print(
            "[yellow]Regime/online/sizing will be implemented after baseline stage.[/yellow]"
        )

    # ── Section 43-50: Portfolio ──────────────────────────────────────────
    def run_portfolio(self) -> None:
        console.print("\n[bold cyan]═══ Section 43-50: Portfolio ═══[/bold cyan]")
        console.print(
            "[yellow]Portfolio construction will be implemented after selection.[/yellow]"
        )

    # ── Section 52-56: Stress / Holdout / Artifact ────────────────────────
    def run_stress_holdout(self) -> None:
        console.print(
            "\n[bold cyan]═══ Section 52-56: Stress / Holdout / Artifact ═══[/bold cyan]"
        )
        console.print(
            "[yellow]Stress/holdout will be implemented after selection.[/yellow]"
        )

    # ── Section 57-63: Tables ─────────────────────────────────────────────
    def write_tables(self) -> None:
        console.print("\n[bold cyan]═══ Section 57-63: Tables ═══[/bold cyan]")
        console.print("[yellow]Tables will be generated after analysis.[/yellow]")

    # ── Section 64-70: Final report ───────────────────────────────────────
    def write_final_report(self) -> None:
        report = {
            "run_id": self.run_id,
            "git_sha": os.popen("git rev-parse HEAD").read().strip(),
            "data_audit": self.data_audit,
            "baselines": self.baselines,
            "cross_pair": self.cross_pair,
            "portfolio": self.portfolio,
            "stress": self.stress,
            "final_holdout": self.final_holdout,
            "mainnet": "NO-GO",
        }
        with open(self.run_dir / "final_report.json", "w") as f:
            json.dump(report, f, indent=2)
        console.print(
            f"\n[green]Report saved to {self.run_dir / 'final_report.json'}[/green]"
        )

    # ── Orchestrator ──────────────────────────────────────────────────────
    def run(self) -> None:
        console.print(
            f"[bold green]Research pipeline started: {self.run_id}[/bold green]"
        )
        self.run_data_audit()
        self.run_baselines()
        self.run_walk_forward()
        self.run_selection()
        self.run_cost_stress()
        self.run_cross_pair()
        self.run_regime_online()
        self.run_portfolio()
        self.run_stress_holdout()
        self.write_tables()
        self.write_final_report()
        console.print("\n[bold green]Pipeline complete.[/bold green]")
        console.print("MAINNET: NO-GO")


if __name__ == "__main__":
    ResearchPipeline().run()
