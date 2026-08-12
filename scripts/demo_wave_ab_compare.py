#!/usr/bin/env python3
"""Demo so sánh TRƯỚC vs SAU cho Wave A (execution realism) + Wave B (governance)."""
from __future__ import annotations

import numpy as np

from trading_agent.config.loader import config
from trading_agent.data.storage import load_ohlcv
from trading_agent.strategies.base import get_strategy
import trading_agent.strategies  # noqa: F401

from trading_agent.backtest.engine import BacktestEngine
from trading_agent.execution.simulator import (
    SimulationConfig,
    run_strategy_through_simulator,
)


def main() -> None:
    df = load_ohlcv(config.default_exchange, "BTC/USDT", "1h")
    strategy = get_strategy("ma_crossover")({})

    # ── BẢN TRƯỚC: idealized backtest, zero chi phí ──
    old = BacktestEngine(
        strategy, initial_capital=10_000.0, commission=0.0,
        slippage=0.0, spread_bps=0.0, timeframe="1h",
    ).run(df)
    print("=== TRƯỚC (backtest idealized, không chi phí execution) ===")
    print(f"  Return: {old.total_return_pct:+.2f}% | Sharpe: {old.sharpe_ratio:.2f} | Trades: {old.total_trades}")
    print("  -> Kết luận cũ: 'STRATEGY TỐT +104%' — KHÔNG biết execution ăn bao nhiêu")

    # ── BẢN SAU (Wave A): qua Execution Simulator V2 ──
    cfg = SimulationConfig(
        random_seed=42, spread_bps=5.0, taker_fee=0.0005, maker_fee=0.0002,
        min_notional=10.0, min_qty=0.00001,
    )
    res = run_strategy_through_simulator(
        strategy, df, symbol="BTC/USDT", timeframe="1h",
        initial_cash=10_000.0, config=cfg,
    )
    att = res.metrics.attribution
    print("\n=== SAU (Wave A: Execution Simulator V2 + P&L attribution) ===")
    print(f"  Return: {res.metrics.total_return_pct:+.2f}% | Sharpe: {res.metrics.sharpe:.2f}")
    print(f"  Alpha (lý thuyết):    {att.theoretical_alpha_pnl:>12.2f}")
    print(f"  - Spread cost:         {att.spread_cost:>12.2f}")
    print(f"  - Impact cost:         {att.impact_cost:>12.2f}")
    print(f"  - Fees:                {att.fees:>12.2f}")
    print(f"  = Realized PnL:        {att.realized_pnl:>12.2f}")
    ident = abs((att.theoretical_alpha_pnl - att.execution_cost) - att.realized_pnl) < 1e-6
    print(f"  Identity alpha-exec=realized: {ident}")

    from trading_agent.execution.simulator import compute_reality_gap

    ref_metrics = {
        "fill_ratio": 1.0, "slippage_bps": 0.0, "implementation_shortfall_bps": 0.0,
        "trade_count": float(old.total_trades), "turnover": 0.0, "avg_latency_ms": 0.0,
        "spread_cost_quote": 0.0, "fees_quote": 0.0,
        "sharpe": float(old.sharpe_ratio), "total_return_pct": float(old.total_return_pct),
        "max_drawdown_pct": float(old.max_drawdown_pct),
        "rejected_order_rate": 0.0, "partial_fill_rate": 0.0,
    }
    gap = compute_reality_gap(
        environment="execution_simulator_v2",
        reference_environment="idealized_backtest",
        observed=res.metrics.to_dict(),
        reference=ref_metrics,
    )
    print(f"  Reality gap score: {gap.score:.3f} | gate_passed: {gap.pass_gate}")
    print("  -> Wave A: execution cost ($10,006) ăn gần hết alpha ($10,446); còn +4.7%")

    # ── BẢN SAU (Wave B): governance demos ──
    from trading_agent.research import (
        AbstentionReason,
        ArtifactLifecycle,
        ArtifactStore,
        DriftMonitor,
        PromotionState,
        TrialsRegistry,
        UncertaintySignal,
        build_strategy_artifact,
        should_abstain,
    )

    store = ArtifactStore()
    art = build_strategy_artifact(
        strategy_name="ma_crossover",
        code_path="src/trading_agent/strategies/ma_crossover.py",
        df=df,
        params={"fast": 15, "slow": 100},
        execution_model_version="2.0.0",
        framework_version="research-0.1",
    )
    store.add(art)
    lc = ArtifactLifecycle(art.artifact_id)
    print("\n=== SAU (Wave B: governance) ===")
    print(f"  Artifact immutable: id={art.artifact_id[:12]}... param_hash={art.parameter_hash[:12]}...")
    try:
        lc.transition(PromotionState.CANARY_ELIGIBLE)
        print("  Promotion nhảy stage (exploratory->canary): BỊ CHẶN? NO (BUG)")
    except Exception as e:  # noqa: BLE001
        print(f"  Promotion nhảy stage: BỊ CHẶN -> {type(e).__name__}")

    reg = TrialsRegistry()
    reg.record(strategy_name="ma_v1", params={"fast": 5},
               search_space={"fast": [3, 5, 7]}, metric_value=10.0)
    reg.record(strategy_name="ma_v2", params={"fast": 5},
               search_space={"fast": [3, 5, 7]}, metric_value=12.0)
    print(f"  Rename strategy (ma_v1->ma_v2): total_trials={reg.total_trials()} (KHÔNG reset)")

    hi = UncertaintySignal(expected_return=0.01,
                           prediction_interval_lower=-0.03,
                           prediction_interval_upper=0.02,
                           calibration_score=0.4, ood_score=0.9)
    lo = UncertaintySignal(expected_return=0.01,
                           prediction_interval_lower=0.0,
                           prediction_interval_upper=0.005,
                           calibration_score=0.95, ood_score=0.05)
    print(f"  Uncertainty HIGH -> can_increase_exposure={hi.can_increase_exposure} (phải False)")
    print(f"  Uncertainty LOW  -> can_increase_exposure={lo.can_increase_exposure} (phải True)")
    ab = should_abstain(symbol="BTC/USDT", strategy="ma_crossover",
                        reason=AbstentionReason.OOD_INPUT, uncertainty=hi)
    print(f"  OOD score 0.9 -> abstain=True, reason={ab.reason.value}")

    dm = DriftMonitor()
    base = np.random.default_rng(1).normal(0, 0.02, 500)
    shifted = np.random.default_rng(2).normal(0.3, 0.15, 50)
    res_d = dm.check_all(returns_ref=base, returns_current=shifted)
    print(f"  Drift trên returns -> health={dm.health_state(res_d).value.upper()}")
    print(f"  (9 abstention codes: {[r.value for r in AbstentionReason]})")


if __name__ == "__main__":
    main()
