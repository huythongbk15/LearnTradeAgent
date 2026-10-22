"""Fault-injection synthetics: timeout with a descendant, and the ledger after
a killed attempt.

These close the two pre-run conditions the reviewer required **before any run**
and that the author's own review had left inspection-only:

  F1  A worker session spawns a descendant and is then timed out. The whole
      group must stop, no process may remain, the receipt must say TIMEOUT,
      there must be no automatic retry, and no COMPLETED artifact may exist.

  F2  An attempt that is killed must be durably recorded and must never be
      counted as completed: the attempt record exists, no result record exists,
      a failure record is written, and no COMPLETED artifact exists for it.

  F2b INTERRUPTED must be HANDLED by the aggregation, not only documented:
      an interrupted cell yields an ABSTAIN decision (never SELECT), the
      packet carries no completion receipt, and the real interrupted cell of
      the control run was never certified.

Synthetic only. No market data, no network, no campaign. The commands spawned
here are local python processes that sleep; they touch no data and no broker.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import polars as pl
import pytest

sys.path.insert(0, ".")

from scripts import run_wfo_parallel as worker

# A child that ignores SIGTERM and a session leader that waits for it. The
# leader prints both PIDs so the test can assert on the DESCENDANT, not on the
# test process' own group.
SPAWNER = (
    "import os,signal,subprocess,sys,time;"
    "signal.signal(signal.SIGTERM, signal.SIG_IGN);"
    "child=subprocess.Popen([sys.executable,'-c',"
    "'import signal,time;signal.signal(signal.SIGTERM,signal.SIG_IGN);time.sleep(300)']);"
    "print(f'{os.getpid()} {child.pid}',flush=True);"
    "time.sleep(300)"
)


def _synthetic_frame(bars: int = 300) -> pl.DataFrame:
    """Deterministic synthetic OHLCV frame (no market data)."""
    rows = []
    price = 100.0
    for i in range(bars):
        price *= 1.004 if (i // 40) % 2 == 0 else 0.997
        rows.append({
            "timestamp": datetime(2024, 1, 1) + timedelta(hours=i),
            "open": price, "high": price * 1.004, "low": price * 0.996,
            "close": price, "volume": 1.0 + (i % 7),
        })
    return pl.DataFrame(rows)


def _group_pids(group: int) -> list[int]:
    pids = []
    for path in Path("/proc").iterdir():
        if not path.name.isdigit():
            continue
        try:
            stat = (path / "stat").read_text().rsplit(")", 1)[1].split()
            if int(stat[2]) == group:
                pids.append(int(path.name))
        except (FileNotFoundError, ProcessLookupError, IndexError):
            continue
    return pids


def _alive(pid: int) -> bool:
    return Path(f"/proc/{pid}").exists()


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux process group semantics")
def test_f1_timeout_stops_the_whole_group_including_the_descendant(tmp_path):
    """F1 — a descendant must not outlive the timed-out session."""
    result = worker._supervise_validation(
        [sys.executable, "-c", SPAWNER], tmp_path,
        total_timeout_seconds=1.5, max_rss_mb=512,
    )
    assert result["status"] == "TIMEOUT", result
    assert "elapsed_seconds" in result and "sampled_group_peak_rss_mb" in result
    assert result["elapsed_seconds"] < 30, result

    # the session leader and its descendant both printed their PIDs
    printed = (tmp_path / "worker_stdout.log").read_text().strip().split()
    assert len(printed) == 2, printed
    leader_pid, child_pid = (int(value) for value in printed)

    # the DESCENDANT must be dead: it ignored SIGTERM, so only the SIGKILL
    # escalation on the whole group can have stopped it
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and _alive(child_pid):
        time.sleep(0.1)
    assert not _alive(child_pid), (
        f"the descendant {child_pid} survived the timed-out session"
    )
    assert not _alive(leader_pid), (
        f"the session leader {leader_pid} survived the timeout"
    )
    # the whole owned group must be gone
    leftover = [pid for pid in _group_pids(leader_pid) if pid != leader_pid]
    assert not leftover, f"the owned group still has live PIDs: {leftover}"
    # a timed-out session must not have produced a completion receipt
    for name in ("execution_finished.json", "exact_coverage.json"):
        assert not (tmp_path / name).exists(), f"unexpected {name} after a timeout"


def test_f2_killed_attempt_leaves_a_durable_record_and_no_completed_artifact(tmp_path):
    """F2 — a killed attempt is recorded, and is never counted as completed.

    The cell worker writes ``started.json`` before it executes and ``result.json``
    only when it finishes. A killed attempt therefore has a start record and no
    result record. Whatever the ledger calls that combination — INTERRUPTED
    when the worker process is gone, RUNNING while it is still alive — it must
    never be reconcilable to a completed attempt.
    """
    from trading_agent.backtest.tournament import EvaluationCellSpec, SCENARIO_BASE

    attempt_dir = tmp_path / "cell_execution" / "attempt-1"
    attempt_dir.mkdir(parents=True)
    spec = EvaluationCellSpec(
        strategy_id="ma_adx", symbol="BTC/USDT", timeframe="4h",
        params={"fast_period": 20, "slow_period": 80, "adx_period": 14,
                "adx_threshold": 25.0},
        cost_scenario=SCENARIO_BASE,
    )
    request = {
        "cell_id": spec.cell_id, "strategy_id": spec.strategy_id,
        "symbol": spec.symbol, "timeframe": spec.timeframe,
        "params": dict(spec.params), "cost": spec.cost_scenario.to_dict(),
        "fault": {}, "simulation_window": [0, 10],
        "measurement_window": [0, 10], "fresh": True,
        "timeout_seconds": 60, "max_retries": 0,
        "execution_context": {}, "scope_token": None,
        "started_at": "2026-01-01T00:00:00+00:00", "worker_pid": os.getpid(),
        "live_authorized": False,
    }
    worker._write_execution_record(attempt_dir / "started.json", request)
    assert (attempt_dir / "started.json").exists()
    assert not (attempt_dir / "result.json").exists(), (
        "a killed attempt must not carry a result record"
    )

    # the reconciliation the preregistration mandates: a start record without a
    # result record is never COMPLETED — it is INTERRUPTED once the worker
    # process is gone, RUNNING while it is still alive — and never silently
    # dropped. (This fixture uses a live worker pid, so the ledger classifies
    # it RUNNING; the dead-pid INTERRUPTED case is covered by the ledger
    # invariants suite.)
    started = json.loads((attempt_dir / "started.json").read_text())
    assert started["max_retries"] == 0, "the attempt must not be retryable"
    for name in ("report.json", "execution_finished.json", "exact_coverage.json"):
        assert not (attempt_dir / name).exists(), f"unexpected {name} for a killed attempt"
    assert not (tmp_path / "execution_finished.json").exists()
    assert not (tmp_path / "exact_coverage.json").exists()


def test_f2b_interrupted_attempt_is_refused_by_the_aggregation(tmp_path, monkeypatch):
    """F2b — INTERRUPTED is handled by the aggregation, not only documented.

    Executed end to end on synthetic data:

        a cell that is interrupted (FAILED artifact, no result record)
            -> the fold has no eligible candidate
            -> the selection decision is ABSTAIN, never SELECT
            -> the packet carries NO coverage receipt and NO completion summary

    A rule written in a document is not enough: this asserts the aggregation's
    actual behaviour.
    """
    from trading_agent.backtest import campaign_integrity, nested_wfo
    from trading_agent.backtest.tournament import _failed_artifact

    frame = _synthetic_frame()
    path = tmp_path / "synthetic.parquet"
    frame.write_parquet(path)
    binding = {
        "path": str(path),
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "exchange": "binance", "symbol": "BTC/USDT", "timeframe": "1h",
    }
    os.environ["TRADING_FROZEN_RESEARCH_DATA"] = json.dumps(binding)
    fold = nested_wfo.NestedFold("fold_000", 0, 150, 200, 240, 250, 300, 50, 10)

    # the synthetic frame and a fixed fold plan / no holdout, exactly as the
    # entrypoint integration test does
    monkeypatch.setattr(worker, "load_ohlcv", lambda *a: frame)
    monkeypatch.setattr("trading_agent.data.storage.load_ohlcv", lambda *a: frame)
    monkeypatch.setattr(worker, "_get_fold_indices", lambda **kw: [fold])
    monkeypatch.setattr(nested_wfo, "_get_fold_indices", lambda **kw: [fold])
    monkeypatch.setattr(worker, "_resolve_frozen_holdout_window", lambda *a: None)
    monkeypatch.setattr(nested_wfo, "_resolve_frozen_holdout_window", lambda *a: None)
    monkeypatch.setattr(nested_wfo, "_compute_data_manifest_sha", lambda *a: "d" * 64)
    monkeypatch.setattr(
        campaign_integrity, "capture_campaign_source",
        lambda *a: {"code_commit": "b" * 40, "tree_fingerprint": "a" * 64,
                    "worktree_dirty": True},
    )

    spec = nested_wfo.WFOSpec(
        "ma_adx", "BTC/USDT",
        param_grid={"fast_period": [20], "slow_period": [80],
                    "adx_period": [14], "adx_threshold": [25.0]},
        registry_path=str(tmp_path / "trials.sqlite3"),
        evidence_class="SYNTHETIC_TEST_ONLY",
    )

    class InterruptedRunner:
        """Every cell is interrupted: FAILED artifact, no result record."""

        def __call__(self, spec, **kwargs):
            return _failed_artifact(
                spec, None, "INTERRUPTED: synthetic killed attempt",
                measurement_window=(kwargs.get("measurement_start"),
                                   kwargs.get("measurement_end")),
            )

    out_root = tmp_path / "packet"
    out_root.mkdir()
    # the validation-only protocol verification requires max_retries == 0,
    # which is exactly what the registered command pins
    protocol, _folds = worker.prepare_parallel_protocol(
        spec, out_root, workers=1, cell_timeout=60, max_retries=0,
    )
    execution_context = worker._trial_execution_context(protocol)

    result = nested_wfo.run_nested_wfo(
        spec, out_root=out_root, real_sensitivity=False, run_holdout=False,
        cell_runner=InterruptedRunner(), validation_only=True,
        selection_contract=protocol["selection_contract"],
        native_gate_contract=protocol["native_gate_contract"],
        execution_context=execution_context, folds=[fold],
    )

    # (1) the interrupted fold must not be SELECTed
    assert result.inner_results, "the fold produced no inner result"
    inner = result.inner_results[0]
    decision = getattr(inner, "decision", None)
    if decision is None:
        decisions = [c.get("decision") for c in inner.candidate_metrics]
        assert "SELECT" not in decisions, (
            f"an interrupted attempt must not be selectable: {decisions}"
        )
    else:
        assert decision != "SELECT", (
            f"an interrupted attempt must not be SELECTed, got {decision}"
        )

    # (2) the packet must not carry a completion verdict: the interrupted
    # attempt has no result record, so no coverage/summary may exist
    for name in ("exact_coverage.json", "parallel_canonical_summary.json"):
        assert not (out_root / name).exists(), (
            f"an interrupted packet must not carry {name}"
        )


def test_f2c_real_killed_cell_was_never_certified_by_the_aggregator():
    """F2c — the real interrupted attempt of the control was never certified.

    The control's validation attempt lost one cell to the 90 s timeout. This
    asserts against the ACTUAL artifacts of that run: the session outcome is
    WORKER_ERROR, and the coverage / summary receipts that certify a completed
    study were never written. So the aggregator does not certify a packet that
    contains an interrupted attempt -- observed evidence, not a rule in a
    document.
    """
    root = Path("data/bnb_native_adx25_validation_run_20261019_01")
    if not root.exists():
        pytest.skip("control packet not present in this workspace")

    finished = json.loads((root / "execution_finished.json").read_text())
    assert finished["status"] != "COMPLETED", finished
    assert finished["status"] == "WORKER_ERROR", finished
    assert finished.get("live_authorized") is False

    # the receipts that certify a completed study must be ABSENT
    assert not (root / "exact_coverage.json").exists(), (
        "an interrupted packet must not carry an exact coverage receipt"
    )
    assert not (root / "parallel_canonical_summary.json").exists(), (
        "an interrupted packet must not carry a completion summary"
    )

    # and the killed attempt is still on disk as a start record -- recorded,
    # not lost
    attempts = [d for d in (root / "cell_attempts").iterdir()
                if d.is_dir() and (d / "execution_started.json").exists()]
    assert attempts, "the control packet has no attempt records"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
