"""Auditable online adaptation with fixed experts and delayed outcomes."""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Optional, Sequence

import numpy as np

from trading_agent.ml.online.indicators import (
    OnlineATR,
    OnlineBollingerBands,
    OnlineCorrelation,
    OnlineEMA,
    OnlineIndicator,
    OnlineMACD,
    OnlineRSI,
    OnlineStandardDeviation,
)


@dataclass(frozen=True)
class AdaptiveConfig:
    """Compatibility and fixed-expert allocation settings."""

    min_period: int = 10
    max_period: int = 50
    adaptation_rate: float = 0.1
    performance_window: int = 100
    min_samples: int = 50
    max_weight: float = 0.60
    turnover_penalty: float = 1.0
    uncertainty_shrinkage: float = 1.0

    def __post_init__(self) -> None:
        if self.min_period < 1 or self.max_period < self.min_period:
            raise ValueError("invalid fixed expert period range")
        if self.performance_window < 1 or self.min_samples < 1:
            raise ValueError("performance_window and min_samples must be positive")


class AdaptiveIndicator:
    """Legacy facade with market observation and outcome learning separated.

    The indicator period is fixed for its lifetime.  New allocation happens
    across independent fixed experts, never by rebuilding this state object.
    """

    def __init__(self, config: AdaptiveConfig):
        self.config = config
        self.current_period = config.min_period
        self.base_indicator: Optional[OnlineIndicator] = None
        self.performance_history: deque[float] = deque(maxlen=config.performance_window)
        self.regime = "unknown"
        self.market_observations = 0
        self.outcome_observations = 0

    def set_indicator(self, indicator: OnlineIndicator) -> None:
        if self.market_observations:
            raise RuntimeError("cannot replace a stateful indicator after observations")
        self.base_indicator = indicator

    def observe_market(self, value: float):
        """Process one market observation exactly once."""

        if self.base_indicator is None:
            return 0.0
        self.market_observations += 1
        return self.base_indicator.update(float(value))

    def observe_outcome(self, performance: float) -> None:
        """Record delayed performance without touching market state."""

        if not math.isfinite(float(performance)):
            raise ValueError("performance must be finite")
        self.performance_history.append(float(performance))
        self.outcome_observations += 1

    def adapt(self) -> None:
        """Compatibility no-op: fixed expert identity/state is never rebuilt."""

    def update(self, value: float, performance: float | None = None):
        result = self.observe_market(value)
        if performance is not None:
            self.observe_outcome(performance)
        return result

    @property
    def is_ready(self) -> bool:
        return self.base_indicator.is_ready if self.base_indicator else False

    def reset(self) -> None:
        if self.base_indicator:
            self.base_indicator.reset()
        self.performance_history.clear()
        self.market_observations = 0
        self.outcome_observations = 0


class AdaptiveEMA(AdaptiveIndicator):
    """Fixed-period EMA compatibility wrapper."""

    def __init__(self, config: Optional[AdaptiveConfig] = None):
        config = config or AdaptiveConfig(min_period=5, max_period=30)
        super().__init__(config)
        self.ema = OnlineEMA(config.min_period)
        self.base_indicator = self.ema
        self.volatility = OnlineStandardDeviation(20)

    def observe_market(self, value: float) -> float:
        self.volatility.update(float(value))
        return float(super().observe_market(value))


class AdaptiveRSI(AdaptiveIndicator):
    """Fixed-period RSI compatibility wrapper."""

    def __init__(self, config: Optional[AdaptiveConfig] = None):
        config = config or AdaptiveConfig(min_period=7, max_period=21)
        super().__init__(config)
        self.rsi = OnlineRSI(config.min_period)
        self.base_indicator = self.rsi
        self.trend_strength = OnlineCorrelation(20)

    def observe_market(self, value: float) -> float:
        previous = self.rsi.prev_close
        result = float(super().observe_market(value))
        if previous is not None:
            self.trend_strength.update(float(value), float(previous))
        return result


class AdaptiveBollingerBands(AdaptiveIndicator):
    """Fixed-period bands; volatility may widen bands but never resets history."""

    def __init__(self, config: Optional[AdaptiveConfig] = None):
        config = config or AdaptiveConfig(min_period=10, max_period=30)
        super().__init__(config)
        self.bb = OnlineBollingerBands(config.min_period, 2.0)
        self.base_indicator = self.bb
        self.volatility = OnlineStandardDeviation(20)
        self.current_std_mult = 2.0

    def observe_market(self, value: float) -> tuple[float, float, float]:
        volatility = self.volatility.update(float(value))
        if self.volatility.is_ready and volatility > 0.0:
            relative = volatility / max(abs(float(value)), 1e-12)
            self.current_std_mult = float(np.clip(1.5 + relative * 50.0, 1.5, 3.0))
            self.bb.num_std = self.current_std_mult
        result = super().observe_market(value)
        return tuple(float(item) for item in result)


class AdaptiveMACD(AdaptiveIndicator):
    """Fixed fast/slow MACD compatibility wrapper."""

    def __init__(self, config: Optional[AdaptiveConfig] = None):
        config = config or AdaptiveConfig(min_period=8, max_period=30)
        super().__init__(config)
        self.macd = OnlineMACD(12, 26, 9)
        self.base_indicator = self.macd

    def observe_market(self, value: float) -> tuple[float, float, float]:
        result = super().observe_market(value)
        return tuple(float(item) for item in result)


@dataclass
class FixedEMAExpert:
    """One immutable expert identity with independent streaming state."""

    name: str
    period: int
    indicator: OnlineEMA = field(init=False, repr=False)
    observation_count: int = 0
    outcome_count: int = 0
    last_forecast: float = 0.0

    def __post_init__(self) -> None:
        if self.period < 1:
            raise ValueError("expert period must be positive")
        self.indicator = OnlineEMA(self.period)

    def observe_market(self, value: float) -> float:
        estimate = self.indicator.update(float(value))
        self.observation_count += 1
        deviation = (float(value) - estimate) / max(abs(estimate), 1e-12)
        self.last_forecast = float(
            np.clip(deviation * math.sqrt(self.period), -1.0, 1.0)
        )
        return self.last_forecast

    def observe_outcome(self, realized_return: float) -> None:
        if not math.isfinite(float(realized_return)):
            raise ValueError("realized_return must be finite")
        self.outcome_count += 1

    def reset(self) -> None:
        self.indicator.reset()
        self.observation_count = 0
        self.outcome_count = 0
        self.last_forecast = 0.0


class FastExpert(FixedEMAExpert):
    def __init__(self, period: int = 5):
        super().__init__("fast", period)


class MediumExpert(FixedEMAExpert):
    def __init__(self, period: int = 20):
        super().__init__("medium", period)


class SlowExpert(FixedEMAExpert):
    def __init__(self, period: int = 60):
        super().__init__("slow", period)


@dataclass(frozen=True)
class AllocationForecast:
    observation_id: int
    forecast: float
    raw_forecast: float
    weights: dict[str, float]
    expert_forecasts: dict[str, float]
    uncertainty: float
    shrinkage: float


class OnlineWeightAllocator:
    """Deterministic delayed-outcome allocator over fixed experts."""

    def __init__(
        self,
        experts: Sequence[FixedEMAExpert] | None = None,
        *,
        learning_rate: float = 5.0,
        max_weight: float = 0.60,
        turnover_penalty: float = 1.0,
        min_observations: int = 30,
        uncertainty_shrinkage: float = 1.0,
        audit_window: int = 1_000,
    ) -> None:
        self.experts = list(experts or [FastExpert(), MediumExpert(), SlowExpert()])
        if not self.experts or len({expert.name for expert in self.experts}) != len(
            self.experts
        ):
            raise ValueError("experts must have unique identities")
        if max_weight < 1.0 / len(self.experts) or max_weight > 1.0:
            raise ValueError("max_weight cannot make the capped simplex infeasible")
        self.learning_rate = float(learning_rate)
        self.max_weight = float(max_weight)
        self.turnover_penalty = max(0.0, float(turnover_penalty))
        self.min_observations = max(1, int(min_observations))
        self.uncertainty_shrinkage = max(0.0, float(uncertainty_shrinkage))
        self.weights = np.full(len(self.experts), 1.0 / len(self.experts))
        self.scores = np.zeros(len(self.experts), dtype=float)
        self._pending: deque[tuple[int, np.ndarray]] = deque()
        self._next_observation_id = 0
        self.outcome_count = 0
        self.audit_log: deque[dict[str, Any]] = deque(maxlen=max(1, int(audit_window)))

    def _project_capped_simplex(self, values: np.ndarray) -> np.ndarray:
        result = np.zeros_like(values, dtype=float)
        remaining = np.ones(len(values), dtype=bool)
        remaining_mass = 1.0
        source = np.maximum(values, 0.0)
        while np.any(remaining):
            indices = np.flatnonzero(remaining)
            subtotal = float(np.sum(source[indices]))
            proposal = (
                np.full(len(indices), remaining_mass / len(indices))
                if subtotal <= 0.0
                else source[indices] / subtotal * remaining_mass
            )
            over = proposal > self.max_weight + 1e-15
            if not np.any(over):
                result[indices] = proposal
                break
            capped_indices = indices[over]
            result[capped_indices] = self.max_weight
            remaining[capped_indices] = False
            remaining_mass = 1.0 - float(np.sum(result[~remaining]))
        result = np.maximum(result, 0.0)
        return result / np.sum(result)

    def observe_market(self, value: float) -> AllocationForecast:
        forecasts = np.asarray(
            [expert.observe_market(float(value)) for expert in self.experts],
            dtype=float,
        )
        observation_id = self._next_observation_id
        self._next_observation_id += 1
        self._pending.append((observation_id, forecasts.copy()))
        raw = float(np.dot(self.weights, forecasts))
        uncertainty = float(np.std(forecasts))
        shrinkage = float(
            np.clip(1.0 - self.uncertainty_shrinkage * uncertainty, 0.0, 1.0)
        )
        result = AllocationForecast(
            observation_id=observation_id,
            forecast=raw * shrinkage,
            raw_forecast=raw,
            weights={
                expert.name: float(weight)
                for expert, weight in zip(self.experts, self.weights)
            },
            expert_forecasts={
                expert.name: float(forecast)
                for expert, forecast in zip(self.experts, forecasts)
            },
            uncertainty=uncertainty,
            shrinkage=shrinkage,
        )
        self.audit_log.append(
            {
                "event": "market",
                "observation_id": observation_id,
                "weights": result.weights,
                "expert_forecasts": result.expert_forecasts,
                "forecast": result.forecast,
            }
        )
        return result

    def observe_outcome(
        self,
        realized_return: float,
        *,
        observation_id: int | None = None,
    ) -> dict[str, float]:
        if not self._pending:
            raise RuntimeError("no pending forecast for delayed outcome")
        if not math.isfinite(float(realized_return)):
            raise ValueError("realized_return must be finite")
        pending_id, forecasts = self._pending[0]
        if observation_id is not None and pending_id != observation_id:
            raise ValueError("outcomes must be observed in forecast order")
        self._pending.popleft()
        for expert in self.experts:
            expert.observe_outcome(float(realized_return))
        self.outcome_count += 1

        utility = forecasts * float(realized_return)
        self.scores += utility
        previous = self.weights.copy()
        if self.outcome_count >= self.min_observations:
            centered = self.scores - float(np.max(self.scores))
            target = np.exp(np.clip(self.learning_rate * centered, -50.0, 0.0))
            target = self._project_capped_simplex(target)
            blend = 1.0 / (1.0 + self.turnover_penalty)
            self.weights = self._project_capped_simplex(
                (1.0 - blend) * previous + blend * target
            )
        turnover = float(np.sum(np.abs(self.weights - previous)))
        result = {
            expert.name: float(weight)
            for expert, weight in zip(self.experts, self.weights)
        }
        self.audit_log.append(
            {
                "event": "outcome",
                "observation_id": pending_id,
                "realized_return": float(realized_return),
                "weights": result,
                "weight_turnover": turnover,
            }
        )
        return result

    def reset(self) -> None:
        for expert in self.experts:
            expert.reset()
        self.weights[:] = 1.0 / len(self.experts)
        self.scores[:] = 0.0
        self._pending.clear()
        self._next_observation_id = 0
        self.outcome_count = 0
        self.audit_log.clear()


class AdaptiveStrategy:
    """Compatibility strategy using single-update indicators and fixed experts."""

    def __init__(self, config: AdaptiveConfig):
        self.config = config
        self.ema = AdaptiveEMA(config)
        self.rsi = AdaptiveRSI(config)
        self.bb = AdaptiveBollingerBands(config)
        self.macd = AdaptiveMACD(config)
        self.atr = OnlineATR(14)
        midpoint = max(
            config.min_period + 1, (config.min_period + config.max_period) // 2
        )
        self.allocator = OnlineWeightAllocator(
            [
                FastExpert(config.min_period),
                MediumExpert(midpoint),
                SlowExpert(config.max_period),
            ],
            max_weight=config.max_weight,
            turnover_penalty=config.turnover_penalty,
            min_observations=config.min_samples,
            uncertainty_shrinkage=config.uncertainty_shrinkage,
        )
        self.position = 0
        self.entry_price = 0.0
        self.performance = 0.0
        self.trades: list[float] = []
        self._last_close: float | None = None

    def update(
        self, high: float, low: float, close: float, volume: float
    ) -> dict[str, Any]:
        close = float(close)
        if self.position != 0:
            self.performance = (close - self.entry_price) * self.position
        else:
            self.performance = 0.0
        if self._last_close is not None:
            realized_return = close / self._last_close - 1.0
            self.allocator.observe_outcome(realized_return)
            for indicator in (self.ema, self.rsi, self.bb, self.macd):
                indicator.observe_outcome(self.performance)

        ema_val = self.ema.observe_market(close)
        rsi_val = self.rsi.observe_market(close)
        bb_mid, bb_up, bb_low = self.bb.observe_market(close)
        macd_val, macd_sig, macd_hist = self.macd.observe_market(close)
        atr_val = self.atr.update(float(high), float(low), close)
        allocation = self.allocator.observe_market(close)
        signal = self._generate_signal(
            close,
            ema_val,
            rsi_val,
            bb_up,
            bb_low,
            macd_val,
            macd_sig,
            macd_hist,
            atr_val,
        )
        self._last_close = close
        return {
            "signal": signal,
            "ema": ema_val,
            "rsi": rsi_val,
            "bb_middle": bb_mid,
            "bb_upper": bb_up,
            "bb_lower": bb_low,
            "macd": macd_val,
            "macd_signal": macd_sig,
            "macd_hist": macd_hist,
            "atr": atr_val,
            "position": self.position,
            "performance": self.performance,
            "expert_forecast": allocation.forecast,
            "expert_weights": allocation.weights,
        }

    def _generate_signal(
        self,
        close: float,
        ema: float,
        rsi: float,
        bb_up: float,
        bb_low: float,
        macd: float,
        macd_sig: float,
        macd_hist: float,
        atr: float,
    ) -> int:
        trend_up = close > ema
        trend_down = close < ema
        oversold = rsi < 30 and close < bb_low
        overbought = rsi > 70 and close > bb_up
        macd_bullish = macd > macd_sig and macd_hist > 0
        macd_bearish = macd < macd_sig and macd_hist < 0
        buy_signal = trend_up and (oversold or macd_bullish)
        sell_signal = trend_down and (overbought or macd_bearish)
        if self.position == 0:
            if buy_signal:
                self.position = 1
                self.entry_price = close
                return 1
            if sell_signal:
                self.position = -1
                self.entry_price = close
                return -1
        elif self.position > 0 and (
            sell_signal or (close - self.entry_price) > 2 * atr
        ):
            self.position = 0
            self.trades.append(close - self.entry_price)
            return -1
        elif self.position < 0 and (buy_signal or (self.entry_price - close) > 2 * atr):
            self.position = 0
            self.trades.append(self.entry_price - close)
            return 1
        return 0

    def reset(self) -> None:
        self.ema.reset()
        self.rsi.reset()
        self.bb.reset()
        self.macd.reset()
        self.atr.reset()
        self.allocator.reset()
        self.position = 0
        self.entry_price = 0.0
        self.performance = 0.0
        self.trades.clear()
        self._last_close = None
