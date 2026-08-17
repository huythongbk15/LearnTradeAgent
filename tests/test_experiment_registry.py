from __future__ import annotations

import json
import sqlite3

import numpy as np
import pandas as pd
import pytest

from trading_agent.alpha_research.feature_store import (
    FeatureStore,
    FeatureStoreError,
    dataframe_schema_hash,
)
from trading_agent.alpha_research.stats import summarize_sharpe
from trading_agent.research.trials import ExperimentRegistry, ExperimentSpec


def _spec(**overrides) -> ExperimentSpec:
    values = {
        "strategy_name": "causal_momentum",
        "strategy_code_sha": "a" * 64,
        "data_manifest_sha": "b" * 64,
        "feature_schema_hash": "c" * 64,
        "params_hash": "d" * 64,
        "search_family": "momentum_grid",
        "search_space_hash": "e" * 64,
        "target_horizon": "5bars",
        "evaluator_version": "nested-wf-v2",
        "seed": 42,
    }
    values.update(overrides)
    return ExperimentSpec.build(**values)


def test_rerun_reuses_spec_and_appends_new_evaluation(tmp_path) -> None:
    registry = ExperimentRegistry(tmp_path / "experiments.sqlite3")
    first = registry.register_experiment(_spec())
    renamed = registry.register_experiment(_spec(strategy_name="renamed_strategy"))
    assert first.experiment_id == renamed.experiment_id

    first_evaluation = registry.append_evaluation(
        experiment_id=first.experiment_id,
        fold_id="outer-0",
        metric_name="net_sharpe",
        metric_value=0.8,
        environment_hash="env-a",
        metadata={"run": 1},
    )
    registry.append_evaluation(
        experiment_id=first.experiment_id,
        fold_id="outer-0",
        metric_name="net_sharpe",
        metric_value=1.1,
        environment_hash="env-a",
        metadata={"run": 2},
    )
    evaluations = registry.evaluations(first.experiment_id)
    assert len(registry.experiments()) == 1
    assert len(evaluations) == 2
    assert evaluations[0] == first_evaluation
    assert evaluations[0].metric_value == 0.8
    assert registry.aliases(first.experiment_id) == [
        "causal_momentum",
        "renamed_strategy",
    ]


def test_sqlite_wal_and_triggers_enforce_append_only(tmp_path) -> None:
    path = tmp_path / "experiments.sqlite3"
    registry = ExperimentRegistry(path)
    spec = registry.register_experiment(_spec())
    evaluation = registry.append_evaluation(
        experiment_id=spec.experiment_id,
        fold_id="outer-1",
        metric_name="net_sharpe",
        metric_value=0.4,
        environment_hash="env",
    )
    connection = sqlite3.connect(path)
    assert connection.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        connection.execute(
            "UPDATE evaluation_records SET metric_value = 99 WHERE evaluation_id = ?",
            (evaluation.evaluation_id,),
        )
    connection.rollback()
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        connection.execute(
            "DELETE FROM experiment_specs WHERE experiment_id = ?",
            (spec.experiment_id,),
        )
    connection.close()


def test_registry_derived_effective_trial_count_and_dsr_source(tmp_path) -> None:
    registry = ExperimentRegistry(tmp_path / "experiments.sqlite3")
    for seed in (1, 2):
        spec = registry.register_experiment(_spec(seed=seed))
        registry.append_evaluation(
            experiment_id=spec.experiment_id,
            fold_id="aggregate",
            metric_name="net_sharpe",
            metric_value=0.5,
            environment_hash="env",
        )
    # A rerun increases evaluation attempts, not unique/effective trials.
    registry.append_evaluation(
        experiment_id=registry.experiments()[0].experiment_id,
        fold_id="aggregate",
        metric_name="net_sharpe",
        metric_value=0.6,
        environment_hash="env",
    )
    counts = registry.trial_counts()
    assert counts.raw_trial_count == counts.effective_trial_count == 2
    assert counts.unique_experiments == 2
    assert counts.evaluation_count == 3
    correlated = registry.trial_counts(empirical_trial_correlation=np.ones((2, 2)))
    assert correlated.effective_trial_count == 1

    rng = np.random.default_rng(8)
    summary = summarize_sharpe(
        rng.normal(0.001, 0.01, 200),
        periods_per_year=252,
        trials=9999,
        experiment_registry=registry,
        bootstrap_iters=100,
    )
    assert summary["trials"] == 2
    assert summary["trial_count_source"] == "experiment_registry"


def test_feature_artifact_identity_binds_data_and_provenance(tmp_path) -> None:
    store = FeatureStore(tmp_path / "features")
    frame = pd.DataFrame({"alpha": [1.0, 2.0, 3.0]})
    common = {
        "params": {"window": 10},
        "feature_code_hash": "1" * 64,
        "schema_hash": dataframe_schema_hash(frame),
        "timeframe": "1h",
    }
    first = store.put(
        "BTC/USDT",
        "momentum",
        frame,
        input_data_manifest_sha="3" * 64,
        **common,
    )
    second = store.put(
        "BTC/USDT",
        "momentum",
        frame,
        input_data_manifest_sha="4" * 64,
        **common,
    )
    assert len(first) == 64
    assert first != second
    assert len(store.versions("BTC/USDT", "momentum")) == 2
    with pytest.raises(FeatureStoreError, match="ambiguous"):
        store.get("BTC/USDT", "momentum", params={"window": 10})
    loaded = store.get("BTC/USDT", "momentum", params={"window": 10}, artifact_id=first)
    pd.testing.assert_frame_equal(loaded, frame)


def test_csv_fallback_is_read_symmetrically_after_cold_start(
    tmp_path, monkeypatch
) -> None:
    index = pd.date_range("2025-01-01", periods=4, freq="h", name="timestamp")
    frame = pd.DataFrame(
        {"alpha": [1.0, 2.5, 3.0, 4.5], "count": [1, 2, 3, 4]}, index=index
    )

    def fail_parquet(*args, **kwargs):
        raise RuntimeError("parquet engine unavailable")

    monkeypatch.setattr(pd.DataFrame, "to_parquet", fail_parquet)
    store = FeatureStore(tmp_path / "features")
    artifact_id = store.put(
        "ETH/USDT",
        "test_feature",
        frame,
        params={"window": 2},
        feature_code_hash="a" * 64,
        input_data_manifest_sha="b" * 64,
        timeframe="1h",
    )
    manifests = list((tmp_path / "features").rglob("*.artifact.json"))
    manifest = json.loads(manifests[0].read_text(encoding="utf-8"))
    assert manifest["storage_format"] == "csv"

    cold_store = FeatureStore(tmp_path / "features")
    loaded = cold_store.get(
        "ETH/USDT",
        "test_feature",
        params={"window": 2},
        artifact_id=artifact_id,
    )
    pd.testing.assert_frame_equal(loaded, frame)
