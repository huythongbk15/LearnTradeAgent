"""
Regime-Specific Strategy Switcher.

Switches between different strategies based on detected market regime:
- TRENDING: Trend-following (MA Crossover)
- SIDEWAYS: Mean-reversion (RSI, Bollinger Bands)
- VOLATILE: Reduced exposure / volatility-adjusted
- UNKNOWN: Conservative / no trade
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

import polars as pl

from trading_agent.online_learning.regime_detector import (
    RegimeDetector,
    RegimeSignal,
    MarketRegime,
    create_regime_detector,
)
from trading_agent.strategies.base import Strategy
from trading_agent.strategies.ma_crossover import MaCrossover
from trading_agent.strategies.rsi import RsiStrategy
from trading_agent.strategies.bbands import BBandsStrategy


# Regime → Strategy mapping
REGIME_STRATEGY_MAP = {
    MarketRegime.TRENDING_UP: "ma_crossover",
    MarketRegime.TRENDING_DOWN: "ma_crossover",
    MarketRegime.SIDEWAYS: "rsi",
    MarketRegime.VOLATILE: "bbands",  # BBands handle volatility well
    MarketRegime.UNKNOWN: "hold",  # No trade
}

# Default parameters per strategy per regime
REGIME_STRATEGY_PARAMS = {
    MarketRegime.TRENDING_UP: {
        "ma_crossover": {"fast_period": 10, "slow_period": 30},  # Fast for trends
    },
    MarketRegime.TRENDING_DOWN: {
        "ma_crossover": {"fast_period": 10, "slow_period": 30},
    },
    MarketRegime.SIDEWAYS: {
        "rsi": {
            "period": 14,
            "oversold": 35,
            "overbought": 65,
        },  # Tighter bands for ranging
        "bbands": {"period": 20, "std_dev": 1.5},  # Tighter bands
    },
    MarketRegime.VOLATILE: {
        "bbands": {"period": 20, "std_dev": 2.5},  # Wider bands for volatility
        "ma_crossover": {
            "fast_period": 50,
            "slow_period": 200,
        },  # Slower to avoid whipsaws
    },
    MarketRegime.UNKNOWN: {},
}


@dataclass(frozen=True, slots=True)
class RegimeSwitchSignal:
    """Signal from regime-switching strategy."""

    symbol: str
    timestamp: datetime
    signal: int  # 1=buy, -1=sell, 0=hold
    regime: MarketRegime
    regime_confidence: float
    active_strategy: str
    params_used: dict[str, Any]
    regime_params: dict[str, Any]


class RegimeSwitchStrategy(Strategy):
    """
    Strategy that SWITCHES base strategy based on market regime.

    Unlike AdaptiveStrategy (which adapts params of ONE strategy),
    this switches between ENTIRELY DIFFERENT strategies:
    - TRENDING → MA Crossover (trend following)
    - SIDEWAYS → RSI / Bollinger Bands (mean reversion)
    - VOLATILE → Bollinger Bands (volatility-adaptive)
    - UNKNOWN → HOLD (conservative)
    """

    STRATEGY_CLASSES = {
        "ma_crossover": MaCrossover,
        "rsi": RsiStrategy,
        "bbands": BBandsStrategy,
        "hold": None,  # No trading
    }

    name = "regime_switch"

    def __init__(
        self,
        regime_detector: RegimeDetector | None = None,
        regime_lookback: int = 100,
        min_regime_confidence: float = 0.6,
        regime_update_bars: int = 20,
        custom_strategy_map: dict[MarketRegime, str] | None = None,
        custom_params: dict[MarketRegime, dict[str, dict[str, Any]]] | None = None,
        allow_trade_unknown: bool = False,
    ):
        """
        Initialize regime-switching strategy.

        Args:
            regime_detector: Optional custom regime detector
            regime_lookback: Bars for regime detection
            min_regime_confidence: Minimum confidence to trust regime
            regime_update_bars: Re-compute regime every N bars
            custom_strategy_map: Override regime→strategy mapping
            custom_params: Override regime→strategy params
            allow_trade_unknown: If True, use fallback strategy in UNKNOWN
        """
        super().__init__({})
        self.regime_detector = regime_detector or create_regime_detector(
            lookback_bars=regime_lookback
        )
        self.min_regime_confidence = min_regime_confidence
        self.regime_update_bars = regime_update_bars
        self.strategy_map = custom_strategy_map or REGIME_STRATEGY_MAP
        self.custom_params = custom_params or REGIME_STRATEGY_PARAMS
        self.allow_trade_unknown = allow_trade_unknown

        # State
        self._current_regime: MarketRegime = MarketRegime.UNKNOWN
        self._current_confidence: float = 0.0
        self._current_strategy: Strategy | None = None
        self._current_strategy_name: str = "hold"
        self._current_params: dict[str, Any] = {}
        self._bars_since_update: int = 0

        # Initialize with UNKNOWN regime (hold)
        self._switch_strategy(MarketRegime.UNKNOWN, {})

    def _get_params_for_regime(
        self, regime: MarketRegime, strategy_name: str
    ) -> dict[str, Any]:
        """Get params for regime/strategy combination."""
        regime_params = self.custom_params.get(regime, {})
        return regime_params.get(strategy_name, {})

    def _switch_strategy(
        self, regime: MarketRegime, signal: RegimeSignal | None
    ) -> None:
        """Switch to strategy appropriate for regime."""
        strategy_name = self.strategy_map.get(regime, "hold")

        # If UNKNOWN and not allowed to trade, use hold
        if regime == MarketRegime.UNKNOWN and not self.allow_trade_unknown:
            strategy_name = "hold"

        params = self._get_params_for_regime(regime, strategy_name)

        if strategy_name == "hold" or strategy_name not in self.STRATEGY_CLASSES:
            self._current_strategy = None
            self._current_strategy_name = "hold"
            self._current_params = {}
            return

        strategy_cls = self.STRATEGY_CLASSES[strategy_name]
        self._current_strategy = strategy_cls(params=params)
        self._current_strategy_name = strategy_name
        self._current_params = params

    def _maybe_update_regime(self, df: pl.DataFrame) -> RegimeSignal | None:
        """Update regime and switch strategy if changed."""
        self._bars_since_update += 1

        if (
            self._bars_since_update >= self.regime_update_bars
            or self._current_regime == MarketRegime.UNKNOWN
        ):
            self._bars_since_update = 0
            signal = self.regime_detector.detect(df)

            # Only update if confident enough
            if signal.confidence >= self.min_regime_confidence:
                old_regime = self._current_regime
                self._current_regime = signal.regime
                self._current_confidence = signal.confidence

                if signal.regime != old_regime:
                    self._switch_strategy(signal.regime, signal)

                return signal

        return None

    def compute_indicators(self, df: pl.DataFrame) -> pl.DataFrame:
        """Compute indicators using current active strategy."""
        # Update regime FIRST
        if len(df) >= self.regime_detector.lookback_bars:
            self._maybe_update_regime(df)

            # Add regime info to DataFrame
            regime_col = pl.Series("regime", [self._current_regime.value] * len(df))
            confidence_col = pl.Series(
                "regime_confidence", [self._current_confidence] * len(df)
            )
            strategy_col = pl.Series(
                "active_strategy", [self._current_strategy_name] * len(df)
            )
            df = df.with_columns([regime_col, confidence_col, strategy_col])

        # Delegate to active strategy
        if self._current_strategy is not None:
            return self._current_strategy.compute_indicators(df)

        return df

    def generate_signals(self, df: pl.DataFrame) -> pl.Series:
        """Generate signals using active strategy for current regime."""
        # First compute indicators (this updates regime if needed)
        df_with_indicators = self.compute_indicators(df)

        # If no active strategy (hold), return zeros
        if self._current_strategy is None:
            return pl.Series("signal", [0] * len(df_with_indicators))

        # Generate signals from active strategy
        return self._current_strategy.generate_signals(df_with_indicators)

    def get_current_regime(self) -> tuple[MarketRegime, float]:
        """Get current regime and confidence."""
        return self._current_regime, self._current_confidence

    def get_active_strategy(self) -> str:
        """Get currently active strategy name."""
        return self._current_strategy_name

    def get_current_params(self) -> dict[str, Any]:
        """Get currently active parameters."""
        return self._current_params.copy()


class MultiRegimeStrategy(Strategy):
    """
    Advanced: Runs MULTIPLE strategies in parallel, weights by regime probability.

    Instead of hard switching, blends signals from multiple strategies
    weighted by regime detection confidence.
    """

    STRATEGY_CLASSES = {
        "ma_crossover": MaCrossover,
        "rsi": RsiStrategy,
        "bbands": BBandsStrategy,
    }

    name = "multi_regime"

    def __init__(
        self,
        regime_detector: RegimeDetector | None = None,
        regime_lookback: int = 100,
        regime_update_bars: int = 20,
        params_by_strategy: dict[str, dict[str, Any]] | None = None,
    ):
        super().__init__({})
        self.regime_detector = regime_detector or create_regime_detector(
            lookback_bars=regime_lookback
        )
        self.regime_update_bars = regime_update_bars
        self._bars_since_update: int = 0
        self._last_regime_probs: dict[MarketRegime, float] = {}

        # Initialize strategies with their default params
        self._strategies: dict[str, Strategy] = {}
        default_params = params_by_strategy or {
            "ma_crossover": {"fast_period": 20, "slow_period": 60},
            "rsi": {"period": 14, "oversold": 35, "overbought": 65},
            "bbands": {"period": 20, "std_dev": 2.0},
        }
        for name, cls in self.STRATEGY_CLASSES.items():
            self._strategies[name] = cls(params=default_params.get(name, {}))

    def _get_regime_weights(
        self, regime_probs: dict[MarketRegime, float]
    ) -> dict[str, float]:
        """Convert regime probabilities to strategy weights."""
        # Map regimes to strategies
        regime_to_strategy = {
            MarketRegime.TRENDING_UP: "ma_crossover",
            MarketRegime.TRENDING_DOWN: "ma_crossover",
            MarketRegime.SIDEWAYS: "rsi",
            MarketRegime.VOLATILE: "bbands",
            MarketRegime.UNKNOWN: "hold",
        }

        weights = {}
        for regime, prob in regime_probs.items():
            strategy = regime_to_strategy.get(regime)
            if strategy and strategy != "hold":
                weights[strategy] = weights.get(strategy, 0.0) + prob

        # Normalize
        total = sum(weights.values())
        if total > 0:
            weights = {k: v / total for k, v in weights.items()}

        return weights

    def compute_indicators(self, df: pl.DataFrame) -> pl.DataFrame:
        """Compute indicators for all strategies."""
        # Update regime
        self._bars_since_update += 1
        if (
            self._bars_since_update >= self.regime_update_bars
            or not self._last_regime_probs
        ):
            self._bars_since_update = 0
            # Get full regime probabilities
            self._last_regime_probs = self.regime_detector.get_regime_probabilities(df)

        # Add regime info
        regime_cols = []
        for regime in MarketRegime:
            prob = self._last_regime_probs.get(regime, 0.0)
            regime_cols.append(
                pl.Series(f"regime_{regime.value}_prob", [prob] * len(df))
            )

        df = df.with_columns(regime_cols)

        # Compute indicators for all strategies
        for strategy in self._strategies.values():
            df = strategy.compute_indicators(df)

        return df

    def generate_signals(self, df: pl.DataFrame) -> pl.Series:
        """Generate weighted signals from all strategies."""
        df_with_indicators = self.compute_indicators(df)

        # Get strategy weights from regime probabilities
        weights = self._get_regime_weights(self._last_regime_probs)

        if not weights:
            return pl.Series("signal", [0] * len(df_with_indicators))

        # Get signals from each strategy and weight them
        weighted_signals = pl.Series("signal", [0.0] * len(df_with_indicators))

        for strategy_name, weight in weights.items():
            if weight > 0 and strategy_name in self._strategies:
                signals = self._strategies[strategy_name].generate_signals(
                    df_with_indicators
                )
                weighted_signals = weighted_signals + (
                    signals.cast(pl.Float64) * weight
                )

        # Convert to discrete signals: 1 if > 0.3, -1 if < -0.3, else 0
        return (
            pl.when(weighted_signals > 0.3)
            .then(1)
            .when(weighted_signals < -0.3)
            .then(-1)
            .otherwise(0)
            .cast(pl.Int64)
            .alias("signal")
        )


def create_regime_switch_strategy(
    regime_lookback: int = 100,
    regime_update_bars: int = 20,
    min_confidence: float = 0.6,
    custom_map: dict[MarketRegime, str] | None = None,
) -> RegimeSwitchStrategy:
    """Factory for RegimeSwitchStrategy."""
    return RegimeSwitchStrategy(
        regime_lookback=regime_lookback,
        regime_update_bars=regime_update_bars,
        min_regime_confidence=min_confidence,
        custom_strategy_map=custom_map,
    )


def create_multi_regime_strategy(
    regime_lookback: int = 100,
    regime_update_bars: int = 20,
) -> MultiRegimeStrategy:
    """Factory for MultiRegimeStrategy."""
    return MultiRegimeStrategy(
        regime_lookback=regime_lookback,
        regime_update_bars=regime_update_bars,
    )
