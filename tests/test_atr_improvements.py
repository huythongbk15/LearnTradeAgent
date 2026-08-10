#!/usr/bin/env python
"""
Test script for ATR-based trailing stop, active take-profit, and volatility position sizing.
"""

from __future__ import annotations

import polars as pl
import shutil
from pathlib import Path

from trading_agent.execution.engine import ExecutionEngine
from trading_agent.execution.indicators import compute_atr
from trading_agent.agents.base import AgentMessage


def reset_paper_state():
    """Reset paper exchange state for clean test."""
    state_dir = Path("data/execution")
    if state_dir.exists():
        shutil.rmtree(state_dir)
    state_dir.mkdir(parents=True, exist_ok=True)


def create_sample_ohlcv(n_bars: int = 500) -> pl.DataFrame:
    """Create sample OHLCV data with realistic price movements."""
    import numpy as np

    np.random.seed(42)
    base_price = 50000.0
    returns = np.random.normal(0.0001, 0.02, n_bars)  # Small drift, 2% volatility
    prices = base_price * np.exp(np.cumsum(returns))

    # Generate OHLC from close prices
    highs = prices * (1 + np.abs(np.random.normal(0, 0.005, n_bars)))
    lows = prices * (1 - np.abs(np.random.normal(0, 0.005, n_bars)))
    opens = np.roll(prices, 1)
    opens[0] = base_price
    volumes = np.random.uniform(10, 100, n_bars)

    return pl.DataFrame({
        "timestamp": pl.datetime_range(
            start=pl.datetime(2024, 1, 1),
            end=pl.datetime(2024, 1, 1) + pl.duration(days=n_bars),
            interval="1h",
            eager=True,
        )[:n_bars],
        "open": opens,
        "high": highs,
        "low": lows,
        "close": prices,
        "volume": volumes,
    })


def test_atr_indicators():
    """Test ATR computation."""
    reset_paper_state()
    print("=" * 60)
    print("Testing ATR Indicators")
    print("=" * 60)

    df = create_sample_ohlcv(200)
    atr = compute_atr(df, period=14)
    df = df.with_columns(atr)

    print(f"Data shape: {df.shape}")
    print(f"ATR columns: {[c for c in df.columns if 'atr' in c.lower()]}")
    print(f"Last 5 ATR values:\n{df['atr'].tail(5)}")
    print(f"Last 5 close prices:\n{df['close'].tail(5)}")
    print()


def test_execution_with_atr():
    """Test execution engine with ATR-based risk management."""
    reset_paper_state()
    print("=" * 60)
    print("Testing Execution Engine with ATR Risk Management")
    print("=" * 60)

    # Create engine
    engine = ExecutionEngine(
        exchange_name="binance",
        initial_capital=10000.0,
        commission=0.001,
        slippage=0.0005,
    )

    # Create sample data
    df = create_sample_ohlcv(300)
    atr_series = compute_atr(df, period=14)
    df = df.with_columns(atr_series)

    # Get ATR value at entry point (bar 150)
    entry_atr = float(df["atr"][150])
    entry_price = float(df["close"][150])

    # Simulate a BUY signal with ATR-based sizing (pass ATR in details)
    signal = AgentMessage(
        role="trader",
        signal="BUY",
        confidence=0.7,
        reasoning="Test signal with ATR risk management",
        details={
            "symbol": "BTC/USDT",
            "max_position_size_pct": 0.25,
            "trailing_atr_mult": 2.0,
            "risk_reward": 2.0,
            "atr": entry_atr,  # Pass ATR directly
        },
    )

    # Execute signal
    engine.update_prices({"BTC/USDT": entry_price})
    orders = engine.execute_signal(signal)
    print(f"Orders placed: {len(orders)}")
    for o in orders:
        print(f"  {o.side.value} {o.amount:.6f} {o.symbol} @ {o.avg_fill_price:.2f}")

    # Check position
    pos = engine.exchange.get_position("BTC/USDT")
    if pos:
        print("\nPosition:")
        print(f"  Entry: ${pos.entry_price:.2f}")
        print(f"  Qty: {pos.quantity:.6f}")
        print(f"  Stop Loss: ${pos.stop_loss:.2f}")
        print(f"  Take Profit: ${pos.take_profit:.2f}")
        print(f"  Trailing Stop Type: {pos.metadata.get('trailing_stop_type')}")
        print(f"  Risk/Reward: {pos.metadata.get('risk_reward')}")

    # Simulate price updates with ATR trailing stop
    print("\n--- Simulating price updates with ATR trailing stop ---")
    for i in range(150, 250):
        price = float(df["close"][i])
        ohlcv_slice = df.slice(0, i+1)
        engine.update_with_atr("BTC/USDT", ohlcv_slice)

        pos = engine.exchange.get_position("BTC/USDT")
        if pos and pos.is_active:
            if i % 20 == 0:
                print(f"  Bar {i}: Price=${price:.2f}, SL=${pos.stop_loss:.2f}, TP=${pos.take_profit:.2f}, PnL={pos.unrealized_pnl_pct:+.2f}%")
        elif pos and not pos.is_active:
            print(f"  Position closed at bar {i} (price=${price:.2f})")
            break

    # Summary
    summary = engine.get_summary()
    print("\nFinal Summary:")
    for k, v in summary.items():
        print(f"  {k}: {v}")

    trades = engine.get_trade_history(10)
    print(f"\nTrades: {len(trades)}")
    for t in trades:
        print(f"  {t['symbol']} {t['side']} PnL=${t['pnl']:.2f} ({t['pnl_pct']:+.2f}%) reason={t['reason']}")

    print()


def test_comparison_fixed_vs_atr():
    """Compare fixed stop vs ATR trailing stop."""
    reset_paper_state()
    print("=" * 60)
    print("Comparison: Fixed Stop vs ATR Trailing Stop")
    print("=" * 60)

    df = create_sample_ohlcv(400)
    atr_series = compute_atr(df, period=14)
    df = df.with_columns(atr_series)

    # Get ATR at entry
    entry_atr = float(df["atr"][150])

    # Test 1: Fixed percentage stop (old way)
    engine_fixed = ExecutionEngine(initial_capital=10000.0, exchange_name="binance_fixed")
    signal = AgentMessage(
        role="trader",
        signal="BUY",
        confidence=0.7,
        reasoning="Fixed stop test",
        details={"symbol": "BTC/USDT", "max_position_size_pct": 0.25},
    )
    engine_fixed.update_prices({"BTC/USDT": float(df["close"][150])})
    engine_fixed.execute_signal(signal)
    pos_fixed = engine_fixed.exchange.get_position("BTC/USDT")
    if pos_fixed:
        pos_fixed.stop_loss = pos_fixed.entry_price * 0.95  # 5% fixed
        pos_fixed.take_profit = pos_fixed.entry_price * 1.10  # 10% fixed
        pos_fixed.trailing_stop_pct = 0.05
        pos_fixed.metadata["trailing_stop_type"] = "fixed_pct"

    # Test 2: ATR-based (new way) - use different exchange name
    engine_atr = ExecutionEngine(initial_capital=10000.0, exchange_name="binance_atr")
    signal_atr = AgentMessage(
        role="trader",
        signal="BUY",
        confidence=0.7,
        reasoning="ATR trailing test",
        details={
            "symbol": "BTC/USDT",
            "max_position_size_pct": 0.25,
            "trailing_atr_mult": 2.0,
            "risk_reward": 2.0,
            "atr": entry_atr,  # Pass ATR
        },
    )
    engine_atr.update_prices({"BTC/USDT": float(df["close"][150])})
    engine_atr.execute_signal(signal_atr)

    # Run both
    for i in range(150, 350):
        price = float(df["close"][i])
        ohlcv_slice = df.slice(0, i+1)

        # Fixed engine (no ATR data passed)
        engine_fixed.update_prices({"BTC/USDT": price})

        # ATR engine
        engine_atr.update_with_atr("BTC/USDT", ohlcv_slice)

        pos_f = engine_fixed.exchange.get_position("BTC/USDT")
        pos_a = engine_atr.exchange.get_position("BTC/USDT")

        # Check if both closed
        if (not pos_f or not pos_f.is_active) and (not pos_a or not pos_a.is_active):
            break

    summary_f = engine_fixed.get_summary()
    summary_a = engine_atr.get_summary()

    print(f"Fixed Stop:   Equity=${summary_f['equity']:.2f}, Return={summary_f['return_pct']:+.2f}%, Trades={summary_f['total_trades']}")
    print(f"ATR Trailing: Equity=${summary_a['equity']:.2f}, Return={summary_a['return_pct']:+.2f}%, Trades={summary_a['total_trades']}")

    trades_f = engine_fixed.get_trade_history(5)
    trades_a = engine_atr.get_trade_history(5)
    print(f"Fixed trades: {len(trades_f)}, ATR trades: {len(trades_a)}")
    if trades_f:
        print(f"  Fixed last: PnL=${trades_f[0]['pnl']:.2f} ({trades_f[0]['pnl_pct']:+.2f}%) reason={trades_f[0]['reason']}")
    if trades_a:
        print(f"  ATR last: PnL=${trades_a[0]['pnl']:.2f} ({trades_a[0]['pnl_pct']:+.2f}%) reason={trades_a[0]['reason']}")

    print()


def test_position_sizing():
    """Test different position sizing methods."""
    reset_paper_state()
    print("=" * 60)
    print("Testing Position Sizing Methods")
    print("=" * 60)

    from trading_agent.execution.indicators import (
        compute_atr_position_size,
        compute_kelly_fraction,
        compute_volatility_target_size,
    )

    equity = 10000.0
    current_price = 50000.0
    atr = 1000.0  # 2% ATR

    # ATR-based sizing
    size_atr = compute_atr_position_size(equity, atr, current_price, risk_pct=0.02, atr_multiplier=2.0)
    print(f"ATR-based (2% risk, 2x ATR): {size_atr:.6f} BTC (${size_atr * current_price:,.2f})")

    # Kelly fraction
    kelly = compute_kelly_fraction(win_rate=0.55, avg_win=0.04, avg_loss=0.02)
    print(f"Kelly fraction (55% WR, 2:1 R:R): {kelly:.4f} ({kelly*100:.1f}%)")

    # Volatility targeting
    vol_size = compute_volatility_target_size(equity, 0.15, 0.50, max_leverage=1.0)
    print(f"Vol targeting (15% target, 50% asset vol): ${vol_size:,.2f}")

    print()


if __name__ == "__main__":
    test_atr_indicators()
    test_position_sizing()
    test_execution_with_atr()
    test_comparison_fixed_vs_atr()
    print("=" * 60)
    print("All tests completed!")
    print("=" * 60)
