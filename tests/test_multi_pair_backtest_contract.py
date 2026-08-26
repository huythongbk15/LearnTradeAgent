"""P0 contract tests for the machine-readable multi-pair backtest runner."""

from __future__ import annotations

import copy
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import multi_pair_1h_backtest as runner
from trading_agent.execution.canonical.instrument_registry import TEN_PAIR_1H_SYMBOLS


@pytest.fixture
def valid_report() -> dict[str, object]:
    return {
        "schema_version": 2,
        "report_type": "full_system_backtest",
        "status": "passed",
        "symbol": "BTC/USDT",
        "timeframe": "1h",
        "final_equity": 101_250.0,
        "total_return_pct": 1.25,
        "sharpe": 0.42,
        "max_drawdown_pct": 2.5,
        "total_trades": 3,
        "win_rate_pct": 50.0,
        "data_manifest_id": "sha256:data",
        "feature_artifact_id": "sha256:features",
        "execution_health": {
            "status": "normal",
            "unknown_orders": 0,
            "manual_interventions": 0,
            "unprotected_positions": [],
            "trade_evidence_complete": True,
        },
        "simulation_window": {"bar_count": 100},
        "active_config": {"config_id": "sha256:config"},
        "data_quality": {"window": {"accepted": True}},
        "metrics": {"cagr_pct": 1.0},
        "cost_attribution": {
            "complete": True,
            "reconciliation_error": 0.0,
        },
        "benchmarks": {"fixed_allocation_buy_and_hold": {}},
    }


def _write_child_report(cmd: list[str], report: object) -> Path:
    report_path = Path(cmd[cmd.index("--report-path") + 1])
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, allow_nan=False), encoding="utf-8")
    return report_path


def test_runner_uses_exactly_the_reviewed_ten_pair_registry() -> None:
    assert len(TEN_PAIR_1H_SYMBOLS) == 10
    assert len(set(TEN_PAIR_1H_SYMBOLS)) == 10
    assert tuple(runner.PAIRS) == TEN_PAIR_1H_SYMBOLS


@pytest.mark.parametrize(
    "pairs",
    [list(TEN_PAIR_1H_SYMBOLS[:-1]), [TEN_PAIR_1H_SYMBOLS[0]] * 10],
)
def test_preflight_fails_closed_when_universe_is_not_ten_unique_pairs(
    monkeypatch: pytest.MonkeyPatch,
    pairs: list[str],
) -> None:
    monkeypatch.setattr(runner, "PAIRS", pairs)

    with pytest.raises(RuntimeError, match="exactly 10 unique pairs"):
        runner._preflight()


def test_validate_report_accepts_finite_metrics_and_safe_health(
    valid_report: dict[str, object],
) -> None:
    assert runner._validate_report("BTC/USDT", valid_report) is valid_report


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf"), True])
@pytest.mark.parametrize("field", sorted(runner.FINITE_METRICS))
def test_validate_report_rejects_non_finite_or_boolean_metrics(
    valid_report: dict[str, object],
    field: str,
    value: float | bool,
) -> None:
    invalid = copy.deepcopy(valid_report)
    invalid[field] = value

    with pytest.raises(ValueError, match=field):
        runner._validate_report("BTC/USDT", invalid)


@pytest.mark.parametrize("value", [-1, 1.5, True, "3"])
def test_validate_report_rejects_invalid_total_trade_count(
    valid_report: dict[str, object],
    value: object,
) -> None:
    invalid = copy.deepcopy(valid_report)
    invalid["total_trades"] = value

    with pytest.raises(ValueError, match="total_trades"):
        runner._validate_report("BTC/USDT", invalid)


@pytest.mark.parametrize(
    ("health_patch", "message"),
    [
        ({"status": "degraded"}, "unsafe terminal execution state"),
        ({"unknown_orders": 1}, "unsafe terminal execution state"),
        ({"manual_interventions": 1}, "unsafe terminal execution state"),
        ({"unprotected_positions": ["BTC/USDT"]}, "unsafe terminal execution state"),
        ({"trade_evidence_complete": False}, "unsafe terminal execution state"),
    ],
)
def test_validate_report_rejects_unsafe_terminal_execution_health(
    valid_report: dict[str, object],
    health_patch: dict[str, object],
    message: str,
) -> None:
    invalid = copy.deepcopy(valid_report)
    health = invalid["execution_health"]
    assert isinstance(health, dict)
    health.update(health_patch)

    with pytest.raises(ValueError, match=message):
        runner._validate_report("BTC/USDT", invalid)


def test_validate_report_rejects_missing_or_mismatched_contract_fields(
    valid_report: dict[str, object],
) -> None:
    missing = copy.deepcopy(valid_report)
    missing.pop("data_manifest_id")
    with pytest.raises(ValueError, match="missing required fields"):
        runner._validate_report("BTC/USDT", missing)

    mismatched = copy.deepcopy(valid_report)
    mismatched["symbol"] = "ETH/USDT"
    with pytest.raises(ValueError, match="identity does not match"):
        runner._validate_report("BTC/USDT", mismatched)


def test_child_result_comes_only_from_json_report_not_stdout_scraping(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    valid_report: dict[str, object],
) -> None:
    monkeypatch.setattr(runner, "RUNS_DIR", tmp_path / "runs")
    captured: dict[str, object] = {}

    def fake_run(cmd: list[str], **kwargs: object) -> SimpleNamespace:
        captured["cmd"] = cmd
        captured["kwargs"] = kwargs
        _write_child_report(cmd, valid_report)
        # Deliberately contradictory legacy console labels. They must be ignored.
        stdout = "Final Equity: $1\nTotal Return: 9999%\nSharpe Ratio: 9999\n"
        return SimpleNamespace(returncode=0, stdout=stdout, stderr="")

    monkeypatch.setattr(runner.subprocess, "run", fake_run)

    result = runner.run_backtest("BTC/USDT", "run-contract")

    assert result["final_equity"] == valid_report["final_equity"]
    assert result["total_return_pct"] == valid_report["total_return_pct"]
    assert result["sharpe"] == valid_report["sharpe"]
    cmd = captured["cmd"]
    assert isinstance(cmd, list)
    assert "--report-path" in cmd
    assert "--state-dir" in cmd
    assert "--allow-new-exposure" in cmd
    kwargs = captured["kwargs"]
    assert isinstance(kwargs, dict)
    child_env = kwargs["env"]
    assert isinstance(child_env, dict)
    assert child_env["BACKTEST_RUN_ID"] == "run-contract"
    assert kwargs["check"] is False


def test_child_fails_closed_when_successful_process_omits_report(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(runner, "RUNS_DIR", tmp_path / "runs")
    monkeypatch.setattr(
        runner.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout="", stderr=""),
    )

    result = runner.run_backtest("BTC/USDT", "missing-report")

    assert "error" in result
    assert "missing child report" in str(result["error"])


def test_child_fails_closed_when_report_is_malformed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(runner, "RUNS_DIR", tmp_path / "runs")

    def fake_run(cmd: list[str], **kwargs: object) -> SimpleNamespace:
        report_path = Path(cmd[cmd.index("--report-path") + 1])
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text("{not-json", encoding="utf-8")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(runner.subprocess, "run", fake_run)

    result = runner.run_backtest("BTC/USDT", "malformed-report")

    assert "error" in result
    assert "Expecting property name" in str(result["error"])


def test_main_exits_nonzero_and_marks_aggregate_failed_on_any_child_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    valid_report: dict[str, object],
) -> None:
    monkeypatch.setattr(runner, "OUT_DIR", tmp_path / "out")
    monkeypatch.setattr(runner, "RUNS_DIR", tmp_path / "runs")
    monkeypatch.setattr(runner, "ProcessPoolExecutor", ThreadPoolExecutor)
    monkeypatch.setattr(runner, "_preflight", lambda: None)

    def fake_backtest(symbol: str, run_id: str) -> dict[str, object]:
        if symbol == TEN_PAIR_1H_SYMBOLS[0]:
            return {"symbol": symbol, "error": "missing child report"}
        result = copy.deepcopy(valid_report)
        result["symbol"] = symbol
        return result

    monkeypatch.setattr(runner, "run_backtest", fake_backtest)

    with pytest.raises(SystemExit) as raised:
        runner.main()

    assert raised.value.code == 1
    outputs = list((tmp_path / "out").glob("multi_pair_1h_*.json"))
    assert len(outputs) == 1
    aggregate = json.loads(outputs[0].read_text(encoding="utf-8"))
    assert aggregate["status"] == "failed"
    assert aggregate["successful_pairs"] == 9
    assert aggregate["failed_pairs"] == 1
    assert len(aggregate["results"]) == 10
