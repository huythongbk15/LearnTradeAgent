"""
Adaptive Strategy with Online Regime Detection.

Wraps base strategies and dynamically adjusts parameters based on detected market regime.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
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


@dataclass(frozen=True, slots=True)
class AdaptiveSignal:
    """Signal from adaptive strategy with regime context."""

    symbol: str
    timestamp: datetime
    signal: int  # 1=buy, -1=sell, 0=hold
    regime: MarketRegime
    regime_confidence: float
    params_used: dict[str, Any]
    base_signal: int
    metadata: dict[str, Any] = field(default_factory=dict)


class AdaptiveStrategy(Strategy):
    """
    Strategy that adapts parameters based on detected market regime.

    Uses RegimeDetector to classify current market state and selects
    optimal parameters for the base strategy.
    """

    STRATEGY_CLASSES = {
        "ma_crossover": MaCrossover,
        "rsi": RsiStrategy,
        "bbands": BBandsStrategy,
    }

    name = "adaptive"

    def __init__(
        self,
        base_strategy: str = "ma_crossover",
        regime_detector: RegimeDetector | None = None,
        regime_lookback: int = 100,
        min_regime_confidence: float = 0.5,
        regime_update_bars: int = 20,  # Re-detect regime every N bars
        params: dict[str, Any] | None = None,
    ):
        """
        Initialize adaptive strategy.

        Args:
            base_strategy: Base strategy name ("ma_crossover", "rsi", "bbands")
            regime_detector: Optional custom regime detector
            regime_lookback: Bars to use for regime detection
            min_regime_confidence: Minimum confidence to trust regime
            regime_update_bars: Re-compute regime every N bars
            params: Base parameters (used as fallback)
        """
        super().__init__(params or {})
        self.base_strategy_name = base_strategy
        self.regime_detector = regime_detector or create_regime_detector(
            lookback_bars=regime_lookback
        )
        self.min_regime_confidence = min_regime_confidence
        self.regime_update_bars = regime_update_bars

        # State
        self._current_regime: MarketRegime = MarketRegime.UNKNOWN
        self._current_confidence: float = 0.0
        self._current_params: dict[str, Any] = {}
        self._base_strategy: Strategy | None = None
        self._bars_since_update: int = 0
        self._last_signal: int = 0

        # Initialize with default params
        self._update_strategy(
            self.regime_detector.get_recommended_params(
                MarketRegime.UNKNOWN, base_strategy
            )
        )

    def _update_strategy(self, params: dict[str, Any]) -> None:
        """Create/recreate base strategy with new params."""
        strategy_cls = self.STRATEGY_CLASSES[self.base_strategy_name]
        self._base_strategy = strategy_cls(params=params)
        self._current_params = params

    def _maybe_update_regime(self, df: pl.DataFrame) -> RegimeSignal | None:
        """Update regime if enough bars passed or first run."""
        self._bars_since_update += 1

        if (
            self._bars_since_update >= self.regime_update_bars
            or self._current_regime == MarketRegime.UNKNOWN
        ):
            self._bars_since_update = 0
            signal = self.regime_detector.detect(df, self.base_strategy_name)

            # Only update if confident enough
            if signal.confidence >= self.min_regime_confidence:
                old_regime = self._current_regime
                self._current_regime = signal.regime
                self._current_confidence = signal.confidence

                if signal.regime != old_regime:
                    self._update_strategy(signal.recommended_params)

                return signal

        return None

    def compute_indicators(self, df: pl.DataFrame) -> pl.DataFrame:
        """Compute indicators using current base strategy."""
        if self._base_strategy is None:
            return df

        # Update regime FIRST (before computing indicators)
        if len(df) >= self.regime_detector.lookback_bars:
            self._maybe_update_regime(df)

            # Add regime info to DataFrame
            regime_col = pl.Series("regime", [self._current_regime.value] * len(df))
            confidence_col = pl.Series(
                "regime_confidence", [self._current_confidence] * len(df)
            )
            df = df.with_columns([regime_col, confidence_col])

        # Delegate to base strategy (which uses updated params)
        return self._base_strategy.compute_indicators(df)

    def generate_signals(self, df: pl.DataFrame) -> pl.Series:
        """Generate signals using current base strategy with regime adaptation."""
        if self._base_strategy is None:
            return pl.Series("signal", [0] * len(df))

        # First compute indicators (this updates regime if needed)
        df_with_indicators = self.compute_indicators(df)

        # Then generate signals from base strategy
        return self._base_strategy.generate_signals(df_with_indicators)

    def get_current_regime(self) -> tuple[MarketRegime, float]:
        """Get current regime and confidence."""
        return self._current_regime, self._current_confidence

    def get_current_params(self) -> dict[str, Any]:
        """Get currently active parameters."""
        return self._current_params.copy()


class OnlineLearningEngine:
    """
    High-level engine for online learning / adaptive trading.

    Combines regime detection, parameter adaptation, and performance tracking.
    """

    def __init__(
        self,
        symbol: str,
        timeframe: str,
        base_strategy: str = "ma_crossover",
        regime_lookback: int = 100,
        update_interval_bars: int = 20,
        performance_window: int = 100,
    ):
        self.symbol = symbol
        self.timeframe = timeframe
        self.base_strategy = base_strategy
        self.performance_window = performance_window

        # Components
        self.regime_detector = create_regime_detector(lookback_bars=regime_lookback)
        self.adaptive_strategy = AdaptiveStrategy(
            base_strategy=base_strategy,
            regime_detector=self.regime_detector,
            regime_lookback=regime_lookback,
            regime_update_bars=update_interval_bars,
        )

        # Performance tracking
        self._regime_history: list[tuple[datetime, MarketRegime, float, dict]] = []
        self._performance_history: list[dict] = []

    def update(self, df: pl.DataFrame) -> tuple[pl.Series, RegimeSignal | None]:
        """
        Update engine with new data.

        Returns (signals_series, regime_signal_if_updated)
        """
        signals = self.adaptive_strategy.generate_signals(df)

        # Track regime changes
        regime, confidence = self.adaptive_strategy.get_current_regime()
        params = self.adaptive_strategy.get_current_params()

        self._regime_history.append(
            (
                datetime.now(UTC),
                regime,
                confidence,
                params.copy(),
            )
        )

        # Keep history bounded
        if len(self._regime_history) > self.performance_window:
            self._regime_history = self._regime_history[-self.performance_window :]

        regime_signal = None
        if len(self._regime_history) >= 2:
            last_regime = self._regime_history[-1][1]
            prev_regime = self._regime_history[-2][1]
            if last_regime != prev_regime:
                regime_signal = RegimeSignal(
                    regime=last_regime,
                    confidence=confidence,
                    features=None,  # type: ignore
                    recommended_params=params,
                    timestamp=datetime.now(UTC),
                )

        return signals, regime_signal

    def get_regime_history(self) -> list[dict]:
        """Get regime change history."""
        return [
            {
                "timestamp": ts.isoformat(),
                "regime": regime.value,
                "confidence": conf,
                "params": params,
            }
            for ts, regime, conf, params in self._regime_history
        ]

    def get_performance_by_regime(self, df: pl.DataFrame) -> dict:
        """Analyze performance per regime (requires historical signals)."""
        # This would need signal history with returns
        # Placeholder for future implementation
        return {}


def create_adaptive_ma_crossover(
    regime_lookback: int = 100,
    regime_update_bars: int = 20,
    min_confidence: float = 0.5,
) -> AdaptiveStrategy:
    """Factory for adaptive MA Crossover."""
    return AdaptiveStrategy(
        base_strategy="ma_crossover",
        regime_lookback=regime_lookback,
        min_regime_confidence=min_confidence,
        regime_update_bars=regime_update_bars,
    )
