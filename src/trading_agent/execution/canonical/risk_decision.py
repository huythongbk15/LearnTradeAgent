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


class EvidenceState(str, Enum):
    """Evidence availability state for calibration/OOD/regime."""

    KNOWN = "KNOWN"  # evidence available and current
    UNKNOWN = "UNKNOWN"  # evidence not computed / unavailable
    MISSING = "MISSING"  # evidence expected but not found
    STALE = "STALE"  # evidence exists but expired


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
    allowed_target_exposure: float  # from forecast (allowed_exposure)
    max_new_exposure: float  # from legacy (max_new_exposure_pct)
    reduce_only: bool  # from legacy

    # ── Risk assessment ─────────────────────────────────────────────────
    risk_level: RiskLevel  # from legacy
    reason_codes: tuple[RiskReason, ...]  # from forecast

    # ── Calibration evidence ────────────────────────────────────────────
    calibration_state: EvidenceState
    calibration_artifact_id: str | None
    calibration_ece: float  # Expected Calibration Error [0, 1]

    # ── OOD evidence ────────────────────────────────────────────────────
    ood_state: EvidenceState
    ood_score: float  # [0, 1], higher = more OOD

    # ── Regime evidence ─────────────────────────────────────────────────
    regime_state: EvidenceState
    regime_entropy: float  # [0, 1], higher = more uncertain

    # ── Uncertainty quantification ──────────────────────────────────────
    interval_width: float  # prediction interval width (normalized)

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
            "calibration_ece",
        ):
            val = float(getattr(self, name))
            if not (0.0 <= val <= 1.0):
                raise ValueError(f"{name} must be in [0,1], got {val}")
        if self.max_new_exposure > self.allowed_target_exposure + 1e-9:
            raise ValueError("max_new_exposure cannot exceed allowed_target_exposure")
        if self.allowed_target_exposure > self.requested_target_exposure + 1e-9:
            raise ValueError(
                "allowed_target_exposure cannot exceed requested_target_exposure"
            )
        if (
            self.calibration_state is EvidenceState.MISSING
            and self.calibration_ece == 0.0
        ):
            raise ValueError("calibration_ece must be > 0")
        if self.ood_state is EvidenceState.UNKNOWN and self.ood_score == 0.0:
            raise ValueError("ood_score must be > 0")
        if self.regime_state is EvidenceState.STALE and self.regime_entropy == 0.0:
            raise ValueError("regime_entropy must be > 0")

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

    @property
    def evidence_complete(self) -> bool:
        """True when all evidence states are KNOWN."""
        return (
            self.calibration_state is EvidenceState.KNOWN
            and self.ood_state is EvidenceState.KNOWN
            and self.regime_state is EvidenceState.KNOWN
        )

    @property
    def has_missing_evidence(self) -> bool:
        """True when any evidence is MISSING or STALE."""
        return any(
            s in (EvidenceState.MISSING, EvidenceState.STALE)
            for s in (
                self.calibration_state,
                self.ood_state,
                self.regime_state,
            )
        )

    @property
    def has_unknown_evidence(self) -> bool:
        """True when any evidence is UNKNOWN."""
        return any(
            s is EvidenceState.UNKNOWN
            for s in (
                self.calibration_state,
                self.ood_state,
                self.regime_state,
            )
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-safe dict for hashing/logging."""
        return {
            "decision_id": self.decision_id,
            "forecast_fingerprint": self.forecast_fingerprint,
            "model_artifact_id": self.model_artifact_id,
            "requested_target_exposure": self.requested_target_exposure,
            "allowed_target_exposure": self.allowed_target_exposure,
            "max_new_exposure": self.max_new_exposure,
            "reduce_only": self.reduce_only,
            "risk_level": self.risk_level.value,
            "reason_codes": [
                rc.value if hasattr(rc, "value") else rc for rc in self.reason_codes
            ],
            "calibration_state": self.calibration_state.value,
            "calibration_artifact_id": self.calibration_artifact_id,
            "calibration_ece": self.calibration_ece,
            "ood_state": self.ood_state.value,
            "ood_score": self.ood_score,
            "regime_state": self.regime_state.value,
            "regime_entropy": self.regime_entropy,
            "interval_width": self.interval_width,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "metadata": self.metadata,
            "warnings": list(self.warnings),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> UnifiedRiskDecision:
        """Reconstruct from a JSON-safe dict produced by to_dict()."""
        created_at = data.get("created_at")
        if isinstance(created_at, str):
            created_at = datetime.fromisoformat(created_at)
        return cls(
            decision_id=data["decision_id"],
            forecast_fingerprint=data.get("forecast_fingerprint", ""),
            model_artifact_id=data.get("model_artifact_id", ""),
            requested_target_exposure=float(data["requested_target_exposure"]),
            allowed_target_exposure=float(data["allowed_target_exposure"]),
            max_new_exposure=float(data["max_new_exposure"]),
            reduce_only=bool(data.get("reduce_only", False)),
            risk_level=RiskLevel(data["risk_level"]),
            reason_codes=tuple(
                rc if isinstance(rc, str) else RiskReason(rc)
                for rc in data.get("reason_codes", [])
            ),
            calibration_state=EvidenceState(data["calibration_state"]),
            calibration_artifact_id=data.get("calibration_artifact_id"),
            calibration_ece=float(data.get("calibration_ece", 0.0)),
            ood_state=EvidenceState(data["ood_state"]),
            ood_score=float(data.get("ood_score", 0.0)),
            regime_state=EvidenceState(data["regime_state"]),
            regime_entropy=float(data.get("regime_entropy", 0.0)),
            interval_width=float(data.get("interval_width", 0.0)),
            created_at=created_at or datetime.now(UTC),
            metadata=data.get("metadata", {}),
            warnings=tuple(data.get("warnings", [])),
        )


class RiskDecisionAdapter:
    """Convert between legacy and canonical RiskDecision types."""

    @staticmethod
    def from_legacy(
        legacy: Any,
        *,
        forecast_fingerprint: str = "",
        model_artifact_id: str = "",
        calibration_state: EvidenceState = EvidenceState.UNKNOWN,
        calibration_artifact_id: str | None = None,
        calibration_ece: float = 1.0,
        ood_state: EvidenceState = EvidenceState.UNKNOWN,
        ood_score: float = 1.0,
        regime_state: EvidenceState = EvidenceState.UNKNOWN,
        regime_entropy: float = 1.0,
        interval_width: float = 1.0,
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
            raise TypeError(f"expected LegacyRiskDecision, got {type(legacy).__name__}")
        now = datetime.now(UTC)
        decision_id = f"legacy_{now.strftime('%Y%m%d%H%M%S')}_{id(legacy):x}"

        # HIGH/EXTREME legacy risk → zero new exposure, reduce_only
        max_new_exposure = legacy.max_new_exposure_pct
        reduce_only = legacy.reduce_only
        if legacy.risk_level.value in ("HIGH", "EXTREME"):
            max_new_exposure = 0.0
            reduce_only = True

        return UnifiedRiskDecision(
            decision_id=decision_id,
            forecast_fingerprint=forecast_fingerprint,
            model_artifact_id=model_artifact_id,
            requested_target_exposure=legacy.target_exposure_pct,
            allowed_target_exposure=legacy.target_exposure_pct,
            max_new_exposure=max_new_exposure,
            reduce_only=reduce_only,
            risk_level=RiskLevel(legacy.risk_level.value),
            reason_codes=tuple(),
            calibration_state=calibration_state,
            calibration_artifact_id=calibration_artifact_id,
            calibration_ece=calibration_ece,
            ood_state=ood_state,
            ood_score=ood_score,
            regime_state=regime_state,
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
        calibration_state: EvidenceState = EvidenceState.UNKNOWN,
        calibration_artifact_id: str | None = None,
        calibration_ece: float = 1.0,
        ood_state: EvidenceState = EvidenceState.UNKNOWN,
        ood_score: float = 1.0,
        regime_state: EvidenceState = EvidenceState.UNKNOWN,
        regime_entropy: float = 1.0,
        interval_width: float = 1.0,
        warnings: tuple[str, ...] = (),
    ) -> UnifiedRiskDecision:
        """Build a UnifiedRiskDecision from the canonical forecast RiskDecision.

        Exposure fields come from the forecast decision; execution policy
        fields and evidence states are injected as EXPLICIT arguments.
        NO auto-defaults for calibration/OOD/regime — callers MUST provide
        evidence or explicitly mark UNKNOWN/MISSING/STALE.
        """
        now = datetime.now(UTC)

        # If forecast doesn't approve, zero out exposure and force reduce_only
        allowed_exposure = forecast_decision.allowed_exposure
        if not forecast_decision.approved:
            allowed_exposure = 0.0
            reduce_only = True
            max_new_exposure = 0.0

        return UnifiedRiskDecision(
            decision_id=forecast_decision.decision_id,
            forecast_fingerprint=forecast_decision.forecast_fingerprint,
            model_artifact_id=forecast_decision.model_artifact_id,
            requested_target_exposure=forecast_decision.requested_exposure,
            allowed_target_exposure=allowed_exposure,
            max_new_exposure=max_new_exposure,
            reduce_only=reduce_only,
            risk_level=RiskLevel.MEDIUM,
            reason_codes=forecast_decision.reason_codes,
            calibration_state=calibration_state,
            calibration_artifact_id=calibration_artifact_id,
            calibration_ece=calibration_ece,
            ood_state=ood_state,
            ood_score=ood_score,
            regime_state=regime_state,
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
        calibration_state: EvidenceState = EvidenceState.UNKNOWN,
        calibration_artifact_id: str | None = None,
        calibration_ece: float = 1.0,
        ood_state: EvidenceState = EvidenceState.UNKNOWN,
        ood_score: float = 1.0,
        regime_state: EvidenceState = EvidenceState.UNKNOWN,
        regime_entropy: float = 1.0,
        interval_width: float = 1.0,
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
            calibration_state=calibration_state,
            calibration_artifact_id=calibration_artifact_id,
            calibration_ece=calibration_ece,
            ood_state=ood_state,
            ood_score=ood_score,
            regime_state=regime_state,
            regime_entropy=regime_entropy,
            interval_width=interval_width,
        )
        forecast_unified = RiskDecisionAdapter.from_forecast(
            forecast,
            max_new_exposure=legacy_unified.max_new_exposure,
            reduce_only=legacy_unified.reduce_only,
            calibration_state=calibration_state,
            calibration_artifact_id=calibration_artifact_id,
            calibration_ece=calibration_ece,
            ood_state=ood_state,
            ood_score=ood_score,
            regime_state=regime_state,
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
                calibration_ece=forecast_unified.calibration_ece,
                ood_state=forecast_unified.ood_state,
                ood_score=forecast_unified.ood_score,
                regime_state=forecast_unified.regime_state,
                regime_entropy=forecast_unified.regime_entropy,
                interval_width=forecast_unified.interval_width,
                created_at=forecast_unified.created_at,
                warnings=forecast_unified.warnings + legacy_unified.warnings,
            )
        return forecast_unified


__all__ = [
    "RiskLevel",
    "EvidenceState",
    "UnifiedRiskDecision",
    "RiskDecisionAdapter",
]
