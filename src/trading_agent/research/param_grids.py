#!/usr/bin/env python3
"""
Param grids for all 8 research strategy candidates.

Maps strategy_id → param grid for WFO optimization.

For cross-sectional strategies (S4, S8), the cs_variant suffix is appended
to the search_family to distinguish long-only vs long-short runs.
"""

from __future__ import annotations

from typing import Any

from trading_agent.backtest.tournament import (
    SCENARIO_BASE,
    SCENARIO_DOUBLE,
    SCENARIO_SLIPPAGE_STRESS,
    CostScenario,
)


# ── Single-asset strategy param grids ───────────────────────────────────────
SINGLE_ASSET_GRIDS: dict[str, dict[str, list[Any]]] = {
    # S1: Trend Pullback
    "trend_pullback": {
        "ma_fast": [10, 20, 30],
        "ma_slow": [80, 120],
        "adx_threshold": [25, 30],
        "fib_retracement": [0.382, 0.5],
        "volume_lookback": [20],
    },
    # S2: Range Mean Reversion
    "range_mean_reversion": {
        "vwap_window": [14, 20, 30],
        "zscore_entry": [1.5, 2.0, 2.5],
        "zscore_exit": [0.5],
        "bb_lookback": [20],
    },
    # S3: Volatility Expansion Breakout
    "volatility_breakout": {
        "bb_period": [14, 20, 21],
        "bb_std_dev": [2.0],
        "compression_percentile": [0.03, 0.05],
        "atr_spike_multiplier": [1.5, 2.0],
        "max_hold_bars": [10, 20],
    },
    # S5: Funding Rate Carry
    "funding_carry": {
        "funding_entry_threshold": [-0.0001, -0.001],
        "funding_exit_threshold": [0.00005, 0.0],
        "max_hold_days": [3],
        "vol_target_daily": [0.10],
    },
    # S6: Volatility Targeting (maps to ma_vol_target)
    "vol_target": {
        "fast_period": [20, 30],
        "slow_period": [60, 80],
    },
    # S7: Regime Ensemble (maps to regime_switching)
    "regime_ensemble": {
        "regime_method": ["rule_based", "hybrid"],
        "min_confidence": [0.4, 0.65],
        "regime_smoothing": [2, 3],
        "base_position_pct": [0.1, 0.2],
    },
}

# ── Cross-sectional strategy param grids ──────────────────────────────────
# Keyed by (strategy_id, cs_variant)
CROSS_SECTIONAL_GRIDS: dict[tuple[str, str], dict[str, list[Any]]] = {
    # S4: Cross-sectional momentum
    ("cross_sectional_momentum", "long_only"): {
        "lookback_days": [60, 90],
        "top_quantile": [0.3, 0.5],
        "rebalance_freq_hours": [24],
    },
    ("cross_sectional_momentum", "long_short"): {
        "lookback_days": [60, 90],
        "top_quantile": [0.3],
        "bottom_quantile": [0.3],
        "rebalance_freq_hours": [24],
    },
    # S8: Statistical arbitrage
    ("stat_arbitrage", "long_only"): {
        "zscore_entry": [1.5, 2.0],
        "zscore_exit": [0.5],
        "cointegration_window": [252],
        "adf_pvalue_threshold": [0.05],
    },
    ("stat_arbitrage", "long_short"): {
        "zscore_entry": [2.0, 2.5],
        "zscore_exit": [0.5],
        "cointegration_window": [252, 378],
        "adf_pvalue_threshold": [0.05, 0.1],
    },
}


def get_param_grid(
    strategy_id: str,
    cs_variant: str | None = None,
) -> dict[str, list[Any]]:
    """Get param grid for a strategy, optionally with cross-sectional variant."""
    if cs_variant is not None:
        return CROSS_SECTIONAL_GRIDS.get(
            (strategy_id, cs_variant),
            SINGLE_ASSET_GRIDS.get(strategy_id, {}),
        )
    return SINGLE_ASSET_GRIDS.get(strategy_id, {})


def get_strategy_code_name(strategy_id: str) -> str:
    """Map catalog strategy_id to the code-level registered name."""
    mapping = {
        "vol_target": "ma_vol_target",
        "regime_ensemble": "regime_switching",
    }
    return mapping.get(strategy_id, strategy_id)


__all__ = [
    "SINGLE_ASSET_GRIDS",
    "CROSS_SECTIONAL_GRIDS",
    "get_param_grid",
    "get_strategy_code_name",
]
