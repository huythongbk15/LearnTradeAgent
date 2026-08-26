"""Tests for BacktestReportV2 schema, semantic validator and JSON Schema artifact."""

from __future__ import annotations

import copy
import json
import math

import pytest

from trading_agent.backtest import report_v2
from trading_agent.backtest.report_v2 import (
    ReportValidationError,
    ensure_valid_report_v2,
    export_json_schema,
    load_json_schema,
    validate_report_v2,
)

_SHA = "sha256:" + "11" * 32
_SHA_FEATURES = "sha256:" + "22" * 32


def _simulation(**overrides: object) -> dict[str, object]:
    evidence: dict[str, object] = {
        "time_source": "simulated_bar",
        "entry_time": "2024-01-03T00:00:00+00:00",
        "exit_time": "2024-01-05T00:00:00+00:00",
        "entry_bar_index": 10,
        "exit_bar_index": 58,
        "entry_reference_price": 100.0,
        "exit_reference_price": 102.0,
        "holding_bars": 48,
        "mae_pct": -1.5,
        "mfe_pct": 3.0,
    }
    evidence.update(overrides)
    return evidence


def _valid_report() -> dict[str, object]:
    return {
        "schema_version": 2,
        "report_type": "full_system_backtest",
        "run_id": "r1",
        "status": "passed",
        "symbol": "BTC/USDT",
        "timeframe": "1h",
        "final_equity": 101_250.0,
        "total_return_pct": 1.25,
        "sharpe": 0.42,
        "max_drawdown_pct": 2.5,
        "total_trades": 1,
        "win_rate_pct": 100.0,
        "commit_sha": "abc123",
        "data_manifest_id": _SHA,
        "feature_artifact_id": _SHA_FEATURES,
        "active_config": {
            "config_id": "cfg-1",
            "provenance": {"commit_sha": "abc123"},
        },
        "execution_health": {
            "status": "normal",
            "unknown_orders": 0,
            "manual_interventions": 0,
            "unprotected_positions": [],
            "trade_evidence_complete": True,
        },
        "simulation_window": {"bar_count": 100},
        "data_quality": {"window": {"accepted": True}},
        "metrics": {"cagr_pct": 1.0},
        "cost_attribution": {
            "complete": True,
            "reconciliation_error": 0.0,
            "gross_alpha": 1250.0,
        },
        "benchmarks": {"fixed_allocation_buy_and_hold": {"total_return_pct": 0.5}},
        "trades": [
            {
                "side": "long",
                "pnl": 25.0,
                "metadata": {"simulation": _simulation()},
            }
        ],
    }


def test_valid_report_has_no_violations() -> None:
    assert validate_report_v2(_valid_report()) == []


def test_ensure_valid_accepts_and_raises() -> None:
    ensure_valid_report_v2(_valid_report())
    broken = _valid_report()
    broken["schema_version"] = 1
    with pytest.raises(ReportValidationError):
        ensure_valid_report_v2(broken)


def test_non_finite_numbers_are_flagged_anywhere() -> None:
    report = _valid_report()
    report["sharpe"] = float("nan")
    report["equity_curve"] = [[0, math.inf]]
    violations = validate_report_v2(report)
    assert any("report.sharpe" in v for v in violations)
    assert any("report.equity_curve[0][1]" in v for v in violations)


def test_wall_clock_trade_ledger_is_rejected() -> None:
    report = _valid_report()
    trade = report["trades"][0]
    assert isinstance(trade, dict)
    metadata = trade["metadata"]
    assert isinstance(metadata, dict)
    metadata["simulation"] = _simulation(time_source="processing_wall_clock")
    violations = validate_report_v2(report)
    assert any("time_source" in v for v in violations)


def test_trade_without_simulation_evidence_is_rejected() -> None:
    report = _valid_report()
    report["trades"] = [{"side": "long"}]
    assert any("missing metadata.simulation" in v for v in validate_report_v2(report))


def test_naive_timestamps_in_ledger_are_rejected() -> None:
    report = _valid_report()
    trade = report["trades"][0]
    assert isinstance(trade, dict)
    metadata = trade["metadata"]
    assert isinstance(metadata, dict)
    metadata["simulation"] = _simulation(entry_time="2024-01-03T00:00:00")
    assert any("tz-aware" in v for v in validate_report_v2(report))


def test_placeholder_manifest_ids_are_rejected() -> None:
    report = _valid_report()
    report["data_manifest_id"] = "sha256:data"
    assert any("data_manifest_id" in v for v in validate_report_v2(report))


def test_cost_reconciliation_error_beyond_tolerance_rejected() -> None:
    report = _valid_report()
    costs = report["cost_attribution"]
    assert isinstance(costs, dict)
    costs["reconciliation_error"] = 0.5
    assert any("reconciliation_error" in v for v in validate_report_v2(report))
    costs["complete"] = False
    assert any("complete" in v for v in validate_report_v2(report))


def test_failed_execution_health_blocks_passed_status() -> None:
    report = _valid_report()
    health = report["execution_health"]
    assert isinstance(health, dict)
    health["unknown_orders"] = 2
    health["unprotected_positions"] = ["BTC/USDT"]
    violations = validate_report_v2(report)
    assert any("unknown_orders" in v for v in violations)
    assert any("unprotected_positions" in v for v in violations)


def test_missing_required_keys_are_listed_once_each() -> None:
    report = _valid_report()
    del report["cost_attribution"]
    del report["benchmarks"]
    violations = validate_report_v2(report)
    assert any("cost_attribution" in v for v in violations)
    assert any("benchmarks" in v for v in violations)


def test_exported_json_schema_matches_committed_artifact() -> None:
    assert json.dumps(export_json_schema(), indent=2) + "\n" == json.dumps(
        load_json_schema(), indent=2
    ) + "\n"


def test_json_schema_validates_the_reference_report() -> None:
    jsonschema = pytest.importorskip("jsonschema")
    validator_cls = getattr(jsonschema, "Draft202012Validator")
    validator = validator_cls(load_json_schema())
    errors = list(validator.iter_errors(_valid_report()))
    assert errors == []


def test_json_schema_flags_wrong_schema_version() -> None:
    jsonschema = pytest.importorskip("jsonschema")
    validator_cls = getattr(jsonschema, "Draft202012Validator")
    validator = validator_cls(load_json_schema())
    report = copy.deepcopy(_valid_report())
    report["schema_version"] = 99
    errors = list(validator.iter_errors(report))
    assert len(errors) == 1


def test_module_exports_are_stable() -> None:
    assert report_v2.SCHEMA_VERSION == 2
    assert "schema_version" in report_v2.REQUIRED_TOP_LEVEL_KEYS
