"""Gate contract tests are synthetic, not a qualified strategy."""

from copy import deepcopy
from types import SimpleNamespace as NS

import pytest

from trading_agent.backtest.campaign_evidence import content_hash
from trading_agent.backtest.native_gate_contract import (
    freeze_native_gate_contract,
    threshold_for,
    validate_native_evidence,
    verify_native_contract,
)


def fixture(holdout=False):
    contract = freeze_native_gate_contract(
        NS(effective_min_oos_trades=30), run_holdout=holdout
    )
    rows = []
    for gate in contract["gates"]:
        threshold = threshold_for(gate, [10, 20, 30])
        observed = threshold + 1 if gate["comparison"] in {">", ">="} else threshold / 2
        rows.append(
            dict(
                gate_id=gate["gate_id"],
                threshold=threshold,
                observed=observed,
                comparison=gate["comparison"],
                policy_version="v1",
                verdict="PASS",
            )
        )
    return contract, rows


@pytest.mark.parametrize("holdout", [False, True])
def test_gate_set_frozen_before_observations(holdout):
    contract, rows = fixture(holdout)
    assert len(contract["gates"]) == (22 if holdout else 20)
    assert not validate_native_evidence(contract, rows, [10, 20, 30])


@pytest.mark.parametrize(
    "mutation", ["missing", "duplicate", "threshold", "version", "verdict", "formula"]
)
def test_native_drift_rejected(mutation):
    contract, rows = fixture()
    if mutation == "missing":
        rows.pop()
    elif mutation == "duplicate":
        rows[-1] = rows[0]
    elif mutation == "threshold":
        rows[0]["threshold"] = 99
    elif mutation == "version":
        rows[0]["policy_version"] = "new"
    elif mutation == "verdict":
        rows[0]["verdict"] = "FAIL"
    else:
        rows[-1]["threshold"] = 0.32
    with pytest.raises(ValueError):
        validate_native_evidence(contract, rows, [10, 20, 30])


def test_rehash_does_not_register_different_contract():
    contract, _ = fixture()
    forged = deepcopy(contract)
    forged["gates"][0]["threshold"] = -99.0
    forged["fingerprint"] = content_hash(
        {key: value for key, value in forged.items() if key != "fingerprint"}
    )
    with pytest.raises(ValueError):
        verify_native_contract(forged)


def test_invalid_measurement_remains_diagnostic_failure():
    contract, rows = fixture()
    rows[0].update(observed=None, verdict="INVALID")
    failed = validate_native_evidence(contract, rows, [10, 20, 30])
    assert failed[0]["scope"] == "native"
    assert failed[0]["verdict"] == "INVALID"
