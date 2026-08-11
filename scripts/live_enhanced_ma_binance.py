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
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal

sys.path.insert(0, "src")

import ccxt
import polars as pl
from dotenv import load_dotenv

from live_config import LOOKBACK, STRATEGY_PARAMS
from trading_agent.execution.live_safety import (
    LIVE_CONFIRMATION,
    LiveRiskLimits,
    LiveRiskStateStore,
    LiveSafetyError,
    make_order_key,
    require_execution_authorization,
    validate_fresh_quote,
    validate_order_risk,
    validate_strategy_evidence,
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
    }
    if any(not math.isfinite(value) or value <= 0 for value in numeric_values.values()):
        raise LiveSafetyError("latest strategy indicators are invalid")
    recent = signals[-24:]
    return {
        "state": "LONG" if in_position else "FLAT",
        **numeric_values,
        "trend_up": bool(last["trend_up"][0]),
        "candle_timestamp": last["timestamp"][0],
        "n_buy_24h": int((recent == 1).sum()),
        "n_sell_24h": int((recent == -1).sum()),
    }


def get_recent_df(symbol: str) -> pl.DataFrame:
    """Fetch only fully closed 1h Binance candles."""

    public_exchange = ccxt.binance({"enableRateLimit": True})
    bars = public_exchange.fetch_ohlcv(symbol, "1h", limit=LOOKBACK)
    now_ms = int(datetime.now(UTC).timestamp() * 1000)
    closed = [bar for bar in bars if int(bar[0]) + 3_600_000 <= now_ms]
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

        if locked_reason and current_qty > 0:
            action, qty, reason = "SELL", current_qty, "RISK_CIRCUIT_BREAKER"
        elif locked_reason:
            continue
        elif state["state"] == "LONG" and delta > deadband:
            action, qty, reason = "BUY", delta / state["price"], "REBALANCE"
        elif state["state"] == "LONG" and delta < -deadband:
            action, qty, reason = "SELL", min(abs(delta) / state["price"], current_qty), "REBALANCE"
        elif state["state"] == "FLAT" and current_qty > 0:
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


def prepare_orders(
    *,
    decisions: list[dict],
    broker: LiveBroker,
    account: dict,
    positions: list[dict],
    limits: LiveRiskLimits,
    locked_reason: str | None,
    testnet: bool,
) -> list[dict]:
    """Preflight the complete batch before any order can be submitted."""

    simulated_cash = float(account["cash"])
    equity = float(account["equity"])
    simulated_positions, simulated_gross = position_snapshot(positions)
    # Testnet tickers update slowly (thin liquidity), so relax quote freshness
    # for testnet only — mainnet keeps strict 15s / 1% limits.
    quote_limits = (
        replace(
            limits,
            max_price_deviation_pct=max(limits.max_price_deviation_pct, 0.25),
            max_quote_age_seconds=max(limits.max_quote_age_seconds, 60.0),
        )
        if testnet
        else limits
    )
    prepared: list[dict] = []

    for decision in decisions:
        pair = decision["market_symbol"]
        symbol = exchange_symbol(pair)
        quote = broker.get_ticker(symbol)
        quote_price = (
            quote.get("ask") if decision["action"] == "BUY" else quote.get("bid")
        ) or quote.get("last")
        if quote_price is None:
            raise LiveSafetyError(f"no executable quote for {pair}")
        validate_fresh_quote(
            signal_price=decision["signal_price"],
            quote_price=float(quote_price),
            quote_timestamp=quote["timestamp"],
            limits=quote_limits,
        )

        existing = simulated_positions.get(pair)
        current_notional = float(existing["market_value"]) if existing else 0.0
        notional = float(decision["qty"]) * float(quote_price)
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
        prepared.append({**decision, "quote_price": float(quote_price), "notional": notional})
    return prepared


def execute_orders(
    *,
    orders: list[dict],
    broker: LiveBroker,
    store: LiveRiskStateStore,
    limits: LiveRiskLimits,
    testnet: bool,
) -> None:
    # Testnet tickers update slowly (thin liquidity), so relax quote freshness
    # for testnet only — mainnet keeps strict 15s / 1% limits.
    quote_limits = (
        replace(
            limits,
            max_price_deviation_pct=max(limits.max_price_deviation_pct, 0.25),
            max_quote_age_seconds=max(limits.max_quote_age_seconds, 60.0),
        )
        if testnet
        else limits
    )
    for planned in orders:
        account = broker.get_account()
        positions = broker.get_positions()
        position_map, gross = position_snapshot(positions)
        pair = planned["market_symbol"]
        symbol = exchange_symbol(pair)
        quote = broker.get_ticker(symbol)
        quote_price = (
            quote.get("ask") if planned["action"] == "BUY" else quote.get("bid")
        ) or quote.get("last")
        if quote_price is None:
            raise LiveSafetyError(f"no executable quote for {pair}")
        validate_fresh_quote(
            signal_price=planned["signal_price"],
            quote_price=float(quote_price),
            quote_timestamp=quote["timestamp"],
            limits=quote_limits,
        )
        current = position_map.get(pair)
        current_notional = float(current["market_value"]) if current else 0.0
        notional = float(planned["qty"]) * float(quote_price)
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
        store.reserve_order(order_key)
        order = Order(
            id="",
            client_order_id=order_key,
            symbol=symbol,
            side=OrderSide.BUY if planned["action"] == "BUY" else OrderSide.SELL,
            type=OrderType.MARKET,
            size=Decimal(str(round(float(planned["qty"]), 8))),
            time_in_force=TimeInForce.GTC,
        )
        result = broker.place_order(order)
        if result.get("error") or result.get("status") not in {"open", "partial", "filled"}:
            raise LiveSafetyError(f"order rejected or failed: {result}")
        print(
            f"  ✅ Order: {result['side']} {result['qty']} {result['symbol']} "
            f"→ {result['status']} ({planned['reason']}, id={order_key})"
        )


def run(args: argparse.Namespace) -> int:
    limits = LiveRiskLimits.from_env()
    allocations = parse_allocations(args.symbols, args.weights, limits)
    require_execution_authorization(
        execute=args.execute,
        testnet=args.testnet,
        cli_confirmation=args.confirm_live,
    )

    default_state = (
        "data/binance_testnet_risk_state.json"
        if args.testnet
        else "data/binance_live_risk_state.json"
    )
    store = LiveRiskStateStore(args.state_file or default_state)
    if args.execute and not args.testnet and not store.existed:
        raise LiveSafetyError("run a successful mainnet dry-run first to initialize risk state")
    if args.execute and not args.testnet:
        validate_strategy_evidence(
            args.evidence_file,
            expected_symbols=[symbol for symbol, _ in allocations],
            expected_params=STRATEGY_PARAMS,
        )

    key_name = "BINANCE_TESTNET_API_KEY" if args.testnet else "BINANCE_API_KEY"
    secret_name = "BINANCE_TESTNET_API_SECRET" if args.testnet else "BINANCE_API_SECRET"
    api_key = os.getenv(key_name, "")
    secret = os.getenv(secret_name, "")
    if not api_key or not secret:
        raise LiveSafetyError(f"{key_name} and {secret_name} are required")

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
            testnet=args.testnet,
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
            testnet=args.testnet,
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


def main() -> int:
    try:
        return run(build_parser().parse_args())
    except Exception as exc:
        print(f"FATAL: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
