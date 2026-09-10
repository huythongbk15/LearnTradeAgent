#!/usr/bin/env python3
"""
Strategy Catalog — Defines 8 candidate strategies with precise logic.

Each strategy is defined by:
- Hypothesis: Economic/statistical rationale for edge
- Signal: What the strategy measures
- Edge source: The fundamental market phenomenon exploited
- Entry/Exit: Precise trading rules
- Regime: When the strategy is expected to perform
- Data requirements: What data feeds are needed
- Parameters: Searchable parameter space
- Complexity: LOW/MEDIUM/HIGH
- Research priority: P0/P1/P2
- Implementation status: ready (mapped to existing code) or TODO
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import polars as pl


@dataclass(frozen=True)
class StrategySpec:
    """Precise definition of a candidate strategy."""

    strategy_id: str
    name: str
    edge_source: str
    hypothesis: str
    signal_description: str
    entry_rules: list[str]
    exit_rules: list[str]
    regime_when_active: list[str]  # e.g. ["trending", "high_vol"]
    failure_modes: list[str]
    required_data: list[str]  # e.g. ["OHLCV", "funding_rates"]
    param_schema: dict[str, dict[str, Any]]
    default_params: dict[str, Any]
    complexity: str  # LOW / MEDIUM / HIGH
    priority: str  # P0 / P1 / P2
    implementation: str  # "READY", "TODO"
    code_class: str | None = None  # Class name in strategies/

    def to_dict(self) -> dict[str, Any]:
        return {
            "strategy_id": self.strategy_id,
            "name": self.name,
            "edge_source": self.edge_source,
            "hypothesis": self.hypothesis,
            "signal_description": self.signal_description,
            "entry_rules": self.entry_rules,
            "exit_rules": self.exit_rules,
            "regime_when_active": self.regime_when_active,
            "failure_modes": self.failure_modes,
            "required_data": self.required_data,
            "param_schema": self.param_schema,
            "default_params": self.default_params,
            "complexity": self.complexity,
            "priority": self.priority,
            "implementation": self.implementation,
            "code_class": self.code_class,
        }


# ── Strategy 1: Trend Pullback ──────────────────────────────────────────────
S1_TREND_PULLBACK = StrategySpec(
    strategy_id="trend_pullback",
    name="Trend Pullback",
    edge_source="Trend persistence after retracement — institutions accumulate during dips",
    hypothesis=(
        "In established trends (ADX > 25), prices tend to pullback to "
        "Fibonacci retracement levels (25-50%) before resuming. "
        "Combining MA slope confirmation with volume spike reduces false signals."
    ),
    signal_description=(
        "Price is in top 25% of daily range, MA(20) slope > 0, "
        "current price touches 38.2% or 50% Fibonacci retracement"
    ),
    entry_rules=[
        "Trend filter: ADX(14) > 25",
        "MA(20) slope > 0 (uptrend) or < 0 (downtrend)",
        "Price pulls back to 25-50% Fibonacci retracement level",
        "Volume spike > 1.5x 20-day average on pullback bar",
        "Confirmation: bullish engulfing or hammer candle at retracement",
    ],
    exit_rules=[
        "Target: 1.618x extension of pullback depth",
        "Trailing stop: 2x ATR(14) from high",
        "Exit: Trend reversal detected (ADX < 20 or MA slope flips)",
    ],
    regime_when_active=["trending"],
    failure_modes=["Choppy/volatility crush → false breakouts", "Strong reversal breaks trend"],
    required_data=["OHLCV"],
    param_schema={
        "ma_fast": {"type": "int", "min": 10, "max": 50},
        "ma_slow": {"type": "int", "min": 80, "max": 200},
        "adx_threshold": {"type": "float", "min": 15, "max": 35},
        "fib_retracement": {"type": "float", "min": 0.25, "max": 0.62},
        "volume_lookback": {"type": "int", "min": 10, "max": 50},
    },
    default_params={
        "ma_fast": 20,
        "ma_slow": 80,
        "adx_threshold": 25,
        "fib_retracement": 0.382,
        "volume_lookback": 20,
    },
    complexity="LOW",
    priority="P0",
    implementation="TODO",
    code_class=None,
)

# ── Strategy 2: Range Mean Reversion ────────────────────────────────────────
S2_RANGE_MEAN_REVERSION = StrategySpec(
    strategy_id="range_mean_reversion",
    name="Range Mean Reversion",
    edge_source="Mean reversion within bounded range; price gravitates to VWAP",
    hypothesis=(
        "In low-volatility, low-ADX environments (ADX < 20), prices exhibit "
        "mean-reverting behavior around VWAP. z-score of price deviation from "
        "VWAP provides statistical edge."
    ),
    signal_description="z-score of price from VWAP(20) > 2.0 or < -2.0, with low Bollinger bandwidth",
    entry_rules=[
        "Regime filter: ADX(14) < 20 (ranging)",
        "Bollinger BandWidth < 10th percentile (low vol compression)",
        "Price z-score from VWAP(20) > 2.0 → short or < -2.0 → long",
        "Volume confirmation: current volume > 0.8x 20-day average",
    ],
    exit_rules=[
        "z-score reverts to ±0.5 (mean reversion completed)",
        "VWAP slope changes sign (range boundary)",
        "Stop: 3x standard deviation from mean",
    ],
    regime_when_active=["ranging", "low_vol"],
    failure_modes=["Breakout from range (volatility expansion)", "Strong momentum override"],
    required_data=["OHLCV"],
    param_schema={
        "vwap_window": {"type": "int", "min": 10, "max": 50},
        "zscore_entry": {"type": "float", "min": 1.0, "max": 4.0},
        "zscore_exit": {"type": "float", "min": 0.1, "max": 1.0},
        "bb_lookback": {"type": "int", "min": 10, "max": 50},
    },
    default_params={
        "vwap_window": 20,
        "zscore_entry": 2.0,
        "zscore_exit": 0.5,
        "bb_lookback": 20,
    },
    complexity="LOW",
    priority="P0",
    implementation="TODO",  # Close to existing bbands/rsi but with VWAP+z-score
    code_class=None,
)

# ── Strategy 3: Volatility Expansion Breakout ───────────────────────────────
S3_VOLATILITY_BREAKOUT = StrategySpec(
    strategy_id="volatility_breakout",
    name="Volatility Expansion Breakout",
    edge_source="Compression → expansion; low vol periods precede high vol moves",
    hypothesis=(
        "Periods of extreme Bollinger Band compression (BandWidth at low percentile) "
        "precede volatility expansion. Breakouts from compressed state with volume "
        "confirmation have positive expected return."
    ),
    signal_description="Bollinger BandWidth at < 3rd percentile → breakout with 2x vol spike",
    entry_rules=[
        "Bollinger BandWidth < 3rd percentile over 60-day lookback",
        "Close breaks above upper band (long) or below lower band (short)",
        "ATR(14) spike > 1.5x 20-day median (volatility expansion)",
        "Volume > 1.2x 20-day average",
    ],
    exit_rules=[
        "Trailing stop: 3x ATR(14)",
        "Volatility contraction: BandWidth returns to > median",
        "Fixed: 10 bars max hold",
    ],
    regime_when_active=["low_vol", "mid_vol"],
    failure_modes=["False breakout (bull/bear trap)", "Immediate reversal"],
    required_data=["OHLCV"],
    param_schema={
        "bb_period": {"type": "int", "min": 10, "max": 30},
        "bb_std_dev": {"type": "float", "min": 1.5, "max": 3.0},
        "compression_percentile": {"type": "float", "min": 0.01, "max": 0.10},
        "atr_spike_multiplier": {"type": "float", "min": 1.2, "max": 2.5},
        "max_hold_bars": {"type": "int", "min": 5, "max": 30},
    },
    default_params={
        "bb_period": 20,
        "bb_std_dev": 2.0,
        "compression_percentile": 0.03,
        "atr_spike_multiplier": 1.5,
        "max_hold_bars": 10,
    },
    complexity="LOW",
    priority="P0",
    implementation="TODO",
    code_class=None,
)

# ── Strategy 4: Cross-Sectional Momentum ────────────────────────────────────
S4_CROSS_SECTIONAL_MOMENTUM = StrategySpec(
    strategy_id="cross_sectional_momentum",
    name="Cross-Sectional Momentum",
    edge_source="Relative strength — outperformers continue outperformance over 1-3 months",
    hypothesis=(
        "Assets with strong relative performance over 60-day rolling windows "
        "continue to outperform for 1-3 months. Cross-sectional ranking provides "
        "diversification vs. single-asset trend following."
    ),
    signal_description="Rank assets by 60-day returns; long top quintile, short bottom quintile",
    entry_rules=[
        "Rank all assets by 60-day total return",
        "Long top 30% (long-only) or long top 30% + short bottom 30% (long-short)",
        "Equal-weight positions within each bucket",
        "Rebalance daily when rank changes by > 1 bucket",
    ],
    exit_rules=[
        "60-day return turns negative (long side)",
        "Rank drops below threshold",
        "Monthly rebalancing (or faster if significant rank change)",
    ],
    regime_when_active=["mid_vol", "low_vol", "trending"],
    failure_modes=["Flight-to-quality (all assets dump together)", "Market regime shift"],
    required_data=["OHLCV (multi-asset)"],
    param_schema={
        "lookback_days": {"type": "int", "min": 20, "max": 120},
        "top_quantile": {"type": "float", "min": 0.1, "max": 0.5},
        "bottom_quantile": {"type": "float", "min": 0.05, "max": 0.3},
        "rebalance_freq_hours": {"type": "int", "min": 12, "max": 72},
    },
    default_params={
        "lookback_days": 60,
        "top_quantile": 0.3,
        "bottom_quantile": 0.3,
        "rebalance_freq_hours": 24,
    },
    complexity="MEDIUM",
    priority="P1",
    implementation="TODO",
    code_class=None,
)

# ── Strategy 5: Funding Rate Carry ──────────────────────────────────────────
S5_FUNDING_CARRY = StrategySpec(
    strategy_id="funding_carry",
    name="Funding Rate Carry",
    edge_source="Perpetual funding rates create drift; accumulate positive expected value",
    hypothesis=(
        "Perpetual swap funding rates represent a transfer between long and short "
        "positions. When funding is negative (shorts pay longs), buying spot and "
        "shorting perps captures the funding rate as drift. Over 3-7 day holding periods, "
        "this provides positive expected return."
    ),
    signal_description="Funding rate > threshold → short perp (long-only mode) or long perp when funding < 0",
    entry_rules=[
        "8h funding rate < -0.01% → enter long perp (collect negative funding)",
        "8h funding rate > 0.05% → enter short perp (pay high funding) or skip",
        "Position size: vol-targeted to 10% daily vol",
        "Directional hedge: spot position offset to reduce market beta",
    ],
    exit_rules=[
        "Funding rate crosses zero",
        "8h funding rate reverts to |0.01%| or tighter",
        "Max hold: 3 days",
        "Stop: 5% adverse move",
    ],
    regime_when_active=["low_vol", "ranging", "low_vol"],
    failure_modes=["Strong directional move (funding direction wrong)", "Funding spikes reversals"],
    required_data=["OHLCV", "funding_rates", "perp_prices"],
    param_schema={
        "funding_entry_threshold": {"type": "float", "min": -0.1, "max": -0.005},
        "funding_exit_threshold": {"type": "float", "min": 0.0, "max": 0.02},
        "max_hold_days": {"type": "int", "min": 1, "max": 7},
        "vol_target_daily": {"type": "float", "min": 0.02, "max": 0.2},
    },
    default_params={
        "funding_entry_threshold": -0.0001,
        "funding_exit_threshold": 0.00005,
        "max_hold_days": 3,
        "vol_target_daily": 0.10,
    },
    complexity="HIGH",
    priority="P1",
    implementation="TODO",
    code_class=None,
)

# ── Strategy 6: Volatility Targeting ─────────────────────────────────────────
S6_VOL_TARGET = StrategySpec(
    strategy_id="vol_target",
    name="Volatility Targeting",
    edge_source="Inverse vol weighting — reduce exposure when vol high, increase when low",
    hypothesis=(
        "Scaling position size inversely to realized volatility stabilizes returns "
        "across regimes. When vol is low (high vol below median), increase exposure; "
        "when vol spikes, reduce risk. Combined with trend filter."
    ),
    signal_description="20-day realized vol percentile × trend direction → position size = k / vol",
    entry_rules=[
        "Base signal: MA crossover (20/80)",
        "Compute 20-day realized volatility",
        "Position size = target_vol / realized_vol (inverse vol weighting)",
        "Target daily vol: 1.0% of capital",
    ],
    exit_rules=[
        "Vol drops back to median → return to full position",
        "Trend reverses → flip position",
        "Vol spikes > 90th percentile → reduce to 25% size",
    ],
    regime_when_active=["all"],
    failure_modes=["Sudden vol spike kills leveraged positions", "Vol targeting lags regime shifts"],
    required_data=["OHLCV"],
    param_schema={
        "target_daily_vol": {"type": "float", "min": 0.005, "max": 0.03},
        "vol_lookback": {"type": "int", "min": 10, "max": 60},
        "vol_cap_percentile": {"type": "float", "min": 0.7, "max": 0.99},
    },
    default_params={
        "target_daily_vol": 0.01,
        "vol_lookback": 20,
        "vol_cap_percentile": 0.95,
    },
    complexity="LOW",
    priority="P0",
    implementation="READY",  # Mapped to ma_vol_target
    code_class="MaVolTargetCrossover",
)

# ── Strategy 7: Regime Ensemble (Architecture B) ───────────────────────────
S7_REGIME_ENSEMBLE = StrategySpec(
    strategy_id="regime_ensemble",
    name="Regime Ensemble",
    edge_source="Probabilistic regime → continuous alpha weighting",
    hypothesis=(
        "Using probabilistic regime classification (P(trending), P(ranging), P(high_vol)), "
        "strategies can be allocated continuously rather than hard-switching. "
        "This reduces whipsaw in regime transitions and captures partial edges."
    ),
    signal_description="P(regime) × alpha_scores → weighted position; continuous weights vs threshold",
    entry_rules=[
        "Compute regime probabilities via HMM + GMM + rule-based ensemble",
        "For each alpha (trend/mmr/vol), compute confidence score",
        "Position = Σ P(regime_i) × alpha_i × risk_budget",
        "Confidence threshold: P > 0.5 for hard-switch variant",
    ],
    exit_rules=[
        "Regime probability shifts > 20% → rebalance allocation",
        "Combined confidence < 0.3 → reduce to 25% size",
        "Trailing stop: 2x ATR(14)",
    ],
    regime_when_active=["all"],
    failure_modes=["Regime misclassification (transition periods)", "Model uncertainty during regime shifts"],
    required_data=["OHLCV"],
    param_schema={
        "regime_method": {"type": "string", "enum": ["hybrid", "rule_based", "hmm", "gmm"]},
        "min_confidence": {"type": "float", "min": 0.3, "max": 0.8},
        "regime_smoothing": {"type": "int", "min": 1, "max": 10},
        "position_mode": {"type": "string", "enum": ["threshold", "continuous"]},
    },
    default_params={
        "regime_method": "rule_based",
        "min_confidence": 0.4,
        "regime_smoothing": 2,
        "base_position_pct": 0.1,
    },
    complexity="HIGH",
    priority="P1",
    implementation="READY",  # Mapped to regime_switching
    code_class="RegimeSwitchingStrategy",
)

# ── Strategy 8: Statistical Arbitrage ───────────────────────────────────────
S8_STAT_ARBITRAGE = StrategySpec(
    strategy_id="stat_arbitrage",
    name="Statistical Arbitrage (Cointegration)",
    edge_source="Cointegration — mean-reverting spread between related assets",
    hypothesis=(
        "BTC/ETH, ETH/BNB exhibit cointegration during certain periods. "
        "OLS hedge ratio + z-score of spread provides mean-reversion signal. "
        "Pairs that break cointegration should be excluded."
    ),
    signal_description="z-score of cointegrated spread > 2 (short spread) or < -2 (long spread)",
    entry_rules=[
        "Test cointegration (Engle-Granger) on 252-day rolling window",
        "Compute OLS hedge ratio: y = α + βx + ε",
        "z-score > 2 → short spread (short y, long βx)",
        "z-score < -2 → long spread",
        "Confirm with ADF test (p < 0.05)",
    ],
    exit_rules=[
        "z-score reverts to ±0.5",
        "ADF test fails (p > 0.2) → close position",
        "Stop: 4x standard deviation",
    ],
    regime_when_active=["ranging", "low_vol", "mid_vol"],
    failure_modes=["Regime shift breaks cointegration (2020 March, 2022 Luna)"],
    required_data=["OHLCV (multi-asset)"],
    param_schema={
        "zscore_entry": {"type": "float", "min": 1.5, "max": 3.0},
        "zscore_exit": {"type": "float", "min": 0.1, "max": 1.0},
        "cointegration_window": {"type": "int", "min": 60, "max": 500},
        "adf_pvalue_threshold": {"type": "float", "min": 0.01, "max": 0.1},
    },
    default_params={
        "zscore_entry": 2.0,
        "zscore_exit": 0.5,
        "cointegration_window": 252,
        "adf_pvalue_threshold": 0.05,
    },
    complexity="HIGH",
    priority="P1",
    implementation="TODO",
    code_class=None,
)


# ── Catalog ─────────────────────────────────────────────────────────────────
STRATEGY_CATALOG: dict[str, StrategySpec] = {
    s.strategy_id: s
    for s in [
        S1_TREND_PULLBACK,
        S2_RANGE_MEAN_REVERSION,
        S3_VOLATILITY_BREAKOUT,
        S4_CROSS_SECTIONAL_MOMENTUM,
        S5_FUNDING_CARRY,
        S6_VOL_TARGET,
        S7_REGIME_ENSEMBLE,
        S8_STAT_ARBITRAGE,
    ]
}


def get_strategy_spec(strategy_id: str) -> StrategySpec:
    """Get strategy specification by ID."""
    if strategy_id not in STRATEGY_CATALOG:
        raise KeyError(
            f"Unknown strategy '{strategy_id}'. "
            f"Available: {list(STRATEGY_CATALOG.keys())}"
        )
    return STRATEGY_CATALOG[strategy_id]


def get_available_strategies(implementation: str | None = None) -> list[str]:
    """List strategy IDs, optionally filtered by implementation status."""
    if implementation is None:
        return list(STRATEGY_CATALOG.keys())
    return [
        sid for sid, spec in STRATEGY_CATALOG.items()
        if spec.implementation == implementation
    ]


def get_mapped_strategy(strategy_id: str) -> str:
    """Return the code-level strategy name if this catalog strategy maps to existing code."""
    spec = STRATEGY_CATALOG[strategy_id]
    if spec.code_class is not None:
        # Map catalog ID to registered strategy name
        mapping = {
            "vol_target": "ma_vol_target",
            "regime_ensemble": "regime_switching",
        }
        return mapping.get(strategy_id, spec.strategy_id)
    return strategy_id


__all__ = [
    "StrategySpec",
    "STRATEGY_CATALOG",
    "get_strategy_spec",
    "get_available_strategies",
    "get_mapped_strategy",
    "S1_TREND_PULLBACK",
    "S2_RANGE_MEAN_REVERSION",
    "S3_VOLATILITY_BREAKOUT",
    "S4_CROSS_SECTIONAL_MOMENTUM",
    "S5_FUNDING_CARRY",
    "S6_VOL_TARGET",
    "S7_REGIME_ENSEMBLE",
    "S8_STAT_ARBITRAGE",
]
