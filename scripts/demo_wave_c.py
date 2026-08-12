#!/usr/bin/env python3
"""Demo so sánh TRƯỚC vs SAU cho Wave C (Execution State & Resilience).

Bằng chứng:
1. Event-sourced lifecycle — replay determinism + crash recovery;
2. Trading invariant chaos tests — 16 fault injections, 9 invariants giữ nguyên;
3. Shadow Mainnet Mode — real data, NO order submission, reality-gap report.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from trading_agent.execution.chaos_invariants import (
    ALL_INVARIANTS,
    FaultType,
    check_invariants,
    run_chaos_scenario,
)
from trading_agent.execution.lifecycle import (
    ExecutionEventStore,
    ExecutionLifecycle,
)
from trading_agent.execution.shadow import (
    ExchangeRules,
    ShadowConfig,
    ShadowMainnetEngine,
)


def demo_lifecycle(tmp: Path) -> None:
    print("=== 1. Event-sourced lifecycle (Wave C) ===")
    store = ExecutionEventStore(tmp / "lifecycle.db").connect()
    lc = ExecutionLifecycle(store, price_source=lambda s: 100.0)
    lc.create_order_intent("i1", "BTC/USDT", "buy", 1.0)
    lc.approve_risk("i1")
    lc.submit_order("i1", exchange_order_id="ex_1")
    lc.acknowledge_broker("i1", broker_order_id="br_1")
    lc.receive_fill("i1", 1.0, 99.5, protective_trigger=90.0)
    lc.book_fee("i1", 0.1)
    first = lc.snapshot_state()
    store.close()

    # Replay từ log — state phải giống hệt
    store2 = ExecutionEventStore(tmp / "lifecycle.db").connect()
    lc2 = ExecutionLifecycle(store2)
    lc2.load()
    print(f"  Events persisted: {store2.count()}")
    print(f"  Replay deterministic: {first == lc2.snapshot_state()}")
    print(
        f"  Order status: {lc2.order('i1').status.value} | "
        f"filled={lc2.order('i1').filled_size} | "
        f"protective={'yes' if lc2.order('i1').protective_order_ids else 'NO'}"
    )
    print(f"  Integrity (seq gaps/dupes): {store2.integrity_check()['ok']}")
    store2.close()


def demo_chaos(tmp: Path) -> None:
    print("\n=== 2. Trading invariant chaos tests (9 invariants × 16 faults) ===")
    print(f"  Invariants: {', '.join(ALL_INVARIANTS)}")
    all_passed = True
    for fault in FaultType:
        store = ExecutionEventStore(tmp / f"chaos_{fault.value}.db").connect()
        lc = ExecutionLifecycle(
            store,
            price_source=lambda s: 100.0,
            inventory_source=lambda sym, side: 5.0,
        )
        result = run_chaos_scenario(lc, fault)
        ok = result.passed and check_invariants(lc) == []
        all_passed &= ok
        print(f"  [{fault.value:<42}] invariants_hold={ok}")
        store.close()
    print(f"  => ALL FAULTS PRESERVE INVARIANTS: {all_passed}")


def demo_shadow(tmp: Path) -> None:
    print("\n=== 3. Shadow Mainnet Mode (real data, NO order submission) ===")
    config = ShadowConfig(
        symbols=["BTC/USDT"],
        strategy_ids=["ma_cross"],
        exchange_rules={
            "BTC/USDT": ExchangeRules(min_qty=0.001, step_size=0.001, min_notional=10.0)
        },
    )
    engine = ShadowMainnetEngine(config, env={"SHADOW_MAINNET": "1"})
    # Real mainnet market data feed (here: sample ticks)
    engine.ingest_market_data(prices={"BTC/USDT": 50000.0})
    intent = engine.create_shadow_intent("BTC/USDT", "buy", 0.01, "ma_cross")
    engine.simulate_fill(intent.order_id)
    engine.set_shadow_protective_order("BTC/USDT", stop_loss=45000.0)
    engine.observe_mid_after_fill("BTC/USDT", 50120.0)
    engine.observe_mid_after_fill("BTC/USDT", 50250.0)
    metrics = engine.execution_metrics()
    report = engine.reality_gap_report()
    print(
        f"  Shadow fills: {metrics['filled']} | "
        f"avg_slippage_bps: {metrics['avg_slippage_bps']} | "
        f"shadow_equity: {metrics['shadow_equity']:.2f}"
    )
    print("  Protective order: stop_loss=45000.0 (shadow state, never sent)")
    print(
        f"  Reality gap fills: {report.summary()['fills']} | "
        f"avg_abs_gap_to_last_mid: {report.summary()['avg_abs_gap_to_last_mid']}"
    )
    try:
        engine._submit_live_order()
        print("  Hard guard: FAILED (submission path reachable!)")
    except Exception as exc:
        print(f"  Hard guard: OK — {type(exc).__name__}: {exc}")


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="wave_c_demo_") as td:
        tmp = Path(td)
        demo_lifecycle(tmp)
        demo_chaos(tmp)
        demo_shadow(tmp)
    print("\nWave C evidence complete.")


if __name__ == "__main__":
    main()
