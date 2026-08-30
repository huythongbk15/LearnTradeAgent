"""Deterministic synthetic OHLCV generator for fast, bounded WFO evidence runs.

This module exists so the full nested-WFO pipeline (fold geometry, provenance
hashes, hard gates, REAL sensitivity re-runs, multi-dimensional eval, and the
final holdout one-shot) can be exercised end-to-end in CI without pulling
multi-year real data or timing out on heavy backtests.

The generator is fully deterministic for a given ``seed`` so evidence runs are
reproducible. The produced schema mirrors ``load_ohlcv`` output exactly
(timestamp, open, high, low, close, volume, exchange, symbol, timeframe, atr,
is_closed) so the production backtest path runs unmodified.
"""
from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import numpy as np
import polars as pl

if TYPE_CHECKING:
    from trading_agent.backtest.nested_wfo import WFOSpec


def generate_synthetic_ohlcv(
    symbol: str = "BTC/USDT",
    timeframe: str = "1h",
    exchange: str = "binance",
    n_bars: int = 1200,
    seed: int = 7,
    start_ts: datetime | None = None,
    regimes: bool = True,
) -> pl.DataFrame:
    """Generate a deterministic OHLCV dataframe with trend/sideways regimes.

    Parameters
    ----------
    n_bars : int
        Number of hourly bars to generate.
    seed : int
        RNG seed for reproducibility.
    start_ts : datetime, optional
        First bar timestamp; defaults to 2025-01-01 UTC.
    regimes : bool
        If True, the series alternates trending-up / sideways / trending-down
        segments so MA-crossover and RSI strategies actually generate trades.

    Returns
    -------
    polars.DataFrame
        Schema-compatible with ``load_ohlcv`` output (sorted by timestamp).
    """
    rng = np.random.default_rng(seed)
    if start_ts is None:
        start_ts = datetime(2025, 1, 1, 0, 0, tzinfo=UTC)
    else:
        start_ts = start_ts.astimezone(UTC) if start_ts.tzinfo else start_ts.replace(tzinfo=UTC)

    timestamps = [start_ts + timedelta(hours=i) for i in range(n_bars)]
    # Build a price path with regime switches.
    price = 100.0
    prices: list[float] = []
    # Drift segments: up, flat, down, flat, repeat.
    if regimes:
        seg = n_bars // 5
        drifts = [0.0008, 0.0, -0.0008, 0.0, 0.0006]
    else:
        seg = n_bars
        drifts = [0.0003]
    for i in range(n_bars):
        drift = drifts[(i // seg) % len(drifts)]
        shock = rng.normal(0.0, 0.004)
        ret = drift + shock
        price *= 1.0 + ret
        prices.append(max(price, 1.0))

    prices_arr = np.array(prices, dtype=float)
    # OHLC around the close path
    opens = np.empty(n_bars)
    opens[0] = prices_arr[0] * (1.0 - rng.normal(0, 0.001))
    opens[1:] = prices_arr[:-1]
    highs = np.maximum(prices_arr, opens) * (1.0 + np.abs(rng.normal(0, 0.0015, n_bars)))
    lows = np.minimum(prices_arr, opens) * (1.0 - np.abs(rng.normal(0, 0.0015, n_bars)))
    closes = prices_arr
    volumes = rng.integers(50, 500, n_bars).astype(float) * 100.0
    # ATR proxy needed by the runtime bridge / sizing
    atr = np.maximum(highs - lows, 0.01)

    df = pl.DataFrame(
        {
            "timestamp": [t.replace(tzinfo=None) for t in timestamps],
            "open": np.round(opens, 6),
            "high": np.round(highs, 6),
            "low": np.round(lows, 6),
            "close": np.round(closes, 6),
            "volume": np.round(volumes, 4),
            "exchange": exchange,
            "symbol": symbol,
            "timeframe": timeframe,
            "atr": np.round(atr, 6),
            "is_closed": [True] * n_bars,
        }
    ).sort("timestamp")
    return df


def synthetic_wfo_spec(
    strategy_id: str = "enhanced_ma",
    symbol: str = "BTC/USDT",
    timeframe: str = "1h",
    n_bars: int = 1200,
    holdout_fraction: float = 0.2,
) -> tuple[WFOSpec, int, int]:
    """Build a bounded WFOSpec + synthetic holdout bar range for evidence runs.

    Returns (spec, holdout_start_bar, holdout_end_bar) where the holdout is the
    final ``holdout_fraction`` of a synthetic dataset of ``n_bars`` bars. This is
    consumed by the evidence test which monkeypatches ``load_ohlcv`` to return
    ``generate_synthetic_ohlcv(n_bars)`` and ``_resolve_frozen_holdout_window``
    to return these bar indices.
    """
    from trading_agent.backtest.nested_wfo import WFOSpec
    from trading_agent.backtest.tournament import SCENARIO_BASE

    spec = WFOSpec(
        strategy_id=strategy_id,
        symbol=symbol,
        timeframe=timeframe,
        param_grid={
            "fast_period": [10],
            "slow_period": [60],
            "trend_adx_threshold": [30],
        },
        cost_scenarios=(SCENARIO_BASE,),
        # These months-based values are valid but intentionally tiny; the
        # evidence test overrides fold geometry via a monkeypatched
        # _get_fold_indices so the WFO runs on a bounded synthetic range.
        train_months=1,
        val_months=1,
        test_months=1,
        step_months=1,
        min_trades_per_fold=3,
        search_family="evidence_smoke",
        seed=seed_safe(strategy_id, symbol),
        evidence_class="SYNTHETIC_TEST_ONLY",
    )
    hb = int(n_bars * (1.0 - holdout_fraction))
    return spec, hb, n_bars - 1


def seed_safe(strategy_id: str, symbol: str) -> int:
    """Deterministic int seed from strategy/symbol for reproducible evidence runs."""
    payload = f"{strategy_id}\0{symbol}".encode()
    return (int.from_bytes(hashlib.sha256(payload).digest()[:8], "big") % (2**31)) + 1
