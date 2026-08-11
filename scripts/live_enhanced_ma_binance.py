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
import sys
from contextlib import suppress
from datetime import UTC, datetime
from decimal import Decimal

sys.path.insert(0, "src")

import ccxt
import polars as pl
from dotenv import load_dotenv

from live_config import ATR_SL_MULT, ATR_SL_WINDOW, LOOKBACK, STRATEGY_PARAMS
from trading_agent.execution.live_safety import (
    LIVE_CONFIRMATION,
    LiveExecutionLock,
    LiveRiskLimits,
    LiveRiskStateStore,
    LiveSafetyError,
    account_fingerprint,
    append_live_audit_event,
    make_order_key,
    require_execution_authorization,
    strategy_fingerprint,
    validate_build_sha,
    validate_fresh_quote,
    validate_order_book_depth,
    validate_order_risk,
    validate_spread,
    validate_strategy_evidence,
    validate_integrity_key,
)
from trading_agent.exchanges.ccxt_adapter import CCXTAdapter, ExchangeConfig
from trading_agent.exchanges.live_broker import LiveBroker
from trading_agent.exchanges.models import (
    AssetClass,
    MarketType,
    Order,
    OrderSide,
    OrderType,
    Symbol,
    TimeInForce,
)
from trading_agent.strategies.enhanced_ma import EnhancedMaCrossover


load_dotenv(".env")

HOUR_MS = 3_600_000
MAX_CLOSED_CANDLE_LAG_SECONDS = 5_400


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true", help="Submit orders (default: dry-run)")
    parser.add_argument("--testnet", action="store_true", help="Use Binance Spot Testnet")
    parser.add_argument(
        "--confirm-live",
        default=None,
        help=f"Mainnet only: must equal {LIVE_CONFIRMATION}",
    )
    parser.add_argument(
        "--symbols",
        default="BTC/USDT,SOL/USDT,AVAX/USDT",
        help="Comma-separated Binance Spot symbols",
    )
    parser.add_argument(
        "--weights",
        default="20,15,15",
        help="Percent of equity per symbol; values are not normalized",
    )
    parser.add_argument("--state-file", default=None, help="Override persistent live-risk state path")
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


def parse_allocations(
    symbols_raw: str,
    weights_raw: str,
    limits: LiveRiskLimits,
) -> list[tuple[str, float]]:
    symbols = [value.strip().upper() for value in symbols_raw.split(",") if value.strip()]
    weight_parts = [value.strip() for value in weights_raw.split(",") if value.strip()]
    if not symbols or len(symbols) != len(weight_parts):
        raise LiveSafetyError("--symbols and --weights must contain the same non-zero count")
    if len(set(symbols)) != len(symbols):
        raise LiveSafetyError("duplicate symbols are not allowed")

    allocations: list[tuple[str, float]] = []
    for symbol, raw_weight in zip(symbols, weight_parts, strict=True):
        parts = symbol.split("/")
        if len(parts) != 2 or not all(parts) or parts[1] != "USDT":
            raise LiveSafetyError(f"only BASE/USDT spot symbols are supported: {symbol}")
        try:
            allocation = float(raw_weight) / 100
        except ValueError as exc:
            raise LiveSafetyError(f"invalid allocation for {symbol}: {raw_weight}") from exc
        if not math.isfinite(allocation) or allocation <= 0:
            raise LiveSafetyError(f"allocation for {symbol} must be positive and finite")
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
            raise LiveSafetyError(f"non-numeric OHLCV bar {index} for {symbol}") from exc
        if timestamp + HOUR_MS > now_ms:
            continue
        open_price, high, low, close, volume = values
        prices = (open_price, high, low, close)
        if any(not math.isfinite(value) or value <= 0 for value in prices):
            raise LiveSafetyError(f"invalid OHLC price in bar {index} for {symbol}")
        if not math.isfinite(volume) or volume < 0:
            raise LiveSafetyError(f"invalid volume in bar {index} for {symbol}")
        if high < max(open_price, low, close) or low > min(open_price, high, close):
            raise LiveSafetyError(f"inconsistent OHLC range in bar {index} for {symbol}")
        closed.append((timestamp, *values))

    timestamps = [bar[0] for bar in closed]
    if timestamps != sorted(timestamps) or len(timestamps) != len(set(timestamps)):
        raise LiveSafetyError(f"duplicate or unordered hourly candles for {symbol}")
    if any(current_ts - previous_ts != HOUR_MS for previous_ts, current_ts in zip(
        timestamps,
        timestamps[1:],
        strict=False,
    )):
        raise LiveSafetyError(f"hourly candle gap detected for {symbol}")
    if closed:
        lag_seconds = (now_ms - (closed[-1][0] + HOUR_MS)) / 1_000
        if lag_seconds > MAX_CLOSED_CANDLE_LAG_SECONDS:
            raise LiveSafetyError(
                f"latest closed candle for {symbol} is stale by {lag_seconds:.0f}s"
            )
    return closed


def get_recent_df(symbol: str) -> pl.DataFrame:
    """Fetch and validate only fully closed 1h Binance candles."""

    public_exchange = ccxt.binance({"enableRateLimit": True})
    bars = public_exchange.fetch_ohlcv(symbol, "1h", limit=LOOKBACK)
    closed = validate_live_hourly_bars(bars, symbol=symbol)
    minimum = STRATEGY_PARAMS["slow_period"] + 50
    if len(closed) < minimum:
        raise LiveSafetyError(
            f"insufficient closed candles for {symbol}: {len(closed)} < {minimum}"
        )
    return pl.DataFrame(
        {
            "timestamp": [datetime.fromtimestamp(bar[0] / 1000, tz=UTC) for bar in closed],
            "open": [bar[1] for bar in closed],
            "high": [bar[2] for bar in closed],
            "low": [bar[3] for bar in closed],
            "close": [bar[4] for bar in closed],
            "volume": [bar[5] for bar in closed],
        }
    ).sort("timestamp")


def require_dedicated_account(adapter: CCXTAdapter, allocations: list[tuple[str, float]]) -> None:
    """Refuse mainnet accounts containing assets outside the managed universe."""

    balances = asyncio.run(adapter.fetch_balance())
    crypto = balances.get(AssetClass.CRYPTO)
    allowed = {"USDT", *(symbol.split("/")[0] for symbol, _ in allocations)}
    unmanaged = []
    for asset, amounts in (crypto.assets.items() if crypto else []):
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
        elif desired_long and delta > deadband:
            action, qty, reason = "BUY", delta / state["price"], "REBALANCE"
        elif desired_long and delta < -deadband:
            action, qty, reason = "SELL", min(abs(delta) / state["price"], current_qty), "REBALANCE"
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


def protected_execution_quote(
    *,
    broker: LiveBroker,
    symbol: Symbol,
    side: str,
    requested_quantity: float,
    signal_price: float,
    limits: LiveRiskLimits,
) -> tuple[float, float]:
    """Return exchange-normalized quantity and depth-aware expected fill price."""

    ticker = broker.get_ticker(symbol)
    bid = ticker.get("bid")
    ask = ticker.get("ask")
    if bid is None or ask is None:
        raise LiveSafetyError(f"two-sided executable quote is missing for {symbol.pair}")
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
) -> list[dict]:
    """Preflight the complete batch before any order can be submitted."""

    simulated_cash = float(account["cash"])
    equity = float(account["equity"])
    simulated_positions, simulated_gross = position_snapshot(positions)
    prepared: list[dict] = []

    for decision in decisions:
        pair = decision["market_symbol"]
        symbol = exchange_symbol(pair)
        quantity, quote_price = protected_execution_quote(
            broker=broker,
            symbol=symbol,
            side=decision["action"],
            requested_quantity=float(decision["qty"]),
            signal_price=decision["signal_price"],
            limits=limits,
        )

        existing = simulated_positions.get(pair)
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
            simulated_positions[pair] = {"market_value": max(0.0, current_notional - notional)}
        prepared.append({
            **decision,
            "qty": quantity,
            "quote_price": float(quote_price),
            "notional": notional,
        })
    return prepared


def execute_orders(
    *,
    orders: list[dict],
    broker: LiveBroker,
    store: LiveRiskStateStore,
    limits: LiveRiskLimits,
    audit_log_path: str | None = None,
) -> None:
    for planned in orders:
        account = broker.get_account()
        positions = broker.get_positions()
        position_map, gross = position_snapshot(positions)
        pair = planned["market_symbol"]
        symbol = exchange_symbol(pair)
        quantity, quote_price = protected_execution_quote(
            broker=broker,
            symbol=symbol,
            side=planned["action"],
            requested_quantity=float(planned["qty"]),
            signal_price=planned["signal_price"],
            limits=limits,
        )
        current = position_map.get(pair)
        current_notional = float(current["market_value"]) if current else 0.0
        notional = quantity * float(quote_price)
        validate_order_risk(
            side=planned["action"],
            notional_usd=notional,
            equity=float(account["equity"]),
            cash=float(account["cash"]),
            current_symbol_notional=current_notional,
            gross_exposure=gross,
            limits=limits,
            locked_reason=store.state.locked_reason,
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
        order = Order(
            id="",
            client_order_id=order_key,
            symbol=symbol,
            side=OrderSide.BUY if planned["action"] == "BUY" else OrderSide.SELL,
            type=OrderType.MARKET,
            size=Decimal(str(quantity)),
            time_in_force=TimeInForce.GTC,
        )
        try:
            result = broker.place_order(order)
        except Exception as exc:
            store.update_order(order_key, status="unknown", error=str(exc))
            if audit_log_path:
                append_live_audit_event(
                    audit_log_path,
                    "order_submission_unknown",
                    {"order_key": order_key, "symbol": pair, "error": str(exc)[:500]},
                )
            try:
                reconciled = broker.get_order_by_client_id(order_key, symbol)
            except Exception as reconcile_exc:
                raise LiveSafetyError(
                    f"order submission outcome is unknown for {order_key}; "
                    f"reconciliation failed: {reconcile_exc}"
                ) from exc
            if reconciled is not None:
                persist_order_result(store, order_key, reconciled)
                raise LiveSafetyError(
                    f"order submission raised but exchange reports "
                    f"{reconciled['status']} for {order_key}; batch stopped"
                ) from exc
            raise LiveSafetyError(
                f"order submission outcome is unknown for {order_key}; "
                "exchange did not find the client order ID"
            ) from exc

        persist_order_result(store, order_key, result)
        if result.get("error") or result.get("status") not in {"open", "partial", "filled"}:
            raise LiveSafetyError(f"order rejected or failed: {result}")
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
                },
            )
        print(
            f"  ✅ Order: {result['side']} {result['qty']} {result['symbol']} "
            f"→ {result['status']} ({planned['reason']}, id={order_key})"
        )


def persist_order_result(
    store: LiveRiskStateStore,
    order_key: str,
    result: dict,
) -> None:
    store.update_order(
        order_key,
        status=str(result.get("status") or "unknown"),
        exchange_order_id=str(result.get("id") or ""),
        filled_quantity=float(result.get("filled_qty") or 0.0),
        average_fill_price=float(result.get("avg_fill_price") or 0.0),
        error=str(result.get("error") or ""),
    )


def reconcile_unfinished_orders(
    *,
    broker: LiveBroker,
    store: LiveRiskStateStore,
    audit_log_path: str | None = None,
) -> None:
    """Reconcile every non-terminal intent before a new batch can be planned."""

    unresolved: list[str] = []
    for order_key, record in store.unfinished_orders().items():
        pair = str(record.get("symbol") or "")
        if not pair:
            unresolved.append(f"{order_key}: missing symbol metadata")
            continue
        try:
            result = broker.get_order_by_client_id(order_key, exchange_symbol(pair))
        except Exception as exc:
            unresolved.append(f"{order_key}: reconciliation error: {exc}")
            continue
        if result is None:
            store.update_order(
                order_key,
                status="unknown",
                error="client order ID not found during reconciliation",
            )
            unresolved.append(f"{order_key}: client order ID not found")
            continue
        persist_order_result(store, order_key, result)
        if audit_log_path:
            append_live_audit_event(
                audit_log_path,
                "order_reconciled",
                {
                    "order_key": order_key,
                    "symbol": pair,
                    "status": str(result.get("status")),
                    "filled_qty": float(result.get("filled_qty") or 0.0),
                },
            )
        if result.get("status") != "filled":
            unresolved.append(f"{order_key}: exchange status {result.get('status')}")
    if unresolved:
        if audit_log_path:
            append_live_audit_event(
                audit_log_path,
                "reconciliation_blocked",
                {"reasons": unresolved},
            )
        raise LiveSafetyError(
            "unfinished order reconciliation blocked the batch: " + "; ".join(unresolved)
        )


def run_locked(
    args: argparse.Namespace,
    *,
    limits: LiveRiskLimits,
    allocations: list[tuple[str, float]],
    state_path: str,
) -> int:
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
        raise LiveSafetyError("run a successful mainnet dry-run first to initialize risk state")
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
    mode = "DRY-RUN" if not args.execute else "TESTNET EXECUTION" if args.testnet else "MAINNET EXECUTION"
    print(f"BINANCE SPOT — Enhanced MA — {mode}")
    print(
        f"Limits: order ${limits.max_order_notional_usd:,.2f}, "
        f"symbol {limits.max_symbol_exposure_pct:.0%}, gross {limits.max_gross_exposure_pct:.0%}, "
        f"cash reserve {limits.min_cash_reserve_pct:.0%}"
    )
    print("=" * 80)

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
        reconcile_unfinished_orders(
            broker=broker,
            store=store,
            audit_log_path=args.audit_log,
        )
        account = broker.get_account()
        positions = broker.get_positions()
        equity = float(account["equity"])
        locked_reason = store.observe_equity(equity, limits)
        metrics = store.metrics(equity)
        print(
            f"Account: equity ${equity:,.2f}, cash ${float(account['cash']):,.2f}, "
            f"DD {float(metrics['drawdown_pct']):.2%}, "
            f"daily loss {float(metrics['daily_loss_pct']):.2%}"
        )
        if locked_reason:
            print(f"⛔ CIRCUIT BREAKER: {locked_reason}; only risk-reducing sells are allowed")

        states: dict[str, dict] = {}
        data_errors: list[str] = []
        for pair, allocation in allocations:
            try:
                state = compute_state(get_recent_df(pair))
                states[pair] = state
                print(
                    f"{pair}: {state['state']} @ ${state['price']:,.2f}, "
                    f"MA {state['ma_fast']:.2f}/{state['ma_slow']:.2f}, "
                    f"ADX {state['adx']:.1f}, target {allocation:.0%}"
                )
            except Exception as exc:
                data_errors.append(f"{pair}: {exc}")
        if data_errors:
            raise LiveSafetyError("market-data batch failed; no orders submitted: " + "; ".join(data_errors))

        apply_atr_protection(states=states, positions=positions, store=store)
        for pair, state in states.items():
            if state.get("risk_exit"):
                print(f"{pair}: RISK EXIT — {state['risk_exit']}")

        decisions = build_decisions(
            allocations=allocations,
            states=states,
            positions=positions,
            equity=equity,
            locked_reason=locked_reason,
        )
        if not decisions:
            print("No trades to execute — positions already match the permitted targets")
            return 0

        prepared = prepare_orders(
            decisions=decisions,
            broker=broker,
            account=account,
            positions=positions,
            limits=limits,
            locked_reason=locked_reason,
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
            store=store,
            limits=limits,
            audit_log_path=args.audit_log,
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
    limits = LiveRiskLimits.from_env()
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
