from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import pytest

from trading_agent.backtest import tournament
from trading_agent.backtest.tournament import (
    EvaluationArtifact,
    EvaluationCellSpec,
    _apply_resource_budget,
    _run_cell_with_retry,
    _run_isolated_process,
    run_cell,
)


def _sleeping_worker(send_conn: Any, delay_seconds: float) -> None:
    time.sleep(delay_seconds)
    send_conn.send({"kind": "result", "artifact": {}})
    send_conn.close()


def _spec_primitives() -> dict[str, Any]:
    spec = EvaluationCellSpec("enhanced_ma", "BTC/USDT")
    return {
        "strategy_id": spec.strategy_id,
        "symbol": spec.symbol,
        "timeframe": spec.timeframe,
        "params": dict(spec.params),
        "cost_scenario": spec.cost_scenario,
        "fault": spec.fault,
    }


def _completed_artifact() -> EvaluationArtifact:
    spec = EvaluationCellSpec("enhanced_ma", "BTC/USDT")
    return EvaluationArtifact(
        cell_id=spec.cell_id,
        status="COMPLETED",
        descriptor_id="descriptor",
        strategy_id=spec.strategy_id,
        symbol=spec.symbol,
        timeframe=spec.timeframe,
        params_hash=spec.params_hash,
        cost_scenario=spec.cost_scenario.name,
        fault_profile=spec.fault.name,
        commission=spec.cost_scenario.commission,
        slippage=spec.cost_scenario.slippage,
        data_manifest_sha="sha256:data",
        commit_sha="commit",
        report_path="report.json",
        metrics={"total_return_pct": 1.0},
        execution_health={"status": "normal"},
    )


def _run_with_control(**overrides: Any) -> EvaluationArtifact:
    kwargs: dict[str, Any] = {
        "spec_primitives": _spec_primitives(),
        "out_root": Path("/tmp/tournament-cell-control"),
        "start": 0,
        "end": None,
        "tail_bars": None,
        "fresh": True,
        "simulation_start": None,
        "measurement_start": None,
        "measurement_end": None,
        "signal_delay_bars": 0,
        "gap_policy": "record",
        "gap_exceptions_path": None,
        "timeout_seconds": 0.1,
        "max_retries": 2,
        "resource_budget": None,
    }
    kwargs.update(overrides)
    return _run_cell_with_retry(**kwargs)


def test_isolated_attempt_stops_worker_at_deadline() -> None:
    started = time.monotonic()
    outcome = _run_isolated_process(
        target=_sleeping_worker,
        target_args=(5.0,),
        timeout_seconds=0.1,
    )

    assert outcome == {"kind": "timeout"}
    assert time.monotonic() - started < 3.0


def test_timeout_retries_are_bounded_and_recorded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts = 0

    def timeout_attempt(**_: Any) -> dict[str, Any]:
        nonlocal attempts
        attempts += 1
        return {"kind": "timeout"}

    monkeypatch.setattr(tournament, "_run_isolated_process", timeout_attempt)
    artifact = _run_with_control(timeout_seconds=2.0, max_retries=2)

    assert attempts == 3
    assert artifact.status == "FAILED"
    assert "execution_failed_after_3_attempts:timeout:2.0s" in artifact.failure_reasons
    assert artifact.execution_control == {
        "isolation": "spawned_process",
        "timeout_seconds": 2.0,
        "max_retries": 2,
        "resource_budget": {},
        "attempts_made": 3,
        "outcome": "failed",
    }


def test_success_after_retry_binds_attempt_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    outcomes = iter(
        (
            {"kind": "timeout"},
            {"kind": "result", "artifact": _completed_artifact().to_dict()},
        )
    )
    monkeypatch.setattr(tournament, "_run_isolated_process", lambda **_: next(outcomes))

    artifact = _run_with_control(timeout_seconds=3.0, max_retries=2)

    assert artifact.status == "COMPLETED"
    assert artifact.execution_control["attempts_made"] == 2
    assert artifact.execution_control["outcome"] == "completed"
    assert EvaluationArtifact.from_dict(artifact.to_dict()) == artifact


def test_fatal_worker_error_is_not_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    attempts = 0

    def fatal_attempt(**_: Any) -> dict[str, Any]:
        nonlocal attempts
        attempts += 1
        return {
            "kind": "error",
            "error_type": "ValueError",
            "message": "invalid parameters",
            "fatal": True,
        }

    monkeypatch.setattr(tournament, "_run_isolated_process", fatal_attempt)
    artifact = _run_with_control(max_retries=5)

    assert attempts == 1
    assert artifact.status == "FAILED"
    assert artifact.execution_control["attempts_made"] == 1
    assert "ValueError:invalid parameters" in artifact.failure_reasons[0]


def test_resource_limit_worker_exit_is_not_retried(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts = 0

    def worker_exit(**_: Any) -> dict[str, Any]:
        nonlocal attempts
        attempts += 1
        return {
            "kind": "error",
            "error_type": "WorkerExit",
            "message": "worker exited without result (exitcode=-9)",
            "fatal": False,
        }

    monkeypatch.setattr(tournament, "_run_isolated_process", worker_exit)
    artifact = _run_with_control(
        max_retries=5,
        resource_budget={"max_memory_mb": 256},
    )

    assert attempts == 1
    assert artifact.status == "FAILED"
    assert artifact.execution_control["attempts_made"] == 1


@pytest.mark.parametrize(
    "budget, message",
    (
        ({"max_memory_mb": 0}, "must be a positive integer"),
        ({"max_cpu_seconds": True}, "must be a positive integer"),
        ({"other": 1}, "unknown resource_budget key"),
    ),
)
def test_resource_budget_validation_fails_closed(
    budget: dict[str, Any], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        run_cell(
            EvaluationCellSpec("ghost_strategy", "BTC/USDT"),
            resource_budget=budget,
            _use_multiprocessing=False,
        )


def test_resource_budget_cannot_be_silently_ignored_inline() -> None:
    with pytest.raises(ValueError, match="requires multiprocessing isolation"):
        run_cell(
            EvaluationCellSpec("ghost_strategy", "BTC/USDT"),
            resource_budget={"max_memory_mb": 256},
            _use_multiprocessing=False,
        )


def test_resource_budget_is_applied_in_worker(monkeypatch: pytest.MonkeyPatch) -> None:
    resource = pytest.importorskip("resource")
    limits: list[tuple[int, tuple[int, int]]] = []
    monkeypatch.setattr(
        resource,
        "getrlimit",
        lambda _: (resource.RLIM_INFINITY, resource.RLIM_INFINITY),
    )
    monkeypatch.setattr(
        resource, "setrlimit", lambda kind, limit: limits.append((kind, limit))
    )

    _apply_resource_budget({"max_memory_mb": 256, "max_cpu_seconds": 7})

    assert (resource.RLIMIT_DATA, (256 * 1024 * 1024, 256 * 1024 * 1024)) in limits
    assert (resource.RLIMIT_CPU, (7, 7)) in limits
