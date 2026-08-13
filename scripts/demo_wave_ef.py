#!/usr/bin/env python
"""Demo: end-to-end artifact → calibration → promotion → decision (Wave E/F).

This script demonstrates the complete research governance pipeline:
1. Build a StrategyArtifact from code + data + params
2. Persist to SQLite with cryptographic chain integrity
3. Calibrate Execution Simulator V2 from testnet fills
4. Promote artifact through lifecycle with evidence gates
5. Make calibrated decisions with risk appetite policies
"""

from __future__ import annotations

import tempfile
from datetime import UTC, datetime
from pathlib import Path

import polars as pl

from trading_agent.execution.simulator import (
    MarketReplayEngine,
    OrderIntent,
    SimOrderType,
    SimSide,
    SimulationConfig,
)
from trading_agent.execution.simulator.calibration import (
    CalibrationSample,
    SimulatorCalibrator,
    collect_testnet_fills,
)
from trading_agent.research import (
    Action,
    ArtifactLifecycle,
    DecisionPolicy,
    DriftMonitor,
    PersistentArtifactStore,
    PromotionPolicy,
    PromotionState,
    ThresholdDecisionPolicy,
    UncertaintySignal,
    build_strategy_artifact,
    calibration_evidence,
    drift_check_evidence,
    manual_review_evidence,
    reality_gap_evidence,
    soak_test_evidence,
    uncertainty_signal_to_decision,
)
from trading_agent.execution.simulator.reality_gap import (
    compute_reality_gap,
)


def make_demo_df(n: int = 100) -> pl.DataFrame:
    """Deterministic OHLCV for demo."""
    import datetime as dt

    rows = []
    for i in range(n):
        o = 100.0 + i * 0.1
        c = o + 0.05
        rows.append(
            {
                "timestamp": dt.datetime(2026, 1, 1, tzinfo=dt.UTC)
                + dt.timedelta(hours=i),
                "open": o,
                "high": o + 0.2,
                "low": o - 0.2,
                "close": c,
                "volume": 10.0 + i * 0.1,
            }
        )
    return pl.DataFrame(rows)


def main():
    print("=" * 70)
    print("WAVE E/F DEMO: Artifact → Calibration → Promotion → Decision")
    print("=" * 70)

    # ─── 1. BUILD STRATEGY ARTIFACT ─────────────────────────────────────
    print("\n[1/5] Building StrategyArtifact...")
    df = make_demo_df(100)
    code_path = Path(__file__)

    artifact = build_strategy_artifact(
        strategy_name="enhanced_ma",
        code_path=code_path,
        df=df,
        params={"fast": 20, "slow": 80, "adx_period": 40},
        execution_model_version="2.0.0",
        framework_version="0.1.0",
        metadata={"author": "trading-agent", "phase": "wave_ef"},
    )
    print(f"    ✓ Artifact created: {artifact.artifact_id}")
    print(f"    ✓ Strategy: {artifact.strategy_name}")
    print(f"    ✓ Params hash: {artifact.parameter_hash[:16]}...")
    print(f"    ✓ Data manifest: {artifact.data_manifest_sha[:16]}...")

    # ─── 2. PERSIST TO SQLITE WITH CRYPTO CHAIN ────────────────────────
    print("\n[2/5] Persisting to SQLite with cryptographic chain...")
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "artifacts.db"
        store = PersistentArtifactStore(db_path)

        # Add artifact (genesis row)
        store.add(artifact)
        print(f"    ✓ Stored artifact {artifact.artifact_id}")

        # Add second version (chained)
        artifact_v2 = build_strategy_artifact(
            strategy_name="enhanced_ma",
            code_path=code_path,
            df=df,
            params={"fast": 20, "slow": 80, "adx_period": 40, "trailing_atr": 2.0},
            execution_model_version="2.0.0",
            framework_version="0.1.0",
            prev_artifact_id=artifact.artifact_id,
            metadata={"author": "trading-agent", "phase": "wave_ef", "version": 2},
        )
        store.add(artifact_v2)
        print(f"    ✓ Stored artifact v2: {artifact_v2.artifact_id}")

        # Verify chain integrity
        ok, err = store.verify_chain()
        print(
            f"    ✓ Chain verification: {'PASS' if ok else 'FAIL'}"
            + (f" ({err})" if not ok else "")
        )

        # Lineage
        chain = store.lineage(artifact_v2.artifact_id)
        print(f"    ✓ Lineage: {' → '.join(a.artifact_id for a in chain)}")

    # ─── 3. CALIBRATE SIMULATOR FROM TESTNET FILLS ─────────────────────
    print("\n[3/5] Calibrating Execution Simulator from testnet fills...")

    config = SimulationConfig(random_seed=42)
    engine = MarketReplayEngine(df, config=config, symbol="DEMO", initial_cash=10_000.0)

    # Simulate some fills as "testnet data"
    def demo_provider(i, eng):
        if i == 10:
            return [
                OrderIntent(
                    order_id="buy1",
                    side=SimSide.BUY,
                    order_type=SimOrderType.MARKET,
                    quantity=1.0,
                )
            ]
        if i == 30:
            return [
                OrderIntent(
                    order_id="sell1",
                    side=SimSide.SELL,
                    order_type=SimOrderType.MARKET,
                    quantity=eng.ledger.inventory_base,
                )
            ]
        if i == 50:
            return [
                OrderIntent(
                    order_id="buy2",
                    side=SimSide.BUY,
                    order_type=SimOrderType.MARKET,
                    quantity=1.0,
                )
            ]
        if i == 70:
            return [
                OrderIntent(
                    order_id="sell2",
                    side=SimSide.SELL,
                    order_type=SimOrderType.MARKET,
                    quantity=eng.ledger.inventory_base,
                )
            ]
        return []

    testnet_samples = collect_testnet_fills(engine, demo_provider)
    print(f"    ✓ Collected {len(testnet_samples)} testnet fill samples")

    # Calibrate
    calibrator = SimulatorCalibrator(config)
    for s in testnet_samples:
        calibrator.add_sample(s)

    # Add some maker samples too
    for i in range(10):
        calibrator.add_sample(
            CalibrationSample(
                bar_index=i,
                side="buy",
                quantity=1.0,
                arrival_mid=100.0 + i * 0.1,
                fill_vwap=100.0 + i * 0.1 + 0.005,
                spread_bps=5.0,
                latency_ms=30.0,
                is_maker=True,
                timestamp=datetime.now(UTC).isoformat(),
                aggressor="limit_passive",
            )
        )

    cal_result = calibrator.calibrate()
    calibrated_config = calibrator.apply_to_config(cal_result)

    print(
        f"    ✓ Calibrated passive_fill_prob: {cal_result.fill_model.passive_fill_prob:.3f}"
    )
    print(f"    ✓ Calibrated impact_coeff: {cal_result.impact_model.impact_coeff:.3f}")
    print(
        f"    ✓ Calibrated decay_half_life: {cal_result.impact_model.impact_decay_half_life_bars:.2f} bars"
    )
    print(
        f"    ✓ Calibrated adverse_selection: {cal_result.impact_model.adverse_selection_bps:.2f} bps"
    )

    # ─── 4. PROMOTE ARTIFACT WITH EVIDENCE GATES ───────────────────────
    print("\n[4/5] Promoting artifact through lifecycle with evidence gates...")

    # Reality Gap Report (simulator vs backtest)
    # Use very close numbers to pass default 0.5 threshold
    backtest_metrics = {
        "fill_ratio": 0.95,
        "slippage_bps": 1.5,
        "implementation_shortfall_bps": 2.0,
        "trade_count": 10,
        "rejected_order_rate": 0.02,
        "partial_fill_rate": 0.05,
        "turnover": 10000.0,
        "avg_latency_ms": 30.0,
        "spread_cost_quote": 5.0,
        "fees_quote": 5.0,
        "sharpe": 2.0,
        "total_return_pct": 0.1,
        "max_drawdown_pct": 0.05,
        "tracking_error_bps": 10.0,
    }
    sim_metrics = {
        "fill_ratio": 0.94,
        "slippage_bps": 1.8,
        "implementation_shortfall_bps": 2.2,
        "trade_count": 9,
        "rejected_order_rate": 0.022,
        "partial_fill_rate": 0.055,
        "turnover": 9900.0,
        "avg_latency_ms": 33.0,
        "spread_cost_quote": 5.3,
        "fees_quote": 5.0,
        "sharpe": 1.95,
        "total_return_pct": 0.095,
        "max_drawdown_pct": 0.052,
        "tracking_error_bps": 11.0,
    }

    # Custom lenient thresholds for demo
    demo_thresholds = {
        "fill_ratio": 0.50,
        "slippage_bps": 0.50,
        "implementation_shortfall_bps": 0.50,
        "trade_count": 0.50,
        "turnover": 0.50,
        "avg_latency_ms": 0.50,
        "spread_cost_quote": 0.50,
        "fees_quote": 0.50,
        "sharpe": 0.50,
        "total_return_pct": 0.50,
        "max_drawdown_pct": 0.50,
        "tracking_error_bps": 0.50,
        "rejected_order_rate": 0.50,
        "partial_fill_rate": 0.50,
    }

    rg_report = compute_reality_gap(
        environment="simulator",
        reference_environment="backtest",
        observed=sim_metrics,
        reference=backtest_metrics,
        thresholds=demo_thresholds,
    )
    print(
        f"    ✓ RealityGap score: {rg_report.score:.3f} (gate: {'PASS' if rg_report.pass_gate else 'FAIL'})"
    )

    # Drift Monitor
    drift = DriftMonitor()
    drift_results = drift.check_all(
        vol_ref=0.01,
        vol_current=0.011,
        spread_ref=5.0,
        spread_current=5.2,
        fill_rate_ref=1.0,
        fill_rate_current=0.95,
    )
    health = drift.health_state(drift_results)
    print(f"    ✓ Drift health: {health.value}")

    # Promotion Policy with evidence - use more lenient threshold for demo
    policy = PromotionPolicy(
        reality_gap_threshold=1.0,  # allow larger gap for demo
        drift_monitor=drift,
        min_calibration_score=0.7,
    )

    lifecycle = ArtifactLifecycle(artifact.artifact_id)

    # EXPLORATORY → REVIEWED (needs manual_review)
    policy.validate_evidence(
        artifact.artifact_id,
        PromotionState.EXPLORATORY,
        PromotionState.REVIEWED,
        [
            manual_review_evidence(
                "Strategy logic reviewed, no look-ahead bias", "analyst"
            )
        ],
    )
    lifecycle.transition(PromotionState.REVIEWED, note="Manual review passed")
    print("    ✓ EXPLORATORY → REVIEWED")

    # REVIEWED → CANARY_ELIGIBLE (needs reality_gap + drift_check)
    policy.validate_evidence(
        artifact.artifact_id,
        PromotionState.REVIEWED,
        PromotionState.CANARY_ELIGIBLE,
        [
            reality_gap_evidence(rg_report),
            drift_check_evidence(
                health.value, {r.detector: r.to_dict() for r in drift_results}
            ),
        ],
    )
    lifecycle.transition(
        PromotionState.CANARY_ELIGIBLE, note="Reality gap + drift checks passed"
    )
    print("    ✓ REVIEWED → CANARY_ELIGIBLE")

    # CANARY_ELIGIBLE → CANARY_PROMOTED (needs calibration evidence too)
    policy.validate_evidence(
        artifact.artifact_id,
        PromotionState.CANARY_ELIGIBLE,
        PromotionState.CANARY_PROMOTED,
        [
            reality_gap_evidence(rg_report),
            drift_check_evidence(
                health.value, {r.detector: r.to_dict() for r in drift_results}
            ),
            calibration_evidence(0.85, {"brier_score": 0.08, "ece": 0.02}),
        ],
    )
    lifecycle.transition(
        PromotionState.CANARY_PROMOTED, note="Calibration evidence added"
    )
    print("    ✓ CANARY_ELIGIBLE → CANARY_PROMOTED")

    # CANARY_PROMOTED → CANARY_LIVE (needs soak_test + drift_check)
    policy.validate_evidence(
        artifact.artifact_id,
        PromotionState.CANARY_PROMOTED,
        PromotionState.CANARY_LIVE,
        [
            soak_test_evidence(
                days=30, lifecycles=100, gates_passed=True, details={"dup_orders": 0}
            ),
            drift_check_evidence(
                health.value, {r.detector: r.to_dict() for r in drift_results}
            ),
        ],
    )
    lifecycle.transition(PromotionState.CANARY_LIVE, note="30-day soak test passed")
    print("    ✓ CANARY_PROMOTED → CANARY_LIVE")

    print(f"    ✓ Final state: {lifecycle.state.value}")
    print(
        f"    ✓ Evidence stored: {len(policy.get_evidence(artifact.artifact_id))} items"
    )
    print(f"    ✓ History: {len(lifecycle.events)} transitions")

    # ─── 5. CALIBRATED DECISIONS WITH RISK APPETITE ────────────────────
    print("\n[5/5] Making calibrated decisions with risk appetite policies...")

    # Model produces UncertaintySignal
    signal = UncertaintySignal(
        expected_return=0.012,  # 1.2% expected return
        prediction_interval_lower=-0.008,
        prediction_interval_upper=0.032,
        calibration_score=0.88,
        ood_score=0.15,
        horizon="1h",
    )
    print(
        f"    ✓ Model signal: E[return]={signal.expected_return:.3f}, calibration={signal.calibration_score:.2f}, OOD={signal.ood_score:.2f}"
    )
    print(f"    ✓ Legacy uncertainty_state: {signal.uncertainty_state.value}")

    # Convert to CalibratedDecision with isotonic calibration
    decision = uncertainty_signal_to_decision(signal, temperature=1.0)
    print("    ✓ CalibratedDecision probs:")
    for a in Action:
        print(f"        {a.value:8s}: {decision.action_probabilities[a]:.3f}")

    # Different risk appetites
    for appetite in ("aggressive", "moderate", "conservative"):
        policy = DecisionPolicy(appetite)
        allowed = policy.allowed_actions(decision)
        recommended = policy.recommended_action(decision)
        allowed_str = "{" + ", ".join(sorted(a.value for a in allowed)) + "}"
        print(
            f"    ✓ {appetite:12s} → allowed: {allowed_str:25s} recommended: {recommended.value}"
        )

    # Backward compat adapter
    adapter = ThresholdDecisionPolicy("moderate")
    allowed_legacy = adapter.allowed_actions(signal)
    print(f"    ✓ Legacy adapter (moderate): {sorted(a.value for a in allowed_legacy)}")

    print("\n" + "=" * 70)
    print("DEMO COMPLETE — All Wave E/F components working end-to-end")
    print("=" * 70)


if __name__ == "__main__":
    main()
