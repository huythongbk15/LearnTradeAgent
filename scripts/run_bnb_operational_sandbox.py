"""Fixed-scope, network-denied historical admission sandbox. Never submits orders.

This unqualified case may exercise signal/planning/permission and local restart,
but cannot grant itself the evidence needed to exercise new-position fills.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import socket
import sys
from collections import Counter
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data/existing_research_audit_20261008/BNB_USDT__4h__pre_holdout.parquet"
DATA_SHA = "379677360902c9734780e496398abb02ea6390531b840e2262bd9d7881e6485f"
PARAMS = {"fast_period": 20, "slow_period": 80, "adx_period": 14, "adx_threshold": 25}
START, STOP = 2360, 3440  # Previously observed validation, never OOS.
SYMBOL = "BNB/USDT"
REPORT = (
    ROOT
    / "data/multipair_screen_20261017_01/packets/BNB_USDT/ma_adx/cell_attempts/c88472c5c14545d994d9542699e22662/ma_adx__BNBUSDT__4h__1x__peb88e3174998__w51613ee3fc806adc/report.json"
)
REPORT_SHA = "5e5c20dbec5a236364e11aa6b6a0e6e14467ce7a29562db1086804c60567eab2"


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def deny_network(*args, **kwargs):
    raise RuntimeError("Network forbidden in local operational sandbox")


def claim_output(path: Path) -> Path:
    path = path.resolve()
    if not path.is_relative_to((ROOT / "data").resolve()):
        raise ValueError("output must be a new directory below workspace/data")
    path.mkdir(parents=True, exist_ok=False)
    return path


def write_new(path: Path, value) -> None:
    with path.open("x", encoding="utf-8") as handle:
        json.dump(value, handle, indent=2, default=str, allow_nan=False)


def canonical_signals(frame, *, start_index: int, end_index: int):
    """Use the research runner's canonical timing, not legacy crossover timing."""
    import polars as pl
    from trading_agent.backtest.tournament import canonical_signal_series
    from trading_agent.strategies.canonical.candidates import (
        build_parameterized_adapter,
    )

    descriptor, adapter = build_parameterized_adapter("ma_adx", PARAMS)
    canonical_frame = frame.rename({"timestamp": "time"})
    if canonical_frame.schema["time"].time_zone is None:
        canonical_frame = canonical_frame.with_columns(
            pl.col("time").dt.replace_time_zone("UTC")
        )
    return canonical_signal_series(
        adapter,
        canonical_frame,
        warmup_bars=descriptor.warmup_bars,
        symbol=SYMBOL,
        start_index=start_index,
        end_index=end_index,
    )


def canonical_execution_actions(frame):
    signals = canonical_signals(frame, start_index=START - 1, end_index=STOP - 1)
    actions = [signals[index - 1] for index in range(START, STOP)]
    if digest(REPORT) != REPORT_SHA:
        raise ValueError("original report identity mismatch")
    report = json.loads(REPORT.read_text())
    rows = report["execution_funnel"]["records"]
    if report["measurement_window"] != [START, STOP] or len(rows) != len(actions):
        raise ValueError("original report window mismatch")
    for index, (value, row) in enumerate(zip(actions, rows), START):
        action = {1: "BUY", -1: "SELL", 0: "HOLD"}[value]
        if row["execution_bar"] != index or row["action"] != action:
            raise ValueError(f"canonical signal parity failed at execution bar {index}")
    return actions


def validated_times(frame):
    # This hash-bound input stores UTC as timezone-naive parquet datetimes.
    raw = frame["timestamp"].to_list()
    if not raw or any(not isinstance(t, datetime) for t in raw):
        raise ValueError("expected datetime timestamp column, not inferred epoch units")
    times = [
        t.replace(tzinfo=UTC) if t.tzinfo is None else t.astimezone(UTC) for t in raw
    ]
    if any(b - a != timedelta(hours=4) for a, b in zip(times, times[1:])):
        raise ValueError("unexpected 4h data geometry")
    return times


def permission_effect(intent):
    # Planner and lifecycle currently publish distinct enum classes.
    from trading_agent.execution.lifecycle.lifecycle import ExposureEffect

    return ExposureEffect[intent.exposure_effect.name]


def run(out: Path) -> dict:
    # Install before importing project code, never load credentials/.env here.
    socket.socket.connect = deny_network
    socket.socket.connect_ex = deny_network
    socket.create_connection = deny_network
    sys.path[:0] = [str(ROOT), str(ROOT / "src")]
    import polars as pl

    from trading_agent.execution.application import CanonicalExecutionService
    from trading_agent.execution.canonical import (
        CurrentPortfolioState,
        InstrumentRules,
        MarketPrice,
        OrderPlanner,
        PaperExecutionAdapter,
        BrokerGateway,
        EnrichedMarketObservation,
    )
    from trading_agent.execution.canonical.risk_decision import (
        EvidenceState,
        RiskLevel,
        UnifiedRiskDecision,
    )
    from trading_agent.execution.lifecycle import (
        ExecutionEventStore,
        ExecutionLifecycle,
        TrustedPrice,
    )
    from trading_agent.execution.paper_exchange import PaperExchange
    from trading_agent.execution.permission import (
        PermissionContext,
        preview_historical_order_permission,
    )
    from trading_agent.research.forecast import TargetExposure

    if digest(DATA) != DATA_SHA:
        raise ValueError("frozen input digest mismatch")
    frame = pl.read_parquet(DATA)
    times = validated_times(frame)
    if (
        len(frame) < STOP
        or not frame["is_closed"].slice(START - 268, STOP - START + 268).all()
    ):
        raise ValueError("unexpected 4h data geometry")
    protected = [
        p for folder in (ROOT / "src", ROOT / "scripts") for p in folder.rglob("*.py")
    ]
    packet = ROOT / "data/multipair_screen_20261017_01/packets/BNB_USDT/ma_adx"
    protected += [p for p in packet.rglob("*") if p.is_file()]
    before = {str(p.relative_to(ROOT)): digest(p) for p in protected}
    manifest = {
        "scope": "HISTORICAL_OPERATIONAL_ADMISSION_ONLY",
        "symbol": SYMBOL,
        "timeframe": "4h",
        "parameters": PARAMS,
        "source_hashes": before,
        "data_sha256": DATA_SHA,
        "timestamp_contract": "hash-bound parquet naive datetimes interpreted as UTC",
        "evaluation_indices": [START, STOP],
        "bar_budget": STOP - START,
        "initial_cash": 10000,
        "commission": 0.001,
        "slippage": 0.0005,
        "target_cap": 0.25,
        "instrument_rules": "LOCAL_SIMULATION_ONLY_NOT_EXCHANGE_VERIFIED",
        "policy_qualified": False,
        "promotion_writes": False,
        "submission_enabled": False,
        "network_connect_denied": True,
        "clock_contract": "historical preview only; runtime permission keeps wall clock",
        "runtime_kill_switch": True,
        "conditional_probe": "preview without kill switch only, never runtime submission",
        "signal_contract": "canonical forecast at j uses head(j); execute at j+1",
        "signal_parity_report_sha256": REPORT_SHA,
        "selection_bias": "case and validation window already observed",
        "stop_rule": "finite bars; any unexpected permission ALLOW aborts; no liquidation",
    }
    write_new(out / "manifest.json", manifest)
    actions = canonical_execution_actions(frame)
    exchange = PaperExchange(state_dir=out / "paper", telemetry=None)
    store = ExecutionEventStore(str(out / "events.db")).connect()
    lifecycle = ExecutionLifecycle(store, kill_switch_active=lambda: True)
    service = CanonicalExecutionService(
        lifecycle=lifecycle,
        gateway=BrokerGateway(
            adapter=PaperExecutionAdapter(exchange), store=store, lifecycle=lifecycle
        ),
        planner=OrderPlanner(
            instrument_rules=InstrumentRules(
                symbol=SYMBOL,
                min_order_qty=0.001,
                max_order_qty=100,
                qty_step=0.001,
                spot_long_only=True,
                max_leverage=1,
            ),
            strategy_version=digest(Path(__file__)),
        ),
    )
    counts, reasons = Counter(), Counter()
    preview_reasons, evidence_probe_reasons = Counter(), Counter()
    trace = []
    for index in range(START, STOP):
        signal = actions[index - START]
        counts[str(signal)] += 1
        price = float(frame["open"][index])
        exchange.update_prices({SYMBOL: price})
        historical_time = times[index]
        record = {"bar_index": index, "signal_from_bar": index - 1, "signal": signal}
        record["feature_window_last_bar"] = index - 2
        if signal == 1:
            identity = hashlib.sha256(
                f"{DATA_SHA}:{index}:{PARAMS}".encode()
            ).hexdigest()
            # Operator budget is a sizing proposal, NOT measured model evidence.
            risk = UnifiedRiskDecision(
                decision_id=identity,
                forecast_fingerprint=identity,
                model_artifact_id="sandbox:" + digest(Path(__file__)),
                requested_target_exposure=0.25,
                allowed_target_exposure=0.25,
                max_new_exposure=0.25,
                reduce_only=False,
                risk_level=RiskLevel.HIGH,
                reason_codes=(),
                calibration_state=EvidenceState.UNKNOWN,
                calibration_artifact_id=None,
                calibration_ece=1,
                ood_state=EvidenceState.UNKNOWN,
                ood_score=1,
                regime_state=EvidenceState.UNKNOWN,
                regime_entropy=1,
                interval_width=1,
                created_at=historical_time,
                metadata={"scope": "UNQUALIFIED_SANDBOX_PROPOSAL"},
            )
            prior = frame.row(index - 1, named=True)
            observation = EnrichedMarketObservation(
                symbol=SYMBOL,
                observed_at=historical_time,
                open=float(prior["open"]),
                high=float(prior["high"]),
                low=float(prior["low"]),
                close=float(prior["close"]),
                volume=float(prior["volume"]),
                observation_id=identity,
                venue="local_historical_simulator",
                timeframe="4h",
                bar_close_at=historical_time,
                is_closed=True,
                data_manifest_id=DATA_SHA,
            )
            plan = service.plan(
                target=TargetExposure(
                    SYMBOL, 0.25, 14400, identity, risk.model_artifact_id, identity
                ),
                risk_decision=risk,
                observation=observation,
                portfolio=CurrentPortfolioState(SYMBOL, 10000, 0, available_cash=10000),
                price=MarketPrice(SYMBOL, price),
            )
            record["planning_status"] = plan.status.value
            if plan.intent is not None:
                intent = plan.intent
                context = PermissionContext(
                    execution_health=lifecycle.state.execution_health,
                    exposure_effect=permission_effect(intent),
                    risk_decision=risk,
                    trusted_price=TrustedPrice(price, historical_time, historical_time),
                    order_size=intent.quantity,
                    order_side=intent.side,
                    kill_switch_active=True,
                )
                permission = service.evaluate_permission(context)
                if permission.allowed():
                    raise RuntimeError(
                        "Unexpected permission ALLOW; submission remains disabled"
                    )
                reasons[permission.reason.value] += 1
                record["permission_reason"] = permission.reason.value
                preview = preview_historical_order_permission(
                    context, reference_time=historical_time
                )
                evidence_probe = preview_historical_order_permission(
                    replace(context, kill_switch_active=False),
                    reference_time=historical_time,
                )
                preview_reasons[preview.reason.value] += 1
                evidence_probe_reasons[evidence_probe.reason.value] += 1
                record.update(
                    {
                        "historical_clock": historical_time.isoformat(),
                        "exchange_timestamp": context.trusted_price.exchange_timestamp.isoformat(),
                        "preview_reason": preview.reason.value,
                        "conditional_without_kill_switch_reason": evidence_probe.reason.value,
                        "evidence_states": {
                            "calibration": "UNKNOWN",
                            "ood": "UNKNOWN",
                            "regime": "UNKNOWN",
                        },
                    }
                )
            else:
                reasons.update(plan.reason_codes)
        trace.append(record)
    # Stop with no fabricated fills/positions; reopen real local persistence.
    assert not lifecycle.state.orders and not exchange.orders and not exchange.positions
    events_before = store.count()
    store.close()
    reopened = ExecutionEventStore(str(out / "events.db")).connect()
    restored = ExecutionLifecycle(reopened, kill_switch_active=lambda: True)
    restored.load()
    restored_exchange = PaperExchange(state_dir=out / "paper", telemetry=None)
    assert not restored.state.orders and reopened.count() == events_before
    assert restored_exchange.get_total_equity() == 10000
    reopened.close()
    after = {str(p.relative_to(ROOT)): digest(p) for p in protected}
    assert before == after and digest(DATA) == DATA_SHA
    write_new(out / "trace.json", trace)
    result = {
        "status": "PASS_ADMISSION_BLOCKED_NOT_TRADING_ACCEPTANCE",
        "bars_processed": len(trace),
        "signal_parity_with_original_report": True,
        "signal_counts": dict(counts),
        "block_reasons": dict(reasons),
        "historical_preview_block_reasons": dict(preview_reasons),
        "conditional_without_kill_switch_block_reasons": dict(evidence_probe_reasons),
        "clock_scope": "diagnostic only, no production clock override",
        "orders_submitted": 0,
        "fills": 0,
        "ending_equity": 10000,
        "flat_restart_pass": True,
        "filled_restart_tested": False,
        "source_and_packet_unchanged": True,
        "protected_files": len(before),
        "economic_verdict": "UNCHANGED_INSUFFICIENT_EVIDENCE_OF_ALPHA",
        "qualification": False,
        "continuous_paper": False,
    }
    write_new(out / "result.json", result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    out = claim_output(args.output)
    try:
        print(json.dumps(run(out)))
    except Exception as exc:
        write_new(out / "failure.json", {"error": str(exc), "type": type(exc).__name__})
        raise
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
