"""Unified RiskDecision — single canonical type for the execution pipeline.

Merges the legacy ``trading_agent.agents.risk_decision.RiskDecision``
(risk_level, target_exposure_pct, max_new_exposure_pct, reduce_only, warnings)
with the canonical ``trading_agent.research.forecast.RiskDecision``
(forecast_fingerprint, model_artifact_id, requested_exposure, allowed_exposure,
 approved, reason_codes, decision_id).

All execution code must consume :class:`UnifiedRiskDecision`.  Legacy
``RiskDecision`` instances are converted via :class:`RiskDecisionAdapter`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any

from trading_agent.research.forecast import (
    RiskDecision as ForecastRiskDecision,
)
from trading_agent.research.forecast import (
    RiskReason,
)


class RiskLevel(str, Enum):
    """Legacy risk level preserved for backward compatibility."""

    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    EXTREME = "EXTREME"


@dataclass(frozen=True)
class UnifiedRiskDecision:
    """Single canonical risk decision for the execution pipeline.

    Contains ALL fields from both legacy and canonical RiskDecision types,
    plus execution-specific metadata.
    """

    # ── Identity ────────────────────────────────────────────────────────
    decision_id: str
    forecast_fingerprint: str
    model_artifact_id: str

    # ── Exposure policy ─────────────────────────────────────────────────
    requested_target_exposure: float  # from forecast (requested_exposure)
    allowed_target_exposure: float    # from forecast (allowed_exposure)
    max_new_exposure: float           # from legacy (max_new_exposure_pct)
    reduce_only: bool                 # from legacy

    # ── Risk assessment ─────────────────────────────────────────────────
    risk_level: RiskLevel              # from legacy
    reason_codes: tuple[RiskReason, ...]  # from forecast

    # ── Calibration evidence ────────────────────────────────────────────
    calibration_state: str             # from forecast (CalibrationState value)
    calibration_artifact_id: str | None

    # ── Model / regime telemetry ────────────────────────────────────────
    ood_score: float                   # from forecast
    regime_entropy: float
    interval_width: float

    # ── Audit ───────────────────────────────────────────────────────────
    created_at: datetime
    metadata: dict[str, Any] = field(default_factory=dict)
    warnings: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        # Validate numeric ranges without mutating the frozen instance.
        for name in (
            "requested_target_exposure",
            "allowed_target_exposure",
            "max_new_exposure",
            "ood_score",
            "regime_entropy",
            "interval_width",
        ):
            val = float(getattr(self, name))
            if not (0.0 <= val <= 1.0):
                raise ValueError(f"{name} must be in [0,1], got {val}")
        if self.max_new_exposure > self.allowed_target_exposure + 1e-9:
            raise ValueError(
                "max_new_exposure cannot exceed allowed_target_exposure"
            )
        if self.allowed_target_exposure > self.requested_target_exposure + 1e-9:
            raise ValueError(
                "allowed_target_exposure cannot exceed requested_target_exposure"
            )

    @property
    def approved(self) -> bool:
        """True when the risk layer allowed a non-zero exposure."""
        return self.allowed_target_exposure > 1e-12

    @property
    def effective_exposure(self) -> float:
        """Exposure actually usable for sizing, respecting reduce_only."""
        if self.reduce_only:
            return 0.0
        return self.allowed_target_exposure


class RiskDecisionAdapter:
    """Convert between legacy and canonical RiskDecision types."""

    @staticmethod
    def from_legacy(
        legacy: Any,
        *,
        forecast_fingerprint: str = "",
        model_artifact_id: str = "",
        calibration_state: str = "UNKNOWN",
        calibration_artifact_id: str | None = None,
        regime_entropy: float = 0.0,
        interval_width: float = 0.0,
        ood_score: float = 0.0,
    ) -> UnifiedRiskDecision:
        """Build a UnifiedRiskDecision from a legacy RiskDecision.

        The legacy type only carries exposure policy; the remaining
        forecast-specific fields are supplied as explicit arguments so the
        adapter remains a pure function with no hidden I/O.
        """
        from trading_agent.agents.risk_decision import (
            RiskDecision as LegacyRiskDecision,
        )

        if not isinstance(legacy, LegacyRiskDecision):
            raise TypeError(
                f"expected LegacyRiskDecision, got {type(legacy).__name__}"
            )
        now = datetime.now(UTC)
        decision_id = (
            f"legacy_{now.strftime('%Y%m%d%H%M%S')}_{id(legacy):x}"
        )
        return UnifiedRiskDecision(
            decision_id=decision_id,
            forecast_fingerprint=forecast_fingerprint,
            model_artifact_id=model_artifact_id,
            requested_target_exposure=legacy.target_exposure_pct,
            allowed_target_exposure=legacy.target_exposure_pct,
            max_new_exposure=legacy.max_new_exposure_pct,
            reduce_only=legacy.reduce_only,
            risk_level=RiskLevel(legacy.risk_level.value),
            reason_codes=tuple(),
            calibration_state=calibration_state,
            calibration_artifact_id=calibration_artifact_id,
            ood_score=ood_score,
            regime_entropy=regime_entropy,
            interval_width=interval_width,
            created_at=now,
            warnings=legacy.warnings,
        )

    @staticmethod
    def from_forecast(
        forecast_decision: ForecastRiskDecision,
        *,
        max_new_exposure: float = 0.0,
        reduce_only: bool = False,
        regime_entropy: float = 0.0,
        interval_width: float = 0.0,
        warnings: tuple[str, ...] = (),
    ) -> UnifiedRiskDecision:
        """Build a UnifiedRiskDecision from the canonical forecast RiskDecision.

        Exposure fields come from the forecast decision; execution policy
        fields are injected as arguments.
        """
        now = datetime.now(UTC)
        return UnifiedRiskDecision(
            decision_id=forecast_decision.decision_id,
            forecast_fingerprint=forecast_decision.forecast_fingerprint,
            model_artifact_id=forecast_decision.model_artifact_id,
            requested_target_exposure=forecast_decision.requested_exposure,
            allowed_target_exposure=forecast_decision.allowed_exposure,
            max_new_exposure=max_new_exposure,
            reduce_only=reduce_only,
            risk_level=RiskLevel.MEDIUM,
            reason_codes=forecast_decision.reason_codes,
            calibration_state="CALIBRATED",
            calibration_artifact_id=forecast_decision.model_artifact_id,
            ood_score=0.0,
            regime_entropy=regime_entropy,
            interval_width=interval_width,
            created_at=now,
            warnings=warnings,
        )

    @staticmethod
    def merge(
        legacy: Any,
        forecast: ForecastRiskDecision,
        *,
        regime_entropy: float = 0.0,
        interval_width: float = 0.0,
    ) -> UnifiedRiskDecision:
        """Merge both legacy and forecast decisions into one unified type.

        Precedence:
        - exposure policy: legacy (more conservative)
        - approval/reasons: forecast (blocking reasons zero out exposure)
        - telemetry: forecast
        """
        legacy_unified = RiskDecisionAdapter.from_legacy(
            legacy,
            forecast_fingerprint=forecast.forecast_fingerprint,
            model_artifact_id=forecast.model_artifact_id,
            regime_entropy=regime_entropy,
            interval_width=interval_width,
        )
        forecast_unified = RiskDecisionAdapter.from_forecast(
            forecast,
            max_new_exposure=legacy_unified.max_new_exposure,
            reduce_only=legacy_unified.reduce_only,
            regime_entropy=regime_entropy,
            interval_width=interval_width,
        )
        # Apply blocking reasons from forecast
        if not forecast.approved:
            return UnifiedRiskDecision(
                decision_id=forecast_unified.decision_id,
                forecast_fingerprint=forecast_unified.forecast_fingerprint,
                model_artifact_id=forecast_unified.model_artifact_id,
                requested_target_exposure=forecast_unified.requested_target_exposure,
                allowed_target_exposure=0.0,
                max_new_exposure=0.0,
                reduce_only=True,
                risk_level=RiskLevel.EXTREME,
                reason_codes=forecast_unified.reason_codes,
                calibration_state=forecast_unified.calibration_state,
                calibration_artifact_id=forecast_unified.calibration_artifact_id,
                ood_score=forecast_unified.ood_score,
                regime_entropy=forecast_unified.regime_entropy,
                interval_width=forecast_unified.interval_width,
                created_at=forecast_unified.created_at,
                warnings=forecast_unified.warnings + legacy_unified.warnings,
            )
        return forecast_unified


__all__ = [
    "RiskLevel",
    "UnifiedRiskDecision",
    "RiskDecisionAdapter",
]
