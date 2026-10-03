#!/usr/bin/env python3
"""Which strategies actually trade on daily bars, and do they clear costs?

plan_promotable_campaign.py found the fold structures the spread gate can
resolve: BTC/USDT 1d at 12/2/2/2 yields 32 folds, where a strategy must
clear costs in 19 to pass.

Before spending hours there it is worth knowing whether anything trades at
all on daily bars. The 1h work showed the registry holds positions only
1.2-1.5% of the time, and TIMEFRAME_PROBE_RESULT.md measured 0.58 trades
per window on 1d, which would make a 32-fold campaign produce empty folds.

Strategies are instantiated by module because trading_agent.strategies
exposes no registry and the canonical registry only hands back an adapter
with a per-bar forecast method. Running the real BacktestEngine keeps the
measurement on the same code path a campaign would use.

Run: .venv/bin/python scripts/probe_daily_tradeability.py
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

import polars as pl

from trading_agent.backtest.engine import BacktestEngine

# (strategy_id, module, class). Verified to match the canonical registry's
# strategy_id for all nine.
STRATEGIES = [
    ("ma_crossover", "ma_crossover", "MaCrossover"),
    ("enhanced_ma", "enhanced_ma", "EnhancedMaCrossover"),
    ("ma_adx", "enhanced_ma", "MaAdxCrossover"),
    ("ma_vol_target", "enhanced_ma", "MaVolTargetCrossover"),
    ("ensemble_ma_adx", "enhanced_ma", "EnsembleMaAdx"),
    ("rsi", "rsi", "RsiStrategy"),
    ("bbands", "bbands", "BBandsStrategy"),
    ("volatility_breakout", "volatility_breakout", "VolatilityBreakoutStrategy"),
    ("trend_pullback", "trend_pullback", "TrendPullbackStrategy"),
]


def main() -> None:
    df = pl.read_parquet(ROOT / "data/raw/binance/BTC_USDT/1d.parquet")
    print(f"BTC/USDT 1d: {df.height} bars")
    print("long_only, fixed_position_pct=0.95, commission 5bps, slippage 2bps")
    print()
    print(f"{'strategy':26s} {'trades':>7} {'ret%':>9} {'win%':>7} {'DD%':>8}")
    print("-" * 62)

    rows = []
    for sid, module, cls_name in STRATEGIES:
        try:
            cls = getattr(
                importlib.import_module(f"trading_agent.strategies.{module}"),
                cls_name,
            )
            engine = BacktestEngine(
                strategy=cls(),
                initial_capital=100000,
                commission=0.0005,
                slippage=0.0002,
                long_only=True,
                fixed_position_pct=0.95,
            )
            res = engine.run(df, symbol="BTC/USDT", timeframe="1d")
            rows.append((sid, res.total_trades, res.total_return_pct,
                         res.win_rate * 100, res.max_drawdown_pct))
            print(f"{sid:26s} {res.total_trades:>7} {res.total_return_pct:>8.2f}% "
                  f"{res.win_rate * 100:>6.1f}% {res.max_drawdown_pct:>7.1f}%")
        except Exception as exc:
            rows.append((sid, None, None, None, None))
            print(f"{sid:26s} {'-':>7} {'-':>9} {'-':>7} {'-':>8}  "
                  f"{type(exc).__name__}: {str(exc)[:28]}")

    traded = [r for r in rows if r[1]]
    profitable = [r for r in rows if r[1] and r[2] and r[2] > 0]

    print()
    print("=" * 62)
    print(f"strategies run      : {sum(1 for r in rows if r[1] is not None)}")
    print(f"with trades on 1d   : {len(traded)}")
    print(f"profitable on 1d    : {len(profitable)}")
    print("=" * 62)

    if not traded:
        print("\nNothing trades on daily bars. A 32-fold campaign would produce")
        print("empty folds and the spread gate could never be met here.")
        print("\nThe choice is then: shorter test windows on 1h, a registry whose")
        print("strategies trade on 1d, or accept that the gate cannot be")
        print("satisfied on the data and symbols available.")
    elif not profitable:
        print("\nStrategies trade but none is profitable on daily bars. A campaign")
        print("would measure the spread of a losing strategy — the gate is")
        print("satisfiable but the outcome is already determined.")
    else:
        print("\nCandidates worth a campaign:")
        for sid, trades, ret, win, dd in sorted(traded, key=lambda r: -r[2]):
            print(f"  {sid:26s} {trades:>4} trades {ret:>8.2f}%  "
                  f"win {win:>5.1f}%  DD {dd:>6.1f}%")


if __name__ == "__main__":
    main()