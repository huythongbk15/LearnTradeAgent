"""Focused S3 provenance and campaign-contract regression tests."""

from __future__ import annotations

import hashlib
import json
from types import SimpleNamespace

import pytest

from trading_agent.backtest.nested_wfo import (
    InnerSelectionFreeze,
    WFOStudyManifest,
    WFOSpec,
    _persist_study_manifest,
    _resolve_training_contract,
)
from trading_agent.backtest.synthetic_data import seed_safe, synthetic_wfo_spec


def _manifest(**overrides) -> WFOStudyManifest:
    values = {
        "strategy_id": "rsi",
        "symbol": "BTC/USDT",
        "timeframe": "1h",
        "param_grid": {"period": [14, 21]},
        "cost_scenarios": (
            {"name": "1x", "fee_multiplier": 1.0, "slippage_multiplier": 1.0},
        ),
        "fold_windows": (
            {
                "fold_id": "fold_000",
                "inner_train_start": 0,
                "inner_train_end": 100,
                "inner_val_start": 110,
                "inner_val_end": 150,
                "outer_test_start": 160,
                "outer_test_end": 200,
                "purge": 10,
                "embargo": 10,
            },
        ),
        "purge_bars": 10,
        "embargo_bars": 10,
        "min_oos_trades": 30,
        "search_family": "s3_wfo",
        "evaluator_version": "v1",
        "training_contract": "STATELESS_DETERMINISTIC",
        "seed": 42,
        "commit_sha": "a" * 40,
        "worktree_dirty": False,
        "strategy_code_sha": "b" * 64,
        "data_manifest_sha": "c" * 64,
        "feature_schema_hash": "d" * 64,
        "search_space_hash": "e" * 64,
        "evidence_class": "REAL_MARKET",
    }
    values.update(overrides)
    return WFOStudyManifest(**values)


def test_study_manifest_is_content_addressed_and_idempotent(tmp_path):
    manifest = _manifest()
    first = _persist_study_manifest(tmp_path, manifest)
    second = _persist_study_manifest(tmp_path, manifest)

    assert first == second
    stored = json.loads(first.read_text(encoding="utf-8"))
    assert stored["manifest_id"] == manifest.manifest_id
    assert stored["provenance_eligible"] is True


def test_study_manifest_rejects_tampered_existing_artifact(tmp_path):
    manifest = _manifest()
    path = _persist_study_manifest(tmp_path, manifest)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["min_oos_trades"] = 1
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="different content"):
        _persist_study_manifest(tmp_path, manifest)


def test_study_identity_binds_data_fold_and_evaluator():
    baseline = _manifest()
    changed_data = _manifest(data_manifest_sha="f" * 64)
    changed_evaluator = _manifest(evaluator_version="v2")
    changed_fold = _manifest(
        fold_windows=({**baseline.fold_windows[0], "outer_test_end": 201},)
    )

    assert (
        len(
            {
                baseline.manifest_id,
                changed_data.manifest_id,
                changed_evaluator.manifest_id,
                changed_fold.manifest_id,
            }
        )
        == 4
    )


def test_synthetic_or_dirty_study_is_not_provenance_eligible():
    assert not _manifest(evidence_class="SYNTHETIC_TEST_ONLY").provenance_eligible
    assert not _manifest(worktree_dirty=True).provenance_eligible


def test_stateful_candidate_is_rejected_until_fold_local_fit_is_supported():
    class StatefulAdapter:
        def fit(self):
            return None

    with pytest.raises(ValueError, match="stateful strategy training is not supported"):
        _resolve_training_contract(StatefulAdapter())

    assert _resolve_training_contract(object()) == "STATELESS_DETERMINISTIC"


def test_outer_freeze_identity_binds_commit_data_features_and_evaluator():
    common = {
        "fold_id": "fold_000",
        "strategy_id": "rsi",
        "symbol": "BTC/USDT",
        "timeframe": "1h",
        "best_params": {"period": 14, "cost_scenario": "1x"},
        "best_val_sharpe": 1.2,
        "inner_train_end": 100,
        "inner_val_start": 110,
        "inner_val_end": 150,
        "search_space_hash": "search",
        "candidate_count": 2,
        "commit_sha": "a" * 40,
        "data_manifest_sha": "b" * 64,
        "feature_schema_hash": "c" * 64,
        "evaluator_version": "v1",
    }
    baseline = InnerSelectionFreeze(**common)
    identities = {baseline.freeze_id}
    for field, value in (
        ("commit_sha", "d" * 40),
        ("data_manifest_sha", "e" * 64),
        ("feature_schema_hash", "f" * 64),
        ("evaluator_version", "v2"),
    ):
        identities.add(InnerSelectionFreeze(**{**common, field: value}).freeze_id)

    assert len(identities) == 5


def test_minimum_trade_policy_is_aggregate_pair_strategy_oos():
    legacy = WFOSpec(strategy_id="rsi", symbol="BTC/USDT", min_trades_per_fold=7)
    explicit = WFOSpec(
        strategy_id="rsi",
        symbol="BTC/USDT",
        min_trades_per_fold=7,
        min_oos_trades=30,
    )

    assert legacy.effective_min_oos_trades == 7
    assert explicit.effective_min_oos_trades == 30


def test_synthetic_seed_is_sha256_stable_and_evidence_is_non_promotable():
    payload = "rsi\0BTC/USDT".encode()
    expected = (
        int.from_bytes(hashlib.sha256(payload).digest()[:8], "big") % (2**31)
    ) + 1
    spec, _, _ = synthetic_wfo_spec("rsi", "BTC/USDT")

    assert seed_safe("rsi", "BTC/USDT") == expected
    assert spec.seed == expected
    assert spec.evidence_class == "SYNTHETIC_TEST_ONLY"


def test_real_evidence_runner_uses_canonical_spec_and_requested_timeframe(
    monkeypatch, tmp_path
):
    import scripts.run_wfo_evidence as runner

    captured = {}

    def fake_build(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(strategy_id=kwargs["strategy_id"])

    fake_result = SimpleNamespace(
        aggregate_metrics={
            "evidence_class": "REAL_MARKET",
            "promotable": False,
            "study_manifest_id": "sha256:study",
        },
        outer_results=[],
        passes_hard_gates=False,
    )
    monkeypatch.setattr(runner, "build_wfo_spec", fake_build)
    monkeypatch.setattr(runner, "run_nested_wfo", lambda *args, **kwargs: fake_result)

    summary = runner.run_real(tmp_path, "rsi", "ETH/USDT", "4h")

    assert captured == {
        "strategy_id": "rsi",
        "symbol": "ETH/USDT",
        "timeframe": "4h",
        "registry_path": str(tmp_path / "experiments.sqlite3"),
        "search_family": "s3_real_evidence",
    }
    assert summary["timeframe"] == "4h"
    assert summary["evidence_class"] == "REAL_MARKET"
