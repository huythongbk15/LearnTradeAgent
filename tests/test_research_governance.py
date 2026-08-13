"""Research governance tests (Wave B) — artifacts, lifecycle, uncertainty,
abstention, drift and multiple-testing governance.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import numpy as np
import polars as pl
import pytest

from trading_agent.research import (
    AbstentionReason,
    ArtifactLifecycle,
    ArtifactStore,
    DriftLevel,
    DriftMonitor,
    PersistentArtifactStore,
    PromotionError,
    PromotionEvidence,
    PromotionPolicy,
    PromotionState,
    StrategyHealthState,
    TrialsRegistry,
    UncertaintySignal,
    UncertaintyState,
    build_strategy_artifact,
    drift_check_evidence,
    param_hash,
    psi,
    reality_gap_evidence,
    search_space_hash,
    should_abstain,
)


def make_df(n: int = 50) -> pl.DataFrame:
    rows = [
        {
            "timestamp": dt.datetime(2026, 1, 1, tzinfo=dt.UTC) + dt.timedelta(hours=i),
            "open": 100.0 + i,
            "high": 101.0 + i,
            "low": 99.0 + i,
            "close": 100.5 + i,
            "volume": 10.0,
        }
        for i in range(n)
    ]
    return pl.DataFrame(rows)


class TestArtifact:
    def test_id_content_addressed(self, tmp_path: Path):
        code = tmp_path / "strat.py"
        code.write_text("def run(): pass")
        a1 = build_strategy_artifact(
            strategy_name="ma", code_path=code, df=make_df(), params={"fast": 5, "slow": 20},
            execution_model_version="2.0.0", framework_version="0.1.0",
        )
        a2 = build_strategy_artifact(
            strategy_name="ma", code_path=code, df=make_df(), params={"fast": 5, "slow": 20},
            execution_model_version="2.0.0", framework_version="0.1.0",
        )
        assert a1.artifact_id == a2.artifact_id
        # Changing any substantive field changes the id.
        a3 = build_strategy_artifact(
            strategy_name="ma", code_path=code, df=make_df(), params={"fast": 7, "slow": 20},
            execution_model_version="2.0.0", framework_version="0.1.0",
        )
        assert a1.artifact_id != a3.artifact_id
        # Data change changes the id.
        a4 = build_strategy_artifact(
            strategy_name="ma", code_path=code, df=make_df(60), params={"fast": 5, "slow": 20},
            execution_model_version="2.0.0", framework_version="0.1.0",
        )
        assert a1.artifact_id != a4.artifact_id

    def test_store_immutable_and_lineage(self):
        store = ArtifactStore()
        code = Path(__file__)
        a0 = build_strategy_artifact(strategy_name="ma", code_path=code, df=make_df(), params={"fast": 5}, execution_model_version="1", framework_version="0")
        a1 = build_strategy_artifact(strategy_name="ma", code_path=code, df=make_df(), params={"fast": 7}, execution_model_version="1", framework_version="0", prev_artifact_id=a0.artifact_id)
        store.add(a0)
        store.add(a1)
        with pytest.raises(ValueError):
            store.add(a0)  # immutable: duplicate rejected
        chain = store.lineage(a1.artifact_id)
        assert [c.artifact_id for c in chain] == [a1.artifact_id, a0.artifact_id]
        assert store.verify_integrity(a1.artifact_id)

    def test_hash_file_missing_raises(self):
        with pytest.raises(FileNotFoundError):
            build_strategy_artifact(strategy_name="x", code_path=Path("/nonexistent.py"), df=make_df(), params={}, execution_model_version="1", framework_version="0")


class TestPersistentArtifactStore:
    def test_persist_and_retrieve(self, tmp_path: Path):
        db = tmp_path / "artifacts.db"
        store = PersistentArtifactStore(db)
        code = Path(__file__)
        a0 = build_strategy_artifact(strategy_name="ma", code_path=code, df=make_df(), params={"fast": 5}, execution_model_version="1", framework_version="0")
        a1 = build_strategy_artifact(strategy_name="ma", code_path=code, df=make_df(), params={"fast": 7}, execution_model_version="1", framework_version="0", prev_artifact_id=a0.artifact_id)
        store.add(a0)
        store.add(a1)

        # Retrieve
        got0 = store.get(a0.artifact_id)
        got1 = store.get(a1.artifact_id)
        assert got0.artifact_id == a0.artifact_id
        assert got1.artifact_id == a1.artifact_id
        assert got1.prev_artifact_id == a0.artifact_id

    def test_immutable_duplicate_rejected(self, tmp_path: Path):
        db = tmp_path / "artifacts.db"
        store = PersistentArtifactStore(db)
        code = Path(__file__)
        a0 = build_strategy_artifact(strategy_name="ma", code_path=code, df=make_df(), params={"fast": 5}, execution_model_version="1", framework_version="0")
        store.add(a0)
        with pytest.raises(ValueError):
            store.add(a0)

    def test_lineage_order(self, tmp_path: Path):
        db = tmp_path / "artifacts.db"
        store = PersistentArtifactStore(db)
        code = Path(__file__)
        a0 = build_strategy_artifact(strategy_name="ma", code_path=code, df=make_df(), params={"fast": 5}, execution_model_version="1", framework_version="0")
        a1 = build_strategy_artifact(strategy_name="ma", code_path=code, df=make_df(), params={"fast": 7}, execution_model_version="1", framework_version="0", prev_artifact_id=a0.artifact_id)
        a2 = build_strategy_artifact(strategy_name="ma", code_path=code, df=make_df(), params={"fast": 9}, execution_model_version="1", framework_version="0", prev_artifact_id=a1.artifact_id)
        store.add(a0)
        store.add(a1)
        store.add(a2)

        chain = store.lineage(a2.artifact_id)
        assert [c.artifact_id for c in chain] == [a2.artifact_id, a1.artifact_id, a0.artifact_id]

    def test_verify_chain_ok(self, tmp_path: Path):
        db = tmp_path / "artifacts.db"
        store = PersistentArtifactStore(db)
        code = Path(__file__)
        a0 = build_strategy_artifact(strategy_name="ma", code_path=code, df=make_df(), params={"fast": 5}, execution_model_version="1", framework_version="0")
        a1 = build_strategy_artifact(strategy_name="ma", code_path=code, df=make_df(), params={"fast": 7}, execution_model_version="1", framework_version="0", prev_artifact_id=a0.artifact_id)
        store.add(a0)
        store.add(a1)

        ok, err = store.verify_chain()
        assert ok
        assert err is None

    def test_verify_chain_detects_tamper(self, tmp_path: Path):
        db = tmp_path / "artifacts.db"
        store = PersistentArtifactStore(db)
        code = Path(__file__)
        a0 = build_strategy_artifact(strategy_name="ma", code_path=code, df=make_df(), params={"fast": 5}, execution_model_version="1", framework_version="0")
        store.add(a0)

        # Tamper: directly modify the DB row
        import sqlite3
        conn = sqlite3.connect(db)
        conn.execute("UPDATE artifacts SET code_sha = 'tampered' WHERE artifact_id = ?", (a0.artifact_id,))
        conn.commit()
        conn.close()

        ok, err = store.verify_chain()
        assert not ok
        assert "integrity chain broken" in err

    def test_verify_integrity(self, tmp_path: Path):
        db = tmp_path / "artifacts.db"
        store = PersistentArtifactStore(db)
        code = Path(__file__)
        a0 = build_strategy_artifact(strategy_name="ma", code_path=code, df=make_df(), params={"fast": 5}, execution_model_version="1", framework_version="0")
        store.add(a0)
        assert store.verify_integrity(a0.artifact_id)
        assert not store.verify_integrity("nonexistent")

    def test_migration_from_memory(self, tmp_path: Path):
        db = tmp_path / "artifacts.db"
        persistent = PersistentArtifactStore(db)
        memory = ArtifactStore()
        code = Path(__file__)
        a0 = build_strategy_artifact(strategy_name="ma", code_path=code, df=make_df(), params={"fast": 5}, execution_model_version="1", framework_version="0")
        a1 = build_strategy_artifact(strategy_name="ma", code_path=code, df=make_df(), params={"fast": 7}, execution_model_version="1", framework_version="0", prev_artifact_id=a0.artifact_id)
        a2 = build_strategy_artifact(strategy_name="rsi", code_path=code, df=make_df(), params={"period": 14}, execution_model_version="1", framework_version="0")
        memory.add(a0)
        memory.add(a1)
        memory.add(a2)

        count = persistent.migrate_from_memory(memory)
        assert count == 3
        assert persistent.get(a0.artifact_id) is not None
        assert persistent.get(a1.artifact_id) is not None
        assert persistent.get(a2.artifact_id) is not None
        ok, _ = persistent.verify_chain()
        assert ok


class TestLifecycle:
    def test_normal_path(self):
        lc = ArtifactLifecycle("art-1")
        lc.transition(PromotionState.REVIEWED, note="manual review")
        lc.transition(PromotionState.CANARY_ELIGIBLE, note="gap gate passed")
        lc.transition(PromotionState.CANARY_PROMOTED, note="canary start")
        lc.transition(PromotionState.CANARY_LIVE, note="soak ok")
        assert lc.state == PromotionState.CANARY_LIVE
        assert len(lc.history) == 4

    def test_no_skip(self):
        lc = ArtifactLifecycle("art-2")
        with pytest.raises(PromotionError):
            lc.transition(PromotionState.CANARY_ELIGIBLE)  # skip REVIEWED

    def test_rejected_terminal(self):
        lc = ArtifactLifecycle("art-3")
        lc.transition(PromotionState.REJECTED, note="overfit")
        with pytest.raises(PromotionError):
            lc.transition(PromotionState.REVIEWED)

    def test_integrity_fail_closed(self):
        lc = ArtifactLifecycle("art-4")
        with pytest.raises(PromotionError):
            lc.transition(PromotionState.REVIEWED, artifact_ok=False)


class TestUncertainty:
    def test_low_only_increases_exposure(self):
        low = UncertaintySignal(expected_return=0.5, prediction_interval_lower=-0.2, prediction_interval_upper=1.2, calibration_score=0.95, ood_score=0.05)
        assert low.uncertainty_state == UncertaintyState.LOW
        assert low.can_increase_exposure

    def test_high_uncertainty_blocks(self):
        high = UncertaintySignal(expected_return=0.5, prediction_interval_lower=-2.0, prediction_interval_upper=3.0, calibration_score=0.4, ood_score=0.8)
        assert high.uncertainty_state == UncertaintyState.HIGH
        assert not high.can_increase_exposure

    def test_medium_calibration(self):
        med = UncertaintySignal(expected_return=0.1, prediction_interval_lower=-0.5, prediction_interval_upper=0.7, calibration_score=0.6, ood_score=0.2)
        assert med.uncertainty_state == UncertaintyState.MEDIUM
        assert not med.can_increase_exposure


class TestAbstention:
    def test_nine_codes(self):
        assert len(AbstentionReason) == 9

    def test_recorded_abstention(self):
        a = should_abstain(symbol="BTC/USDT", strategy="ma", reason=AbstentionReason.HIGH_UNCERTAINTY, detail="interval width 8x")
        assert a.reason == AbstentionReason.HIGH_UNCERTAINTY
        assert a.to_dict()["symbol"] == "BTC/USDT"

    def test_invalid_reason_rejected(self):
        with pytest.raises(ValueError):
            should_abstain(symbol="X", strategy="y", reason="not-a-code")  # type: ignore[arg-type]


class TestDrift:
    def test_psi_same_distribution_zero(self):
        x = np.random.default_rng(0).normal(0, 1, 1000)
        assert psi(x, x) == pytest.approx(0.0, abs=1e-6)

    def test_psi_shifted_large(self):
        x = np.random.default_rng(0).normal(0, 1, 1000)
        y = np.random.default_rng(1).normal(3, 1, 1000)
        assert psi(x, y) > 0.25

    def test_health_states(self):
        m = DriftMonitor()
        results = m.check_all(
            features_ref=np.array([1.0] * 100), features_current=np.array([1.0] * 100),
            returns_ref=np.array([0.0] * 100), returns_current=np.array([0.0] * 100),
            vol_ref=0.01, vol_current=0.011,
            spread_ref=5.0, spread_current=5.2,
            fill_rate_ref=1.0, fill_rate_current=0.99,
        )
        assert m.health_state(results) == StrategyHealthState.HEALTHY

        bad = m.check_all(
            features_ref=np.array([1.0] * 100), features_current=np.array([5.0] * 100),  # big shift
            returns_ref=np.array([0.0] * 100), returns_current=np.array([0.0] * 100),
        )
        assert any(r.level == DriftLevel.RED for r in bad)
        assert m.health_state(bad) == StrategyHealthState.SUSPENDED

    def test_volatility_drift_yellow(self):
        m = DriftMonitor()
        res = m.check_all(vol_ref=0.01, vol_current=0.016)
        assert res[0].level == DriftLevel.YELLOW
        assert m.health_state(res) == StrategyHealthState.DEGRADED


class TestTrials:
    def test_rename_does_not_reset_history(self):
        reg = TrialsRegistry()
        t1 = reg.record(strategy_name="ma", params={"fast": 5}, search_space={"fast": [3, 5, 7]}, metric_value=10.0)
        # Same content under a different display name → same trial id.
        t2 = reg.record(strategy_name="ma_v2", params={"fast": 5}, search_space={"fast": [3, 5, 7]}, metric_value=12.0)
        # param_hash is the same, so this is the SAME experiment content.
        assert param_hash({"fast": 5}) == t1.param_hash == t2.param_hash
        assert t1.trial_id == t2.trial_id
        assert reg.total_trials() == 1
        assert reg.evaluation_count() == 2
        assert "ma_v2" in t1.metadata["alias_names"]

    def test_search_space_hash_grouping(self):
        reg = TrialsRegistry()
        reg.record(strategy_name="ma", params={"fast": 5}, search_space={"fast": [3, 5, 7]}, metric_value=1.0)
        reg.record(strategy_name="ma", params={"fast": 7}, search_space={"fast": [3, 5, 7]}, metric_value=2.0)
        reg.record(strategy_name="ma", params={"fast": 9}, search_space={"fast": [3, 5, 9]}, metric_value=3.0)
        trials = reg.trials_for_strategy("ma")
        assert len(trials) == 3
        assert search_space_hash({"fast": [3, 5, 7]}) == trials[0].search_space_hash
        # Two of the three share the same search space.
        assert sum(1 for t in trials if t.search_space_hash == trials[0].search_space_hash) == 2

    def test_best_trial(self):
        reg = TrialsRegistry()
        reg.record(strategy_name="ma", params={"fast": 5}, search_space={}, metric_value=10.0)
        reg.record(strategy_name="ma", params={"fast": 7}, search_space={}, metric_value=22.0)
        best = reg.best_trial("ma")
        assert best.metric_value == 22.0
