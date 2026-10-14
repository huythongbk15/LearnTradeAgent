"""Synthetic engineering regressions: cadence and complete stress attribution."""

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import polars as pl
import pytest

from trading_agent.backtest import nested_wfo as wfo, tournament
from trading_agent.backtest.reporting import DataQualityError, assess_ohlcv, timeframe_delta


def bars(hours, count=90):
    return pl.DataFrame({
        "timestamp": [datetime(2024, 1, 1, tzinfo=UTC) + timedelta(hours=hours * i) for i in range(count)],
        "open": [100.0] * count, "high": [101.0] * count,
        "low": [99.0] * count, "close": [100.0] * count, "volume": [1.0] * count,
    })


@pytest.mark.parametrize("tf,seconds", [("15m", 900), ("1h", 3600), ("4h", 14400), ("1d", 86400)])
def test_duration_shared_with_simulator_and_bridge(monkeypatch, tf, seconds):
    from scripts.full_system_backtest import _timeframe_delta
    from trading_agent.strategies.canonical import bridge
    seen = {}
    monkeypatch.setattr(bridge, "build_canonical_runtime", lambda **kwargs: seen.update(kwargs))
    spec = tournament.EvaluationCellSpec("ma_vol_target", "BTC/USDT", tf, {"fast_period": 20, "slow_period": 60})
    factory = tournament._bridge_runtime_factory(spec, None, SimpleNamespace(warmup_bars=61))
    factory("fixture", {})
    assert seen["timeframe_delta"] == _timeframe_delta(tf) == timeframe_delta(tf)
    assert seen["timeframe_delta"].total_seconds() == seconds


@pytest.mark.parametrize("tf", [None, "", "0h", "-1h", "1w", "1.5h"])
def test_invalid_cadence_rejected(tf):
    with pytest.raises(ValueError):
        timeframe_delta(tf)


@pytest.mark.parametrize("hours", [1, 4])
def test_cell_quality_uses_declared_cadence_without_sorting_away_errors(tmp_path, monkeypatch, hours):
    import scripts.full_system_backtest as simulator
    frame = bars(hours)
    seen = []
    real_assess = tournament.assess_ohlcv

    def assess(frame, **kwargs):
        try:
            result = real_assess(frame, **kwargs)
        except DataQualityError as error:
            seen.append(error.report)
            raise
        seen.append(result)
        return result

    class FakeSimulator:
        def __init__(self, **kwargs):
            pass

        def run(self, **kwargs):
            metrics = {key: 1.0 for key in tournament._REQUIRED_METRICS}
            metrics.update(total_trades=0, profit_factor=None)
            return {"metrics": metrics, "execution_health": {
                "unknown_orders": 0, "manual_interventions": 0, "unprotected_positions": 0}}

    monkeypatch.setattr(simulator, "FullSystemSimulator", FakeSimulator)
    monkeypatch.setattr(tournament, "assess_ohlcv", assess)
    monkeypatch.setattr(tournament, "load_ohlcv", lambda *args: frame)
    monkeypatch.setattr(tournament, "canonical_signal_series", lambda *args, **kwargs: [0] * frame.height)
    spec = tournament.EvaluationCellSpec("ma_vol_target", "BTC/USDT", f"{hours}h", {"fast_period": 20, "slow_period": 60})
    artifact = tournament.run_cell(spec, out_root=tmp_path / "valid", fresh=True, _use_multiprocessing=False)
    assert artifact.status == "COMPLETED"
    assert seen[-1].expected_interval_seconds == 3600 * hours
    assert seen[-1].gap_count == 0
    frame = frame[[1, 0, *range(2, frame.height)]]
    artifact = tournament.run_cell(spec, out_root=tmp_path / "bad_order", fresh=True, _use_multiprocessing=False)
    assert artifact.status == "FAILED"
    assert any("data_quality" in reason for reason in artifact.failure_reasons)
    assert seen[-1].out_of_order_rows > 0


def test_wrong_cadence_is_not_quality_pass():
    with pytest.raises(DataQualityError) as error:
        assess_ohlcv(bars(1), expected_interval=timeframe_delta("4h"), gap_policy="reject")
    assert not error.value.report.accepted


def sensitivity_fixture(tmp_path, monkeypatch, *, fail_one=False):
    spec = wfo.WFOSpec("ma_vol_target", "BTC/USDT", "4h",
                       param_grid={"fast_period": [10, 20], "slow_period": [60]},
                       evidence_class="SYNTHETIC_TEST_ONLY")
    descriptor = tournament.build_default_registry().describe(spec.strategy_id)
    folds = [wfo.NestedFold(f"fold_{index:03d}", 0, 150, 200, 240, start, start + 50, 50, 10)
             for index, start in enumerate((300, 400))]

    def make(params, cost, fold, root, delay=0):
        cell = tournament.EvaluationCellSpec(spec.strategy_id, spec.symbol, spec.timeframe, params, cost)
        directory = Path(root) / f"{fold.fold_id}_{cell.params_hash}_{cost.name}_{delay}"
        directory.mkdir(parents=True, exist_ok=True)
        metrics = {key: 1.0 for key in tournament._REQUIRED_METRICS}
        metrics.update(total_trades=2, profit_factor=1.5, net_pnl=8.0)
        report = {"metrics": metrics, "trades": [{"pnl": 10.0}, {"pnl": -2.0}],
                  "commit_sha": "b" * 40, "measurement_window": [fold.outer_test_start, fold.outer_test_end],
                  "execution_health": {"unknown_orders": 0, "manual_interventions": 0, "unprotected_positions": 0}}
        (directory / "report.json").write_text(json.dumps(report))
        return tournament._artifact_from_report(cell, descriptor, directory, report, "d" * 64,
                                               measurement_window=(fold.outer_test_start, fold.outer_test_end),
                                               simulation_window=(fold.outer_test_start, fold.outer_test_end),
                                               signal_delay_bars=delay)

    selected = {"fast_period": 20, "slow_period": 60}
    outers = []
    for fold in folds:
        artifact = make(selected, tournament.SCENARIO_BASE, fold, tmp_path / "outer")
        outers.append(wfo.WFOOuterResult(fold.fold_id, fold.outer_test_start, fold.outer_test_end,
                                       {**selected, "cost_scenario": "1x"}, dict(artifact.metrics),
                                       {}, artifact))
    calls = []

    def rerun(spec_, params, cost, fold, root, descriptor_, signal_delay_bars=0):
        calls.append((fold.fold_id, cost.name, signal_delay_bars))
        # Plan must be durable BEFORE any rerun, including failure.
        assert (Path(root) / "sensitivity_plan.json").exists()
        artifact = make(params, cost, fold, root, signal_delay_bars)
        if fail_one and fold.fold_id == "fold_001" and cost.name == "2x":
            return replace(artifact, status="FAILED", failure_reasons=("fixture timeout",))
        return artifact

    monkeypatch.setattr(wfo, "_run_outer_eval", rerun)
    return spec, descriptor, folds, outers, calls


def test_sensitivity_exact_schedule_and_two_point_neighbors(tmp_path, monkeypatch):
    spec, descriptor, folds, outers, calls = sensitivity_fixture(tmp_path, monkeypatch)
    result = wfo._compute_sensitivity_analysis(spec, outers, folds, out_root=tmp_path, descriptor=descriptor)
    assert len(calls) == 8  # 3 stresses + 1 adjacent neighbor, on EACH fold.
    assert result["coverage"]["status"] == "PASS"
    assert len(result["coverage"]["expected"]) == len(result["coverage"]["observed"]) == 8
    assert len(result["parameter_neighbors"]) == 2
    assert result["cost_2x"]["aggregate"]["median_net_pnl"] == 8.0
    assert result["drop_best_trade"]["aggregate"]["n_folds"] == 2
    # Schedule does not depend on already-observed OOS performance.
    changed = [replace(outer, test_metrics={"sharpe": -999}) for outer in outers]
    assert wfo._sensitivity_requests(spec, changed, folds) == result["coverage"]["expected"]


def test_failed_rerun_retained_and_cannot_make_partial_aggregate(tmp_path, monkeypatch):
    spec, descriptor, folds, outers, _ = sensitivity_fixture(tmp_path, monkeypatch, fail_one=True)
    result = wfo._compute_sensitivity_analysis(spec, outers, folds, out_root=tmp_path, descriptor=descriptor)
    coverage = result["coverage"]
    assert coverage["status"] == "INCOMPLETE"
    assert len(coverage["observed"]) == len(coverage["expected"]) == 8
    assert len([row for row in coverage["observed"] if row["status"] == "FAILED"]) == 1
    assert result["cost_2x"]["aggregate"]["median_net_pnl"] is None
    assert result["delay_1_bar"]["aggregate"]["median_profit_factor"] is None


def test_sensitivity_missing_fold_refused_before_dispatch(tmp_path, monkeypatch):
    spec, descriptor, folds, outers, calls = sensitivity_fixture(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="every distinct"):
        wfo._compute_sensitivity_analysis(spec, outers[:1], folds, out_root=tmp_path, descriptor=descriptor)
    assert not calls


def test_sensitivity_receipt_tampering_rejected(tmp_path, monkeypatch):
    spec, descriptor, folds, outers, _ = sensitivity_fixture(tmp_path, monkeypatch)
    result = wfo._compute_sensitivity_analysis(spec, outers, folds, out_root=tmp_path, descriptor=descriptor)
    receipt = result["coverage"]["observed"][0]
    artifact = tournament.EvaluationArtifact.from_dict(receipt["artifact"])
    wrong = replace(artifact, signal_delay_bars=1)
    assert wfo._sensitivity_receipt(receipt["request"], wrong, tmp_path / "sensitivity")["status"] == "INVALID_SOURCE"
    Path(artifact.report_path).write_text("{}")
    assert wfo._sensitivity_receipt(receipt["request"], artifact, tmp_path / "sensitivity")["status"] == "INVALID_SOURCE"


def test_sensitivity_rerun_starts_flat_at_primary_boundary(tmp_path, monkeypatch):
    seen = {}
    monkeypatch.setattr(wfo, "run_cell", lambda cell, **kwargs: seen.update(kwargs))
    spec = wfo.WFOSpec("ma_vol_target", "BTC/USDT")
    fold = wfo.NestedFold("f", 0, 100, 150, 200, 300, 350, 50, 10)
    wfo._run_outer_eval(spec, {"fast_period": 20, "slow_period": 60}, tournament.SCENARIO_DOUBLE,
                        fold, tmp_path, SimpleNamespace(warmup_bars=61))
    assert seen["start"] == seen["measurement_start"] == 300
    assert seen["end"] == seen["measurement_end"] == 350
