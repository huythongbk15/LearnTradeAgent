"""Canonical, broker-free strategy forecast and risk contracts.

Strategy implementations terminate at :class:`Forecast`.  Position sizing,
order permission and execution are separate layers so the same strategy logic
can run in research, backtest, paper, testnet and shadow environments.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from types import MappingProxyType
from typing import Any, Mapping, Protocol, runtime_checkable

from trading_agent.research.calibration import (
    CalibrationState,
    ExposureUncertainty,
    MonotonicExposurePolicy,
    PredictionInterval,
)


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType(
            {
                str(key): _freeze(item)
                for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
            }
        )
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, (set, frozenset)):
        return tuple(sorted((_freeze(item) for item in value), key=repr))
    return value


def _jsonable(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in sorted(value.items())}
    if isinstance(value, tuple):
        return [_jsonable(item) for item in value]
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    return value


def _sha256(payload: Mapping[str, Any]) -> str:
    canonical = json.dumps(
        _jsonable(payload), sort_keys=True, separators=(",", ":"), allow_nan=False
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _require_aware(timestamp: datetime, label: str) -> None:
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise ValueError(f"{label} must be timezone-aware")


@dataclass(frozen=True)
class MarketObservation:
    """A point-in-time market input; it contains no broker or account handle."""

    symbol: str
    observed_at: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    features: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.symbol.strip():
            raise ValueError("symbol is required")
        _require_aware(self.observed_at, "observed_at")
        values = (self.open, self.high, self.low, self.close, self.volume)
        if not all(math.isfinite(float(value)) for value in values):
            raise ValueError("OHLCV values must be finite")
        if min(self.open, self.high, self.low, self.close) <= 0.0:
            raise ValueError("OHLC prices must be positive")
        if self.volume < 0.0:
            raise ValueError("volume must be non-negative")
        if self.low > min(self.open, self.close) or self.high < max(
            self.open, self.close
        ):
            raise ValueError("OHLC values are inconsistent")
        if self.low > self.high:
            raise ValueError("low cannot exceed high")
        object.__setattr__(self, "features", _freeze(self.features))


@dataclass(frozen=True)
class Forecast:
    """Immutable strategy output consumed by the independent risk layer."""

    expected_excess_return: float
    horizon: int
    lower_bound: float
    upper_bound: float
    direction_probability: float | None
    calibration_state: CalibrationState
    ood_score: float
    model_artifact_id: str
    generated_at: datetime
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        numeric = (
            self.expected_excess_return,
            self.lower_bound,
            self.upper_bound,
            self.ood_score,
        )
        if not all(math.isfinite(float(value)) for value in numeric):
            raise ValueError("forecast values must be finite")
        if self.horizon <= 0:
            raise ValueError("horizon must be positive")
        if not self.lower_bound <= self.expected_excess_return <= self.upper_bound:
            raise ValueError("expected_excess_return must lie within forecast bounds")
        if self.direction_probability is not None:
            if not math.isfinite(float(self.direction_probability)):
                raise ValueError("direction_probability must be finite")
            if not 0.0 <= self.direction_probability <= 1.0:
                raise ValueError("direction_probability must be in [0, 1]")
        if not 0.0 <= self.ood_score <= 1.0:
            raise ValueError("ood_score must be in [0, 1]")
        if not isinstance(self.calibration_state, CalibrationState):
            raise TypeError("calibration_state must be a CalibrationState")
        if not self.model_artifact_id.strip():
            raise ValueError("model_artifact_id is required")
        _require_aware(self.generated_at, "generated_at")
        object.__setattr__(self, "metadata", _freeze(self.metadata))

    @property
    def fingerprint(self) -> str:
        return _sha256(
            {
                "expected_excess_return": self.expected_excess_return,
                "horizon": self.horizon,
                "lower_bound": self.lower_bound,
                "upper_bound": self.upper_bound,
                "direction_probability": self.direction_probability,
                "calibration_state": self.calibration_state,
                "ood_score": self.ood_score,
                "model_artifact_id": self.model_artifact_id,
                "generated_at": self.generated_at,
                "metadata": self.metadata,
            }
        )


@runtime_checkable
class ForecastStrategy(Protocol):
    """The only canonical strategy API.  It has no execution capability."""

    def forecast(self, observation: MarketObservation) -> Forecast: ...


class RiskReason(str, Enum):
    APPROVED = "approved"
    NO_EXPECTED_EDGE = "no_expected_edge"
    INTERVAL_CROSSES_ZERO = "interval_crosses_zero"
    CALIBRATION_NOT_CURRENT = "calibration_not_current"
    OOD_LIMIT = "ood_limit"
    UNCERTAINTY_REDUCED = "uncertainty_reduced"


@dataclass(frozen=True)
class RiskDecision:
    forecast_fingerprint: str
    model_artifact_id: str
    requested_exposure: float
    allowed_exposure: float
    approved: bool
    reason_codes: tuple[RiskReason, ...]
    decision_id: str


@dataclass(frozen=True)
class TargetExposure:
    symbol: str
    exposure: float
    horizon: int
    forecast_fingerprint: str
    model_artifact_id: str
    risk_decision_id: str

    def __post_init__(self) -> None:
        if not math.isfinite(self.exposure) or abs(self.exposure) > 1.0:
            raise ValueError("target exposure must be finite and in [-1, 1]")


class ForecastRiskPolicy:
    """Deterministic monotone sizing policy for immutable forecasts."""

    def __init__(
        self,
        *,
        max_ood: float = 0.70,
        interval_width_scale: float = 0.01,
        require_current_calibration: bool = True,
    ) -> None:
        if not 0.0 <= max_ood <= 1.0:
            raise ValueError("max_ood must be in [0, 1]")
        self.max_ood = float(max_ood)
        self.require_current_calibration = bool(require_current_calibration)
        self._exposure_policy = MonotonicExposurePolicy(
            interval_width_scale=interval_width_scale
        )

    def evaluate(
        self,
        forecast: Forecast,
        *,
        requested_exposure: float,
        calibration_ece: float = 0.0,
        regime_entropy: float = 0.0,
    ) -> RiskDecision:
        request = min(1.0, max(0.0, float(requested_exposure)))
        if not all(
            math.isfinite(value) for value in (request, calibration_ece, regime_entropy)
        ):
            raise ValueError("risk policy inputs must be finite")
        if not 0.0 <= calibration_ece <= 1.0:
            raise ValueError("calibration_ece must be in [0, 1]")
        if not 0.0 <= regime_entropy <= 1.0:
            raise ValueError("regime_entropy must be in [0, 1]")

        reasons: list[RiskReason] = []
        direction = 1.0 if forecast.expected_excess_return > 0.0 else -1.0
        if forecast.expected_excess_return == 0.0:
            direction = 0.0
            reasons.append(RiskReason.NO_EXPECTED_EDGE)
        interval = PredictionInterval(
            lower=forecast.lower_bound, upper=forecast.upper_bound, coverage=0.90
        )
        if interval.crosses_zero:
            reasons.append(RiskReason.INTERVAL_CROSSES_ZERO)
        if (
            self.require_current_calibration
            and forecast.calibration_state != CalibrationState.CALIBRATED
        ):
            reasons.append(RiskReason.CALIBRATION_NOT_CURRENT)
        if forecast.ood_score > self.max_ood:
            reasons.append(RiskReason.OOD_LIMIT)

        allowed_magnitude = self._exposure_policy.allowed_directional_exposure(
            request,
            ExposureUncertainty(
                calibration_state=forecast.calibration_state,
                ece=calibration_ece,
                ood_score=forecast.ood_score,
                interval=interval,
                regime_entropy=regime_entropy,
            ),
        )
        blocking = {
            RiskReason.NO_EXPECTED_EDGE,
            RiskReason.INTERVAL_CROSSES_ZERO,
            RiskReason.CALIBRATION_NOT_CURRENT,
            RiskReason.OOD_LIMIT,
        }
        if any(reason in blocking for reason in reasons):
            allowed_magnitude = 0.0
        elif allowed_magnitude + 1e-15 < request:
            reasons.append(RiskReason.UNCERTAINTY_REDUCED)
        if allowed_magnitude > 0.0:
            reasons.append(RiskReason.APPROVED)
        allowed = direction * allowed_magnitude
        requested = direction * request
        decision_payload = {
            "forecast_fingerprint": forecast.fingerprint,
            "model_artifact_id": forecast.model_artifact_id,
            "requested_exposure": requested,
            "allowed_exposure": allowed,
            "approved": allowed_magnitude > 0.0,
            "reason_codes": [reason.value for reason in reasons],
        }
        return RiskDecision(
            forecast_fingerprint=forecast.fingerprint,
            model_artifact_id=forecast.model_artifact_id,
            requested_exposure=requested,
            allowed_exposure=allowed,
            approved=allowed_magnitude > 0.0,
            reason_codes=tuple(reasons),
            decision_id=f"risk_{_sha256(decision_payload)[:32]}",
        )


@dataclass(frozen=True)
class DecisionBundle:
    observation: MarketObservation
    forecast: Forecast
    risk_decision: RiskDecision
    target_exposure: TargetExposure


class StrategyRiskPipeline:
    """Environment-neutral MarketObservation -> TargetExposure pipeline."""

    def __init__(
        self,
        strategy: ForecastStrategy,
        risk_policy: ForecastRiskPolicy,
        *,
        requested_exposure: float,
    ) -> None:
        if not isinstance(strategy, ForecastStrategy):
            raise TypeError("strategy must implement forecast(MarketObservation)")
        self._strategy = strategy
        self._risk_policy = risk_policy
        self._requested_exposure = requested_exposure

    def evaluate(
        self,
        observation: MarketObservation,
        *,
        calibration_ece: float = 0.0,
        regime_entropy: float = 0.0,
    ) -> DecisionBundle:
        forecast = self._strategy.forecast(observation)
        if not isinstance(forecast, Forecast):
            raise TypeError("strategy.forecast must return Forecast")
        decision = self._risk_policy.evaluate(
            forecast,
            requested_exposure=self._requested_exposure,
            calibration_ece=calibration_ece,
            regime_entropy=regime_entropy,
        )
        target = TargetExposure(
            symbol=observation.symbol,
            exposure=decision.allowed_exposure,
            horizon=forecast.horizon,
            forecast_fingerprint=forecast.fingerprint,
            model_artifact_id=forecast.model_artifact_id,
            risk_decision_id=decision.decision_id,
        )
        return DecisionBundle(observation, forecast, decision, target)


class DecisionEnvironment(str, Enum):
    RESEARCH = "research"
    BACKTEST = "backtest"
    PAPER = "paper"
    TESTNET = "testnet"
    SHADOW = "shadow"


class EnvironmentAdapter(Protocol):
    """Only environment I/O differs; strategy and risk logic remain identical."""

    environment: DecisionEnvironment

    def publish(self, decision: DecisionBundle) -> None: ...


class ResearchStrategyRuntime:
    """Research-only forecast pipeline runtime.

    This name is intentionally distinct from the authority-bound execution
    ``StrategyRuntime``.  ``StrategyRuntime`` remains a compatibility alias so
    existing research callers do not silently change behavior.
    """

    def __init__(
        self, pipeline: StrategyRiskPipeline, adapter: EnvironmentAdapter
    ) -> None:
        self._pipeline = pipeline
        self._adapter = adapter

    def on_observation(self, observation: MarketObservation) -> DecisionBundle:
        decision = self._pipeline.evaluate(observation)
        self._adapter.publish(decision)
        return decision


# Backward-compatible import surface for research clients.  New code should
# use ``ResearchStrategyRuntime`` to avoid confusing it with the authority
# runtime that can submit orders.
StrategyRuntime = ResearchStrategyRuntime


def utc_now() -> datetime:
    """Injectable convenience for strategy implementations."""

    return datetime.now(UTC)
