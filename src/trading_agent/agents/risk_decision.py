"""Risk decision semantics — typed exposure policy.

Splits the legacy ``max_position_size_pct`` into explicit fields so order
gates can distinguish:
- target exposure the portfolio should hold,
- maximum new exposure this order may create,
- whether the order must be reduce-only.

Invariant:
  uncertainty ↑ / risk ↑  →  max_new_exposure_pct must not increase.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class RiskLevel(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    EXTREME = "EXTREME"


@dataclass(frozen=True)
class RiskDecision:
    """Typed risk decision for order permissioning."""

    risk_level: RiskLevel = RiskLevel.MEDIUM
    target_exposure_pct: float = 0.0
    max_new_exposure_pct: float = 0.0
    reduce_only: bool = False
    warnings: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        # Clamp and validate fields without mutating frozen instance by
        # relying on the caller to construct valid values. We only validate
        # here to fail fast during development.
        if not isinstance(self.risk_level, RiskLevel):
            raise TypeError(
                f"risk_level must be RiskLevel, got {type(self.risk_level)}"
            )
        for name in ("target_exposure_pct", "max_new_exposure_pct"):
            val = getattr(self, name)
            if not (0.0 <= val <= 1.0):
                raise ValueError(f"{name} must be in [0,1], got {val}")
        if self.max_new_exposure_pct > self.target_exposure_pct + 1e-9:
            raise ValueError("max_new_exposure_pct cannot exceed target_exposure_pct")

    @classmethod
    def from_legacy(
        cls,
        max_position_size_pct: float | None,
        risk_level: str | None = None,
        warnings: list[str] | None = None,
    ) -> RiskDecision:
        """Create a RiskDecision from the legacy ``max_position_size_pct`` field.

        Preserves backward compatibility with older RiskManager outputs.
        """
        try:
            max_pos = float(max_position_size_pct or 0.0)
        except (TypeError, ValueError):
            max_pos = 0.0
        max_pos = max(0.0, min(1.0, max_pos))
        level = RiskLevel.MEDIUM
        if risk_level is not None:
            try:
                level = RiskLevel(str(risk_level).upper())
            except ValueError:
                level = RiskLevel.MEDIUM
        if level == RiskLevel.HIGH or level == RiskLevel.EXTREME:
            return cls(
                risk_level=level,
                target_exposure_pct=0.0,
                max_new_exposure_pct=0.0,
                reduce_only=True,
                warnings=tuple(warnings or []),
            )
        return cls(
            risk_level=level,
            target_exposure_pct=max_pos,
            max_new_exposure_pct=max_pos,
            reduce_only=False,
            warnings=tuple(warnings or []),
        )


__all__ = ["RiskLevel", "RiskDecision"]
