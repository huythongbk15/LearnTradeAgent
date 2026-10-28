"""X2 preregistration for the BTC-context contrast. Not a run.

The entry rule is frozen. A market evaluation still needs a separate
authorization. This module does not grant it.
"""

from __future__ import annotations

import math

from trading_agent.research.btc_context_features import (
    CONTRACT_VERSION,
    FEATURE_NAMES,
    contract_fingerprint,
)
from trading_agent.research.btc_context_rule import (
    CANONICAL_WARMUP_BARS,
    ENTRY_RULE_ID,
    rule_fingerprint,
)

C1_FEATURES: tuple[str, ...] = FEATURE_NAMES[:3]
C2_FEATURES: tuple[str, ...] = FEATURE_NAMES[3:]

TARGETS: tuple[str, ...] = ("ETH/USDT", "BNB/USDT")
ARMS: tuple[str, ...] = ("A0", "A1", "A2")
COSTS: tuple[str, ...] = ("1x", "2x", "slip_stress")
FOLDS = 1
CELL_TIMEOUT_SECONDS = 600
WORKER_COUNT = 1
MAX_RETRIES = 0

INPUTS: dict[str, dict[str, str]] = {
    "BTC/USDT": {
        "role": "context_not_target",
        "path": "data/btc1h_preholdout_revision_20261024_01/input.parquet",
        "sha256": "45ba1b1bc6a91986eda445de8026cab9995fcabe011a735244a4c7cf0e0825ce",
        "exposure": "DEVELOPMENT_EXPOSED_NOT_CONFIRMATION",
    },
    "ETH/USDT": {
        "role": "target",
        "path": "data/existing_research_audit_20261008/ETH_USDT__1h__pre_holdout.parquet",
        "sha256": "50c1edff5ac70a62c5939eaf32c52d711af6aa95a4a75d9551c4d86494f9cb23",
        "exposure": "QUALITY_ACCEPTED_DEVELOPMENT_INPUT_NOT_CONFIRMATION",
    },
    "BNB/USDT": {
        "role": "target",
        "path": "data/existing_research_audit_20261008/BNB_USDT__1h__pre_holdout.parquet",
        "sha256": "2301c4ee9f3fecc791150f62d10c57b98fedb68679388bc3489ef4aaf6e42eb6",
        "exposure": "QUALITY_ACCEPTED_DEVELOPMENT_INPUT_NOT_CONFIRMATION",
    },
}

CLOCK_ROWS = 25198
CLOCK_FIRST_UTC = "2023-03-24T14:00:00+00:00"
CLOCK_LAST_UTC = "2026-02-06T11:00:00+00:00"
HOLDOUT_START_UTC = "2026-02-06T16:00:00+00:00"
HOLDOUT_END_UTC = "2026-08-05T16:00:00+00:00"

# Canonical signal windows are a fixed tail, so the first bar that can both
# enter and see an exit HOLD_BARS later is the warmup length, not 192.
FIRST_DECISION_INDEX = CANONICAL_WARMUP_BARS
SIMULATION_START = 0
SIMULATION_STOP = CLOCK_ROWS
MEASUREMENT_START = FIRST_DECISION_INDEX
MEASUREMENT_STOP = CLOCK_ROWS
PURGE_BARS = CANONICAL_WARMUP_BARS
EMBARGO_BARS = CANONICAL_WARMUP_BARS

MIN_TRADES = 30
PRIMARY_METRIC = "marked_net_usdt_return"
LAUNCHER_BLOCKERS: tuple[str, ...] = ()
CLOSED_RULES_NOT_CONTROLS: tuple[str, ...] = (
    "H-PULLBACK-01",
    "H-PULLBACK-TEMPORAL-01",
    "H-VOLBREAK-01",
    "H-LIQ-REVERSAL-01",
    "MA20/50",
    "RSI_FIXED",
)


def arm_features(arm: str) -> tuple[str, ...]:
    """Context columns an arm may read. Own price history is outside this list."""
    if arm == "A0":
        return ()
    if arm == "A1":
        return C1_FEATURES
    if arm == "A2":
        return C1_FEATURES + C2_FEATURES
    raise ValueError("arm must be A0, A1 or A2")


def cell_count() -> int:
    """One fixed config per target. Personalization cells are not included."""
    return len(TARGETS) * len(ARMS) * len(COSTS) * FOLDS


def cell_budget_seconds() -> int:
    return cell_count() * CELL_TIMEOUT_SECONDS


def execution_authority() -> dict[str, object]:
    """The rule is frozen. This module still does not authorize a run."""
    if ENTRY_RULE_ID in CLOSED_RULES_NOT_CONTROLS:
        raise RuntimeError("closed rule reused as the context control")
    return {
        "market_run": False,
        "live_authorized": False,
        "qualification_possible_on_this_bundle": False,
        "reason": "PATH_READY_RUN_NOT_STARTED",
        "blockers": LAUNCHER_BLOCKERS,
        "entry_rule_id": ENTRY_RULE_ID,
        "rule_fingerprint": rule_fingerprint(),
        "feature_contract": CONTRACT_VERSION,
        "feature_fingerprint": contract_fingerprint(),
    }


def keep_added_arm(
    *,
    trades: int,
    sharpe: float,
    marked_net_return: float,
    previous_marked_net_return: float,
    max_drawdown: float,
    previous_max_drawdown: float,
    same_execution_path: bool,
) -> str:
    """Preregistered label for one pair at one cost. Not an engine gate.

    ``IMPROVES`` is only a diagnostic label. It does not qualify a strategy
    and it does not authorize confirmation.
    """
    values = (
        sharpe, marked_net_return, previous_marked_net_return,
        max_drawdown, previous_max_drawdown,
    )
    if any(not math.isfinite(value) for value in values):
        return "INCONCLUSIVE"
    if trades < MIN_TRADES:
        return "INCONCLUSIVE"
    if not same_execution_path:
        return "INCONCLUSIVE"
    delta = marked_net_return - previous_marked_net_return
    if delta <= 0.0 or max_drawdown > previous_max_drawdown:
        return "NO_INCREMENT"
    if sharpe <= 0.0 or marked_net_return <= 0.0:
        return "NO_CONFIRMATION"
    return "IMPROVES"
