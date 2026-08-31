from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from scripts.full_system_backtest import _load_strategy_artifact
from scripts.full_system_backtest import FullSystemSimulator
from trading_agent.research.artifact import (
    StrategyArtifact,
    canonical_params,
    sha256_hex,
)
from trading_agent.strategies.canonical.candidates import build_legacy_candidate
from trading_agent.backtest.tournament import EvaluationCellSpec, run_cell
from trading_agent.research.forecast import ResearchStrategyRuntime, StrategyRuntime


def _artifact() -> StrategyArtifact:
    descriptor, _ = build_legacy_candidate(
        "enhanced_ma",
        {"fast_period": 15, "slow_period": 50, "target_exposure_pct": 0.25},
    )
    return StrategyArtifact(
        strategy_name="enhanced_ma",
        code_sha=descriptor.code_sha,
        data_manifest_sha="sha256:" + "a" * 64,
        parameter_hash=sha256_hex(
            canonical_params(
                {"fast_period": 15, "slow_period": 50, "target_exposure_pct": 0.25}
            )
        ),
        execution_model_version="full-system-v2",
        framework_version="authority-chain-v1",
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        metadata={
            "symbol": "BTC/USDT",
            "timeframe": "1h",
            "parameters": {
                "fast_period": 15,
                "slow_period": 50,
                "target_exposure_pct": 0.25,
            },
        },
    )


def test_strategy_artifact_manifest_round_trips_with_content_id(tmp_path):
    artifact = _artifact()
    path = tmp_path / "strategy-artifact.json"
    path.write_text(json.dumps(artifact.to_dict()), encoding="utf-8")

    loaded = _load_strategy_artifact(path)

    assert loaded.artifact_id == artifact.artifact_id
    assert loaded.data_manifest_sha == artifact.data_manifest_sha


def test_strategy_artifact_manifest_rejects_tampered_content(tmp_path):
    artifact = _artifact()
    payload = artifact.to_dict()
    payload["metadata"]["parameters"]["fast_period"] = 16
    path = tmp_path / "tampered.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="artifact_id does not match content"):
        _load_strategy_artifact(path)


def test_tournament_tail_window_contract_is_explicit():
    spec = EvaluationCellSpec("enhanced_ma", "BTC/USDT")

    with pytest.raises(ValueError, match="tail_bars must be positive"):
        run_cell(spec, tail_bars=0)
    with pytest.raises(ValueError, match="cannot be combined"):
        run_cell(spec, start=1, tail_bars=10)


def test_adaptive_simulator_requires_all_runtime_dependencies():
    with pytest.raises(ValueError, match="must be provided together"):
        FullSystemSimulator(adaptive_router=object())


def test_research_runtime_has_explicit_name_with_compatibility_alias():
    assert StrategyRuntime is ResearchStrategyRuntime
