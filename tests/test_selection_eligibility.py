import copy
import hashlib
import json

import pytest

from trading_agent.backtest.selection_eligibility import (
    assess_validation_candidates,
    freeze_selection_contract,
    verify_selection_contract,
    verify_validation_reports,
)


def contract():
    # Synthetic test policy; not a production threshold recommendation.
    return freeze_selection_contract(min_validation_trades=3, min_validation_sharpe=0)


def candidate(fast=5, trades=3, sharpe=1):
    return dict(
        params={"fast_period": fast},
        cost_scenario="1x",
        val_metrics={"total_trades": trades, "sharpe": sharpe},
    )


@pytest.mark.parametrize("trades", [0, 1, None, True, -1, 3.0])
def test_ineligible_activity_abstains(trades):
    result = assess_validation_candidates(contract(), [candidate(trades=trades)])
    assert result["decision"] == "ABSTAIN"
    assert result["selected"] is None
    assert result["candidates"][0]["reasons"]
    assert result["live_authorized"] is False


@pytest.mark.parametrize("sharpe", [None, float("nan"), float("inf"), True, 0, -1])
def test_invalid_or_nonpositive_score_abstains(sharpe):
    assert (
        assess_validation_candidates(contract(), [candidate(sharpe=sharpe)])["decision"]
        == "ABSTAIN"
    )


def test_no_trade_winner_does_not_force_losing_active_candidate():
    result = assess_validation_candidates(
        contract(), [candidate(trades=0, sharpe=0), candidate(fast=10, sharpe=-1)]
    )
    assert result["decision"] == "ABSTAIN"


def test_replay_ties_and_oos_isolation():
    items = [candidate(fast=10), candidate(fast=5)]
    expected = assess_validation_candidates(contract(), items)
    items[0]["oos_sharpe"] = 1000
    items[1]["val_sharpe"] = -1000
    assert assess_validation_candidates(contract(), list(reversed(items))) == expected
    assert expected["decision"] == "SELECT"
    items[0]["params"]["fast_period"] = 999
    assert expected["selected"]["params"]["fast_period"] != 999


def test_contract_tampering_rejected_even_with_valid_hash():
    original = contract()
    forged = copy.deepcopy(original)
    forged["tie_break"] = "oos_sharpe"
    body = {key: value for key, value in forged.items() if key != "fingerprint"}
    forged["fingerprint"] = hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    with pytest.raises(ValueError):
        verify_selection_contract(forged)


@pytest.mark.parametrize(
    "trades,sharpe", [(0, 0), (True, 0), (1, -1), (1, float("nan"))]
)
def test_invalid_contract_thresholds(trades, sharpe):
    with pytest.raises(ValueError):
        freeze_selection_contract(
            min_validation_trades=trades, min_validation_sharpe=sharpe
        )


def test_empty_and_duplicate_candidates():
    assert assess_validation_candidates(contract(), [])["decision"] == "ABSTAIN"
    with pytest.raises(ValueError, match="duplicate"):
        assess_validation_candidates(contract(), [candidate(), candidate()])


def test_missing_validation_does_not_use_oos():
    item = candidate()
    item.pop("val_metrics")
    item["oos_metrics"] = {"total_trades": 100, "sharpe": 10}
    assert assess_validation_candidates(contract(), [item])["decision"] == "ABSTAIN"


def test_selects_highest_eligible_score_not_highest_inactive_score():
    result = assess_validation_candidates(
        contract(),
        [
            candidate(fast=5, trades=0, sharpe=10),
            candidate(fast=10, sharpe=2),
            candidate(fast=15, sharpe=1),
        ],
    )
    assert result["selected"]["params"] == {"fast_period": 10}
    assert result["live_authorized"] is False


@pytest.mark.parametrize(
    "forgery",
    [
        {"cell_id": "forged"},
        {"cost_scenario": "2x"},
        {"commission": 0},
        {"slippage": 0},
        {"fault_profile": "latency"},
        {"signal_delay_bars": 1},
        {"signal_delay_bars": True},
        {"simulation_window": (0, 21)},
        {"simulation_window": (11, 20)},
        {"commit_sha": "c" * 40},
        {"data_manifest_sha": "c" * 64},
    ],
)
def test_validation_artifact_reconstruction_branch(tmp_path, monkeypatch, forgery):
    from dataclasses import replace
    from types import SimpleNamespace
    from trading_agent.backtest import tournament

    item, path = report_candidate(tmp_path)
    cell = tournament.EvaluationCellSpec(
        strategy_id="ma_crossover", symbol="BTC/USDT", params=item["params"]
    )
    artifact = tournament.EvaluationArtifact(
        cell_id=cell.cell_id,
        status="COMPLETED",
        descriptor_id="synthetic",
        strategy_id=cell.strategy_id,
        symbol=cell.symbol,
        timeframe=cell.timeframe,
        params_hash=cell.params_hash,
        cost_scenario="1x",
        fault_profile="none",
        commission=cell.cost_scenario.commission,
        slippage=cell.cost_scenario.slippage,
        data_manifest_sha="a" * 64,
        commit_sha="b" * 40,
        report_path=str(path),
        metrics=item["val_metrics"],
        execution_health={},
        measurement_window=(10, 20),
        simulation_window=(0, 20),
    )
    item["validation_source"]["artifact"] = artifact.to_dict()
    spec = SimpleNamespace(
        strategy_id=cell.strategy_id,
        symbol=cell.symbol,
        timeframe=cell.timeframe,
        cost_scenarios=(cell.cost_scenario,),
    )
    calls = []

    def rebuild(*args, **kwargs):
        calls.append(kwargs)
        return artifact

    monkeypatch.setattr(tournament, "_artifact_from_report", rebuild)
    result = verify_validation_reports(
        contract(),
        [item],
        root=tmp_path,
        validation_window=(10, 20),
        expected_spec=spec,
        expected_commit=artifact.commit_sha,
        expected_manifest_sha=artifact.data_manifest_sha,
    )
    assert result["decision"] == "SELECT"
    assert calls[0]["measurement_window"] == (10, 20)
    # Fresh content hash is valid: semantic identity, not merely hashing, must reject it.
    item["validation_source"]["artifact"] = replace(artifact, **forgery).to_dict()
    with pytest.raises(ValueError):
        verify_validation_reports(
            contract(),
            [item],
            root=tmp_path,
            validation_window=(10, 20),
            expected_spec=spec,
            expected_commit=artifact.commit_sha,
            expected_manifest_sha=artifact.data_manifest_sha,
        )
    item["validation_source"]["artifact"]["params_hash"] = "forged"
    with pytest.raises(ValueError, match="content hash mismatch"):
        verify_validation_reports(
            contract(),
            [item],
            root=tmp_path,
            validation_window=(10, 20),
            expected_spec=spec,
        )


def report_candidate(tmp_path):
    item = candidate()
    path = tmp_path / "validation.json"
    raw = json.dumps(
        {"measurement_window": [10, 20], "metrics": item["val_metrics"]}
    ).encode()
    path.write_bytes(raw)
    item["validation_source"] = {
        "report_path": path.name,
        "report_sha256": hashlib.sha256(raw).hexdigest(),
    }
    return item, path


def test_validation_source_select_replay(tmp_path):
    item, _ = report_candidate(tmp_path)
    actual = verify_validation_reports(
        contract(), [item], root=tmp_path, validation_window=(10, 20)
    )
    assert actual == assess_validation_candidates(contract(), [item])


@pytest.mark.parametrize(
    "mutation",
    [
        "bytes",
        "metric",
        "window",
        "missing",
        "escape",
        "unavailable",
        "invalid_json",
        "scalar",
        "missing_path",
    ],
)
def test_validation_source_tampering_refused(tmp_path, mutation):
    item, path = report_candidate(tmp_path)
    window = (10, 20)
    if mutation == "bytes":
        path.write_text("{}")
    elif mutation == "metric":
        item["val_metrics"]["sharpe"] = 99
    elif mutation == "window":
        window = (20, 30)
    elif mutation == "missing":
        item.pop("validation_source")
    elif mutation == "escape":
        item["validation_source"]["report_path"] = "../outside.json"
    elif mutation == "unavailable":
        item["validation_source"]["report_path"] = "missing.json"
    elif mutation == "missing_path":
        item["validation_source"].pop("report_path")
    else:
        raw = b"invalid JSON" if mutation == "invalid_json" else b"[]"
        path.write_bytes(raw)
        item["validation_source"]["report_sha256"] = hashlib.sha256(raw).hexdigest()
    with pytest.raises(ValueError):
        verify_validation_reports(
            contract(), [item], root=tmp_path, validation_window=window
        )
