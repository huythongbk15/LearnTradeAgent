"""X2a locks the contrast and refuses a market run while the entry rule is unset."""

import json
from pathlib import Path

import pytest

from trading_agent.research.btc_context_features import (
    CONTRACT_VERSION,
    FEATURE_NAMES,
    contract_fingerprint,
)
from trading_agent.research.btc_context_prereg import (
    C1_FEATURES,
    C2_FEATURES,
    CLOCK_LAST_UTC,
    CLOCK_ROWS,
    COSTS,
    EMBARGO_BARS,
    ENTRY_RULE_ID,
    FIRST_DECISION_INDEX,
    FOLDS,
    HOLDOUT_START_UTC,
    INPUTS,
    MEASUREMENT_START,
    MEASUREMENT_STOP,
    MIN_TRADES,
    PURGE_BARS,
    TARGETS,
    arm_features,
    cell_budget_seconds,
    cell_count,
    execution_authority,
    keep_added_arm,
)

FIXTURE = Path(__file__).parent / "fixtures/btc_context_admission_metadata.json"


def test_feature_sets_match_the_x1_catalog():
    assert C1_FEATURES == FEATURE_NAMES[:3]
    assert C2_FEATURES == FEATURE_NAMES[3:]
    assert arm_features("A0") == ()
    assert arm_features("A1") == C1_FEATURES
    assert arm_features("A2") == FEATURE_NAMES
    assert "residual_sum_24" not in arm_features("A1")


def test_budget_is_eighteen_cells_and_not_the_old_envelope():
    assert (TARGETS, FOLDS, COSTS) == (
        ("ETH/USDT", "BNB/USDT"),
        1,
        ("1x", "2x", "slip_stress"),
    )
    assert cell_count() == 18
    assert cell_budget_seconds() == 10800
    assert cell_budget_seconds() != 2100


def test_clock_ends_before_holdout_and_warmup_covers_the_residual():
    assert CLOCK_ROWS == 25198
    assert CLOCK_LAST_UTC < HOLDOUT_START_UTC
    assert FIRST_DECISION_INDEX == 216
    assert MEASUREMENT_START == 216
    assert MEASUREMENT_STOP == CLOCK_ROWS
    assert PURGE_BARS == EMBARGO_BARS == 216


def test_inputs_match_the_x0_receipts():
    metadata = json.loads(FIXTURE.read_text())
    assert metadata["evidence_class"] == "RECORDED_METADATA_NOT_MARKET_DATA"
    alignment = metadata["alignment"]
    quality = metadata["quality"]
    for item in alignment["files"].values():
        assert item["rows"] == CLOCK_ROWS
        assert item["timestamps_identical_to_btc"] is True
    by_path = {item["path"]: item for item in quality["files"]}
    for spec in INPUTS.values():
        if spec["role"] == "context_not_target":
            continue
        receipt = by_path[spec["path"]]
        assert receipt["sha256"] == spec["sha256"]
        assert receipt["accepted"] is True


def test_frozen_rule_still_refuses_a_market_run():
    authority = execution_authority()
    assert ENTRY_RULE_ID == "H-OWN24-CONTEXT-01"
    assert authority["market_run"] is False
    assert authority["live_authorized"] is False
    assert authority["qualification_possible_on_this_bundle"] is False
    assert authority["reason"] == "PATH_READY_RUN_NOT_STARTED"
    assert authority["blockers"] == ()
    assert authority["feature_contract"] == CONTRACT_VERSION
    assert authority["feature_fingerprint"] == contract_fingerprint()
    assert len(authority["rule_fingerprint"]) == 64


def test_keep_rule_is_directional_and_not_a_qualification():
    common = dict(
        trades=MIN_TRADES,
        sharpe=0.01,
        marked_net_return=0.02,
        previous_marked_net_return=0.01,
        max_drawdown=0.05,
        previous_max_drawdown=0.05,
        same_execution_path=True,
    )
    assert keep_added_arm(**common) == "IMPROVES"
    assert keep_added_arm(**(common | {"trades": 29})) == "INCONCLUSIVE"
    assert keep_added_arm(**(common | {"same_execution_path": False})) == "INCONCLUSIVE"
    assert keep_added_arm(**(common | {"marked_net_return": 0.01})) == "NO_INCREMENT"
    assert keep_added_arm(**(common | {"max_drawdown": 0.06})) == "NO_INCREMENT"
    assert keep_added_arm(**(common | {"sharpe": 0.0})) == "NO_CONFIRMATION"
    assert keep_added_arm(**(common | {"marked_net_return": -0.01, "previous_marked_net_return": -0.02})) == "NO_CONFIRMATION"


@pytest.mark.parametrize("field", [
    "sharpe", "marked_net_return", "previous_marked_net_return",
    "max_drawdown", "previous_max_drawdown",
])
@pytest.mark.parametrize("invalid", [float("nan"), float("inf"), -float("inf")])
def test_nonfinite_evidence_cannot_improve(field, invalid):
    values = dict(
        trades=30, sharpe=1.0, marked_net_return=0.02,
        previous_marked_net_return=0.01, max_drawdown=0.04,
        previous_max_drawdown=0.05, same_execution_path=True,
    )
    values[field] = invalid
    assert keep_added_arm(**values) == "INCONCLUSIVE"
