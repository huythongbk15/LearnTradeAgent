#!/usr/bin/env python3
"""
Regime-Switching Ensemble Strategy

Dynamically selects and weights sub-strategies based on detected market regime.

Architecture:
- Uses existing regime detection (HMM/GMM/Rule-based/Hybrid)
- Maps regimes to optimal sub-strategies:
    - BULL_TREND / BEAR_TREND → Trend-following (MA Crossover, Enhanced MA)
    - SIDEWAYS → Mean-reversion (RSI, Bollinger Bands)
    - HIGH_VOLATILITY → Volatility strategies, reduced size
    - LOW_VOLATILITY → Breakout strategies, increased size
    - CRISIS / RECOVERY → Defensive / aggressive respectively
- Combines signals with regime-confidence-weighted ensemble

FIXES applied:
- Pre-fit detector once (O(n) instead of O(n²))
- Use rolling predict instead of re-fitting every bar
- Regime labels stable across bars
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import numpy as np
import polars as pl

from trading_agent.ml.regime_detection import (
    MarketRegime,
    RegimeMethod,
    HybridRegimeDetector,
    RuleBasedStrategy,
    HMMStrategy,
    GMMStrategy,
)
from trading_agent.strategies.base import Strategy, register_strategy
from trading_agent.strategies.ma_crossover import MaCrossover
from trading_agent.strategies.rsi import RsiStrategy
from trading_agent.strategies.bbands import BBandsStrategy
from trading_agent.strategies.enhanced_ma import EnhancedMaCrossover

logger = logging.getLogger(__name__)


@dataclass
class RegimeStrategyConfig:
    """Configuration for a sub-strategy within a regime."""

    strategy_name: str
    weight: float
    params: dict[str, Any]


# Default regime → strategy mapping
DEFAULT_REGIME_STRATEGIES: dict[MarketRegime, list[RegimeStrategyConfig]] = {
    MarketRegime.BULL_TREND: [
        RegimeStrategyConfig(
            "enhanced_ma", 0.6, {"fast": 10, "slow": 30, "adx_threshold": 20}
        ),
        RegimeStrategyConfig(
            "ma_crossover", 0.4, {"fast_period": 8, "slow_period": 21}
        ),
    ],
    MarketRegime.BEAR_TREND: [
        RegimeStrategyConfig(
            "enhanced_ma", 0.6, {"fast": 8, "slow": 25, "adx_threshold": 20}
        ),
        RegimeStrategyConfig(
            "ma_crossover", 0.4, {"fast_period": 5, "slow_period": 15}
        ),
    ],
    MarketRegime.SIDEWAYS: [
        RegimeStrategyConfig(
            "rsi", 0.5, {"period": 14, "oversold": 30, "overbought": 70}
        ),
        RegimeStrategyConfig("bbands", 0.5, {"period": 20, "std_dev": 2.0}),
    ],
    MarketRegime.HIGH_VOLATILITY: [
        RegimeStrategyConfig(
            "rsi", 0.4, {"period": 14, "oversold": 25, "overbought": 75}
        ),
        RegimeStrategyConfig("bbands", 0.4, {"period": 20, "std_dev": 2.5}),
        RegimeStrategyConfig(
            "enhanced_ma", 0.2, {"fast": 5, "slow": 15, "adx_threshold": 30}
        ),
    ],
    MarketRegime.LOW_VOLATILITY: [
        RegimeStrategyConfig("bbands", 0.5, {"period": 20, "std_dev": 1.5}),
        RegimeStrategyConfig(
            "ma_crossover", 0.3, {"fast_period": 3, "slow_period": 10}
        ),
        RegimeStrategyConfig(
            "enhanced_ma", 0.2, {"fast": 5, "slow": 20, "adx_threshold": 15}
        ),
    ],
    MarketRegime.CRISIS: [
        RegimeStrategyConfig(
            "rsi", 0.6, {"period": 14, "oversold": 20, "overbought": 80}
        ),
        RegimeStrategyConfig(
            "enhanced_ma", 0.4, {"fast": 3, "slow": 10, "adx_threshold": 40}
        ),
    ],
    MarketRegime.RECOVERY: [
        RegimeStrategyConfig(
            "enhanced_ma", 0.5, {"fast": 10, "slow": 30, "adx_threshold": 20}
        ),
        RegimeStrategyConfig(
            "ma_crossover", 0.3, {"fast_period": 8, "slow_period": 21}
        ),
        RegimeStrategyConfig("bbands", 0.2, {"period": 20, "std_dev": 2.0}),
    ],
    MarketRegime.UNKNOWN: [
        RegimeStrategyConfig("enhanced_ma", 0.5, {}),
        RegimeStrategyConfig("rsi", 0.3, {}),
        RegimeStrategyConfig("bbands", 0.2, {}),
    ],
}

STRATEGY_MAP = {
    "ma_crossover": MaCrossover,
    "rsi": RsiStrategy,
    "bbands": BBandsStrategy,
    "enhanced_ma": EnhancedMaCrossover,
}


@register_strategy("regime_switching")
class RegimeSwitchingStrategy(Strategy):
    """
    Regime-Switching Ensemble Strategy.

    Detects market regime and dynamically combines sub-strategies
    optimized for that regime. Uses confidence-weighted ensemble.

    PERFORMANCE FIX: Detector is fit ONCE on the first `lookback` bars,
    then only `predict` is called for subsequent bars (O(n) instead of O(n²)).
    """

    def __init__(self, params: dict[str, Any] | None = None):
        params = params or {}
        super().__init__(params)

        # Regime detection config
        self.regime_method = params.get(
            "regime_method", "hybrid"
        )  # hmm, gmm, rule_based, hybrid
        self.lookback = int(params.get("lookback", 200))
        self.min_confidence = float(params.get("min_confidence", 0.55))
        self.regime_smoothing = int(
            params.get("regime_smoothing", 3)
        )  # bars to confirm regime change
        self.refit_every = int(
            params.get("refit_every", 0)
        )  # 0 = never refit, else refit every N bars

        # Custom regime-strategy mapping (overrides defaults)
        self.custom_mapping: dict[str, list[dict]] | None = params.get(
            "regime_strategies"
        )

        # Position sizing
        self.position_sizing = params.get("position_sizing", "fixed")
        self.base_position_pct = float(params.get("base_position_pct", 0.1))

        # Internal state
        self._detector = None
        self._sub_strategies: dict[str, Strategy] = {}
        self._current_regime = MarketRegime.UNKNOWN
        self._regime_confidence = 0.0
        self._regime_history: list[MarketRegime] = []
        self._regime_stable_count = 0
        self._detector_fitted = False
        self._bars_since_refit = 0

        # Pre-instantiate all possible sub-strategies
        self._init_sub_strategies()

    def _init_sub_strategies(self) -> None:
        """Instantiate all sub-strategies with default params."""
        for name, cls in STRATEGY_MAP.items():
            self._sub_strategies[name] = cls({})

    def _get_detector(self):
        """Lazy-initialize regime detector."""
        if self._detector is not None:
            return self._detector

        method = RegimeMethod(self.regime_method)
        if method == RegimeMethod.HYBRID:
            self._detector = HybridRegimeDetector()
        elif method == RegimeMethod.HMM:
            self._detector = HMMStrategy(lookback=self.lookback)
        elif method == RegimeMethod.GMM:
            self._detector = GMMStrategy()
        elif method == RegimeMethod.RULE_BASED:
            self._detector = RuleBasedStrategy()
        else:
            self._detector = HybridRegimeDetector()

        return self._detector

    def _get_regime_strategies(
        self, regime: MarketRegime
    ) -> list[RegimeStrategyConfig]:
        """Get strategy configs for a regime."""
        if self.custom_mapping and regime.value in self.custom_mapping:
            configs = []
            for cfg in self.custom_mapping[regime.value]:
                configs.append(
                    RegimeStrategyConfig(
                        strategy_name=cfg["strategy_name"],
                        weight=cfg.get("weight", 1.0),
                        params=cfg.get("params", {}),
                    )
                )
            return configs
        return DEFAULT_REGIME_STRATEGIES.get(
            regime, DEFAULT_REGIME_STRATEGIES[MarketRegime.UNKNOWN]
        )

    def _fit_detector(self, df: pl.DataFrame) -> None:
        """Fit detector ONCE on initial data (first lookback bars)."""
        if self._detector_fitted:
            return

        prices = df["close"][: self.lookback]
        volumes = df["volume"][: self.lookback] if "volume" in df.columns else None

        if len(prices) < 50:
            logger.warning(f"Not enough data to fit detector: {len(prices)} bars")
            return

        try:
            detector = self._get_detector()
            prices_pd = prices.to_pandas()
            volumes_pd = volumes.to_pandas() if volumes is not None else None

            if isinstance(detector, HybridRegimeDetector):
                detector.initialize(prices_pd, volumes_pd)
            elif isinstance(detector, HMMStrategy):
                detector.fit(prices_pd, volumes_pd)
            elif isinstance(detector, GMMStrategy):
                returns = np.log(prices_pd / prices_pd.shift(1)).dropna()
                detector.fit(returns)
            # RuleBasedStrategy doesn't need fitting

            self._detector_fitted = True
            self._bars_since_refit = 0
            logger.info(
                f"Regime detector ({self.regime_method}) fitted on {len(prices)} bars"
            )

        except Exception as e:
            logger.warning(f"Failed to fit regime detector: {e}")

    def _predict_regime(
        self, df: pl.DataFrame, bar_idx: int
    ) -> tuple[MarketRegime, float]:
        """Predict regime at bar_idx using already-fitted detector (fast, no re-fit)."""
        if not self._detector_fitted:
            self._fit_detector(df)
            if not self._detector_fitted:
                return MarketRegime.UNKNOWN, 0.0

        # Use expanding window up to bar_idx for prediction
        hist_df = df.slice(0, bar_idx + 1)
        if len(hist_df) < 50:
            return MarketRegime.UNKNOWN, 0.0

        prices = hist_df["close"]
        volumes = hist_df["volume"] if "volume" in hist_df.columns else None

        try:
            detector = self._get_detector()
            prices_pd = prices.to_pandas()
            volumes_pd = volumes.to_pandas() if volumes is not None else None

            if isinstance(detector, HybridRegimeDetector):
                state = detector.detect(prices_pd, volumes_pd)
            elif isinstance(detector, HMMStrategy):
                state = detector.predict(prices_pd, volumes_pd)
            elif isinstance(detector, GMMStrategy):
                returns = np.log(prices_pd / prices_pd.shift(1)).dropna()
                state = detector.predict(returns)
            elif isinstance(detector, RuleBasedStrategy):
                state = detector.detect(prices_pd)
            else:
                return MarketRegime.UNKNOWN, 0.0

            return state.regime, state.confidence

        except Exception as e:
            logger.debug(f"Regime prediction failed at bar {bar_idx}: {e}")
            return MarketRegime.UNKNOWN, 0.0

    def _maybe_refit(self, df: pl.DataFrame, bar_idx: int) -> None:
        """Optionally re-fit detector periodically (e.g., every 500 bars)."""
        if self.refit_every <= 0:
            return
        self._bars_since_refit += 1
        if self._bars_since_refit >= self.refit_every:
            # Re-fit on recent data (last lookback bars)
            start = max(0, bar_idx - self.lookback)
            hist_df = df.slice(start, bar_idx - start + 1)
            try:
                detector = self._get_detector()
                prices_pd = hist_df["close"].to_pandas()
                volumes_pd = (
                    hist_df["volume"].to_pandas()
                    if "volume" in hist_df.columns
                    else None
                )

                if isinstance(detector, HybridRegimeDetector):
                    detector.initialize(prices_pd, volumes_pd)
                elif isinstance(detector, HMMStrategy):
                    detector.fit(prices_pd, volumes_pd)
                elif isinstance(detector, GMMStrategy):
                    returns = np.log(prices_pd / prices_pd.shift(1)).dropna()
                    detector.fit(returns)

                self._bars_since_refit = 0
                logger.debug(f"Regime detector re-fitted at bar {bar_idx}")
            except Exception as e:
                logger.warning(f"Failed to re-fit detector: {e}")

    def _is_regime_stable(self, regime: MarketRegime) -> bool:
        """Check if regime has been stable for smoothing period."""
        self._regime_history.append(regime)
        if len(self._regime_history) > self.regime_smoothing:
            self._regime_history.pop(0)

        if len(self._regime_history) < self.regime_smoothing:
            return False

        return all(r == regime for r in self._regime_history)

    @property
    def name(self) -> str:
        return "regime_switching"

    def compute_indicators(self, df: pl.DataFrame) -> pl.DataFrame:
        """Compute indicators for all sub-strategies and regime detection."""
        # Compute indicators for each sub-strategy
        for strategy in self._sub_strategies.values():
            df = strategy.compute_indicators(df)

        # Add regime indicators if using rule-based (adds ATR percentile, ADX, etc.)
        if self.regime_method in ("rule_based", "hybrid"):
            from trading_agent.regime import add_regime_indicators

            df = add_regime_indicators(df)

        # Pre-fit detector on initial data
        self._fit_detector(df)

        return df

    def generate_signals(self, df: pl.DataFrame) -> pl.Series:
        """Generate regime-aware ensemble signals (discrete: 1=buy, -1=sell, 0=hold)."""
        n = len(df)
        raw_signals = np.zeros(n, dtype=np.float64)

        # Pre-compute all sub-strategy signals
        sub_signals: dict[str, np.ndarray] = {}
        for name, strategy in self._sub_strategies.items():
            try:
                sig = strategy.generate_signals(df).to_numpy()
                sub_signals[name] = sig
            except Exception as e:
                logger.warning(f"Sub-strategy {name} failed: {e}")
                sub_signals[name] = np.zeros(n, dtype=np.float64)

        # Bar-by-bar regime detection and ensemble
        in_position = False
        for i in range(self.lookback, n):
            # Predict regime (fast - no re-fit)
            regime, confidence = self._predict_regime(df, i)

            # Optionally re-fit periodically
            self._maybe_refit(df, i)

            # Smooth regime transitions
            if regime == self._current_regime:
                self._regime_stable_count += 1
            else:
                self._regime_stable_count = 0
                self._current_regime = regime

            # Only use regime if stable and confident
            use_regime = (
                self._current_regime
                if (
                    self._regime_stable_count >= self.regime_smoothing
                    and confidence >= self.min_confidence
                )
                else MarketRegime.UNKNOWN
            )

            # Get strategies for current regime
            regime_strategies = self._get_regime_strategies(use_regime)

            # Weighted ensemble of sub-strategy signals
            weighted_signal = 0.0
            total_weight = 0.0

            for cfg in regime_strategies:
                if cfg.strategy_name in sub_signals:
                    sig_val = sub_signals[cfg.strategy_name][i]
                    # Scale by regime confidence
                    effective_weight = cfg.weight * confidence
                    weighted_signal += sig_val * effective_weight
                    total_weight += effective_weight

            ensemble_score = weighted_signal / total_weight if total_weight > 0 else 0.0

            # Convert continuous score to discrete signals with hysteresis
            if not in_position:
                # Flat -> look for entry (score > 0.3)
                if ensemble_score > 0.3:
                    raw_signals[i] = 1.0
                    in_position = True
                else:
                    raw_signals[i] = 0.0
            else:
                # In position -> look for exit (score < -0.3 or regime change)
                if ensemble_score < -0.3:
                    raw_signals[i] = -1.0
                    in_position = False
                else:
                    raw_signals[i] = 0.0  # Hold

        return pl.Series("signal", raw_signals)


# Convenience function for quick backtest
def run_regime_switching_backtest(
    df: pl.DataFrame,
    initial_capital: float = 10000,
    params: dict | None = None,
) -> dict:
    """Quick backtest helper for regime switching strategy."""
    from trading_agent.backtest.engine import BacktestEngine

    strategy = RegimeSwitchingStrategy(params)
    engine = BacktestEngine(strategy, initial_capital=initial_capital)
    result = engine.run(df)
    return {
        "total_return": result.total_return_pct / 100,
        "sharpe": result.sharpe_ratio,
        "max_drawdown": result.max_drawdown_pct / 100,
        "n_trades": result.total_trades,
        "win_rate": result.win_rate,
        "profit_factor": result.profit_factor,
    }


if __name__ == "__main__":
    # Quick demo

    np.random.seed(42)
    n = 1000
    # Synthetic data with regime changes
    returns = np.concatenate(
        [
            np.random.normal(0.0008, 0.008, 250),  # bull
            np.random.normal(0.0001, 0.004, 250),  # sideways
            np.random.normal(-0.0009, 0.012, 250),  # bear
            np.random.normal(0.0002, 0.025, 250),  # high vol
        ]
    )
    prices = 100 * np.exp(np.cumsum(returns))

    df = pl.DataFrame(
        {
            "open": prices + np.random.randn(n) * 0.5,
            "high": prices + abs(np.random.randn(n) * 1.0),
            "low": prices - abs(np.random.randn(n) * 1.0),
            "close": prices,
            "volume": np.random.exponential(1000, n),
        }
    )

    result = run_regime_switching_backtest(df, params={"regime_method": "rule_based"})
    print("Regime Switching Backtest:")
    print(f"  Return: {result['total_return']:.2%}")
    print(f"  Sharpe: {result['sharpe']:.2f}")
    print(f"  Max DD: {result['max_drawdown']:.2%}")
    print(f"  Trades: {result['n_trades']}")
    print(f"  Win Rate: {result['win_rate']:.1%}")
    print(f"  Profit Factor: {result['profit_factor']:.2f}")
