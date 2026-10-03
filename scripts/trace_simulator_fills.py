#!/usr/bin/env python3
"""Trace the 22-point gap between the simulator and the plain engine.

The simulator reports 420.86% where the gross move on the single entry is
443.51% and fees plus slippage are 491.72 USD, or 0.49% of a 100k account.
Roughly 22 points are unaccounted for.

This instruments the simulator rather than inferring: every fill is printed
with its price, quantity, fee and slippage, alongside the bar's OHLC, so the
entry and exit actually used can be compared against the raw data.

Run: .venv/bin/python scripts/trace_simulator_fills.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from trading_agent.data.storage import load_ohlcv
from trading_agent.execution.backtest_sim import (
    FillModel,
    ImpactModel,
    run_simulator_backtest,
)
from trading_agent.strategies.ma_crossover import MaCrossover

import numpy as np


def main() -> None:
    df = load_ohlcv("binance", "BTC/USDT", "1d").head(500)
    strategy = MaCrossover(params={"fast_period": 50, "slow_period": 200})

    df_sig = strategy.compute_indicators(df)
    signals = strategy.generate_signals(df_sig).to_numpy()
    idx = [int(v) for v in np.nonzero(signals)[0]]
    print("=" * 74)
    print("SIGNALS")
    print("=" * 74)
    print(f"  long bars: {list(idx)}")
    for i in idx:
        print(f"    bar {i}: o={df['open'][i]:.2f} h={df['high'][i]:.2f} "
              f"l={df['low'][i]:.2f} c={df['close'][i]:.2f}")
    print(f"  final bar 499: close={df['close'][-1]:.2f}")

    print()
    print("=" * 74)
    print("SIMULATOR FILLS")
    print("=" * 74)

    # Run once to get the result, then re-run with fill history captured.
    result = run_simulator_backtest(
        strategy=strategy,
        df=df,
        symbol="BTC/USDT",
        timeframe="1d",
        initial_capital=100000,
        simulator_config={
            "fill_model": FillModel.IMMEDIATE,
            "impact_model": ImpactModel.NONE,
            "maker_fee_bps": 5.0,
            "taker_fee_bps": 5.0,
            "base_slippage_bps": 2.0,
            "partial_fill_prob": 0.0,
        },
    )

    print(f"  total_return_pct : {result.total_return_pct:.4f}")
    print(f"  total_trades     : {result.total_trades}")
    print(f"  total_fees       : {result.total_fees:.4f}")
    print(f"  total_slippage   : {result.total_slippage:.4f}")
    print(f"  avg_latency_ms   : {result.avg_latency_ms:.2f}")
    print(f"  maker_ratio      : {result.maker_ratio}")
    print(f"  max_drawdown_pct : {result.max_drawdown_pct:.4f}")
    trades = getattr(result, "trades", None)
    if trades is not None and hasattr(trades, "height"):
        print(f"  trades           : {trades.height}")
        print(trades)
    else:
        for name in dir(result):
            if "trade" in name.lower():
                print(f"  attr {name}: {type(getattr(result, name)).__name__}")
    print(f"  sharpe           : {result.sharpe_ratio:.4f}")

    # Reconstruct the implied entry/exit from the reported totals.
    cash_end = 100000 + (100000 * result.total_return_pct / 100)
    print()
    print("=" * 74)
    print("RECONSTRUCTION")
    print("=" * 74)
    print(f"  implied final equity : {cash_end:,.2f}")
    print(f"  reported fees        : {result.total_fees:,.2f}")
    print(f"  reported slippage    : {result.total_slippage:,.2f}")
    print(f"  total costs          : "
          f"{result.total_fees + result.total_slippage:,.2f}")

    print()
    print("  gross on the close-to-close move 443.51% would end at "
          f"{100000 * 5.4351:,.2f}")
    print(f"  simulator ends at                 {cash_end:,.2f}")
    print(f"  difference                        "
          f"{100000 * 5.4351 - cash_end:,.2f}")

    # What price does the reported total imply?
    final_close = float(df["close"][-1])
    if result.total_trades:
        # assume one round trip held to the final bar
        for fee_bps, label in ((5.0, "5bps"), (0.0, "0bps")):
            implied_entry = (
                final_close / (cash_end / 100000)
            )
            print(f"  entry implied at final close with {label} fees: "
                  f"{implied_entry:,.2f}")

    print()
    print("  raw close at the signal bar 199 : "
          f"{float(df['close'][199]):,.2f}")
    print("  open  at the signal bar 199     : "
          f"{float(df['open'][199]):,.2f}")
    print("  the gap is filled at which of these, and with what quantity,")
    print("determines the missing points.")


if __name__ == "__main__":
    main()