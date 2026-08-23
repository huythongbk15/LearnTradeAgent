#!/usr/bin/env python3
"""Fail-closed Binance Spot runner for the Enhanced MA strategy.

Dry-run is the default.  Testnet execution requires the execution and mode
environment gates.  Mainnet additionally requires matching environment and CLI
confirmation phrases; see ``docs/LIVE_TRADING_RUNBOOK.md``.
"""

from __future__ import annotations

import argparse
import asyncio
import math
import os
import random
import sys
import time
from contextlib import suppress
from datetime import UTC, datetime

sys.path.insert(0, "src")

import ccxt
import polars as pl
from dotenv import load_dotenv
from live_config import (
    ATR_SL_MULT,
    ATR_SL_WINDOW,
    DEFAULT_CLOCK_SKEW_S,
    LOOKBACK,
    STRATEGY_PARAMS,
)

from trading_agent.exchanges.ccxt_adapter import CCXTAdapter, ExchangeConfig
from trading_agent.exchanges.live_broker import LiveBroker
from trading_agent.exchanges.models import (
    AssetClass,
    MarketType,
    OrderConstraintError,
    Symbol,
)
from trading_agent.execution.canonical import (
    BrokerGateway,
    CancelState,
)
from trading_agent.execution.canonical.adapters import (
    BrokerSubmitFact,
    BrokerSubmitState,
    LiveBrokerExecutionAdapter,
)
from trading_agent.execution.canonical.risk_decision import UnifiedRiskDecision
from trading_agent.execution.lifecycle import ExecutionEventStore
from trading_agent.execution.lifecycle.lifecycle import (
    EmergencyReduceRequest,
    ExecutionLifecycle,
    PortfolioRiskSnapshot,
    TrustedPrice,
)
from trading_agent.execution.correlation import bind_run_correlation
from trading_agent.execution.data_trust import (
    BINANCE_MAINNET_TIME_URL,
    BINANCE_TESTNET_TIME_URL,
    DataTrustError,
    DataTrustMonitor,
    SequenceGapError,
    ServerClock,
    TimeStampedFetch,
    reject_high_latency,
)
from trading_agent.execution.live_safety import (
    LIVE_CONFIRMATION,
    RISK_INCREASE_CONFIRMATION,
    LiveExecutionLock,
    LiveRiskLimits,
    LiveRiskStateStore,
    LiveSafetyError,
    account_fingerprint,
    append_live_audit_event,
    configured_entry_lock_reason,
    make_order_key,
    require_execution_authorization,
    strategy_fingerprint,
    validate_build_sha,
    validate_fresh_quote,
    validate_integrity_key,
    validate_order_book_depth,
    validate_order_risk,
    validate_spread,
    validate_strategy_evidence,
)
from trading_agent.strategies.enhanced_ma import EnhancedMaCrossover

load_dotenv(".env")

HOUR_MS = 3_600_000
MAX_CLOSED_CANDLE_LAG_SECONDS = 5_400
ORDER_TERMINAL_STATUSES = frozenset(
    {
        "filled",
        "cancelled",
        "rejected",
        "expired",
    }
)
ORDER_ACCEPTED_STATUSES = frozenset({"open", "partial", "filled"})
DEFAULT_ORDER_RECONCILIATION_TIMEOUT_SECONDS = 20.0


def order_reconciliation_timeout_seconds(
    env: dict[str, str] | None = None,
) -> float:
    source = os.environ if env is None else env
    raw = str(source.get("LIVE_ORDER_RECONCILE_TIMEOUT_SECONDS") or "20").strip()
    try:
        value = float(raw)
    except ValueError as exc:
        raise LiveSafetyError(
            "LIVE_ORDER_RECONCILE_TIMEOUT_SECONDS must be a number"
        ) from exc
    if not math.isfinite(value) or not 1 <= value <= 120:
        raise LiveSafetyError(
            "LIVE_ORDER_RECONCILE_TIMEOUT_SECONDS must be between 1 and 120"
        )
    return value


def poll_order_by_client_id(
    *,
    broker: LiveBroker,
    order_key: str,
    symbol: Symbol,
    timeout_seconds: float,
    initial_delay_seconds: float = 0.25,
    max_delay_seconds: float = 2.0,
    sleep_fn=time.sleep,
    monotonic_fn=time.monotonic,
) -> tuple[dict | None, str]:
    """Poll to a terminal exchange state without an unbounded runner hang."""

    numeric = (timeout_seconds, initial_delay_seconds, max_delay_seconds)
    if any(not math.isfinite(value) or value < 0 for value in numeric):
        raise LiveSafetyError(
            "order reconciliation timing must be finite and non-negative"
        )
    if timeout_seconds > 0 and initial_delay_seconds == 0:
        raise LiveSafetyError(
            "positive reconciliation timeout requires a polling delay"
        )
    if max_delay_seconds < initial_delay_seconds:
        raise LiveSafetyError(
            "maximum reconciliation delay cannot be below initial delay"
        )

    deadline = monotonic_fn() + timeout_seconds
    attempt = 0
    last_result: dict | None = None
    last_error = ""
    while True:
        try:
            current = broker.get_order_by_client_id(order_key, symbol)
        except Exception as exc:
            last_error = str(exc)
        else:
            if current is not None:
                last_result = current
                status = str(current.get("status") or "unknown").strip().lower()
                if status in ORDER_TERMINAL_STATUSES:
                    return current, last_error

        remaining = deadline - monotonic_fn()
        if remaining <= 0:
            return last_result, last_error
        base_delay = min(
            max_delay_seconds,
            initial_delay_seconds * (2 ** min(attempt, 8)),
        )
        jitter = random.Random(f"{order_key}:{attempt}").uniform(0.85, 1.15)
        delay = min(max_delay_seconds, base_delay * jitter, remaining)
        sleep_fn(delay)
        attempt += 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--execute", action="store_true", help="Submit orders (default: dry-run)"
    )
    parser.add_argument(
        "--testnet", action="store_true", help="Use Binance Spot Testnet"
    )
    parser.add_argument(
        "--profile",
        choices=("testnet", "mainnet-canary", "mainnet-normal"),
        default=None,
        help="Risk profile; defaults to testnet or mainnet-canary based on mode",
    )
    parser.add_argument(
        "--confirm-live",
        default=None,
        help=f"Mainnet only: must equal {LIVE_CONFIRMATION}",
    )
    parser.add_argument(
        "--confirm-risk-increase",
        default=None,
        help=(
            "Required when persisted risk limits would become less restrictive; "
            f"must equal {RISK_INCREASE_CONFIRMATION}"
        ),
    )
    parser.add_argument(
        "--symbols",
        default="BTC/USDT,SOL/USDT,AVAX/USDT",
        help="Comma-separated Binance Spot symbols",
    )
    parser.add_argument(
        "--weights",
        default="4,3,3",
        help="Percent of equity per symbol; values are not normalized",
    )
    parser.add_argument(
        "--state-file", default=None, help="Override persistent live-risk state path"
    )
    parser.add_argument(
        "--evidence-file",
        default="data/live_strategy_evidence.json",
        help="Cost-aware walk-forward evidence required for mainnet execution",
    )
    parser.add_argument(
        "--audit-log",
        default="data/execution/binance_live_audit.jsonl",
        help="Durable local JSONL execution and heartbeat audit log",
    )
    return parser


def resolve_trading_profile(
    args: argparse.Namespace,
    env: dict[str, str] | None = None,
) -> str:
    source = os.environ if env is None else env
    cli_profile = str(getattr(args, "profile", None) or "").strip().lower()
    env_profile = str(source.get("LIVE_TRADING_PROFILE") or "").strip().lower()
    if cli_profile and env_profile and cli_profile != env_profile:
        raise LiveSafetyError("CLI and environment live trading profiles do not match")
    profile = (
        cli_profile or env_profile or ("testnet" if args.testnet else "mainnet-canary")
    )
    allowed = {"testnet", "mainnet-canary", "mainnet-normal"}
    if profile not in allowed:
        raise LiveSafetyError(f"unsupported live trading profile: {profile}")
    if args.testnet and profile != "testnet":
        raise LiveSafetyError("Binance Testnet requires the testnet risk profile")
    if not args.testnet and profile == "testnet":
        raise LiveSafetyError("Binance mainnet cannot use the testnet risk profile")
    return profile


def parse_allocations(
    symbols_raw: str,
    weights_raw: str,
    limits: LiveRiskLimits,
) -> list[tuple[str, float]]:
    symbols = [
        value.strip().upper() for value in symbols_raw.split(",") if value.strip()
    ]
    weight_parts = [value.strip() for value in weights_raw.split(",") if value.strip()]
    if not symbols or len(symbols) != len(weight_parts):
        raise LiveSafetyError(
            "--symbols and --weights must contain the same non-zero count"
        )
    if len(set(symbols)) != len(symbols):
        raise LiveSafetyError("duplicate symbols are not allowed")

    allocations: list[tuple[str, float]] = []
    for symbol, raw_weight in zip(symbols, weight_parts, strict=True):
        parts = symbol.split("/")
        if len(parts) != 2 or not all(parts) or parts[1] != "USDT":
            raise LiveSafetyError(
                f"only BASE/USDT spot symbols are supported: {symbol}"
            )
        try:
            allocation = float(raw_weight) / 100
        except ValueError as exc:
            raise LiveSafetyError(
                f"invalid allocation for {symbol}: {raw_weight}"
            ) from exc
        if not math.isfinite(allocation) or allocation <= 0:
            raise LiveSafetyError(
                f"allocation for {symbol} must be positive and finite"
            )
        if allocation > limits.max_symbol_exposure_pct:
            raise LiveSafetyError(
                f"allocation for {symbol} ({allocation:.1%}) exceeds symbol limit "
                f"({limits.max_symbol_exposure_pct:.1%})"
            )
        allocations.append((symbol, allocation))

    total = sum(weight for _, weight in allocations)
    if total > limits.max_gross_exposure_pct + 1e-12:
        raise LiveSafetyError(
            f"total allocation {total:.1%} exceeds gross limit "
            f"{limits.max_gross_exposure_pct:.1%}"
        )
    return allocations


def exchange_symbol(pair: str) -> Symbol:
    base, quote = pair.split("/")
    return Symbol(
        base=base,
        quote=quote,
        asset_class=AssetClass.CRYPTO,
        market_type=MarketType.SPOT,
        exchange="binance",
    )


def compute_state(frame: pl.DataFrame) -> dict:
    """Replay the strategy and return the desired state on the last closed bar."""

    strategy = EnhancedMaCrossover(STRATEGY_PARAMS)
    enriched = strategy.compute_indicators(frame)
    signals = strategy.generate_signals(enriched).to_numpy()
    in_position = False
    for signal in signals:
        if not in_position and signal == 1:
            in_position = True
        elif in_position and signal == -1:
            in_position = False

    last = enriched.tail(1)
    required = [
        "timestamp",
        "close",
        "high",
        f"ma_{STRATEGY_PARAMS['fast_period']}",
        f"ma_{STRATEGY_PARAMS['slow_period']}",
        "adx",
        "trend_up",
    ]
    if any(column not in last.columns for column in required):
        raise LiveSafetyError("strategy output is missing required indicators")
    numeric_values = {
        "price": float(last["close"][0]),
        "ma_fast": float(last[f"ma_{STRATEGY_PARAMS['fast_period']}"][0]),
        "ma_slow": float(last[f"ma_{STRATEGY_PARAMS['slow_period']}"][0]),
        "adx": float(last["adx"][0]),
        "atr": float(last["atr"][0]),
    }
    if any(not math.isfinite(value) or value <= 0 for value in numeric_values.values()):
        raise LiveSafetyError("latest strategy indicators are invalid")
    recent = signals[-24:]
    return {
        "state": "LONG" if in_position else "FLAT",
        **numeric_values,
        "trend_up": bool(last["trend_up"][0]),
        "recent_high": float(enriched["high"].tail(ATR_SL_WINDOW).max()),
        "candle_timestamp": last["timestamp"][0],
        "n_buy_24h": int((recent == 1).sum()),
        "n_sell_24h": int((recent == -1).sum()),
    }


def apply_atr_protection(
    *,
    states: dict[str, dict],
    positions: list[dict],
    store: LiveRiskStateStore,
) -> None:
    """Force a risk-reducing exit when a persistent ATR trail is breached."""

    position_map = {position["symbol"]: position for position in positions}
    for pair, state in states.items():
        position = position_map.get(pair)
        quantity = float(position["qty"]) if position else 0.0
        if quantity <= 0:
            protection = store.protective_order_state(pair)
            if not protection.get("active") and not protection.get("pending"):
                store.clear_position_risk(pair)
            state["atr_stop"] = None
            continue
        peak, stop = store.observe_position_risk(
            pair,
            quantity=quantity,
            observed_high=float(state["recent_high"]),
            atr=float(state["atr"]),
            atr_multiplier=ATR_SL_MULT,
        )
        state["atr_stop"] = stop
        if float(state["price"]) <= stop:
            state["state"] = "FLAT"
            state["risk_exit"] = (
                f"ATR trail breached: {state['price']:.2f} <= {stop:.2f} "
                f"(peak {peak:.2f})"
            )


def _audit_protective_event(
    audit_log_path: str | None,
    event: str,
    payload: dict,
) -> None:
    if audit_log_path:
        append_live_audit_event(audit_log_path, event, payload)


def reconcile_protective_stop(
    *,
    pair: str,
    broker: LiveBroker,
    store: LiveRiskStateStore,
    audit_log_path: str | None = None,
) -> dict | None:
    """Reconcile active and pending protective client IDs after restart/timeouts."""

    symbol = exchange_symbol(pair)
    protection = store.protective_order_state(pair)
    active = protection.get("active")
    pending = protection.get("pending")

    def lookup(record: dict | None) -> dict | None:
        if not isinstance(record, dict):
            return None
        client_order_id = str(record.get("client_order_id") or "")
        if not client_order_id:
            raise LiveSafetyError(f"protective order metadata is invalid for {pair}")
        try:
            return broker.get_order_by_client_id(client_order_id, symbol)
        except Exception as exc:
            raise LiveSafetyError(
                f"protective order reconciliation failed for {pair}: {exc}"
            ) from exc

    if isinstance(pending, dict):
        pending_result = lookup(pending)
        if pending_result is not None:
            pending_status = str(pending_result.get("status") or "unknown").lower()
            if pending_status == "open":
                if isinstance(active, dict):
                    active_result = lookup(active)
                    active_result_status = (
                        str(active_result.get("status") or "unknown").lower()
                        if active_result is not None
                        else "missing"
                    )
                    if (
                        active_result is not None
                        and active_result_status == "open"
                        and str(active.get("client_order_id"))
                        != str(pending.get("client_order_id"))
                    ):
                        raise LiveSafetyError(
                            f"duplicate active protective orders detected for {pair}"
                        )
                    if active_result_status in {"partial", "filled"}:
                        raise LiveSafetyError(
                            f"previous protective stop changed position while "
                            f"replacement was pending for {pair}: {active_result_status}"
                        )
                pending_exchange_id = str(pending_result.get("id") or "")
                if not pending_exchange_id:
                    raise LiveSafetyError(
                        f"confirmed protective order ID is missing for {pair}"
                    )
                confirmed = store.activate_pending_protective_order(
                    pair,
                    exchange_order_id=pending_exchange_id,
                    status="open",
                    quantity=float(
                        pending_result.get("qty") or pending.get("quantity") or 0.0
                    ),
                    stop_price=float(
                        pending_result.get("stop_price")
                        or pending.get("stop_price")
                        or 0.0
                    ),
                )
                _audit_protective_event(
                    audit_log_path,
                    "protective_stop_reconciled",
                    {"symbol": pair, **confirmed},
                )
                return confirmed
            store.update_pending_protective_order(
                pair,
                status=pending_status,
                exchange_order_id=str(pending_result.get("id") or ""),
                error=str(pending_result.get("error") or ""),
            )
            if pending_status in {"partial", "filled"}:
                raise LiveSafetyError(
                    f"protective stop changed position while reconciling {pair}: "
                    f"{pending_status}"
                )
            store.abandon_pending_protective_order(pair)
        elif isinstance(active, dict):
            active_result = lookup(active)
            active_status = (
                str(active_result.get("status") or "unknown").lower()
                if active_result is not None
                else "missing"
            )
            if active_status == "open":
                store.abandon_pending_protective_order(pair)
                return active
            if active_status in {"partial", "filled"}:
                raise LiveSafetyError(
                    f"protective stop changed position while recovering {pair}: "
                    f"{active_status}"
                )
            store.clear_active_protective_order(pair)
            store.abandon_pending_protective_order(pair)
            active = None
        else:
            store.update_pending_protective_order(
                pair,
                status="unknown",
                error="client order ID not found during protective reconciliation",
            )
            raise LiveSafetyError(
                f"protective stop outcome is unknown for {pair}; no previous stop exists"
            )

    protection = store.protective_order_state(pair)
    active = protection.get("active")
    if not isinstance(active, dict):
        return None
    active_result = lookup(active)
    if active_result is None:
        store.clear_active_protective_order(pair)
        return None
    active_status = str(active_result.get("status") or "unknown").lower()
    if active_status == "open":
        return active
    if active_status in {"partial", "filled"}:
        raise LiveSafetyError(
            f"protective stop changed position for {pair}: {active_status}"
        )
    if active_status in {"cancelled", "rejected", "expired"}:
        store.clear_active_protective_order(pair)
        return None
    raise LiveSafetyError(
        f"protective stop has unsupported exchange status for {pair}: {active_status}"
    )


def ensure_protective_stop(
    *,
    pair: str,
    quantity: float,
    desired_stop: float,
    current_price: float,
    broker: LiveBroker,
    lifecycle: ExecutionLifecycle,
    gateway: BrokerGateway,
    store: LiveRiskStateStore,
    limits: LiveRiskLimits | None = None,
    audit_log_path: str | None = None,
) -> dict:
    """Create or tighten one exchange-native stop for an existing position."""

    if any(
        not math.isfinite(value) or value <= 0
        for value in (quantity, desired_stop, current_price)
    ):
        raise LiveSafetyError(f"invalid protective stop inputs for {pair}")
    if desired_stop >= current_price:
        raise LiveSafetyError(
            f"protective stop is already breached for {pair}: "
            f"{desired_stop:.8f} >= {current_price:.8f}"
        )
    symbol = exchange_symbol(pair)
    active = reconcile_protective_stop(
        pair=pair,
        broker=broker,
        store=store,
        audit_log_path=audit_log_path,
    )
    try:
        normalized_quantity = broker.normalize_order_amount(
            symbol,
            quantity,
            reference_price=desired_stop,
        )
    except OrderConstraintError as exc:
        dust = classify_controlled_dust(
            pair=pair,
            quantity=quantity,
            reference_price=current_price,
            error=exc,
            store=store,
            limits=limits,
            context="protective_stop",
            audit_log_path=audit_log_path,
        )
        if dust is not None:
            return dust
        raise
    if isinstance(active, dict):
        active_stop = float(active.get("stop_price") or 0.0)
        active_quantity = float(active.get("quantity") or 0.0)
        stop_is_tight_enough = active_stop >= desired_stop * (1 - 1e-10)
        quantity_matches = abs(active_quantity - normalized_quantity) <= max(
            1e-12,
            normalized_quantity * 1e-8,
        )
        if stop_is_tight_enough and quantity_matches:
            store.clear_position_dust(pair)
            return active
        desired_stop = max(desired_stop, active_stop)

    pending = store.reserve_protective_order(
        pair,
        quantity=normalized_quantity,
        stop_price=desired_stop,
    )
    operation = "protective_stop_placed"
    try:
        if isinstance(active, dict):
            exchange_order_id = str(active.get("exchange_order_id") or "")
            if not exchange_order_id:
                raise LiveSafetyError(
                    f"active protective order ID is missing for {pair}"
                )
            _cancel_canonical_order(
                lifecycle=lifecycle,
                gateway=gateway,
                intent_id=str(active.get("client_order_id") or ""),
                broker_order_id=exchange_order_id,
                symbol=pair,
                reason="tighten protective stop",
            )
            store.clear_active_protective_order(pair)
            operation = "protective_stop_replaced"
        intent_id = str(pending["client_order_id"])
        authorization_id = _authorize_live_order(
            lifecycle=lifecycle,
            planned={"action": "SELL"},
            intent_id=intent_id,
            symbol=pair,
            quantity=normalized_quantity,
            reason="PROTECTIVE_EMERGENCY_EXIT",
            order_type="stop",
            stop_price=desired_stop,
        )
        submission = gateway.submit(
            authorization_id,
            correlation_id=intent_id,
        )
        if submission.success and submission.broker_order_id:
            lifecycle.submit_order(
                intent_id,
                exchange_order_id=submission.broker_order_id,
            )
        result = _canonical_result_payload(submission)
        if submission.state == BrokerSubmitState.UNKNOWN:
            raise LiveSafetyError(f"protective submission state is unknown for {pair}")
        lifecycle.record_broker_submit_result(intent_id, submission)
    except Exception as exc:
        store.update_pending_protective_order(
            pair,
            status="unknown",
            error=str(exc),
        )
        recovered = reconcile_protective_stop(
            pair=pair,
            broker=broker,
            store=store,
            audit_log_path=audit_log_path,
        )
        if recovered is not None and str(recovered.get("client_order_id")) == str(
            pending.get("client_order_id")
        ):
            _record_reconciled_submission(lifecycle, intent_id, recovered)
            return recovered
        raise LiveSafetyError(
            f"protective stop update failed for {pair}; previous stop was retained"
        ) from exc

    status = str(result.get("status") or "unknown").lower()
    if status != "open":
        store.update_pending_protective_order(
            pair,
            status=status,
            exchange_order_id=str(result.get("id") or ""),
            error=str(result.get("error") or ""),
        )
        raise LiveSafetyError(
            f"protective stop did not become active for {pair}: {status}"
        )
    result_exchange_id = str(result.get("id") or "")
    if not result_exchange_id:
        store.update_pending_protective_order(
            pair,
            status="unknown",
            error="exchange acknowledgement omitted the order ID",
        )
        raise LiveSafetyError(f"protective stop order ID is missing for {pair}")
    confirmed = store.activate_pending_protective_order(
        pair,
        exchange_order_id=result_exchange_id,
        status="open",
        quantity=float(result.get("qty") or pending.get("quantity") or 0.0),
        stop_price=float(result.get("stop_price") or pending.get("stop_price") or 0.0),
    )
    _audit_protective_event(
        audit_log_path,
        operation,
        {"symbol": pair, **confirmed},
    )
    return confirmed


def ensure_protective_stops(
    *,
    states: dict[str, dict],
    positions: list[dict],
    broker: LiveBroker,
    lifecycle: ExecutionLifecycle,
    gateway: BrokerGateway,
    store: LiveRiskStateStore,
    limits: LiveRiskLimits | None = None,
    skip_symbols: set[str] | None = None,
    audit_log_path: str | None = None,
) -> None:
    """Ensure every managed position not already exiting has exchange protection."""

    skipped = skip_symbols or set()
    position_map = {position["symbol"]: position for position in positions}
    for pair, state in states.items():
        position = position_map.get(pair)
        if (
            position is None
            or float(position.get("qty") or 0.0) <= 0
            or pair in skipped
        ):
            continue
        stop = state.get("atr_stop")
        if stop is None:
            raise LiveSafetyError(f"ATR stop is missing for open position {pair}")
        ensure_protective_stop(
            pair=pair,
            quantity=float(position["qty"]),
            desired_stop=float(stop),
            current_price=float(state["price"]),
            broker=broker,
            lifecycle=lifecycle,
            gateway=gateway,
            store=store,
            limits=limits,
            audit_log_path=audit_log_path,
        )


def cleanup_orphan_protective_stops(
    *,
    managed_symbols: list[str],
    positions: list[dict],
    broker: LiveBroker,
    lifecycle: ExecutionLifecycle,
    gateway: BrokerGateway,
    store: LiveRiskStateStore,
    audit_log_path: str | None = None,
) -> None:
    """Cancel recorded stops when the exchange balance no longer has a position."""

    held = {
        position["symbol"]
        for position in positions
        if float(position.get("qty") or 0.0) > 0
    }
    for pair in managed_symbols:
        if pair in held:
            continue
        protection = store.protective_order_state(pair)
        records = [protection.get("pending"), protection.get("active")]
        seen: set[str] = set()
        for record in records:
            if not isinstance(record, dict):
                continue
            client_order_id = str(record.get("client_order_id") or "")
            if not client_order_id or client_order_id in seen:
                continue
            seen.add(client_order_id)
            result = broker.get_order_by_client_id(
                client_order_id,
                exchange_symbol(pair),
            )
            if result is None:
                continue
            status = str(result.get("status") or "unknown").lower()
            if status in {"open", "partial"}:
                exchange_order_id = str(result.get("id") or "")
                if not exchange_order_id:
                    raise LiveSafetyError(
                        f"cannot cancel orphan protective order for {pair}"
                    )
                _cancel_canonical_order(
                    lifecycle=lifecycle,
                    gateway=gateway,
                    intent_id=client_order_id,
                    broker_order_id=exchange_order_id,
                    symbol=pair,
                    reason="remove orphan protective stop",
                )
            elif status not in {"filled", "cancelled", "rejected", "expired"}:
                raise LiveSafetyError(
                    f"orphan protective order has unknown status for {pair}: {status}"
                )
        if any(isinstance(record, dict) for record in records):
            _audit_protective_event(
                audit_log_path,
                "orphan_protective_stop_cleared",
                {"symbol": pair, "client_order_ids": sorted(seen)},
            )
        store.clear_position_risk(pair)


def validate_live_hourly_bars(
    bars: list[list],
    *,
    symbol: str,
    now: datetime | None = None,
) -> list[tuple[int, float, float, float, float, float]]:
    """Normalize and reject stale, gapped or malformed closed Binance bars."""

    current = (now or datetime.now(UTC)).astimezone(UTC)
    now_ms = int(current.timestamp() * 1000)
    closed: list[tuple[int, float, float, float, float, float]] = []
    for index, bar in enumerate(bars):
        if not isinstance(bar, (list, tuple)) or len(bar) < 6:
            raise LiveSafetyError(f"malformed OHLCV bar {index} for {symbol}")
        try:
            timestamp = int(bar[0])
            values = tuple(float(value) for value in bar[1:6])
        except (TypeError, ValueError, OverflowError) as exc:
            raise LiveSafetyError(
                f"non-numeric OHLCV bar {index} for {symbol}"
            ) from exc
        if timestamp + HOUR_MS > now_ms:
            continue
        open_price, high, low, close, volume = values
        prices = (open_price, high, low, close)
        if any(not math.isfinite(value) or value <= 0 for value in prices):
            raise LiveSafetyError(f"invalid OHLC price in bar {index} for {symbol}")
        if not math.isfinite(volume) or volume < 0:
            raise LiveSafetyError(f"invalid volume in bar {index} for {symbol}")
        if high < max(open_price, low, close) or low > min(open_price, high, close):
            raise LiveSafetyError(
                f"inconsistent OHLC range in bar {index} for {symbol}"
            )
        closed.append((timestamp, *values))

    timestamps = [bar[0] for bar in closed]
    if timestamps != sorted(timestamps) or len(timestamps) != len(set(timestamps)):
        raise LiveSafetyError(f"duplicate or unordered hourly candles for {symbol}")
    if any(
        current_ts - previous_ts != HOUR_MS
        for previous_ts, current_ts in zip(
            timestamps,
            timestamps[1:],
            strict=False,
        )
    ):
        raise LiveSafetyError(f"hourly candle gap detected for {symbol}")
    if closed:
        lag_seconds = (now_ms - (closed[-1][0] + HOUR_MS)) / 1_000
        if lag_seconds > MAX_CLOSED_CANDLE_LAG_SECONDS:
            raise LiveSafetyError(
                f"latest closed candle for {symbol} is stale by {lag_seconds:.0f}s"
            )
    return closed


def get_recent_df(
    symbol: str,
    monitor: DataTrustMonitor | None = None,
) -> pl.DataFrame:
    """Fetch and validate only fully closed 1h Binance candles."""

    public_exchange = ccxt.binance({"enableRateLimit": True})
    started_at = time.monotonic()
    bars = public_exchange.fetch_ohlcv(symbol, "1h", limit=LOOKBACK)
    received_at = time.monotonic()
    closed = validate_live_hourly_bars(bars, symbol=symbol)
    if monitor is not None:
        # Trust metrics use the newest *closed* candle (the open-time of the
        # in-progress bar is by design up to one full interval old, so the
        # stale-quote gate does not apply — only latency is enforced here).
        fetch = TimeStampedFetch(
            exchange_timestamp=float(closed[-1][0]) if closed else None,
            request_started_at=started_at,
            received_at=received_at,
        )
        monitor.record_fetch(symbol, fetch, reject_stale=False)
    minimum = STRATEGY_PARAMS["slow_period"] + 50
    if len(closed) < minimum:
        raise LiveSafetyError(
            f"insufficient closed candles for {symbol}: {len(closed)} < {minimum}"
        )
    return pl.DataFrame(
        {
            "timestamp": [
                datetime.fromtimestamp(bar[0] / 1000, tz=UTC) for bar in closed
            ],
            "open": [bar[1] for bar in closed],
            "high": [bar[2] for bar in closed],
            "low": [bar[3] for bar in closed],
            "close": [bar[4] for bar in closed],
            "volume": [bar[5] for bar in closed],
        }
    ).sort("timestamp")


def require_dedicated_account(
    adapter: CCXTAdapter, allocations: list[tuple[str, float]]
) -> None:
    """Refuse mainnet accounts containing assets outside the managed universe."""

    balances = asyncio.run(adapter.fetch_balance())
    crypto = balances.get(AssetClass.CRYPTO)
    allowed = {"USDT", *(symbol.split("/")[0] for symbol, _ in allocations)}
    unmanaged = []
    for asset, amounts in crypto.assets.items() if crypto else []:
        if asset not in allowed and float(amounts.get("total", 0)) > 0:
            unmanaged.append(asset)
    if unmanaged:
        raise LiveSafetyError(
            "mainnet account is not dedicated; unmanaged positive balances: "
            + ", ".join(sorted(unmanaged))
        )


def build_decisions(
    *,
    allocations: list[tuple[str, float]],
    states: dict[str, dict],
    positions: list[dict],
    equity: float,
    locked_reason: str | None,
    entries_locked_reason: str | None = None,
    limits: LiveRiskLimits | None = None,
) -> list[dict]:
    position_map = {position["symbol"]: position for position in positions}
    decisions: list[dict] = []
    for market_symbol, allocation in allocations:
        state = states[market_symbol]
        existing = position_map.get(market_symbol)
        current_qty = float(existing["qty"]) if existing else 0.0
        current_notional = current_qty * state["price"]
        target_notional = equity * allocation
        delta = target_notional - current_notional
        deadband = max(target_notional * 0.05, 10.0)
        # The finite replay window may not contain an old entry crossover. An
        # existing position remains valid while the fast MA is still above the
        # slow MA; otherwise a healthy long held for >LOOKBACK bars could be
        # liquidated merely because its original entry fell out of memory.
        desired_long = state["state"] == "LONG" or (
            current_qty > 0
            and state.get("risk_exit") is None
            and float(state["ma_fast"]) > float(state["ma_slow"])
        )

        if locked_reason and current_qty > 0:
            action, qty, reason = "SELL", current_qty, "RISK_CIRCUIT_BREAKER"
        elif locked_reason:
            continue
        elif desired_long and delta > deadband and entries_locked_reason:
            continue
        elif desired_long and delta > deadband:
            buy_notional = delta
            if limits is not None:
                safe_order_budget = limits.effective_max_order_notional(equity) / (
                    1 + limits.max_price_deviation_pct
                )
                buy_notional = min(
                    buy_notional,
                    safe_order_budget,
                )
            action, qty, reason = "BUY", buy_notional / state["price"], "REBALANCE"
        elif desired_long and delta < -deadband:
            action, qty, reason = (
                "SELL",
                min(abs(delta) / state["price"], current_qty),
                "REBALANCE",
            )
        elif not desired_long and current_qty > 0:
            action, qty, reason = "SELL", current_qty, "STRATEGY_FLAT"
        else:
            continue
        if qty <= 0 or not math.isfinite(qty):
            raise LiveSafetyError(f"invalid decision quantity for {market_symbol}")
        decisions.append(
            {
                "market_symbol": market_symbol,
                "action": action,
                "qty": qty,
                "signal_price": state["price"],
                "candle_timestamp": state["candle_timestamp"],
                "atr": state.get("atr"),
                "observed_high": state.get("recent_high"),
                "reason": reason,
            }
        )
    return sorted(decisions, key=lambda item: 0 if item["action"] == "SELL" else 1)


def position_snapshot(positions: list[dict]) -> tuple[dict[str, dict], float]:
    by_symbol = {position["symbol"]: position for position in positions}
    gross = sum(float(position["market_value"]) for position in positions)
    if not math.isfinite(gross) or gross < 0:
        raise LiveSafetyError("invalid gross exposure")
    return by_symbol, gross


CONTROLLED_DUST_CONSTRAINTS = frozenset(
    {
        "amount_zero",
        "minimum_amount",
        "minimum_notional",
    }
)


def classify_controlled_dust(
    *,
    pair: str,
    quantity: float,
    reference_price: float,
    error: OrderConstraintError,
    store: LiveRiskStateStore,
    limits: LiveRiskLimits | None,
    context: str,
    audit_log_path: str | None = None,
) -> dict[str, object] | None:
    """Persist only deterministic, locally bounded exchange-filter dust."""

    if limits is None or error.constraint not in CONTROLLED_DUST_CONSTRAINTS:
        return None
    if (
        not math.isfinite(quantity)
        or quantity <= 0
        or not math.isfinite(reference_price)
        or reference_price <= 0
    ):
        return None
    estimated_notional = quantity * reference_price
    if (
        not math.isfinite(estimated_notional)
        or estimated_notional < 0
        or estimated_notional > limits.max_dust_notional_usd + 1e-12
    ):
        return None
    dust = store.mark_position_dust(
        pair,
        quantity=quantity,
        estimated_notional=estimated_notional,
        reason=error.constraint,
    )
    if audit_log_path:
        append_live_audit_event(
            audit_log_path,
            "position_dust_classified",
            {
                "symbol": pair,
                "quantity": quantity,
                "estimated_notional": estimated_notional,
                "constraint": error.constraint,
                "context": context,
            },
        )
    return dust


def sellable_position_quantity(
    position: dict,
    active_protective: dict[str, object] | None,
) -> float:
    """Return free quantity plus the balance reserved by our active stop."""

    try:
        total = float(position.get("qty") or 0.0)
        free = float(position.get("free_qty", total))
        locked = float(position.get("locked_qty", max(0.0, total - free)))
    except (TypeError, ValueError) as exc:
        raise LiveSafetyError("spot position quantities must be numeric") from exc
    if any(not math.isfinite(value) or value < 0 for value in (total, free, locked)):
        raise LiveSafetyError(
            "spot position quantities must be finite and non-negative"
        )
    tolerance = max(1e-12, total * 1e-8)
    if (
        free > total + tolerance
        or locked > total + tolerance
        or free + locked > total + tolerance
    ):
        raise LiveSafetyError("spot free or locked quantity exceeds total position")

    protective_reserved = 0.0
    if isinstance(active_protective, dict):
        status = str(active_protective.get("status") or "").lower()
        if status in {"open", "partial"}:
            try:
                protected_quantity = float(active_protective.get("quantity") or 0.0)
            except (TypeError, ValueError) as exc:
                raise LiveSafetyError(
                    "active protective quantity must be numeric"
                ) from exc
            if not math.isfinite(protected_quantity) or protected_quantity <= 0:
                raise LiveSafetyError(
                    "active protective quantity must be finite and positive"
                )
            protective_reserved = min(locked, protected_quantity)
    return min(total, free + protective_reserved)


def validate_sell_quantity_capacity(
    *,
    pair: str,
    requested_quantity: float,
    position: dict | None,
    active_protective: dict[str, object] | None,
) -> None:
    if position is None:
        raise LiveSafetyError(f"cannot sell {pair}: no current spot position")
    if not math.isfinite(requested_quantity) or requested_quantity <= 0:
        raise LiveSafetyError(f"invalid sell quantity for {pair}")
    capacity = sellable_position_quantity(position, active_protective)
    tolerance = max(1e-12, requested_quantity * 1e-8)
    if requested_quantity > capacity + tolerance:
        raise LiveSafetyError(
            f"sell quantity exceeds available balance for {pair}: "
            f"requested {requested_quantity:.12g}, safely available {capacity:.12g}"
        )


def protected_execution_quote(
    *,
    broker: LiveBroker,
    symbol: Symbol,
    side: str,
    requested_quantity: float,
    signal_price: float,
    limits: LiveRiskLimits,
    monitor: DataTrustMonitor | None = None,
) -> tuple[float, float]:
    """Return exchange-normalized quantity and depth-aware expected fill price."""

    ticker = broker.get_ticker(symbol)
    bid = ticker.get("bid")
    ask = ticker.get("ask")
    if bid is None or ask is None:
        raise LiveSafetyError(
            f"two-sided executable quote is missing for {symbol.pair}"
        )
    ticker_started = ticker.get("request_started_at")
    ticker_received = ticker.get("received_at")
    if ticker_started is not None and ticker_received is not None:
        ticker_fetch = TimeStampedFetch(
            exchange_timestamp=ticker["timestamp"].timestamp() * 1000,
            request_started_at=float(ticker_started),
            received_at=float(ticker_received),
        )
        reject_high_latency(ticker_fetch, context=f"{symbol.pair} ticker")
        if monitor is not None:
            monitor.record_fetch(symbol.pair, ticker_fetch)
    validate_spread(bid=float(bid), ask=float(ask), limits=limits)
    top_price = float(ask if side == "BUY" else bid)
    validate_fresh_quote(
        signal_price=signal_price,
        quote_price=top_price,
        quote_timestamp=ticker["timestamp"],
        limits=limits,
    )
    quantity = broker.normalize_order_amount(
        symbol,
        requested_quantity,
        reference_price=top_price,
    )
    book = broker.get_order_book(symbol, limit=50)
    if not book["bids"] or not book["asks"]:
        raise LiveSafetyError(f"two-sided order book is missing for {symbol.pair}")
    book_started = book.get("request_started_at")
    book_received = book.get("received_at")
    if book_started is not None and book_received is not None:
        book_fetch = TimeStampedFetch(
            exchange_timestamp=book["timestamp"].timestamp() * 1000,
            request_started_at=float(book_started),
            received_at=float(book_received),
        )
        reject_high_latency(book_fetch, context=f"{symbol.pair} order book")
        if monitor is not None:
            monitor.record_fetch(symbol.pair, book_fetch)
    if monitor is not None and book.get("sequence") is not None:
        seq_status = monitor.sequences.on_rest_snapshot(symbol.pair, book["sequence"])
        if seq_status in ("stale", "duplicate", "invalid"):
            raise SequenceGapError(
                f"order book sequence for {symbol.pair} is untrusted ({seq_status})"
            )
    validate_spread(
        bid=float(book["bids"][0][0]),
        ask=float(book["asks"][0][0]),
        limits=limits,
    )
    expected_vwap = validate_order_book_depth(
        side=side,
        quantity=quantity,
        bids=book["bids"],
        asks=book["asks"],
        book_timestamp=book["timestamp"],
        limits=limits,
    )
    quantity = broker.normalize_order_amount(
        symbol,
        quantity,
        reference_price=expected_vwap,
    )
    validate_fresh_quote(
        signal_price=signal_price,
        quote_price=expected_vwap,
        quote_timestamp=book["timestamp"],
        limits=limits,
    )
    return quantity, expected_vwap


def prepare_orders(
    *,
    decisions: list[dict],
    broker: LiveBroker,
    account: dict,
    positions: list[dict],
    limits: LiveRiskLimits,
    locked_reason: str | None,
    store: LiveRiskStateStore,
    audit_log_path: str | None = None,
    monitor: DataTrustMonitor | None = None,
) -> list[dict]:
    """Preflight the complete batch before any order can be submitted."""

    simulated_cash = float(account["cash"])
    equity = float(account["equity"])
    simulated_positions, simulated_gross = position_snapshot(positions)
    prepared: list[dict] = []

    for decision in decisions:
        pair = decision["market_symbol"]
        symbol = exchange_symbol(pair)
        existing = simulated_positions.get(pair)
        active_protective = None
        if decision["action"] == "SELL":
            active = store.protective_order_state(pair).get("active")
            active_protective = active if isinstance(active, dict) else None
        try:
            quantity, quote_price = protected_execution_quote(
                broker=broker,
                symbol=symbol,
                side=decision["action"],
                requested_quantity=float(decision["qty"]),
                signal_price=decision["signal_price"],
                limits=limits,
                monitor=monitor,
            )
        except OrderConstraintError as exc:
            dust = None
            if decision["action"] == "SELL":
                dust = classify_controlled_dust(
                    pair=pair,
                    quantity=float(decision["qty"]),
                    reference_price=float(decision["signal_price"]),
                    error=exc,
                    store=store,
                    limits=limits,
                    context="preflight_exit",
                    audit_log_path=audit_log_path,
                )
            if dust is not None:
                continue
            raise
        if decision["action"] == "SELL":
            validate_sell_quantity_capacity(
                pair=pair,
                requested_quantity=quantity,
                position=existing,
                active_protective=active_protective,
            )
            store.clear_position_dust(pair)

        current_notional = float(existing["market_value"]) if existing else 0.0
        notional = quantity * float(quote_price)
        validate_order_risk(
            side=decision["action"],
            notional_usd=notional,
            equity=equity,
            cash=simulated_cash,
            current_symbol_notional=current_notional,
            gross_exposure=simulated_gross,
            limits=limits,
            locked_reason=locked_reason,
        )

        if decision["action"] == "BUY":
            simulated_cash -= notional
            simulated_gross += notional
            simulated_positions[pair] = {"market_value": current_notional + notional}
        else:
            simulated_cash += notional
            simulated_gross = max(0.0, simulated_gross - notional)
            simulated_positions[pair] = {
                **(existing or {}),
                "qty": max(0.0, float(existing["qty"]) - quantity),
                "free_qty": max(
                    0.0,
                    float(existing.get("free_qty", existing["qty"])) - quantity,
                ),
                "market_value": max(0.0, current_notional - notional),
            }
        prepared.append(
            {
                **decision,
                "qty": quantity,
                "quote_price": float(quote_price),
                "notional": notional,
            }
        )
    return prepared


def protect_remaining_position(
    *,
    planned: dict,
    result: dict,
    broker: LiveBroker,
    lifecycle: ExecutionLifecycle,
    gateway: BrokerGateway,
    store: LiveRiskStateStore,
    limits: LiveRiskLimits | None = None,
    audit_log_path: str | None = None,
) -> float:
    """Protect any position left after a filled or partially filled order."""

    pair = planned["market_symbol"]
    refreshed_positions = broker.get_positions()
    refreshed = next(
        (position for position in refreshed_positions if position["symbol"] == pair),
        None,
    )
    remaining_quantity = float(refreshed["qty"]) if refreshed else 0.0
    if not math.isfinite(remaining_quantity) or remaining_quantity < 0:
        raise LiveSafetyError(f"invalid post-order position quantity for {pair}")
    if remaining_quantity == 0:
        store.clear_position_risk(pair)
        return 0.0

    atr = float(planned.get("atr") or 0.0)
    if not math.isfinite(atr) or atr <= 0:
        raise LiveSafetyError(
            f"cannot protect post-order position without a valid ATR for {pair}"
        )
    fill_price = float(result.get("avg_fill_price") or planned["signal_price"])
    if planned["action"] == "BUY":
        observed_high = max(fill_price, float(planned["signal_price"]))
        _, desired_stop = store.observe_position_risk(
            pair,
            quantity=remaining_quantity,
            observed_high=observed_high,
            atr=atr,
            atr_multiplier=ATR_SL_MULT,
        )
    else:
        risk_record = store.state.position_risk.get(pair) or {}
        desired_stop = float(risk_record.get("trailing_stop") or 0.0)
        if not math.isfinite(desired_stop) or desired_stop <= 0:
            raise LiveSafetyError(
                f"cannot restore protection after a partial exit for {pair}: "
                "trailing stop state is missing"
            )
    try:
        ensure_protective_stop(
            pair=pair,
            quantity=remaining_quantity,
            desired_stop=desired_stop,
            current_price=float(planned["signal_price"]),
            broker=broker,
            lifecycle=lifecycle,
            gateway=gateway,
            store=store,
            limits=limits,
            audit_log_path=audit_log_path,
        )
    except Exception as exc:
        if audit_log_path:
            append_live_audit_event(
                audit_log_path,
                "position_protection_failed",
                {
                    "symbol": pair,
                    "side": planned["action"],
                    "order_status": str(result.get("status") or "unknown"),
                    "remaining_quantity": remaining_quantity,
                    "estimated_notional": (
                        remaining_quantity * float(planned["signal_price"])
                    ),
                    "error": str(exc)[:500],
                },
            )
        raise LiveSafetyError(
            f"post-order position cannot be protected for {pair}: {exc}"
        ) from exc
    return remaining_quantity


def _canonical_result_payload(result) -> dict:
    """Preserve the broker payload while making canonical state explicit."""

    payload = dict(result.raw_response or {})
    state_to_status = {
        BrokerSubmitState.ACCEPTED: "open",
        BrokerSubmitState.OPEN: "open",
        BrokerSubmitState.PARTIALLY_FILLED: "partial",
        BrokerSubmitState.FILLED: "filled",
        BrokerSubmitState.REJECTED: "rejected",
        BrokerSubmitState.FAILED_LOCAL: "rejected",
        BrokerSubmitState.UNKNOWN: "unknown",
    }
    payload.setdefault("id", result.broker_order_id or "")
    payload.setdefault("status", state_to_status.get(result.state, "unknown"))
    payload.setdefault("error", result.error)
    return payload


def _record_reconciled_submission(
    lifecycle: ExecutionLifecycle,
    intent_id: str,
    result: dict,
) -> None:
    """Persist client-ID reconciliation as typed lifecycle evidence."""

    status = str(result.get("status") or "unknown").lower()
    state = {
        "open": BrokerSubmitState.OPEN,
        "partial": BrokerSubmitState.PARTIALLY_FILLED,
        "filled": BrokerSubmitState.FILLED,
        "rejected": BrokerSubmitState.REJECTED,
        "cancelled": BrokerSubmitState.REJECTED,
        "expired": BrokerSubmitState.REJECTED,
    }.get(status)
    if state is None:
        raise LiveSafetyError(
            f"reconciliation returned non-actionable status {status} for {intent_id}"
        )

    broker_order_id = (
        str(
            result.get("id")
            or result.get("order_id")
            or result.get("exchange_order_id")
            or ""
        )
        or None
    )
    if state != BrokerSubmitState.REJECTED and broker_order_id is None:
        raise LiveSafetyError(
            f"reconciliation returned {status} without broker order ID for {intent_id}"
        )

    order = lifecycle.state.order(intent_id)
    if broker_order_id and order is not None and not order.exchange_order_id:
        lifecycle.submit_order(intent_id, exchange_order_id=broker_order_id)

    normalized = dict(result)
    normalized.setdefault(
        "filled_qty",
        result.get("filled_amount", result.get("filled", 0.0)),
    )
    normalized.setdefault(
        "avg_fill_price",
        result.get("average", result.get("price", 0.0)),
    )
    error = str(result.get("error") or "") or None
    lifecycle.record_broker_submit_result(
        intent_id,
        BrokerSubmitFact(
            state=state,
            broker_order_id=broker_order_id,
            client_order_id=str(result.get("client_order_id") or intent_id),
            venue="binance",
            broker_status=status,
            observed_at=datetime.now(UTC),
            error=error,
            raw_response=normalized,
        ),
    )


def _cancel_canonical_order(
    *,
    lifecycle: ExecutionLifecycle,
    gateway: BrokerGateway,
    intent_id: str,
    broker_order_id: str,
    symbol: str,
    reason: str,
) -> None:
    """Persist a cancel request and require broker-confirmed terminal evidence."""

    lifecycle_order = lifecycle.state.order(intent_id)
    if lifecycle_order is not None:
        if lifecycle_order.status.value != "cancel_requested":
            lifecycle.request_cancel(intent_id, reason=reason)
    cancel = gateway.cancel(
        broker_order_id,
        correlation_id=f"{intent_id}-cancel",
        symbol=symbol,
    )
    if (
        not cancel.success
        or cancel.evidence is None
        or cancel.evidence.state is not CancelState.CANCELED
    ):
        raise LiveSafetyError(
            f"broker did not confirm cancellation for {broker_order_id}: "
            f"{cancel.error or 'unknown cancel state'}"
        )
    if lifecycle_order is not None:
        lifecycle.confirm_cancel(intent_id, cancel.evidence)


def _authorize_live_order(
    *,
    lifecycle: ExecutionLifecycle,
    planned: dict,
    intent_id: str,
    symbol: str,
    quantity: float,
    reason: str,
    order_type: str = "market",
    stop_price: float | None = None,
) -> str:
    """Create a durable authorization without caller-controlled risk facts."""

    metadata: dict[str, object] = {
        "order_type": order_type,
        "time_in_force": "gtc",
    }
    if stop_price is not None:
        metadata["stop_price"] = stop_price
    side = str(planned["action"]).lower()
    if side == "sell":
        auth = lifecycle.emergency_reduce(
            EmergencyReduceRequest(
                intent_id=intent_id,
                symbol=symbol,
                side="sell",
                quantity=quantity,
                reason=reason,
                idempotency_key=intent_id,
                metadata=metadata,
            )
        )
        return str(auth.payload["authorization_id"])

    risk_decision = planned.get("risk_decision")
    if not isinstance(risk_decision, UnifiedRiskDecision):
        raise LiveSafetyError(
            f"BUY {symbol} has no promoted UnifiedRiskDecision; exposure increase blocked"
        )
    lifecycle.create_order_intent(
        intent_id=intent_id,
        symbol=symbol,
        side="buy",
        size=quantity,
        idempotency_key=intent_id,
    )
    lifecycle.approve_risk(intent_id, risk_decision=risk_decision)
    auth = lifecycle.authorize_order(
        intent_id=intent_id,
        idempotency_key=intent_id,
        metadata=metadata,
    )
    lifecycle.request_broker_submission(intent_id)
    return str(auth.payload["authorization_id"])


def execute_orders(
    *,
    orders: list[dict],
    broker: LiveBroker,
    store: LiveRiskStateStore,
    limits: LiveRiskLimits,
    locked_reason: str | None = None,
    audit_log_path: str | None = None,
    reconciliation_timeout_seconds: float = 0.0,
    monitor: DataTrustMonitor | None = None,
    lifecycle: ExecutionLifecycle | None = None,
    gateway: BrokerGateway | None = None,
    sleep_fn=time.sleep,
    monotonic_fn=time.monotonic,
) -> None:
    if lifecycle is None or gateway is None:
        raise LiveSafetyError(
            "canonical lifecycle and gateway are required for execution"
        )
    for planned in orders:
        account = broker.get_account()
        positions = broker.get_positions()
        position_map, gross = position_snapshot(positions)
        pair = planned["market_symbol"]
        symbol = exchange_symbol(pair)
        current = position_map.get(pair)
        active_protective = None
        if planned["action"] == "SELL":
            active_protective = reconcile_protective_stop(
                pair=pair,
                broker=broker,
                store=store,
                audit_log_path=audit_log_path,
            )
        try:
            quantity, quote_price = protected_execution_quote(
                broker=broker,
                symbol=symbol,
                side=planned["action"],
                requested_quantity=float(planned["qty"]),
                signal_price=planned["signal_price"],
                limits=limits,
                monitor=monitor,
            )
        except OrderConstraintError as exc:
            dust = None
            if planned["action"] == "SELL":
                dust = classify_controlled_dust(
                    pair=pair,
                    quantity=float(planned["qty"]),
                    reference_price=float(planned["signal_price"]),
                    error=exc,
                    store=store,
                    limits=limits,
                    context="execution_exit",
                    audit_log_path=audit_log_path,
                )
            if dust is not None:
                continue
            raise
        if planned["action"] == "SELL":
            validate_sell_quantity_capacity(
                pair=pair,
                requested_quantity=quantity,
                position=current,
                active_protective=active_protective,
            )
            store.clear_position_dust(pair)
        current_notional = float(current["market_value"]) if current else 0.0
        notional = quantity * float(quote_price)
        risk_decision = planned.get("risk_decision")
        validate_order_risk(
            side=planned["action"],
            notional_usd=notional,
            equity=float(account["equity"]),
            cash=float(account["cash"]),
            current_symbol_notional=current_notional,
            gross_exposure=gross,
            limits=limits,
            locked_reason=locked_reason or store.state.locked_reason,
            risk_decision=risk_decision,
        )

        order_key = make_order_key(
            symbol=pair,
            side=planned["action"],
            candle_timestamp=planned["candle_timestamp"],
        )
        store.reserve_order(
            order_key,
            symbol=pair,
            side=planned["action"],
            quantity=quantity,
            signal_timestamp=planned["candle_timestamp"],
        )
        store.update_order(order_key, status="submitted")
        try:
            if isinstance(active_protective, dict):
                protective_exchange_id = str(
                    active_protective.get("exchange_order_id") or ""
                )
                if not protective_exchange_id:
                    raise LiveSafetyError(
                        f"active protective order ID is missing for {pair}"
                    )
                _cancel_canonical_order(
                    lifecycle=lifecycle,
                    gateway=gateway,
                    intent_id=str(active_protective.get("client_order_id") or ""),
                    broker_order_id=protective_exchange_id,
                    symbol=pair,
                    reason="hand protective stop to market exit",
                )
                store.clear_active_protective_order(pair)
                _audit_protective_event(
                    audit_log_path,
                    "protective_stop_handed_to_exit",
                    {
                        "symbol": pair,
                        "protective_client_order_id": active_protective.get(
                            "client_order_id"
                        ),
                        "exit_client_order_id": order_key,
                    },
                )
            authorization_id = _authorize_live_order(
                lifecycle=lifecycle,
                planned=planned,
                intent_id=order_key,
                symbol=pair,
                quantity=quantity,
                reason="STRATEGY_EXIT"
                if planned["action"] == "SELL"
                else "STRATEGY_ENTRY",
            )
            submission = gateway.submit(
                authorization_id,
                correlation_id=order_key,
            )
            if submission.success and submission.broker_order_id:
                lifecycle.submit_order(
                    order_key,
                    exchange_order_id=submission.broker_order_id,
                )
            result = _canonical_result_payload(submission)
            if submission.state == BrokerSubmitState.UNKNOWN:
                raise LiveSafetyError(
                    f"broker submission state is unknown for {order_key}"
                )
            lifecycle.record_broker_submit_result(order_key, submission)
        except Exception as exc:
            store.update_order(order_key, status="reconciling", error=str(exc))
            if audit_log_path:
                append_live_audit_event(
                    audit_log_path,
                    "order_submission_unknown",
                    {"order_key": order_key, "symbol": pair, "error": str(exc)[:500]},
                )
            reconciled, reconcile_error = poll_order_by_client_id(
                broker=broker,
                order_key=order_key,
                symbol=symbol,
                timeout_seconds=reconciliation_timeout_seconds,
                sleep_fn=sleep_fn,
                monotonic_fn=monotonic_fn,
            )
            if reconciled is not None:
                persist_order_result(store, order_key, reconciled)
                _record_reconciled_submission(lifecycle, order_key, reconciled)
                audit_order_result(
                    audit_log_path,
                    "order_reconciled_after_submission_error",
                    order_key,
                    reconciled,
                )
                if isinstance(active_protective, dict):
                    reconciled_status = str(
                        reconciled.get("status") or "unknown"
                    ).lower()
                    old_stop = broker.get_order_by_client_id(
                        str(active_protective.get("client_order_id") or ""),
                        symbol,
                    )
                    old_status = (
                        str(old_stop.get("status") or "unknown").lower()
                        if old_stop is not None
                        else "missing"
                    )
                    if old_status != "open" or reconciled_status in {
                        "open",
                        "partial",
                        "filled",
                    }:
                        store.clear_active_protective_order(pair)
                if str(reconciled.get("status") or "unknown").lower() in {
                    "open",
                    "partial",
                    "filled",
                }:
                    protect_remaining_position(
                        planned=planned,
                        result=reconciled,
                        broker=broker,
                        lifecycle=lifecycle,
                        gateway=gateway,
                        store=store,
                        limits=limits,
                        audit_log_path=audit_log_path,
                    )
                reconciled_status = str(reconciled.get("status") or "unknown").lower()
                if reconciled_status not in ORDER_TERMINAL_STATUSES:
                    store.update_order(
                        order_key,
                        status="manual_intervention",
                        error=(
                            "reconciliation deadline expired with exchange status "
                            f"{reconciled_status}"
                        ),
                    )
                raise LiveSafetyError(
                    f"order submission raised but exchange reports "
                    f"{reconciled['status']} for {order_key}; batch stopped"
                ) from exc
            store.update_order(
                order_key,
                status="manual_intervention",
                error=(
                    "client order ID not found before reconciliation deadline"
                    + (f"; last error: {reconcile_error}" if reconcile_error else "")
                ),
            )
            if isinstance(active_protective, dict):
                try:
                    old_stop = broker.get_order_by_client_id(
                        str(active_protective.get("client_order_id") or ""),
                        symbol,
                    )
                except Exception as stop_reconcile_exc:
                    raise LiveSafetyError(
                        f"exit and protective stop outcomes are unknown for {pair}: "
                        f"{stop_reconcile_exc}"
                    ) from exc
                if (
                    old_stop is not None
                    and str(old_stop.get("status") or "").lower() == "open"
                ):
                    raise LiveSafetyError(
                        f"exit outcome is unknown for {order_key}; previous protective "
                        "stop remains active"
                    ) from exc
                store.clear_active_protective_order(pair)
            raise LiveSafetyError(
                f"order submission outcome is unknown for {order_key}; "
                "exchange did not find the client order ID before the deadline"
            ) from exc

        persist_order_result(store, order_key, result)
        audit_order_result(
            audit_log_path,
            "order_acknowledged",
            order_key,
            result,
        )
        if result.get("error") or result.get("status") not in ORDER_ACCEPTED_STATUSES:
            raise LiveSafetyError(f"order rejected or failed: {result}")
        protect_remaining_position(
            planned=planned,
            result=result,
            broker=broker,
            lifecycle=lifecycle,
            gateway=gateway,
            store=store,
            limits=limits,
            audit_log_path=audit_log_path,
        )
        if result.get("status") in {"open", "partial"}:
            store.update_order(order_key, status="reconciling")
            reconciled, reconcile_error = poll_order_by_client_id(
                broker=broker,
                order_key=order_key,
                symbol=symbol,
                timeout_seconds=reconciliation_timeout_seconds,
                sleep_fn=sleep_fn,
                monotonic_fn=monotonic_fn,
            )
            if reconciled is not None:
                persist_order_result(store, order_key, reconciled)
                result = reconciled
                audit_order_result(
                    audit_log_path,
                    "order_polled",
                    order_key,
                    result,
                )
                if result.get("status") in ORDER_ACCEPTED_STATUSES:
                    protect_remaining_position(
                        planned=planned,
                        result=result,
                        broker=broker,
                        lifecycle=lifecycle,
                        gateway=gateway,
                        store=store,
                        limits=limits,
                        audit_log_path=audit_log_path,
                    )
            if (
                reconciled is None
                or result.get("status") not in ORDER_TERMINAL_STATUSES
            ):
                store.update_order(
                    order_key,
                    status="manual_intervention",
                    error=(
                        "reconciliation deadline expired"
                        + (
                            f"; last error: {reconcile_error}"
                            if reconcile_error
                            else ""
                        )
                    ),
                )
        if result.get("status") != "filled":
            if audit_log_path:
                append_live_audit_event(
                    audit_log_path,
                    "order_non_terminal",
                    {
                        "order_key": order_key,
                        "symbol": pair,
                        "status": str(result.get("status")),
                        "filled_qty": float(result.get("filled_qty") or 0.0),
                    },
                )
            raise LiveSafetyError(
                f"order {order_key} is {result.get('status')}; "
                "batch stopped until reconciliation completes"
            )
        if audit_log_path:
            append_live_audit_event(
                audit_log_path,
                "order_filled",
                {
                    "order_key": order_key,
                    "exchange_order_id": str(result.get("id") or ""),
                    "symbol": pair,
                    "side": planned["action"],
                    "filled_qty": float(result.get("filled_qty") or 0.0),
                    "average_fill_price": float(result.get("avg_fill_price") or 0.0),
                    "signal_price": float(planned["signal_price"]),
                    "quote_cost": float(result.get("quote_cost") or 0.0),
                    "exchange_status": str(result.get("exchange_status") or ""),
                    "trade_ids": list(result.get("trade_ids") or []),
                    "fees": dict(result.get("fees") or {}),
                },
            )
        print(
            f"  ✅ Order: {result['side']} {result['qty']} {result['symbol']} "
            f"→ {result['status']} ({planned['reason']}, id={order_key})"
        )


def audit_order_result(
    audit_log_path: str | None,
    event: str,
    order_key: str,
    result: dict,
) -> None:
    if not audit_log_path:
        return
    append_live_audit_event(
        audit_log_path,
        event,
        {
            "order_key": order_key,
            "exchange_order_id": str(result.get("id") or ""),
            "status": str(result.get("status") or "unknown"),
            "exchange_status": str(result.get("exchange_status") or ""),
            "filled_qty": float(result.get("filled_qty") or 0.0),
            "average_fill_price": float(result.get("avg_fill_price") or 0.0),
            "quote_cost": float(result.get("quote_cost") or 0.0),
            "trade_ids": list(result.get("trade_ids") or []),
            "fees": dict(result.get("fees") or {}),
            "error": str(result.get("error") or "")[:500],
        },
    )


def persist_order_result(
    store: LiveRiskStateStore,
    order_key: str,
    result: dict,
) -> None:
    raw_status = str(result.get("status") or "unknown").strip().lower()
    target_status = {
        "open": "open",
        "partial": "partial",
        "filled": "filled",
        "closed": "filled",
        "cancelled": "cancelled",
        "canceled": "cancelled",
        "rejected": "rejected",
        "expired": "expired",
    }.get(raw_status, "manual_intervention")
    fees = result.get("fees")
    trade_ids = result.get("trade_ids")
    evidence = {
        "exchange_order_id": str(result.get("id") or ""),
        "filled_quantity": float(result.get("filled_qty") or 0.0),
        "average_fill_price": float(result.get("avg_fill_price") or 0.0),
        "quote_cost": (
            float(result["quote_cost"])
            if result.get("quote_cost") is not None
            else None
        ),
        "fees": fees if isinstance(fees, dict) else None,
        "trade_ids": trade_ids if isinstance(trade_ids, (list, tuple)) else None,
        "exchange_status": str(result.get("exchange_status") or ""),
        "error": str(result.get("error") or ""),
    }
    record = store.state.order_ledger.get(order_key)
    if not isinstance(record, dict):
        raise LiveSafetyError(f"order intent is not present in ledger: {order_key}")
    current_status = str(record.get("status") or "reserved").strip().lower()
    if current_status == "reserved":
        store.update_order(order_key, status="submitted")
        current_status = "submitted"
    if current_status in {"manual_intervention", "unknown"}:
        store.update_order(order_key, status="reconciling")
        current_status = "reconciling"
    if current_status in {"submitted", "reconciling"}:
        store.update_order(order_key, status="acknowledged", **evidence)
    store.update_order(order_key, status=target_status, **evidence)


def reconcile_unfinished_orders(
    *,
    broker: LiveBroker,
    store: LiveRiskStateStore,
    audit_log_path: str | None = None,
    timeout_seconds: float = 0.0,
    sleep_fn=time.sleep,
    monotonic_fn=time.monotonic,
) -> None:
    """Reconcile every non-terminal intent before a new batch can be planned."""

    unresolved: list[str] = []
    reconciled_any = False
    for order_key, record in store.unfinished_orders().items():
        pair = str(record.get("symbol") or "")
        if not pair:
            store.update_order(
                order_key,
                status="manual_intervention",
                error="order ledger is missing symbol metadata",
            )
            unresolved.append(f"{order_key}: missing symbol metadata")
            continue
        current_status = str(record.get("status") or "reserved").lower()
        if current_status != "reconciling":
            store.update_order(order_key, status="reconciling")
        result, reconcile_error = poll_order_by_client_id(
            broker=broker,
            order_key=order_key,
            symbol=exchange_symbol(pair),
            timeout_seconds=timeout_seconds,
            sleep_fn=sleep_fn,
            monotonic_fn=monotonic_fn,
        )
        if result is None:
            store.update_order(
                order_key,
                status="manual_intervention",
                error=(
                    "client order ID not found before reconciliation deadline"
                    + (f"; last error: {reconcile_error}" if reconcile_error else "")
                ),
            )
            unresolved.append(f"{order_key}: client order ID not found")
            continue
        reconciled_any = True
        persist_order_result(store, order_key, result)
        ledger_status = str(
            store.state.order_ledger[order_key].get("status") or ""
        ).lower()
        if audit_log_path:
            append_live_audit_event(
                audit_log_path,
                "order_reconciled",
                {
                    "order_key": order_key,
                    "symbol": pair,
                    "status": ledger_status,
                    "exchange_status": str(result.get("exchange_status") or ""),
                    "filled_qty": float(result.get("filled_qty") or 0.0),
                    "quote_cost": float(result.get("quote_cost") or 0.0),
                    "trade_ids": list(result.get("trade_ids") or []),
                    "fees": dict(result.get("fees") or {}),
                },
            )
        if ledger_status not in ORDER_TERMINAL_STATUSES:
            if ledger_status != "manual_intervention":
                store.update_order(
                    order_key,
                    status="manual_intervention",
                    error=(
                        "reconciliation deadline expired with exchange status "
                        f"{result.get('status')}"
                    ),
                )
            unresolved.append(f"{order_key}: exchange status {result.get('status')}")

    if reconciled_any:
        try:
            account = broker.get_account()
            positions = broker.get_positions()
            equity = float(account["equity"])
            cash = float(account["cash"])
            if not math.isfinite(equity) or not math.isfinite(cash):
                raise LiveSafetyError(
                    "account reconciliation returned non-finite balances"
                )
            if not isinstance(positions, list):
                raise LiveSafetyError("position reconciliation did not return a list")
        except Exception as exc:
            unresolved.append(f"balance reconciliation failed: {exc}")
        else:
            if audit_log_path:
                append_live_audit_event(
                    audit_log_path,
                    "order_balance_reconciled",
                    {
                        "equity": equity,
                        "cash": cash,
                        "position_count": len(positions),
                    },
                )
    if unresolved:
        if audit_log_path:
            append_live_audit_event(
                audit_log_path,
                "reconciliation_blocked",
                {"reasons": unresolved},
            )
        raise LiveSafetyError(
            "unfinished order reconciliation blocked the batch: "
            + "; ".join(unresolved)
        )


def run_locked(
    args: argparse.Namespace,
    *,
    limits: LiveRiskLimits,
    allocations: list[tuple[str, float]],
    state_path: str,
    profile: str,
    run_id: str,
) -> int:
    reconciliation_timeout = order_reconciliation_timeout_seconds()
    key_name = "BINANCE_TESTNET_API_KEY" if args.testnet else "BINANCE_API_KEY"
    secret_name = "BINANCE_TESTNET_API_SECRET" if args.testnet else "BINANCE_API_SECRET"
    api_key = os.getenv(key_name, "")
    secret = os.getenv(secret_name, "")
    if not api_key or not secret:
        raise LiveSafetyError(f"{key_name} and {secret_name} are required")
    # Context binding persists state even during dry-runs, so every runner mode
    # requires the integrity key. This prevents creating an unsigned baseline
    # that cannot be safely reused for later execution.
    integrity_key = validate_integrity_key(os.getenv("LIVE_SAFETY_HMAC_KEY", ""))
    store = LiveRiskStateStore(
        state_path,
        integrity_key=integrity_key,
    )
    if args.execute and not args.testnet and not store.existed:
        raise LiveSafetyError(
            "run a successful mainnet dry-run first to initialize risk state"
        )
    allocation_map = dict(allocations)
    store.bind_context(
        account=account_fingerprint(
            exchange="binance-testnet" if args.testnet else "binance-mainnet",
            api_key=api_key,
        ),
        strategy=strategy_fingerprint(
            strategy="enhanced_ma",
            params=STRATEGY_PARAMS,
            allocations=allocation_map,
        ),
        symbols=list(allocation_map),
    )
    try:
        limit_change = store.bind_risk_limits(
            profile=profile,
            limits=limits,
            approve_increase=(
                getattr(args, "confirm_risk_increase", None)
                == RISK_INCREASE_CONFIRMATION
            ),
        )
    except LiveSafetyError as exc:
        if args.audit_log:
            append_live_audit_event(
                args.audit_log,
                "risk_limits_change_blocked",
                {"requested_profile": profile, "error": str(exc)[:500]},
            )
        raise
    if limit_change is not None and args.audit_log:
        append_live_audit_event(
            args.audit_log,
            (
                "risk_limits_initialized"
                if not limit_change["previous_limits"]
                else "risk_limits_changed"
            ),
            limit_change,
        )
    if args.execute and not args.testnet:
        build_sha = validate_build_sha(os.getenv("TRADING_BUILD_SHA"))
        validate_strategy_evidence(
            args.evidence_file,
            expected_symbols=[symbol for symbol, _ in allocations],
            expected_params=STRATEGY_PARAMS,
            expected_allocations=allocation_map,
            expected_build_sha=build_sha,
            integrity_key=integrity_key,
        )

    print("=" * 80)
    mode = (
        "DRY-RUN"
        if not args.execute
        else "TESTNET EXECUTION"
        if args.testnet
        else "MAINNET EXECUTION"
    )
    print(f"BINANCE SPOT — Enhanced MA — {mode}")
    print(f"Run ID: {run_id}")
    print(
        f"Profile {profile}; limits: order min(${limits.max_order_notional_usd:,.2f}, "
        f"{limits.max_order_equity_pct:.2%} equity), "
        f"symbol {limits.max_symbol_exposure_pct:.0%}, gross {limits.max_gross_exposure_pct:.0%}, "
        f"cash reserve {limits.min_cash_reserve_pct:.0%}"
    )
    print("=" * 80)

    # P0.3 — trusted time: sync local clock with the exchange before any
    # market data or order activity, and fail closed on excessive skew.
    clock = ServerClock(
        time_url=BINANCE_TESTNET_TIME_URL if args.testnet else BINANCE_MAINNET_TIME_URL,
        tolerance_s=DEFAULT_CLOCK_SKEW_S,
    )
    monitor = DataTrustMonitor(clock=clock)
    try:
        clock.sync()
        skew = clock.check()
    except DataTrustError as exc:
        raise LiveSafetyError(f"clock sync failed: {exc}") from exc
    print(
        f"Clock sync: exchange offset {skew:+.3f}s (tolerance {clock.tolerance_s:.1f}s)"
    )

    adapter = CCXTAdapter(
        ExchangeConfig(
            id="binance",
            name="Binance",
            api_key=api_key,
            secret=secret,
            testnet=args.testnet,
            enable_rate_limit=True,
            markets=[MarketType.SPOT],
            options={"defaultType": "spot"},
        )
    )
    asyncio.run(adapter.connect())
    try:
        if not args.testnet:
            require_dedicated_account(adapter, allocations)
        broker = LiveBroker(
            "binance",
            adapter,
            pricing_symbols=[symbol for symbol, _ in allocations],
            strict_pricing=True,
        )

        def _canonical_price_source(symbol_value: str) -> TrustedPrice | None:
            ticker = broker.get_ticker(exchange_symbol(str(symbol_value)))
            price = float(ticker.get("last") or 0.0)
            exchange_timestamp = ticker.get("timestamp")
            received_at = ticker.get("received_at")
            if (
                not math.isfinite(price)
                or price <= 0
                or not isinstance(exchange_timestamp, datetime)
                or exchange_timestamp.tzinfo is None
                or not isinstance(received_at, datetime)
                or received_at.tzinfo is None
            ):
                return None
            return TrustedPrice(
                price=price,
                exchange_timestamp=exchange_timestamp.astimezone(UTC),
                received_at=received_at.astimezone(UTC),
            )

        def _canonical_portfolio_source(
            symbol_value: str,
        ) -> PortfolioRiskSnapshot | None:
            account_snapshot = broker.get_account()
            equity_value = float(account_snapshot.get("equity") or 0.0)
            cash_value = float(account_snapshot.get("cash") or 0.0)
            if (
                not math.isfinite(equity_value)
                or equity_value <= 0
                or not math.isfinite(cash_value)
                or cash_value < 0
            ):
                return None
            position = next(
                (
                    item
                    for item in broker.get_positions()
                    if str(item.get("symbol")) == str(symbol_value)
                ),
                None,
            )
            position_quantity = float(position.get("qty") or 0.0) if position else 0.0
            available_quantity = (
                float(position.get("free_qty") or 0.0)
                if position and "free_qty" in position
                else position_quantity
            )
            if any(
                not math.isfinite(value) or value < 0
                for value in (position_quantity, available_quantity)
            ):
                return None
            return PortfolioRiskSnapshot(
                symbol=str(symbol_value),
                position_quantity=position_quantity,
                available_quantity=available_quantity,
                equity=equity_value,
                available_cash=cash_value,
                observed_at=datetime.now(UTC),
                source="binance_live_broker",
            )

        canonical_store = ExecutionEventStore("data/execution/events.db").connect()
        canonical_lifecycle = ExecutionLifecycle(
            canonical_store,
            price_source=_canonical_price_source,
            inventory_source=lambda symbol_value, side: (
                snapshot.available_quantity
                if (snapshot := _canonical_portfolio_source(symbol_value)) is not None
                else 0.0
            ),
            portfolio_source=_canonical_portfolio_source,
        )
        canonical_gateway = BrokerGateway(
            adapter=LiveBrokerExecutionAdapter(broker),
            store=canonical_store,
            lifecycle=canonical_lifecycle,
        )

        reconcile_unfinished_orders(
            broker=broker,
            store=store,
            audit_log_path=args.audit_log,
            timeout_seconds=reconciliation_timeout,
        )
        account = broker.get_account()
        positions = broker.get_positions()
        if args.execute:
            cleanup_orphan_protective_stops(
                managed_symbols=[symbol for symbol, _ in allocations],
                positions=positions,
                broker=broker,
                lifecycle=canonical_lifecycle,
                gateway=canonical_gateway,
                store=store,
                audit_log_path=args.audit_log,
            )
        equity = float(account["equity"])
        risk_locked_reason = store.observe_equity(equity, limits)
        entries_locked_reason = configured_entry_lock_reason()
        locked_reason = risk_locked_reason or entries_locked_reason
        metrics = store.metrics(equity)
        print(
            f"Account: equity ${equity:,.2f}, cash ${float(account['cash']):,.2f}, "
            f"DD {float(metrics['drawdown_pct']):.2%}, "
            f"daily loss {float(metrics['daily_loss_pct']):.2%}"
        )
        if risk_locked_reason:
            print(
                f"⛔ CIRCUIT BREAKER: {risk_locked_reason}; "
                "positions will be reduced and buys are blocked"
            )
        if entries_locked_reason:
            print(
                f"⛔ ENTRY LOCK: {entries_locked_reason}; "
                "buys are blocked while risk-reducing sells remain enabled"
            )
            if args.audit_log:
                append_live_audit_event(
                    args.audit_log,
                    "entry_kill_switch_active",
                    {"reason": entries_locked_reason},
                )

        states: dict[str, dict] = {}
        data_errors: list[str] = []
        for pair, allocation in allocations:
            try:
                state = compute_state(get_recent_df(pair, monitor=monitor))
                states[pair] = state
                print(
                    f"{pair}: {state['state']} @ ${state['price']:,.2f}, "
                    f"MA {state['ma_fast']:.2f}/{state['ma_slow']:.2f}, "
                    f"ADX {state['adx']:.1f}, target {allocation:.0%}"
                )
            except Exception as exc:
                data_errors.append(f"{pair}: {exc}")
        if data_errors:
            raise LiveSafetyError(
                "market-data batch failed; no orders submitted: "
                + "; ".join(data_errors)
            )
        trust_metrics = monitor.metrics()
        print(
            f"Data trust: max data age {trust_metrics['max_quote_age_s']:.1f}s "
            f"(candles), max latency {trust_metrics['max_request_latency_s']:.1f}s, "
            f"clock skew {trust_metrics['clock_skew_s']:+.2f}s, "
            f"sequence gaps {trust_metrics['max_sequence_gap']}"
        )

        apply_atr_protection(states=states, positions=positions, store=store)
        for pair, state in states.items():
            if state.get("risk_exit"):
                print(f"{pair}: RISK EXIT — {state['risk_exit']}")

        decisions = build_decisions(
            allocations=allocations,
            states=states,
            positions=positions,
            equity=equity,
            locked_reason=risk_locked_reason,
            entries_locked_reason=entries_locked_reason,
            limits=limits,
        )
        if args.execute:
            ensure_protective_stops(
                states=states,
                positions=positions,
                broker=broker,
                lifecycle=canonical_lifecycle,
                gateway=canonical_gateway,
                store=store,
                limits=limits,
                skip_symbols={
                    decision["market_symbol"]
                    for decision in decisions
                    if decision["action"] == "SELL"
                },
                audit_log_path=args.audit_log,
            )
        if not decisions:
            print(
                "No trades to execute — positions already match the permitted targets"
            )
            return 0

        prepared = prepare_orders(
            decisions=decisions,
            broker=broker,
            account=account,
            positions=positions,
            limits=limits,
            locked_reason=locked_reason,
            store=store,
            audit_log_path=args.audit_log,
            monitor=monitor,
        )
        print("\nEXECUTION PLAN")
        for planned in prepared:
            print(
                f"  {planned['action']} {planned['qty']:.8f} {planned['market_symbol']} "
                f"≈ ${planned['notional']:,.2f} ({planned['reason']})"
            )
        if not args.execute:
            print("DRY-RUN complete — no orders submitted")
            return 0

        execute_orders(
            orders=prepared,
            broker=broker,
            lifecycle=canonical_lifecycle,
            gateway=canonical_gateway,
            store=store,
            limits=limits,
            locked_reason=locked_reason,
            audit_log_path=args.audit_log,
            reconciliation_timeout_seconds=reconciliation_timeout,
            monitor=monitor,
        )
        final_account = broker.get_account()
        final_positions = broker.get_positions()
        print(
            f"Final: equity ${float(final_account['equity']):,.2f}, "
            f"cash ${float(final_account['cash']):,.2f}, positions {len(final_positions)}"
        )
        return 0
    finally:
        with suppress(Exception):
            asyncio.run(adapter.disconnect())


def run(args: argparse.Namespace) -> int:
    # P1.2: one correlation ID per runner invocation; every audit event and
    # error raised inside the run is tagged with it.
    run_id = bind_run_correlation()
    profile = resolve_trading_profile(args)
    limits = LiveRiskLimits.for_profile(profile)
    allocations = parse_allocations(args.symbols, args.weights, limits)
    require_execution_authorization(
        execute=args.execute,
        testnet=args.testnet,
        cli_confirmation=args.confirm_live,
    )
    state_path = args.state_file or (
        "data/binance_testnet_risk_state.json"
        if args.testnet
        else "data/binance_live_risk_state.json"
    )
    with LiveExecutionLock(f"{state_path}.lock"):
        return run_locked(
            args,
            limits=limits,
            allocations=allocations,
            state_path=state_path,
            profile=profile,
            run_id=run_id,
        )


def main() -> int:
    args = build_parser().parse_args()
    append_live_audit_event(
        args.audit_log,
        "run_started",
        {
            "execute": bool(args.execute),
            "testnet": bool(args.testnet),
            "symbols": args.symbols,
        },
    )
    try:
        result = run(args)
        append_live_audit_event(args.audit_log, "run_completed", {"exit_code": result})
        return result
    except Exception as exc:
        append_live_audit_event(
            args.audit_log,
            "run_failed",
            {"error_type": type(exc).__name__, "error": str(exc)[:1000]},
        )
        print(f"FATAL: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
