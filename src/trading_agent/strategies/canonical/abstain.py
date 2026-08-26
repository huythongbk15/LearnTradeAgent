"""Canonical abstain / NO_TRADE strategy (STR-0104).

``AbstainStrategy`` is the canonical "do nothing" implementation of the
``ForecastStrategy`` contract.  Its forecast is engineered so that the
downstream deterministic risk policy (:class:`ForecastRiskPolicy`) must yield
``allowed_exposure == 0``:

- ``expected_excess_return == 0.0`` with degenerate bounds ``[0, 0]``
  → the interval crosses zero and carries no edge;
- ``direction_probability is None`` (no directional claim);
- metadata marks the canonical action explicitly as ``NO_TRADE``.

The forecast is fully deterministic per observation: ``generated_at`` mirrors
``observation.observed_at``, so the same observation always produces the same
forecast fingerprint (S1 exit gate: determinism).
"""

from __future__ import annotations

from trading_agent.research.calibration import CalibrationState
from trading_agent.research.forecast import Forecast, MarketObservation

#: Metadata key marking the canonical no-trade action.
CANONICAL_ACTION_NO_TRADE = "NO_TRADE"

#: Default artifact id for the canonical abstainer (not content-addressed:
#: it has no trained parameters to address).
ABSTAIN_ARTIFACT_ID = "canonical.abstain.v1"


class AbstainStrategy:
    """Canonical NO_TRADE strategy — safe default for any pipeline stage."""

    def __init__(
        self,
        *,
        model_artifact_id: str = ABSTAIN_ARTIFACT_ID,
        horizon_bars: int = 1,
    ) -> None:
        if horizon_bars <= 0:
            raise ValueError("horizon_bars must be positive")
        if not model_artifact_id.strip():
            raise ValueError("model_artifact_id is required")
        self._model_artifact_id = model_artifact_id
        self._horizon_bars = int(horizon_bars)

    def forecast(self, observation: MarketObservation) -> Forecast:
        """Emit the canonical no-edge forecast for *observation*."""
        return Forecast(
            expected_excess_return=0.0,
            horizon=self._horizon_bars,
            lower_bound=0.0,
            upper_bound=0.0,
            direction_probability=None,
            calibration_state=CalibrationState.CALIBRATED,
            ood_score=0.0,
            model_artifact_id=self._model_artifact_id,
            generated_at=observation.observed_at,
            metadata={
                "canonical_action": CANONICAL_ACTION_NO_TRADE,
                "symbol": observation.symbol,
                "strategy_id": "abstain",
            },
        )
