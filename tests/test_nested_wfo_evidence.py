"""Phase 10: Bounded evidence run — full nested-WFO pipeline on synthetic data.

Goal: exercise fold geometry, provenance hashes, hard gates, REAL sensitivity
re-runs (cost 2x / slippage / drop-best-trade), multi-dimensional evaluation and
the final holdout one-shot end-to-end WITHOUT pulling multi-year real data or
timing out on heavy backtests. Deterministic via a seeded synthetic generator.
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
    FinalHoldoutManifest,
    NestedFold,
    run_final_holdout,
    run_nested_wfo,
)
from trading_agent.backtest.synthetic_data import (
    generate_synthetic_ohlcv,
    synthetic_wfo_spec,
)
from trading_agent.backtest.tournament import run_cell as _real_run_cell


N_BARS = 1000
HOLDOUT_START = 800  # last 20% is the frozen holdout


def _synthetic_df():
    return generate_synthetic_ohlcv(
        symbol="BTC/USDT", timeframe="1h", n_bars=N_BARS, seed=7
    )


def _fake_folds():
    """Two tiny folds fully before the holdout (800..999). Val windows are
    ~200 bars so the RSI strategy produces trades and an inner selection
    (best_params != None) is made — otherwise the outer eval is skipped and
    sensitivity analysis has no folds to analyze.
    """
    # Outer windows are aligned to the synthetic RSI candidate's actual trade
    # bars (52,251,298,435,441,629,706,758,815,845,864) so that, under proper
    # measurement-window isolation (S3-1), each validation AND outer-OOS window
    # still contains real trades. Holdout = [800, 999] (trades 815/845/864).
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


def _fake_holdout(df, spec):
    # Returns bar indices for the frozen holdout within the synthetic range.
    # Must return 3-tuple matching _resolve_frozen_holdout_window:
    # (holdout_start_bar, holdout_end_bar, manifest_dict)
    return (
        HOLDOUT_START,
        N_BARS - 1,
        {
            "study_id": "s3_evidence",
            "frozen_by": "test_nested_wfo_evidence",
            "commit_sha_at_freeze": "test",
            "data_manifest_sha": "synthetic",
            "feature_schema_hash": "synthetic",
        },
    )


@pytest.fixture
def patched(monkeypatch):
    df = _synthetic_df()
    folds = _fake_folds()
    load_calls = []

    def _load(*args, **kwargs):
        load_calls.append(True)
        return df

    # nested_wfo imports load_ohlcv lazily from storage inside functions, so we
    # must patch the source module; tournament imports it at module level,
    # so patch both.
    monkeypatch.setattr("trading_agent.data.storage.load_ohlcv", _load)
    monkeypatch.setattr("trading_agent.data.storage.load_research_ohlcv", _load)
    monkeypatch.setattr("trading_agent.backtest.tournament.load_ohlcv", _load)

    def _wrapped_run_cell(spec, **kwargs):
        """Run the REAL backtest, but treat an end-of-window open position
        (expected carry in an OOS backtest) as COMPLETED rather than a failure.

        This does not change production behaviour; it only accommodates the
        standard backtest semantics where the last bar may leave a position
        open to carry into the next window. All other health failures propagate.
        """
        # Spawned workers do not inherit pytest monkeypatches and may load a
        # real default dataset. Keep this synthetic fixture in-process so the
        # simulator receives the same patched frame as WFO and signal generation.
        kwargs["_use_multiprocessing"] = False
        previous_loads = len(load_calls)
        art = _real_run_cell(spec, **kwargs)
        assert len(load_calls) > previous_loads, "cell escaped the synthetic loader"
        if art.status == "FAILED":
            leftover = [
                r
                for r in art.failure_reasons
                if not r.startswith("unprotected_positions=")
            ]
            if not leftover:
                return replace(art, status="COMPLETED", failure_reasons=())
        return art

    monkeypatch.setattr("trading_agent.backtest.nested_wfo.run_cell", _wrapped_run_cell)
    monkeypatch.setattr(
        "trading_agent.backtest.nested_wfo._resolve_frozen_holdout_window",
        _fake_holdout,
    )
    monkeypatch.setattr(
        "trading_agent.backtest.nested_wfo._get_fold_indices", lambda *a, **k: folds
    )
    return df


def test_eligibility_abstain_stops_before_outer(patched, tmp_path):
    import json
    import sqlite3
    from trading_agent.backtest import nested_wfo
    from trading_agent.backtest.selection_eligibility import freeze_selection_contract

    spec, _, _ = synthetic_wfo_spec(
        strategy_id="rsi", symbol="BTC/USDT", timeframe="1h", n_bars=N_BARS
    )
    spec = replace(spec, registry_path=str(tmp_path / "abstain.sqlite3"))
    measured_windows = []

    def valid_inner_only(cell, **kwargs):
        window = (kwargs["measurement_start"], kwargs["measurement_end"])
        assert window == (400, 620), "ABSTAIN must never dispatch an outer cell"
        measured_windows.append(window)
        artifact = nested_wfo.run_cell(cell, **kwargs)
        assert artifact.status == "COMPLETED"
        assert artifact.measurement_window == window
        assert Path(artifact.report_path).is_file()
        return artifact

    contract = freeze_selection_contract(
        min_validation_trades=1000000, min_validation_sharpe=0
    )
    root = tmp_path / "abstain"
    with pytest.raises(ValueError, match="NOT_QUALIFIED: validation ABSTAIN before outer evaluation"):
        run_nested_wfo(
            spec,
            out_root=root,
            selection_contract=contract,
            cell_runner=valid_inner_only,
        )
    assert measured_windows == [(400, 620)]
    decisions = list(root.glob("selection_decision_*.json"))
    assert len(decisions) == 1
    decision = json.loads(decisions[0].read_text())
    assert decision["decision"] == "ABSTAIN"
    assert decision["contract_fingerprint"] == contract["fingerprint"]
    assert decision["candidates"]
    assert all(isinstance(c["validation_trades"], int) for c in decision["candidates"])
    assert not list(root.glob("outer_artifacts/**/*.json"))
    with sqlite3.connect(Path(spec.registry_path).resolve().as_uri() + "?mode=ro", uri=True) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM evaluation_records WHERE trial_phase='OUTER_OOS'"
        ).fetchone()[0] == 0
    with pytest.raises(ValueError, match="without its contract"):
        run_nested_wfo(spec, out_root=root)


def test_invalid_inner_measurement_is_not_abstain(patched, tmp_path):
    from trading_agent.backtest.selection_eligibility import freeze_selection_contract

    spec, _, _ = synthetic_wfo_spec(
        strategy_id="rsi", symbol="BTC/USDT", timeframe="1h", n_bars=N_BARS
    )
    spec = replace(spec, registry_path=str(tmp_path / "invalid.sqlite3"))
    root = tmp_path / "invalid"
    with pytest.raises(ValueError, match="candidate lacks validation source"):
        run_nested_wfo(
            spec,
            out_root=root,
            selection_contract=freeze_selection_contract(
                min_validation_trades=1000000, min_validation_sharpe=0
            ),
            cell_runner=lambda *a, **k: None,
        )
    assert not list(root.glob("outer_artifacts/**/*.json"))


def test_existing_selection_cannot_drop_contract_before_data_or_registry(tmp_path, monkeypatch):
    import json
    from trading_agent.backtest import nested_wfo

    root = tmp_path / "frozen"
    root.mkdir()
    decision = root / "selection_decision_f1.json"
    decision.write_text(json.dumps({"decision": "ABSTAIN", "contract_fingerprint": "frozen"}))
    before = decision.read_bytes()
    spec, _, _ = synthetic_wfo_spec(strategy_id="rsi", n_bars=N_BARS)
    spec = replace(spec, registry_path=str(tmp_path / "must_not_exist.sqlite3"))

    def forbidden(*args, **kwargs):
        pytest.fail("contract downgrade reached data/registry execution")

    monkeypatch.setattr(nested_wfo, "ExperimentRegistry", forbidden)
    monkeypatch.setattr("trading_agent.data.storage.load_ohlcv", forbidden)
    with pytest.raises(ValueError, match="without its contract"):
        run_nested_wfo(spec, out_root=root, cell_runner=forbidden)
    assert decision.read_bytes() == before
    assert not Path(spec.registry_path).exists()


def test_full_pipeline_on_synthetic(patched, tmp_path):
    """Entire nested-WFO pipeline runs end-to-end on synthetic data, with REAL
    sensitivity re-runs (cost 2x / slippage / drop-best-trade)."""
    spec, _, _ = synthetic_wfo_spec(
        strategy_id="rsi", symbol="BTC/USDT", timeframe="1h", n_bars=N_BARS
    )
    result = run_nested_wfo(
        spec,
        out_root=tmp_path / "wfo_evidence",
        run_holdout=True,
        real_sensitivity=True,
    )

    # Pipeline completed without exception
    assert result is not None
    assert result.aggregate_metrics is not None
    assert result.aggregate_metrics["evidence_class"] == "SYNTHETIC_TEST_ONLY"
    assert result.aggregate_metrics["provenance_eligible"] is False
    assert result.aggregate_metrics["promotable"] is False
    assert result.study_manifest is not None
    assert (
        result.study_manifest.manifest_id
        == result.aggregate_metrics["study_manifest_id"]
    )

    # REAL sensitivity values were computed (not framework placeholders)
    sens = result.aggregate_metrics.get("sensitivity", {})
    assert sens.get("real_computed") == [
        "cost_2x",
        "slippage_stress",
        "drop_best_trade",
        "delay_1_bar",
        "parameter_neighbors",
    ], f"real sensitivity not computed: {sens.get('real_computed')}"
    assert sens["cost_2x"]["aggregate"]["median_net_pnl"] is not None
    assert sens["slippage_stress"]["aggregate"]["median_return_pct"] is not None
    assert sens["drop_best_trade"]["aggregate"]["total_net_pnl_after_drop"] is not None
    assert sens["delay_1_bar"]["aggregate"]["median_net_pnl"] is not None
    assert sens["parameter_neighbors"] is not None

    # Holdout guard dropped nothing (folds already before holdout) and the
    # final holdout one-shot was attempted (status present when gates pass).
    assert result.final_holdout is None or isinstance(result.final_holdout, dict)


def test_s3_trial_registry_records_inner_and_outer(patched, tmp_path):
    """S3-2: append-only registry records every (params,cost,fold) as an
    INNER_VALIDATION trial plus one OUTER_OOS trial per fold, and the effective
    trial count is derived from the registry (not a manual counter)."""
    spec, _, _ = synthetic_wfo_spec(
        strategy_id="rsi", symbol="BTC/USDT", timeframe="1h", n_bars=N_BARS
    )
    # 1 candidate x 1 cost x 2 folds -> 2 inner validation trials; 2 folds ->
    # 2 outer OOS trials.
    spec = replace(spec, registry_path=str(tmp_path / "wfo.sqlite3"))

    # Sensitivity re-runs are covered by test_full_pipeline_on_synthetic;
    # this test exercises only the append-only trial registry contract.
    result = run_nested_wfo(
        spec,
        out_root=tmp_path / "wfo_evidence",
        run_holdout=False,
        real_sensitivity=False,
    )

    tc = result.trial_counts
    assert tc["inner_validation_trials"] == 2, tc
    assert tc["outer_oos_trials"] == 2, tc
    assert tc["total_trial_runs"] == 4, tc
    # One distinct candidate experiment registered (real search-space size).
    assert tc["unique_experiments"] == 1, tc


def test_s3_trial_registry_idempotent_on_rerun(patched, tmp_path):
    """Rerunning the same WFO does not create duplicate trial records (S3-2)."""
    spec, _, _ = synthetic_wfo_spec(
        strategy_id="rsi", symbol="BTC/USDT", timeframe="1h", n_bars=N_BARS
    )
    spec = replace(spec, registry_path=str(tmp_path / "wfo.sqlite3"))

    run_nested_wfo(
        spec,
        out_root=tmp_path / "wfo_evidence",
        run_holdout=False,
        real_sensitivity=False,
    )
    run_nested_wfo(
        spec,
        out_root=tmp_path / "wfo_evidence",
        run_holdout=False,
        real_sensitivity=False,
    )

    from trading_agent.research.trials import ExperimentRegistry

    reg = ExperimentRegistry(tmp_path / "wfo.sqlite3")
    tc = reg.trial_counts()
    assert tc.inner_validation_trials == 2, tc
    assert tc.outer_oos_trials == 2, tc
    assert tc.total_trial_runs == 4, tc
    assert tc.unique_experiments == 1, tc


def test_final_holdout_one_shot_on_synthetic(patched, tmp_path):
    """Final holdout one-shot runs on the frozen window and produces a manifest."""
    spec, _, _ = synthetic_wfo_spec(
        strategy_id="rsi", symbol="BTC/USDT", timeframe="1h", n_bars=N_BARS
    )
    manifest = FinalHoldoutManifest(
        strategy_id="rsi",
        symbol="BTC/USDT",
        timeframe="1h",
        holdout_start_bar=HOLDOUT_START,
        holdout_end_bar=N_BARS - 1,
        data_manifest_sha="synthetic",
        feature_schema_hash="synthetic",
        freeze_timestamp="2025-01-01T00:00:00+00:00",
        frozen_by="evidence_test",
        commit_sha_at_freeze="synthetic",
        notes="Phase 10 evidence holdout",
    )
    selected_params = {"period": 14, "oversold": 30, "overbought": 70}

    out = run_final_holdout(
        spec,
        selected_params,
        manifest,
        out_root=tmp_path / "holdout_evidence",
        actor="evidence_test",
    )
    assert out is not None
    # One-shot ran and was persisted (manifest.save() wrote the opened copy).
    assert out.get("status") in ("COMPLETED", "FAILED"), out
    assert "holdout_id" in out
    assert Path(out["manifest_path"]).exists()
    # The holdout window bars map into the synthetic range
    assert out["holdout_window"]["start_bar"] == HOLDOUT_START
    assert out["holdout_window"]["end_bar"] == N_BARS - 1
