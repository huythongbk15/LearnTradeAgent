"""Fail-closed adapter from legacy DataFrame strategies to the canonical
``ForecastStrategy`` contract (STR-0103).

Legacy strategies implement ``compute_indicators(pl.DataFrame)`` +
``generate_signals(pl.DataFrame) -> pl.Series`` and know nothing about the
canonical :class:`MarketObservation` / :class:`Forecast` types.  This adapter
bridges them **without** weakening any safety property:

Fail-closed rules
-----------------
1. The OHLCV history window must be supplied by the caller inside
   ``observation.features["ohlcv_window"]`` as a ``pl.DataFrame`` with at
   least the canonical OHLCV columns.  Missing/invalid window → raise.
2. Point-in-time: if a ``time`` column exists, its maximum must be
   ``<= observation.observed_at``; otherwise the window leaks future data →
   raise.
3. The window must contain at least ``warmup_bars + 1`` rows.
4. The final signal value must be finite; anything else → raise.

Research-only marking
---------------------
The adapter produces *directional* forecasts only — legacy signals carry no
calibrated expected-return estimate.  Until parity against the golden S0
fixture is proven (S1 exit gate), descriptors built by this adapter are
flagged ``research_only=True`` and the registry refuses them outside research
environments.
"""

from __future__ import annotations

import math

import polars as pl

from trading_agent.research.calibration import CalibrationState
from trading_agent.research.forecast import Forecast, MarketObservation
from trading_agent.strategies.base import Strategy

#: Feature key that carries the point-in-time OHLCV window.
OHLCV_WINDOW_FEATURE = "ohlcv_window"

#: Minimal columns every legacy strategy may rely on.
_REQUIRED_COLUMNS = ("open", "high", "low", "close", "volume")

#: Canonical action labels surfaced in forecast metadata.
ACTION_BUY = "BUY"
ACTION_SELL = "SELL"
ACTION_NO_TRADE = "NO_TRADE"


class LegacyAdapterError(RuntimeError):
    """Raised when the adapter cannot produce a safe forecast (fail-closed)."""


class LegacyDataFrameAdapter:
    """Wrap one legacy :class:`Strategy` behind the canonical contract."""

    def __init__(
        self,
        strategy: Strategy,
        *,
        model_artifact_id: str,
        warmup_bars: int = 1,
        horizon_bars: int = 1,
        edge_scale: float = 0.01,
        research_only: bool = True,
        strategy_id: str | None = None,
    ) -> None:
        if not isinstance(strategy, Strategy):
            raise TypeError(
                "strategy must subclass trading_agent.strategies.base.Strategy"
            )
        if horizon_bars <= 0:
            raise ValueError("horizon_bars must be positive")
        if warmup_bars < 0:
            raise ValueError("warmup_bars cannot be negative")
        if not math.isfinite(edge_scale) or edge_scale <= 0.0:
            raise ValueError("edge_scale must be positive and finite")
        if not model_artifact_id.strip():
            raise ValueError("model_artifact_id is required")
        self._strategy = strategy
        self._model_artifact_id = model_artifact_id
        self._warmup_bars = int(warmup_bars)
        self._horizon_bars = int(horizon_bars)
        self._edge_scale = float(edge_scale)
        self._research_only = bool(research_only)
        self.strategy_id = strategy_id or getattr(
            strategy, "name", type(strategy).__name__
        )

    # ── Canonical API ───────────────────────────────────────────────────
    def forecast(self, observation: MarketObservation) -> Forecast:
        window = self._extract_window(observation)
        signal_value = self._last_signal(window)

        action = (
            ACTION_BUY
            if signal_value > 0
            else ACTION_SELL
            if signal_value < 0
            else ACTION_NO_TRADE
        )

        metadata = {
            "canonical_action": action,
            "legacy_strategy": self.strategy_id,
            "raw_signal": float(signal_value),
            "research_only": self._research_only,
        }

        if action is ACTION_NO_TRADE:
            expected, lower, upper = 0.0, 0.0, 0.0
            direction_probability = None
        elif action is ACTION_BUY:
            expected = self._edge_scale
            lower, upper = 0.0, 2.0 * self._edge_scale
            direction_probability = None
        else:  # SELL
            expected = -self._edge_scale
            lower, upper = -2.0 * self._edge_scale, 0.0
            direction_probability = None

        return Forecast(
            expected_excess_return=expected,
            horizon=self._horizon_bars,
            lower_bound=lower,
            upper_bound=upper,
            direction_probability=direction_probability,
            calibration_state=(
                CalibrationState.CALIBRATED
                if not self._research_only
                else CalibrationState.UNCALIBRATED
            ),
            ood_score=0.0,
            model_artifact_id=self._model_artifact_id,
            generated_at=observation.observed_at,
            metadata=metadata,
        )

    # ── Fail-closed helpers ─────────────────────────────────────────────
    def _extract_window(self, observation: MarketObservation) -> pl.DataFrame:
        raw = observation.features.get(OHLCV_WINDOW_FEATURE)
        if not isinstance(raw, pl.DataFrame):
            raise LegacyAdapterError(
                f"observation.features[{OHLCV_WINDOW_FEATURE!r}] must be a "
                f"polars DataFrame; got {type(raw).__name__}"
            )
        missing = [col for col in _REQUIRED_COLUMNS if col not in raw.columns]
        if missing:
            raise LegacyAdapterError(f"ohlcv_window missing columns: {missing}")
        if len(raw) < self._warmup_bars + 1:
            raise LegacyAdapterError(
                f"ohlcv_window has {len(raw)} rows; need >= {self._warmup_bars + 1} "
                f"(warmup={self._warmup_bars} + current bar)"
            )
        if "time" in raw.columns:
            try:
                max_time = raw.select(pl.col("time").max()).item()
            except Exception as exc:  # pragma: no cover - polars type errors
                raise LegacyAdapterError(f"unreadable time column: {exc}") from exc
            if max_time is not None and max_time > observation.observed_at:
                raise LegacyAdapterError(
                    f"point-in-time violation: window time {max_time} exceeds "
                    f"observation time {observation.observed_at}"
                )
        return raw

    def _last_signal(self, window: pl.DataFrame) -> float:
        try:
            with_indicators = self._strategy.compute_indicators(window)
            series = self._strategy.generate_signals(with_indicators)
        except Exception as exc:
            raise LegacyAdapterError(
                f"legacy strategy {self.strategy_id!r} raised during evaluation: {exc}"
            ) from exc
        if series is None or len(series) == 0:
            raise LegacyAdapterError("legacy strategy produced an empty signal series")
        value = series.to_numpy()[-1]
        value = float(value)
        if not math.isfinite(value):
            raise LegacyAdapterError(f"final signal value is non-finite: {value!r}")
        return value
