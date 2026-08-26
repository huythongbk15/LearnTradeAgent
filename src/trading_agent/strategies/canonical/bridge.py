"""Legacy-compatible runtime bridge for canonical strategies.

The execution engine resolves a ``StrategyRuntime`` whose strategy must
expose the legacy interface (``compute_indicators`` + ``generate_signals``
returning a polars Series). Canonical-only strategies (e.g. ma_adx,
ma_vol_target) have no legacy counterpart class, so this bridge adapts the
canonical ``ForecastStrategy.forecast(MarketObservation)`` contract to
that interface WITHOUT changing any engine code:

- ``compute_indicators(df)`` returns the frame untouched (canonical
  features are built per forecast from the raw OHLCV window);
- ``generate_signals(df)`` decides on the LAST bar's close (observed_at =
  last bar open + timeframe delta, window = all bars in df) and returns a
  one-element Series that the runtime maps exactly like legacy signals:
  >0 → BUY, <0 → SELL, 0 → HOLD.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import polars as pl

from trading_agent.strategies.canonical.adapter import (
    ACTION_BUY,
    ACTION_SELL,
    LegacyDataFrameAdapter,
)
from trading_agent.strategies.canonical.features import (
    FEATURE_OHLCV_WINDOW,
    FeatureUnavailableError,
    build_ohlcv_window,
)

_TIME_COL_ALIASES = ("time", "timestamp")


class CanonicalRuntimeBridge:
    """Adapts a canonical ForecastStrategy adapter to the legacy interface."""

    def __init__(
        self,
        adapter: LegacyDataFrameAdapter,
        *,
        warmup_bars: int,
        symbol: str,
        timeframe_delta: timedelta,
    ):
        self._adapter = adapter
        self._warmup_bars = warmup_bars
        self._symbol = symbol
        self._timeframe_delta = timeframe_delta

    # ── Legacy interface expected by StrategyRuntime.execute ──────────

    def compute_indicators(self, frame: pl.DataFrame) -> pl.DataFrame:
        return frame  # canonical features are built per forecast call

    def generate_signals(self, frame: pl.DataFrame) -> pl.Series:
        value = self._decide_last_bar(frame)
        return pl.Series("signal", [value])

    def get_indicator_names(self) -> list[str]:  # pragma: no cover - optional API
        return []

    # ── Internals ──────────────────────────────────────────────────────

    @staticmethod
    def _time_column(frame: pl.DataFrame) -> str:
        for alias in _TIME_COL_ALIASES:
            if alias in frame.columns:
                return alias
        raise ValueError(
            f"frame must carry a time column (one of {_TIME_COL_ALIASES})"
        )

    def _decide_last_bar(self, frame: pl.DataFrame) -> int:
        """Decide exactly like ``canonical_signal_series`` does at bar j.

        The engine hands us a slice whose LAST row is the decision bar j
        (closed-bar decision executed at the NEXT open). The decision
        window therefore ends strictly BEFORE bar j: observed_at =
        times[j] and the window covers bars [.., j-1]. This is the same
        point-in-time convention proven in the S1 parity tests.
        """
        from trading_agent.research.forecast import MarketObservation

        if frame.height < 2:
            return 0
        time_col = self._time_column(frame)
        observed_at = frame[time_col][-1]
        if getattr(observed_at, "tzinfo", None) is None:
            observed_at = observed_at.replace(tzinfo=UTC)

        try:
            canon = frame.rename({time_col: "time"})
            t_dtype = canon.schema["time"]
            if t_dtype == pl.Utf8:
                canon = canon.with_columns(
                    pl.col("time").str.to_datetime(time_zone="UTC")
                )
            elif t_dtype.time_zone is None:
                canon = canon.with_columns(
                    pl.col("time").dt.replace_time_zone("UTC")
                )
            window = build_ohlcv_window(
                canon.head(-1),  # exclude the decision bar itself
                observed_at=observed_at,
                bars=self._warmup_bars + 1,
            )
        except FeatureUnavailableError:
            return 0  # insufficient history → stay flat

        row = frame.row(-1, named=True)
        observation = MarketObservation(
            symbol=self._symbol,
            observed_at=observed_at,
            open=float(row["open"]),
            high=float(row["high"]),
            low=float(row["low"]),
            close=float(row["close"]),
            volume=float(row.get("volume", 0.0)),
            features={FEATURE_OHLCV_WINDOW: window},
        )
        forecast = self._adapter.forecast(observation)
        action = forecast.metadata.get("canonical_action")
        if action == ACTION_BUY:
            return 1
        if action == ACTION_SELL:
            return -1
        return 0


def build_canonical_runtime(
    *,
    strategy_id: str,
    spec_params: dict[str, Any],
    artifact_id: str,
    artifact_metadata: dict[str, Any],
    adapter: LegacyDataFrameAdapter,
    descriptor_warmup_bars: int,
    symbol: str,
    timeframe: str,
    timeframe_delta: timedelta,
    default_target_exposure_pct: float,
):
    """Construct a paper-environment StrategyRuntime around the bridge.

    Used by tournament cells whose strategy has no legacy resolver entry;
    keeps every authority check intact because the underlying artifact was
    registered + promoted through the same stores as resolved ones.
    """
    from trading_agent.authority.config import Environment
    from trading_agent.authority.resolver import StrategyRuntime

    parameters = dict(spec_params)
    parameters.setdefault("target_exposure_pct", default_target_exposure_pct)

    bridge = CanonicalRuntimeBridge(
        adapter,
        warmup_bars=descriptor_warmup_bars,
        symbol=symbol,
        timeframe_delta=timeframe_delta,
    )
    return StrategyRuntime(
        strategy=bridge,
        artifact_id=artifact_id,
        strategy_name=strategy_id,
        symbol=symbol,
        timeframe=timeframe,
        environment=Environment.PAPER,
        parameters=parameters,
        promoted_at=datetime.now(UTC),
        promotion_stage="paper_eligible",
        artifact_metadata=artifact_metadata,
    )
