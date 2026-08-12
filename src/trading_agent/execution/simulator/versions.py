"""Versioned execution model — Wave A (Reality Gap Foundation).

Every execution simulation must carry the exact model versions that produced
it.  Backtest/paper/testnet evidence is NOT equivalent if the execution model
changed but the old artifact is still treated as comparable.
"""

from __future__ import annotations

# Bump these when the corresponding model behaviour changes.
EXECUTION_MODEL_VERSION = "2.0.0"
FILL_MODEL_VERSION = "1.0.0"
IMPACT_MODEL_VERSION = "1.0.0"
FEE_MODEL_VERSION = "1.0.0"
# Wave D — slice-selection algorithms (liquidity-aware TWAP / POV / MPC layer).
ALGORITHMS_VERSION = "1.0.0"


def model_versions() -> dict[str, str]:
    """Snapshot of every model version used by the simulator."""
    return {
        "execution_model_version": EXECUTION_MODEL_VERSION,
        "fill_model_version": FILL_MODEL_VERSION,
        "impact_model_version": IMPACT_MODEL_VERSION,
        "fee_model_version": FEE_MODEL_VERSION,
    }


def algorithms_versions() -> dict[str, str]:
    """Snapshot of execution-algorithm versions (Wave D)."""
    return {
        "algorithms_version": ALGORITHMS_VERSION,
    }
