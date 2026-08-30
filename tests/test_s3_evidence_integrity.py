"""Focused regression tests for immutable S3 evaluation evidence."""

from __future__ import annotations

import json
from dataclasses import replace

import pytest

from trading_agent.backtest.nested_wfo import (
    _find_existing_outer_artifact,
    _outer_artifact_path,
    _persist_outer_artifact,
)
from trading_agent.backtest.tournament import EvaluationArtifact


def _artifact(*, status: str = "COMPLETED") -> EvaluationArtifact:
    return EvaluationArtifact(
        cell_id="rsi__BTCUSDT__1h__1x__ptest",
        status=status,
        descriptor_id="descriptor-v1",
        strategy_id="rsi",
        symbol="BTC/USDT",
        timeframe="1h",
        params_hash="params-v1",
        cost_scenario="1x",
        fault_profile="none",
        commission=0.001,
        slippage=0.0005,
        data_manifest_sha="data-v1",
        commit_sha="commit-v1",
        report_path="/evidence/report.json",
        metrics={"sharpe": 1.2, "net_pnl": 500.0},
        execution_health={
            "unknown_orders": 0,
            "manual_interventions": 0,
            "unprotected_positions": [],
        },
        failure_reasons=() if status == "COMPLETED" else ("execution_failed",),
        created_at="2026-01-01T00:00:00+00:00",
        measurement_window=(100, 200),
        simulation_window=(50, 200),
        signal_delay_bars=0,
    )


def test_evaluation_artifact_round_trip_and_tamper_detection() -> None:
    artifact = _artifact()
    payload = artifact.to_dict()

    rebuilt = EvaluationArtifact.from_dict(payload)
    assert rebuilt.artifact_id == artifact.artifact_id
    assert rebuilt.measurement_window == (100, 200)
    assert rebuilt.simulation_window == (50, 200)

    for field, value in (
        ("status", "FAILED"),
        ("symbol", "ETH/USDT"),
        ("commission", 0.01),
        ("report_path", "/tampered/report.json"),
        ("execution_health", {"unknown_orders": 1}),
        ("failure_reasons", ["tampered"]),
        ("measurement_window", [101, 200]),
    ):
        tampered = dict(payload)
        tampered[field] = value
        with pytest.raises(ValueError, match="content hash mismatch"):
            EvaluationArtifact.from_dict(tampered)


def test_outer_one_shot_persists_success_and_failure_exactly_once(tmp_path) -> None:
    freeze_id = "sha256:freeze-a"
    success = _persist_outer_artifact(tmp_path, freeze_id, "fold_001", _artifact())
    assert success.selection_freeze_id == freeze_id
    assert _find_existing_outer_artifact(tmp_path, freeze_id, "fold_001") == success

    # An idempotent replay returns the existing immutable artifact.
    assert (
        _persist_outer_artifact(tmp_path, freeze_id, "fold_001", _artifact()) == success
    )
    changed = replace(_artifact(), metrics={"sharpe": 9.9, "net_pnl": 500.0})
    with pytest.raises(ValueError, match="different content"):
        _persist_outer_artifact(tmp_path, freeze_id, "fold_001", changed)

    failed = _persist_outer_artifact(
        tmp_path, "sha256:freeze-b", "fold_002", _artifact(status="FAILED")
    )
    assert failed.status == "FAILED"
    assert (
        _find_existing_outer_artifact(tmp_path, "sha256:freeze-b", "fold_002") == failed
    )


def test_corrupt_outer_one_shot_fails_closed(tmp_path) -> None:
    freeze_id = "sha256:freeze-a"
    _persist_outer_artifact(tmp_path, freeze_id, "fold_001", _artifact())
    path = _outer_artifact_path(tmp_path, freeze_id, "fold_001")
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["status"] = "FAILED"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="content hash mismatch"):
        _find_existing_outer_artifact(tmp_path, freeze_id, "fold_001")
