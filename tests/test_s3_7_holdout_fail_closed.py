"""S3-7: final holdout fail-closed (P0).

STR-0309 requires the final holdout to be a truly independent confirmation run
on a FROZEN window that never influenced selection.  The pre-S3-7 code path
had two holes:

  1. The holdout was only RUN when ``run_holdout=True and passes``.  Its
     ``status`` was recorded on ``WFOResult.final_holdout`` but never
     influenced ``passes_hard_gates`` — a FAILED holdout still reported PASS.
  2. When no frozen manifest existed, the code fell back to a last-10%
     window.  That window already influenced selection, so it is NOT an
     independent confirmation — yet the code only printed a warning and
     continued.

S3-7 closes both holes: a Gate 13 (``final_holdout_completed``) is added to
the hard-gate set, and a missing frozen manifest is treated as ERROR (not a
warning), which fails the gate and fail-closes the run.

These tests run on the synthetic evidence pipeline so they stay CI-safe.
"""

from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

import pytest

pytestmark = [
    pytest.mark.slow,
    pytest.mark.wfo,
]

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from trading_agent.backtest.nested_wfo import (
    NestedFold,
    run_nested_wfo,
)
from trading_agent.backtest.synthetic_data import (
    generate_synthetic_ohlcv,
    synthetic_wfo_spec,
)


N_BARS = 1000
HOLDOUT_START = 800


def _synthetic_df():
    return generate_synthetic_ohlcv(
        symbol="BTC/USDT", timeframe="1h", n_bars=N_BARS, seed=7
    )


def _fake_folds():
    return [
        NestedFold(
            fold_id="f1",
            inner_train_start=0,
            inner_train_end=400,
            inner_val_start=400,
            inner_val_end=620,
            outer_test_start=620,
            outer_test_end=800,
            purge=0,
            embargo=0,
        ),
        NestedFold(
            fold_id="f2",
            inner_train_start=200,
            inner_train_end=600,
            inner_val_start=600,
            inner_val_end=720,
            outer_test_start=720,
            outer_test_end=800,
            purge=0,
            embargo=0,
        ),
    ]


@pytest.fixture
def patched(monkeypatch):
    df = _synthetic_df()
    folds = _fake_folds()

    def _load(*args, **kwargs):
        return df

    monkeypatch.setattr("trading_agent.data.storage.load_ohlcv", _load)
    monkeypatch.setattr("trading_agent.backtest.tournament.load_ohlcv", _load)
    monkeypatch.setattr(
        "trading_agent.backtest.nested_wfo._resolve_frozen_holdout_window",
        lambda *a, **kw: (HOLDOUT_START, N_BARS - 1),
    )
    monkeypatch.setattr(
        "trading_agent.backtest.nested_wfo._get_fold_indices",
        lambda *a, **kw: folds,
    )
    return df, folds


def _make_spec(tmp_path):
    spec, _, _ = synthetic_wfo_spec(
        strategy_id="rsi", symbol="BTC/USDT", timeframe="1h", n_bars=N_BARS
    )
    return replace(spec, registry_path=str(tmp_path / "wfo.sqlite3"))


def test_run_holdout_without_frozen_manifest_fail_closed(patched, tmp_path):
    """run_holdout=True with NO frozen manifest → ERROR status, gate FAIL,
    passes_hard_gates=False. No last-10% fallback silently allowed."""
    _df, _folds = patched
    # Remove the frozen-manifest patch so no frozen window is resolved.
    import trading_agent.backtest.nested_wfo as nw

    nw._resolve_frozen_holdout_window = lambda *a, **kw: None

    spec = _make_spec(tmp_path)
    result = run_nested_wfo(spec, out_root=tmp_path / "wfo_ev", run_holdout=True)

    fh = result.final_holdout
    assert fh is not None
    assert fh.get("status") == "ERROR", fh
    assert "frozen research_manifest" in fh.get("error", ""), fh
    assert result.passes_hard_gates is False, (
        "Holdout with no frozen manifest must fail-closed, not pass"
    )
    assert "final_holdout_completed" in result.gate_failures, (
        f"final_holdout_completed must be in gate_failures, got {result.gate_failures}"
    )


def test_run_holdout_with_frozen_manifest_runs_gates(patched, tmp_path):
    """run_holdout=True WITH a frozen manifest → holdout COMPLETED, gate PASS,
    and the frozen-window gate is present in the gate set."""
    _df, _folds = patched
    spec = _make_spec(tmp_path)
    result = run_nested_wfo(spec, out_root=tmp_path / "wfo_ev", run_holdout=True)

    fh = result.final_holdout
    assert fh is not None
    assert fh.get("status") == "COMPLETED", fh
    gate_ids = {g.gate_id for g in result.gate_results}
    assert "final_holdout_completed" in gate_ids, gate_ids
    assert "final_holdout_frozen_window" in gate_ids, gate_ids
    # All holdout gates must be PASS when the holdout completed on a frozen window.
    holdout_gates = [
        g
        for g in result.gate_results
        if g.gate_id in ("final_holdout_completed", "final_holdout_frozen_window")
    ]
    assert all(g.verdict == "PASS" for g in holdout_gates), holdout_gates


def test_holdout_failed_status_fail_closed(patched, monkeypatch, tmp_path):
    """If run_final_holdout raises, the holdout status becomes ERROR and the
    run is fail-closed (passes_hard_gates=False)."""
    _df, _folds = patched
    spec = _make_spec(tmp_path)

    import trading_agent.backtest.nested_wfo as nw

    def _boom(*a, **kw):
        raise RuntimeError("simulated holdout failure")

    monkeypatch.setattr(nw, "run_final_holdout", _boom)
    result = run_nested_wfo(spec, out_root=tmp_path / "wfo_ev", run_holdout=True)

    fh = result.final_holdout
    assert fh is not None
    assert fh.get("status") == "ERROR", fh
    assert "simulated holdout failure" in fh.get("error", ""), fh
    assert result.passes_hard_gates is False, (
        "A failed holdout must fail-closed the whole WFO run"
    )
    assert "final_holdout_completed" in result.gate_failures, (
        f"final_holdout_completed must fail, got {result.gate_failures}"
    )


def test_holdout_gate_not_added_when_run_holdout_false(patched, tmp_path):
    """When run_holdout=False, no holdout gates are added and the gate set is
    unchanged from the pre-S3-7 behaviour."""
    _df, _folds = patched
    spec = _make_spec(tmp_path)
    result = run_nested_wfo(spec, out_root=tmp_path / "wfo_ev", run_holdout=False)

    gate_ids = {g.gate_id for g in result.gate_results}
    assert "final_holdout_completed" not in gate_ids, gate_ids
    assert "final_holdout_frozen_window" not in gate_ids, gate_ids
    assert result.final_holdout is None
