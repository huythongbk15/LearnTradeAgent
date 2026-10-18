"""Safety contract for the fixed, non-submitting BNB admission sandbox."""

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from dataclasses import replace

import polars as pl
import pytest

from scripts import run_bnb_operational_sandbox as sandbox


def test_output_is_new_and_confined(tmp_path, monkeypatch):
    monkeypatch.setattr(sandbox, "ROOT", tmp_path)
    path = sandbox.claim_output(tmp_path / "data/run")
    sandbox.write_new(path / "marker.json", {"preserve": True})
    with pytest.raises(FileExistsError):
        sandbox.claim_output(path)
    assert json.loads((path / "marker.json").read_text()) == {"preserve": True}
    with pytest.raises(ValueError):
        sandbox.claim_output(tmp_path / "outside")


def test_symlink_escape_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(sandbox, "ROOT", tmp_path)
    (tmp_path / "data").mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (tmp_path / "data/link").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError):
        sandbox.claim_output(tmp_path / "data/link/run")


def test_json_evidence_cannot_be_overwritten(tmp_path):
    path = tmp_path / "receipt.json"
    sandbox.write_new(path, {"first": True})
    with pytest.raises(FileExistsError):
        sandbox.write_new(path, {"first": False})
    assert json.loads(path.read_text()) == {"first": True}


def test_network_guard_raises():
    with pytest.raises(RuntimeError, match="Network forbidden"):
        sandbox.deny_network("example.invalid", 443)


def test_execution_bar_and_future_cannot_change_signal():
    closes = [100 + i * 0.03 for i in range(302)]
    frame = pl.DataFrame(
        {
            "timestamp": [
                datetime(2024, 1, 1, tzinfo=UTC) + timedelta(hours=4 * i)
                for i in range(302)
            ],
            "open": closes,
            "high": [c + 1 for c in closes],
            "low": [c - 1 for c in closes],
            "close": closes,
            "volume": [100.0] * len(closes),
        }
    )
    changed = (
        frame.with_row_index()
        .with_columns(
            [
                pl.when(pl.col("index") >= 299).then(9999).otherwise(pl.col(c)).alias(c)
                for c in ("open", "high", "low", "close")
            ]
        )
        .drop("index")
    )
    assert (
        sandbox.canonical_signals(frame, start_index=299, end_index=300)[299]
        == sandbox.canonical_signals(changed, start_index=299, end_index=300)[299]
    )


def test_case_is_not_configurable_into_new_research():
    assert sandbox.PARAMS == {
        "fast_period": 20,
        "slow_period": 80,
        "adx_period": 14,
        "adx_threshold": 25,
    }
    assert sandbox.SYMBOL == "BNB/USDT"
    assert sandbox.STOP - sandbox.START == 1080


@pytest.mark.parametrize("wrong_action", [False, True])
def test_parity_requires_exact_action_and_execution_bar(
    tmp_path, monkeypatch, wrong_action
):
    rows = [
        {"execution_bar": 1, "action": "BUY"},
        {"execution_bar": 2, "action": "SELL"},
    ]
    if wrong_action:
        rows[1]["action"] = "HOLD"
    report = tmp_path / "report.json"
    report.write_text(
        json.dumps(
            {"measurement_window": [1, 3], "execution_funnel": {"records": rows}}
        )
    )
    monkeypatch.setattr(sandbox, "REPORT", report)
    monkeypatch.setattr(sandbox, "REPORT_SHA", sandbox.digest(report))
    monkeypatch.setattr(sandbox, "START", 1)
    monkeypatch.setattr(sandbox, "STOP", 3)
    monkeypatch.setattr(
        sandbox, "canonical_signals", lambda *args, **kwargs: [1, -1, 0]
    )
    if wrong_action:
        with pytest.raises(ValueError, match="parity failed"):
            sandbox.canonical_execution_actions(None)
    else:
        assert sandbox.canonical_execution_actions(None) == [1, -1]


def test_parquet_datetime_geometry_not_epoch_guessing():
    start = datetime(2024, 1, 1)
    frame = pl.DataFrame({"timestamp": [start, start + timedelta(hours=4)]})
    assert sandbox.validated_times(frame) == [
        start.replace(tzinfo=UTC),
        (start + timedelta(hours=4)).replace(tzinfo=UTC),
    ]
    with pytest.raises(ValueError, match="geometry"):
        sandbox.validated_times(pl.DataFrame({"timestamp": [start, start]}))
    with pytest.raises(ValueError, match="datetime"):
        sandbox.validated_times(pl.DataFrame({"timestamp": [0, 14400000]}))


def test_permission_uses_lifecycle_enum_not_planner_enum():
    from trading_agent.execution.canonical.order_planner import (
        ExposureEffect as PlannerEffect,
    )
    from trading_agent.execution.lifecycle.lifecycle import ExposureEffect

    for effect in PlannerEffect:
        mapped = sandbox.permission_effect(SimpleNamespace(exposure_effect=effect))
        assert isinstance(mapped, ExposureEffect)
        assert mapped.name == effect.name


def test_unknown_evidence_stale_quote_and_kill_switch_remain_blocking():
    from trading_agent.execution.canonical.risk_decision import (
        EvidenceState,
        RiskLevel,
        UnifiedRiskDecision,
    )
    from trading_agent.execution.lifecycle.lifecycle import (
        ExecutionHealth,
        ExposureEffect,
        TrustedPrice,
    )
    from trading_agent.execution.permission import (
        PermissionContext,
        PermissionReason,
        evaluate_order_permission,
    )

    now = datetime.now(UTC)
    risk = UnifiedRiskDecision(
        decision_id="unit-fixture",
        forecast_fingerprint="unit-fixture",
        model_artifact_id="unit-fixture",
        requested_target_exposure=0.25,
        allowed_target_exposure=0.25,
        max_new_exposure=0.25,
        reduce_only=False,
        risk_level=RiskLevel.HIGH,
        reason_codes=(),
        calibration_state=EvidenceState.UNKNOWN,
        calibration_artifact_id=None,
        calibration_ece=1,
        ood_state=EvidenceState.UNKNOWN,
        ood_score=1,
        regime_state=EvidenceState.UNKNOWN,
        regime_entropy=1,
        interval_width=1,
        created_at=now,
    )
    context = PermissionContext(
        execution_health=ExecutionHealth.NORMAL,
        exposure_effect=ExposureEffect.INCREASE,
        risk_decision=risk,
        trusted_price=TrustedPrice(500, now, now),
        order_size=1,
    )
    result = evaluate_order_permission(context)
    assert (
        not result.allowed()
        and result.reason is PermissionReason.MISSING_CALIBRATION_EVIDENCE
    )
    stale = replace(
        context, trusted_price=TrustedPrice(500, now - timedelta(hours=1), now)
    )
    assert evaluate_order_permission(stale).reason is PermissionReason.STALE_MARKET_DATA
    stopped = replace(context, kill_switch_active=True)
    assert (
        evaluate_order_permission(stopped).reason
        is PermissionReason.KILL_SWITCH_INCREASE
    )
