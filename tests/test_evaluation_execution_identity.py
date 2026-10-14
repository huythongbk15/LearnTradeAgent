"""Versioned trial accounting, exclusively temporary/synthetic registries."""
import copy
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pytest

from trading_agent.alpha_research.stats import summarize_sharpe
from trading_agent.research.trials import ExperimentRegistry, ExperimentSpec, execution_scope_identity


@pytest.fixture
def trial(tmp_path):
    registry = ExperimentRegistry(tmp_path / "trial.sqlite3")
    spec = ExperimentSpec.build(strategy_name="synthetic", strategy_code_sha="a", data_manifest_sha="b",
                                feature_schema_hash="c", params_hash="d", search_family="test",
                                search_space_hash="e", target_horizon="1h", evaluator_version="v1", seed=42)
    registry.register_experiment(spec)
    context = {"trial_identity_version": 2, "protocol_fingerprint": "a" * 64,
               "source": {"code_commit": "b" * 40, "tree_fingerprint": "c" * 64},
               "environment": {"python": "synthetic"}, "runtime_config": {"initial_capital": 10000}}
    return registry, dict(experiment_id=spec.experiment_id, fold_id="fold_000", trial_phase="INNER_VALIDATION",
                          metric_name="sharpe", metric_value=1.0, environment_hash="environment",
                          metadata={"evaluation_identity": {"execution_context": context}, "status": "COMPLETED"},
                          idempotent=True, execution_context=context)


def new_scope(kwargs, **context_changes):
    newer = copy.deepcopy(kwargs)
    newer["execution_context"].update(protocol_fingerprint="d" * 64, **context_changes)
    newer["metadata"]["evaluation_identity"]["execution_context"] = copy.deepcopy(newer["execution_context"])
    return newer


def test_scope_preserves_legacy_new_attempt_and_exact_replay(trial):
    registry, kwargs = trial
    legacy = registry.append_evaluation(**{k: v for k, v in kwargs.items() if k not in ("metadata", "execution_context")},
                                        metadata={"legacy": True})
    first = registry.append_evaluation(**kwargs)
    assert registry.append_evaluation(**kwargs) == first
    second = registry.append_evaluation(**new_scope(kwargs))
    assert second.evaluation_id != first.evaluation_id != legacy.evaluation_id
    assert registry.evaluations() == [legacy, first, second]
    counts = registry.trial_counts()
    assert counts.unique_experiments == 1
    assert counts.raw_trial_count == counts.effective_trial_count == 3
    assert counts.total_trial_runs == counts.evaluation_count == 3
    assert sum(counts.search_family_counts.values()) == 3
    summary = summarize_sharpe(np.random.default_rng(42).normal(.001, .01, 100), periods_per_year=252,
                               experiment_registry=registry, trials=1, bootstrap_iters=20)
    assert summary["trials"] == 3  # Caller cannot reset burden by supplying trials=1.
    with pytest.raises(ValueError, match="candidate-context trial count"):
        registry.trial_counts(empirical_trial_correlation=np.ones((1, 1)))


@pytest.mark.parametrize("change", [{"metric_value": 2}, {"environment_hash": "changed"},
                                   {"metric_name": "changed"}])
def test_same_context_metric_or_environment_change_is_rejected(trial, change):
    registry, kwargs = trial
    first = registry.append_evaluation(**kwargs)
    with pytest.raises(ValueError, match="trial replay"):
        registry.append_evaluation(**{**kwargs, **change})
    assert registry.evaluations() == [first]


def test_failed_trial_is_retained_and_not_replaced_by_success(trial):
    registry, kwargs = trial
    failed = copy.deepcopy(kwargs)
    failed.update(metric_value=0, metric_name="inner_val_trial_failed")
    failed["metadata"]["status"] = "FAILED"
    failure = registry.append_evaluation(**failed)
    with pytest.raises(ValueError, match="trial replay"):
        registry.append_evaluation(**kwargs)
    successful = registry.append_evaluation(**new_scope(kwargs))
    assert registry.evaluations() == [failure, successful]
    assert registry.trial_counts().raw_trial_count == 2


def test_fold_and_phase_repetition_does_not_multiply_selection_opportunities(trial):
    registry, kwargs = trial
    for fold in ("fold_000", "fold_001"):
        for phase in ("INNER_VALIDATION", "OUTER_OOS"):
            registry.append_evaluation(**{**kwargs, "fold_id": fold, "trial_phase": phase})
    counts = registry.trial_counts()
    assert counts.evaluation_count == counts.total_trial_runs == 4
    assert counts.unique_experiments == counts.raw_trial_count == counts.effective_trial_count == 1


def test_independent_callers_converge_with_prior_legacy_and_other_scope(trial):
    registry, kwargs = trial
    registry.append_evaluation(**new_scope(kwargs))
    with ThreadPoolExecutor(max_workers=2) as pool:
        records = list(pool.map(lambda _: ExperimentRegistry(registry.path).append_evaluation(**kwargs), range(2)))
    assert records[0] == records[1]
    assert registry.trial_counts().evaluation_count == registry.trial_counts().raw_trial_count == 2


@pytest.mark.parametrize("mutation", ["wrong_version", "nan", "missing_source", "bad_fingerprint",
                                      "unserializable", "bool_version"])
def test_invalid_context_fails_before_append(trial, mutation):
    registry, kwargs = trial
    context = copy.deepcopy(kwargs["execution_context"])
    if mutation == "wrong_version":
        context["trial_identity_version"] = 1
    elif mutation == "bool_version":
        context["trial_identity_version"] = True
    elif mutation == "nan":
        context["runtime_config"]["initial_capital"] = float("nan")
    elif mutation == "missing_source":
        context["source"] = {}
    elif mutation == "unserializable":
        context["runtime_config"]["unknown"] = object()
    else:
        context["protocol_fingerprint"] = "not-a-hash"
    with pytest.raises((ValueError, TypeError)):
        execution_scope_identity(context)
    assert registry.evaluations() == []


def test_explicit_context_required_and_marker_cannot_be_injected(trial):
    registry, kwargs = trial
    record = registry.append_evaluation(**kwargs)
    injected = {k: v for k, v in kwargs.items() if k != "execution_context"}
    injected["metadata"] = record.metadata
    with pytest.raises(ValueError, match="explicit execution context"):
        registry.append_evaluation(**injected)
    with pytest.raises(ValueError, match="atomic idempotent"):
        registry.append_evaluation(**{**kwargs, "idempotent": False})
    assert registry.evaluations() == [record]


@pytest.mark.parametrize("fold", ["fold_000", "fold_001"])
def test_same_protocol_cannot_redeclare_runtime_context(trial, fold):
    registry, kwargs = trial
    first = registry.append_evaluation(**kwargs)
    changed = new_scope(kwargs, runtime_config={"initial_capital": 999})
    changed["fold_id"] = fold
    changed["execution_context"]["protocol_fingerprint"] = kwargs["execution_context"]["protocol_fingerprint"]
    changed["metadata"]["evaluation_identity"]["execution_context"] = copy.deepcopy(changed["execution_context"])
    with pytest.raises(ValueError, match="same protocol declares"):
        registry.append_evaluation(**changed)
    assert registry.evaluations() == [first]


def test_returned_record_owns_a_frozen_metadata_snapshot(trial):
    registry, kwargs = trial
    record = registry.append_evaluation(**kwargs)
    kwargs["metadata"]["status"] = "CHANGED_AFTER_APPEND"
    kwargs["execution_context"]["runtime_config"]["initial_capital"] = 999
    assert record.metadata["status"] == "COMPLETED"
    assert record.metadata["evaluation_identity"]["execution_context"]["runtime_config"]["initial_capital"] == 10000
    assert registry.evaluations() == [record]
