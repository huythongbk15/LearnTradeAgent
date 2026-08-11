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


def _protective_order(
    *,
    symbol: Symbol,
    client_order_id: str,
    quantity: float,
    stop_price: float,
) -> Order:
    return Order(
        id="",
        client_order_id=client_order_id,
        symbol=symbol,
        side=OrderSide.SELL,
        type=OrderType.STOP,
        size=Decimal(str(quantity)),
        stop_price=Decimal(str(stop_price)),
        time_in_force=TimeInForce.GTC,
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
    store: LiveRiskStateStore,
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
        )Ôù<∂âûÀk∫wµÁeïπ—}•ê†(ÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÅÕ—»°Öç—•Ÿï}¡…Ω—ïç—•Ÿîπùï–†âç±•ïπ—}Ω…ëï…}•êà§ÅΩ»Äàà§∞(ÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÅÕÂµâΩ∞∞(ÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄ§(ÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÅï·çï¡–Å·çï¡—•Ω∏ÅÖÃÅÕ—Ω¡}…ïçΩπç•±ï}ï·åË(ÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÅ…Ö•ÕîÅ1•ŸïMÖôï—Â……Ω»†(ÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÅòâï·•–ÅÖπêÅ¡…Ω—ïç—•ŸîÅÕ—Ω¿ÅΩ’—çΩµïÃÅÖ…îÅ’π≠πΩ›∏ÅôΩ»ÅÌ¡Ö•…ÙËÄà(ÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÅòâÌÕ—Ω¡}…ïçΩπç•±ï}ï·çÙà(ÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄ§Åô…Ω¥Åï·å(ÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÅ•òÄ†(ÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÅΩ±ë}Õ—Ω¿Å•ÃÅπΩ–Å9Ωπî(ÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÅÖπêÅÕ—»°Ω±ë}Õ—Ω¿πùï–†âÕ—Ö—’Ãà§ÅΩ»Äàà§π±Ω›ï»†§ÄÙÙÄâΩ¡ï∏à(ÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄ§Ë(ÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÅ…Ö•ÕîÅ1•ŸïMÖôï—Â……Ω»†(ÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÅòâï·•–ÅΩ’—çΩµîÅ•ÃÅ’π≠πΩ›∏ÅôΩ»ÅÌΩ…ëï…}≠ïÂÙÏÅ¡…ïŸ•Ω’ÃÅ¡…Ω—ïç—•ŸîÄà(ÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄâÕ—Ω¿Å…ïµÖ•πÃÅÖç—•Ÿîà(ÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄ§Åô…Ω¥Åï·å(ÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÅÕ—Ω…îπç±ïÖ…}Öç—•Ÿï}¡…Ω—ïç—•Ÿï}Ω…ëï»°¡Ö•»§(ÄÄÄÄÄÄÄÄÄÄÄÅ…Ö•ÕîÅ1•ŸïMÖôï—Â……Ω»†(ÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÅòâΩ…ëï»ÅÕ’âµ•ÕÕ•Ω∏ÅΩ’—çΩµîÅ•ÃÅ’π≠πΩ›∏ÅôΩ»ÅÌΩ…ëï…}≠ïÂÙÏÄà(ÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄâï·ç°ÖπùîÅë•êÅπΩ–Åô•πêÅ—°îÅç±•ïπ–ÅΩ…ëï»Å%à(ÄÄÄÄÄÄÄÄÄÄÄÄ§Åô…Ω¥Åï·å((ÄÄÄÄÄÄÄÅ¡ï…Õ•Õ—}Ω…ëï…}…ïÕ’±–°Õ—Ω…î∞ÅΩ…ëï…}≠ï‰∞Å…ïÕ’±–§(ÄÄÄÄÄÄÄÅ•òÅ…ïÕ’±–πùï–†âï……Ω»à§ÅΩ»Å…ïÕ’±–πùï–†âÕ—Ö—’Ãà§ÅπΩ–Å•∏ÅÏâΩ¡ï∏à∞Äâ¡Ö…—•Ö∞à∞Äâô•±±ïêâÙË(ÄÄÄÄÄÄÄÄÄÄÄÅ…Ö•ÕîÅ1•ŸïMÖôï—Â……Ω»°òâΩ…ëï»Å…ï©ïç—ïêÅΩ»ÅôÖ•±ïêËÅÌ…ïÕ’±—Ùà§(ÄÄÄÄÄÄÄÅ•òÅ…ïÕ’±–πùï–†âÕ—Ö—’Ãà§ÄÑÙÄâô•±±ïêàË(ÄÄÄÄÄÄÄÄÄÄÄÅ•òÅÖ’ë•—}±Ωù}¡Ö—†Ë(ÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÅÖ¡¡ïπë}±•Ÿï}Ö’ë•—}ïŸïπ–†(ÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÅÖ’ë•—}±Ωù}¡Ö—†∞(ÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄâΩ…ëï…}πΩπ}—ï…µ•πÖ∞à∞(ÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÅÏ(ÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄâΩ…ëï…}≠ï‰àËÅΩ…ëï…}≠ï‰∞(ÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄâÕÂµâΩ∞àËÅ¡Ö•»∞(ÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄâÕ—Ö—’ÃàËÅÕ—»°…ïÕ’±–πùï–†âÕ—Ö—’Ãà§§∞(ÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄâô•±±ïë}≈—‰àËÅô±ΩÖ–°…ïÕ’±–πùï–†âô•±±ïë}≈—‰à§ÅΩ»Ä¿∏¿§∞(ÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÅÙ∞(ÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄ§(ÄÄÄÄÄÄÄÄÄÄÄÅ…Ö•ÕîÅ1•ŸïMÖôï—Â……Ω»†(ÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÅòâΩ…ëï»ÅÌΩ…ëï…}≠ïÂÙÅ•ÃÅÌ…ïÕ’±–πùï–†ùÕ—Ö—’Ãú•ÙÏÄà(ÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄââÖ—ç†ÅÕ—Ω¡¡ïêÅ’π—•∞Å…ïçΩπç•±•Ö—•Ω∏ÅçΩµ¡±ï—ïÃà(ÄÄÄÄÄÄÄÄÄÄÄÄ§((ÄÄÄÄÄÄÄÅ…ïô…ïÕ°ïë}¡ΩÕ•—•ΩπÃÄÙÅâ…Ω≠ï»πùï—}¡ΩÕ•—•ΩπÃ†§(ÄÄÄÄÄÄÄÅ…ïô…ïÕ°ïêÄÙÅπï·–†(ÄÄÄÄÄÄÄÄÄÄÄÄ°¡ΩÕ•—•Ω∏ÅôΩ»Å¡ΩÕ•—•Ω∏Å•∏Å…ïô…ïÕ°ïë}¡ΩÕ•—•ΩπÃÅ•òÅ¡ΩÕ•—•ΩπlâÕÂµâΩ∞âtÄÙÙÅ¡Ö•»§∞(ÄÄÄÄÄÄÄÄÄÄÄÅ9Ωπî∞(ÄÄÄÄÄÄÄÄ§(ÄÄÄÄÄÄÄÅ…ïµÖ•π•πù}≈’Öπ—•—‰ÄÙÅô±ΩÖ–°…ïô…ïÕ°ïëlâ≈—‰ât§Å•òÅ…ïô…ïÕ°ïêÅï±ÕîÄ¿∏¿(ÄÄÄÄÄÄÄÅ•òÅ…ïµÖ•π•πù}≈’Öπ—•—‰Ä¯Ä¿Ë(ÄÄÄÄÄÄÄÄÄÄÄÅÖ—»ÄÙÅô±ΩÖ–°¡±Öππïêπùï–†âÖ—»à§ÅΩ»Ä¿∏¿§(ÄÄÄÄÄÄÄÄÄÄÄÅ•òÅπΩ–ÅµÖ—†π•Õô•π•—î°Ö—»§ÅΩ»ÅÖ—»ÄÙÄ¿Ë(ÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÅ…Ö•ÕîÅ1•ŸïMÖôï—Â……Ω»†(ÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÅòâçÖππΩ–Å¡…Ω—ïç–Åô•±±ïêÅΩ…ëï»Å›•—°Ω’–ÅÑÅŸÖ±•êÅQHÅôΩ»ÅÌ¡Ö•…Ùà(ÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄ§(ÄÄÄÄÄÄÄÄÄÄÄÅô•±±}¡…•çîÄÙÅô±ΩÖ–°…ïÕ’±–πùï–†âÖŸù}ô•±±}¡…•çîà§ÅΩ»Å¡±ÖππïëlâÕ•ùπÖ±}¡…•çîât§(ÄÄÄÄÄÄÄÄÄÄÄÅ•òÅ¡±ÖππïëlâÖç—•Ω∏âtÄÙÙÄâ	UdàË(ÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÅΩâÕï…Ÿïë}°•ù†ÄÙÅµÖ‡°ô•±±}¡…•çî∞Åô±ΩÖ–°¡±ÖππïëlâÕ•ùπÖ±}¡…•çîât§§(ÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÅ|∞ÅëïÕ•…ïë}Õ—Ω¿ÄÙÅÕ—Ω…îπΩâÕï…Ÿï}¡ΩÕ•—•Ωπ}…•Õ¨†(ÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÅ¡Ö•»∞(ÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÅ≈’Öπ—•—‰ı…ïµÖ•π•πù}≈’Öπ—•—‰∞(ÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÅΩâÕï…Ÿïë}°•ù†ıΩâÕï…Ÿïë}°•ù†∞(ÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÅÖ—»ıÖ—»∞(ÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÅÖ—…}µ’±—•¡±•ï»ıQI}M1}5U1P∞(ÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄ§(ÄÄÄÄÄÄÄÄÄÄÄÅï±ÕîË(ÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÅ…•Õ≠}…ïçΩ…êÄÙÅÕ—Ω…îπÕ—Ö—îπ¡ΩÕ•—•Ωπ}…•Õ¨πùï–°¡Ö•»§ÅΩ»ÅÌÙ(ÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÅëïÕ•…ïë}Õ—Ω¿ÄÙÅô±ΩÖ–°…•Õ≠}…ïçΩ…êπùï–†â—…Ö•±•πù}Õ—Ω¿à§ÅΩ»Ä¿∏¿§(ÄÄÄÄÄÄÄÄÄÄÄÅïπÕ’…ï}¡…Ω—ïç—•Ÿï}Õ—Ω¿†(ÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÅ¡Ö•»ı¡Ö•»∞(ÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÅ≈’Öπ—•—‰ı…ïµÖ•π•πù}≈’Öπ—•—‰∞(ÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÅëïÕ•…ïë}Õ—Ω¿ıëïÕ•…ïë}Õ—Ω¿∞(ÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÅç’……ïπ—}¡…•çîıô±ΩÖ–°¡±ÖππïëlâÕ•ùπÖ±}¡…•çîât§∞(ÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÅâ…Ω≠ï»ıâ…Ω≠ï»∞(ÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÅÕ—Ω…îıÕ—Ω…î∞(ÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÅÖ’ë•—}±Ωù}¡Ö—†ıÖ’ë•—}±Ωù}¡Ö—†∞(ÄÄÄÄÄÄÄÄÄÄÄÄ§(ÄÄÄÄÄÄÄÅï±ÕîË(ÄÄÄÄÄÄÄÄÄÄÄÅÕ—Ω…îπç±ïÖ…}¡ΩÕ•—•Ωπ}…•Õ¨°¡Ö•»§(ÄÄÄÄÄÄÄÅ•òÅÖ’ë•—}±Ωù}¡Ö—†Ë(ÄÄÄÄÄÄÄÄÄÄÄÅÖ¡¡ïπë}±•Ÿï}Ö’ë•—}ïŸïπ–†(ÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÅÖ’ë•—}±Ωù}¡Ö—†∞(ÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄâΩ…ëï…}ô•±±ïêà∞(ÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÅÏ(ÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄâΩ…ëï…}≠ï‰àËÅΩ…ëï…}≠ï‰∞(ÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄâï·ç°Öπùï}Ω…ëï…}•êàËÅÕ—»°…ïÕ’±–πùï–†â•êà§ÅΩ»Äàà§∞(ÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄâÕÂµâΩ∞àËÅ¡Ö•»∞(ÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄâÕ•ëîàËÅ¡±ÖππïëlâÖç—•Ω∏ât∞(ÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄâô•±±ïë}≈—‰àËÅô±ΩÖ–°…ïÕ’±–πùï–†âô•±±ïë}≈—‰à§ÅΩ»Ä¿∏¿§∞(ÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄâÖŸï…Öùï}ô•±±}¡…•çîàËÅô±ΩÖ–°…ïÕ’±–πùï–†âÖŸù}ô•±±}¡…•çîà§ÅΩ»Ä¿∏¿§∞(ÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÅÙ∞(ÄÄÄÄÄÄÄÄÄÄÄÄ§(ÄÄÄÄÄÄÄÅ¡…•π–†(ÄÄÄÄÄÄÄÄÄÄÄÅòàÄÉärÅ=…ëï»ËÅÌ…ïÕ’±—lùÕ•ëîùuÙÅÌ…ïÕ’±—lù≈—‰ùuÙÅÌ…ïÕ’±—lùÕÂµâΩ∞ùuÙÄà(ÄÄÄÄÄÄÄÄÄÄÄÅòãäHÅÌ…ïÕ’±—lùÕ—Ö—’ÃùuÙÄ°Ì¡±Öππïëlù…ïÖÕΩ∏ùuÙ∞Å•êıÌΩ…ëï…}≠ïÂÙ§à(ÄÄÄÄÄÄÄÄ§(()ëïòÅ¡ï…Õ•Õ—}Ω…ëï…}…ïÕ’±–†(ÄÄÄÅÕ—Ω…îËÅ1•ŸïI•Õ≠M—Ö—ïM—Ω…î∞(ÄÄÄÅΩ…ëï…}≠ï‰ËÅÕ—»∞(ÄÄÄÅ…ïÕ’±–ËÅë•ç–∞(§Ä¥¯Å9ΩπîË(ÄÄÄÅÕ—Ω…îπ’¡ëÖ—ï}Ω…ëï»†(ÄÄÄÄÄÄÄÅΩ…ëï…}≠ï‰∞(ÄÄÄÄÄÄÄÅÕ—Ö—’ÃıÕ—»°…ïÕ’±–πùï–†âÕ—Ö—’Ãà§ÅΩ»Äâ’π≠πΩ›∏à§∞(ÄÄÄÄÄÄÄÅï·ç°Öπùï}Ω…ëï…}•êıÕ—»°…ïÕ’±–πùï–†â•êà§ÅΩ»Äàà§∞(ÄÄÄÄÄÄÄÅô•±±ïë}≈’Öπ—•—‰ıô±ΩÖ–°…ïÕ’±–πùï–†âô•±±ïë}≈—‰à§ÅΩ»Ä¿∏¿§∞(ÄÄÄÄÄÄÄÅÖŸï…Öùï}ô•±±}¡…•çîıô±ΩÖ–°…ïÕ’±–πùï–†âÖŸù}ô•±±}¡…•çîà§ÅΩ»Ä¿∏¿§∞(ÄÄÄÄÄÄÄÅï……Ω»ıÕ—»°…ïÕ’±–πùï–†âï……Ω»à§ÅΩ»Äàà§∞(ÄÄÄÄ§(()ëïòÅ…ïçΩπç•±ï}’πô•π•Õ°ïë}Ω…ëï…Ã†(ÄÄÄÄ®∞(ÄÄÄÅâ…Ω≠ï»ËÅ1•Ÿï	…Ω≠ï»∞(ÄÄÄÅÕ—Ω…îËÅ1•ŸïI•Õ≠M—Ö—ïM—Ω…î∞(ÄÄÄÅÖ’ë•—}±Ωù}¡Ö—†ËÅÕ—»ÅÅ9ΩπîÄÙÅ9Ωπî∞(§Ä¥¯Å9ΩπîË(ÄÄÄÄààâIïçΩπç•±îÅïŸï…‰ÅπΩ∏µ—ï…µ•πÖ∞Å•π—ïπ–ÅâïôΩ…îÅÑÅπï‹ÅâÖ—ç†ÅçÖ∏ÅâîÅ¡±Öππïê∏ààà((ÄÄÄÅ’π…ïÕΩ±ŸïêËÅ±•Õ—mÕ—…tÄÙÅmt(ÄÄÄÅôΩ»ÅΩ…ëï…}≠ï‰∞Å…ïçΩ…êÅ•∏ÅÕ—Ω…îπ’πô•π•Õ°ïë}Ω…ëï…Ã†§π•—ïµÃ†§Ë(ÄÄÄÄÄÄÄÅ¡Ö•»ÄÙÅÕ—»°…ïçΩ…êπùï–†âÕÂµâΩ∞à§ÅΩ»Äàà§(ÄÄÄÄÄÄÄÅ•òÅπΩ–Å¡Ö•»Ë(ÄÄÄÄÄÄÄÄÄÄÄÅ’π…ïÕΩ±ŸïêπÖ¡¡ïπê°òâÌΩ…ëï…}≠ïÂÙËÅµ•ÕÕ•πúÅÕÂµâΩ∞Åµï—ÖëÖ—Ñà§(ÄÄÄÄÄÄÄÄÄÄÄÅçΩπ—•π’î(ÄÄÄÄÄÄÄÅ—…‰Ë(ÄÄÄÄÄÄÄÄÄÄÄÅ…ïÕ’±–ÄÙÅâ…Ω≠ï»πùï—}Ω…ëï…}âÂ}ç±•ïπ—}•ê°Ω…ëï…}≠ï‰∞Åï·ç°Öπùï}ÕÂµâΩ∞°¡Ö•»§§(ÄÄÄÄÄÄÄÅï·çï¡–Å·çï¡—•Ω∏ÅÖÃÅï·åË(ÄÄÄÄÄÄÄÄÄÄÄÅ’π…ïÕΩ±ŸïêπÖ¡¡ïπê°òâÌΩ…ëï…}≠ïÂÙËÅ…ïçΩπç•±•Ö—•Ω∏Åï……Ω»ËÅÌï·çÙà§(ÄÄÄÄÄÄÄÄÄÄÄÅçΩπ—•π’î(ÄÄÄÄÄÄÄÅ•òÅ…ïÕ’±–Å•ÃÅ9ΩπîË(ÄÄÄÄÄÄÄÄÄÄÄÅÕ—Ω…îπ’¡ëÖ—ï}Ω…ëï»†(ÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÅΩ…ëï…}≠ï‰∞(ÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÅÕ—Ö—’ÃÙâ’π≠πΩ›∏à∞(ÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÅï……Ω»Ùâç±•ïπ–ÅΩ…ëï»Å%ÅπΩ–ÅôΩ’πêÅë’…•πúÅ…ïçΩπç•±•Ö—•Ω∏à∞(ÄÄÄÄÄÄÄÄÄÄÄÄ§(ÄÄÄÄÄÄÄÄÄÄÄÅ’π…ïÕΩ±ŸïêπÖ¡¡ïπê°òâÌΩ…ëï…}≠ïÂÙËÅç±•ïπ–ÅΩ…ëï»Å%ÅπΩ–ÅôΩ’πêà§(ÄÄÄÄÄÄÄÄÄÄÄÅçΩπ—•π’î(ÄÄÄÄÄÄÄÅ¡ï…Õ•Õ—}Ω…ëï…}…ïÕ’±–°Õ—Ω…î∞ÅΩ…ëï…}≠ï‰∞Å…ïÕ’±–§(ÄÄÄÄÄÄÄÅ•òÅÖ’ë•—}±Ωù}¡Ö—†Ë(ÄÄÄÄÄÄÄÄÄÄÄÅÖ¡¡ïπë}±•Ÿï}Ö’ë•—}ïŸïπ–†(ÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÅÖ’ë•—}±Ωù}¡Ö—†∞(ÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄâΩ…ëï…}…ïçΩπç•±ïêà∞(ÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÅÏ(ÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄâΩ…ëï…}≠ï‰àËÅΩ…ëï…}≠ï‰∞(ÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄâÕÂµâΩ∞àËÅ¡Ö•»∞(ÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄâÕ—Ö—’ÃàËÅÕ—»°…ïÕ’±–πùï–†âÕ—Ö—’Ãà§§∞(ÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄâô•±±ïë}≈—‰àËÅô±ΩÖ–°…ïÕ’±–πùï–†âô•±±ïë}≈—‰à§ÅΩ»Ä¿∏¿§∞(ÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÅÙ∞(ÄÄÄÄÄÄÄÄÄÄÄÄ§(ÄÄÄÄÄÄÄÅ•òÅ…ïÕ’±–πùï–†âÕ—Ö—’Ãà§ÄÑÙÄâô•±±ïêàË(ÄÄÄÄÄÄÄÄÄÄÄÅ’π…ïÕΩ±ŸïêπÖ¡¡ïπê°òâÌΩ…ëï…}≠ïÂÙËÅï·ç°ÖπùîÅÕ—Ö—’ÃÅÌ…ïÕ’±–πùï–†ùÕ—Ö—’Ãú•Ùà§(ÄÄÄÅ•òÅ’π…ïÕΩ±ŸïêË(ÄÄÄÄÄÄÄÅ•òÅÖ’ë•—}±Ωù}¡Ö—†Ë(ÄÄÄÄÄÄÄÄÄÄÄÅÖ¡¡ïπë}±•Ÿï}Ö’ë•—}ïŸïπ–†(ÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÅÖ’ë•—}±Ωù}¡Ö—†∞(ÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄâ…ïçΩπç•±•Ö—•Ωπ}â±Ωç≠ïêà∞(ÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÅÏâ…ïÖÕΩπÃàËÅ’π…ïÕΩ±ŸïëÙ∞(ÄÄÄÄÄÄÄÄÄÄÄÄ§(ÄÄÄÄÄÄÄÅ…Ö•ÕîÅ1•ŸïMÖôï—Â……Ω»†(ÄÄÄÄÄÄÄÄÄÄÄÄâ’πô•π•Õ°ïêÅΩ…ëï»Å…ïçΩπç•±•Ö—•Ω∏Åâ±Ωç≠ïêÅ—°îÅâÖ—ç†ËÄàÄ¨ÄàÏÄàπ©Ω•∏°’π…ïÕΩ±Ÿïê§(ÄÄÄÄÄÄÄÄ§(()ëïòÅ…’π}±Ωç≠ïê†(ÄÄÄÅÖ…ùÃËÅÖ…ù¡Ö…Õîπ9ÖµïÕ¡Öçî∞(ÄÄÄÄ®∞(ÄÄÄÅ±•µ•—ÃËÅ1•ŸïI•Õ≠1•µ•—Ã∞(ÄÄÄÅÖ±±ΩçÖ—•ΩπÃËÅ±•Õ—m—’¡±ïmÕ—»∞Åô±ΩÖ—ut∞(ÄÄÄÅÕ—Ö—ï}¡Ö—†ËÅÕ—»∞(§Ä¥¯Å•π–Ë(ÄÄÄÅ≠ïÂ}πÖµîÄÙÄâ	%99}QMQ9Q}A%}-dàÅ•òÅÖ…ùÃπ—ïÕ—πï–Åï±ÕîÄâ	%99}A%}-dà(ÄÄÄÅÕïç…ï—}πÖµîÄÙÄâ	%99}QMQ9Q}A%}MIPàÅ•òÅÖ…ùÃπ—ïÕ—πï–Åï±ÕîÄâ	%99}A%}MIPà(ÄÄÄÅÖ¡•}≠ï‰ÄÙÅΩÃπùï—ïπÿ°≠ïÂ}πÖµî∞Äàà§(ÄÄÄÅÕïç…ï–ÄÙÅΩÃπùï—ïπÿ°Õïç…ï—}πÖµî∞Äàà§(ÄÄÄÅ•òÅπΩ–ÅÖ¡•}≠ï‰ÅΩ»ÅπΩ–ÅÕïç…ï–Ë(ÄÄÄÄÄÄÄÅ…Ö•ÕîÅ1•ŸïMÖôï—Â……Ω»°òâÌ≠ïÂ}πÖµïÙÅÖπêÅÌÕïç…ï—}πÖµïÙÅÖ…îÅ…ï≈’•…ïêà§(ÄÄÄÄåÅΩπ—ï·–Åâ•πë•πúÅ¡ï…Õ•Õ—ÃÅÕ—Ö—îÅïŸï∏Åë’…•πúÅë…‰µ…’πÃ∞ÅÕºÅïŸï…‰Å…’ππï»ÅµΩëî(ÄÄÄÄåÅ…ï≈’•…ïÃÅ—°îÅ•π—ïù…•—‰Å≠ï‰∏ÅQ°•ÃÅ¡…ïŸïπ—ÃÅç…ïÖ—•πúÅÖ∏Å’πÕ•ùπïêÅâÖÕï±•πî(ÄÄÄÄåÅ—°Ö–ÅçÖππΩ–ÅâîÅÕÖôï±‰Å…ï’ÕïêÅôΩ»Å±Ö—ï»Åï·ïç’—•Ω∏∏(ÄÄÄÅ•π—ïù…•—Â}≠ï‰ÄÙÅŸÖ±•ëÖ—ï}•π—ïù…•—Â}≠ï‰°ΩÃπùï—ïπÿ†â1%Y}MQe}!5}-dà∞Äàà§§(ÄÄÄÅÕ—Ω…îÄÙÅ1•ŸïI•Õ≠M—Ö—ïM—Ω…î†(ÄÄÄÄÄÄÄÅÕ—Ö—ï}¡Ö—†∞(ÄÄÄÄÄÄÄÅ•π—ïù…•—Â}≠ï‰ı•π—ïù…•—Â}≠ï‰∞(ÄÄÄÄ§(ÄÄÄÅ•òÅÖ…ùÃπï·ïç’—îÅÖπêÅπΩ–ÅÖ…ùÃπ—ïÕ—πï–ÅÖπêÅπΩ–ÅÕ—Ω…îπï·•Õ—ïêË(ÄÄÄÄÄÄÄÅ…Ö•ÕîÅ1•ŸïMÖôï—Â……Ω»†â…’∏ÅÑÅÕ’ççïÕÕô’∞ÅµÖ•ππï–Åë…‰µ…’∏Åô•…Õ–Å—ºÅ•π•—•Ö±•ÈîÅ…•Õ¨ÅÕ—Ö—îà§(ÄÄÄÅÖ±±ΩçÖ—•Ωπ}µÖ¿ÄÙÅë•ç–°Ö±±ΩçÖ—•ΩπÃ§(ÄÄÄÅÕ—Ω…îπâ•πë}çΩπ—ï·–†(ÄÄÄÄÄÄÄÅÖççΩ’π–ıÖççΩ’π—}ô•πùï…¡…•π–†(ÄÄÄÄÄÄÄÄÄÄÄÅï·ç°ÖπùîÙââ•πÖπçîµ—ïÕ—πï–àÅ•òÅÖ…ùÃπ—ïÕ—πï–Åï±ÕîÄââ•πÖπçîµµÖ•ππï–à∞(ÄÄÄÄÄÄÄÄÄÄÄÅÖ¡•}≠ï‰ıÖ¡•}≠ï‰∞(ÄÄÄÄÄÄÄÄ§∞(ÄÄÄÄÄÄÄÅÕ—…Ö—ïù‰ıÕ—…Ö—ïùÂ}ô•πùï…¡…•π–†(ÄÄÄÄÄÄÄÄÄÄÄÅÕ—…Ö—ïù‰Ùâïπ°Öπçïë}µÑà∞(ÄÄÄÄÄÄÄÄÄÄÄÅ¡Ö…ÖµÃıMQIQe}AI5L∞(ÄÄÄÄÄÄÄÄÄÄÄÅÖ±±ΩçÖ—•ΩπÃıÖ±±ΩçÖ—•Ωπ}µÖ¿∞(ÄÄÄÄÄÄÄÄ§∞(ÄÄÄÄÄÄÄÅÕÂµâΩ±Ãı±•Õ–°Ö±±ΩçÖ—•Ωπ}µÖ¿§∞(ÄÄÄÄ§(ÄÄÄÅ•òÅÖ…ùÃπï·ïç’—îÅÖπêÅπΩ–ÅÖ…ùÃπ—ïÕ—πï–Ë(ÄÄÄÄÄÄÄÅâ’•±ë}Õ°ÑÄÙÅŸÖ±•ëÖ—ï}â’•±ë}Õ°Ñ°ΩÃπùï—ïπÿ†âQI%9}	U%1}M!à§§(ÄÄÄÄÄÄÄÅŸÖ±•ëÖ—ï}Õ—…Ö—ïùÂ}ïŸ•ëïπçî†(ÄÄÄÄÄÄÄÄÄÄÄÅÖ…ùÃπïŸ•ëïπçï}ô•±î∞(ÄÄÄÄÄÄÄÄÄÄÄÅï·¡ïç—ïë}ÕÂµâΩ±ÃımÕÂµâΩ∞ÅôΩ»ÅÕÂµâΩ∞∞Å|Å•∏ÅÖ±±ΩçÖ—•ΩπÕt∞(ÄÄÄÄÄÄÄÄÄÄÄÅï·¡ïç—ïë}¡Ö…ÖµÃıMQIQe}AI5L∞(ÄÄÄÄÄÄÄÄÄÄÄÅï·¡ïç—ïë}Ö±±ΩçÖ—•ΩπÃıÖ±±ΩçÖ—•Ωπ}µÖ¿∞(ÄÄÄÄÄÄÄÄÄÄÄÅï·¡ïç—ïë}â’•±ë}Õ°Ñıâ’•±ë}Õ°Ñ∞(ÄÄÄÄÄÄÄÄÄÄÄÅ•π—ïù…•—Â}≠ï‰ı•π—ïù…•—Â}≠ï‰∞(ÄÄÄÄÄÄÄÄ§((ÄÄÄÅ¡…•π–†àÙàÄ®Ä‡¿§(ÄÄÄÅµΩëîÄÙÄâIdµIU8àÅ•òÅπΩ–ÅÖ…ùÃπï·ïç’—îÅï±ÕîÄâQMQ9PÅaUQ%=8àÅ•òÅÖ…ùÃπ—ïÕ—πï–Åï±ÕîÄâ5%99PÅaUQ%=8à(ÄÄÄÅ¡…•π–°òâ	%99ÅMA=PÉäPÅπ°ÖπçïêÅ5ÉäPÅÌµΩëïÙà§(ÄÄÄÅ¡…•π–†(ÄÄÄÄÄÄÄÅòâ1•µ•—ÃËÅΩ…ëï»ÄëÌ±•µ•—ÃπµÖ·}Ω…ëï…}πΩ—•ΩπÖ±}’ÕêË∞∏…ôÙ∞Äà(ÄÄÄÄÄÄÄÅòâÕÂµâΩ∞ÅÌ±•µ•—ÃπµÖ·}ÕÂµâΩ±}ï·¡ΩÕ’…ï}¡ç–Ë∏¿ïÙ∞Åù…ΩÕÃÅÌ±•µ•—ÃπµÖ·}ù…ΩÕÕ}ï·¡ΩÕ’…ï}¡ç–Ë∏¿ïÙ∞Äà(ÄÄÄÄÄÄÄÅòâçÖÕ†Å…ïÕï…ŸîÅÌ±•µ•—Ãπµ•π}çÖÕ°}…ïÕï…Ÿï}¡ç–Ë∏¿ïÙà(ÄÄÄÄ§(ÄÄÄÅ¡…•π–†àÙàÄ®Ä‡¿§((ÄÄÄÅÖëÖ¡—ï»ÄÙÅaQëÖ¡—ï»†(ÄÄÄÄÄÄÄÅ·ç°ÖπùïΩπô•ú†(ÄÄÄÄÄÄÄÄÄÄÄÅ•êÙââ•πÖπçîà∞(ÄÄÄÄÄÄÄÄÄÄÄÅπÖµîÙâ	•πÖπçîà∞(ÄÄÄÄÄÄÄÄÄÄÄÅÖ¡•}≠ï‰ıÖ¡•}≠ï‰∞(ÄÄÄÄÄÄÄÄÄÄÄÅÕïç…ï–ıÕïç…ï–∞(ÄÄÄÄÄÄÄÄÄÄÄÅ—ïÕ—πï–ıÖ…ùÃπ—ïÕ—πï–∞(ÄÄÄÄÄÄÄÄÄÄÄÅïπÖâ±ï}…Ö—ï}±•µ•–ıQ…’î∞(ÄÄÄÄÄÄÄÄÄÄÄÅµÖ…≠ï—Ãım5Ö…≠ï—QÂ¡îπMA=Qt∞(ÄÄÄÄÄÄÄÄÄÄÄÅΩ¡—•ΩπÃıÏâëïôÖ’±—QÂ¡îàËÄâÕ¡Ω–âÙ∞(ÄÄÄÄÄÄÄÄ§(ÄÄÄÄ§(ÄÄÄÅÖÕÂπç•ºπ…’∏°ÖëÖ¡—ï»πçΩππïç–†§§(ÄÄÄÅ—…‰Ë(ÄÄÄÄÄÄÄÅ•òÅπΩ–ÅÖ…ùÃπ—ïÕ—πï–Ë(ÄÄÄÄÄÄÄÄÄÄÄÅ…ï≈’•…ï}ëïë•çÖ—ïë}ÖççΩ’π–°ÖëÖ¡—ï»∞ÅÖ±±ΩçÖ—•ΩπÃ§(ÄÄÄÄÄÄÄÅâ…Ω≠ï»ÄÙÅ1•Ÿï	…Ω≠ï»†(ÄÄÄÄÄÄÄÄÄÄÄÄââ•πÖπçîà∞(ÄÄÄÄÄÄÄÄÄÄÄÅÖëÖ¡—ï»∞(ÄÄÄÄÄÄÄÄÄÄÄÅ¡…•ç•πù}ÕÂµâΩ±ÃımÕÂµâΩ∞ÅôΩ»ÅÕÂµâΩ∞∞Å|Å•∏ÅÖ±±ΩçÖ—•ΩπÕt∞(ÄÄÄÄÄÄÄÄÄÄÄÅÕ—…•ç—}¡…•ç•πúıQ…’î∞(ÄÄÄÄÄÄÄÄ§(ÄÄÄÄÄÄÄÅ…ïçΩπç•±ï}’πô•π•Õ°ïë}Ω…ëï…Ã†(ÄÄÄÄÄÄÄÄÄÄÄÅâ…Ω≠ï»ıâ…Ω≠ï»∞(ÄÄÄÄÄÄÄÄÄÄÄÅÕ—Ω…îıÕ—Ω…î∞(ÄÄÄÄÄÄÄÄÄÄÄÅÖ’ë•—}±Ωù}¡Ö—†ıÖ…ùÃπÖ’ë•—}±Ωú∞(ÄÄÄÄÄÄÄÄ§(ÄÄÄÄÄÄÄÅÖççΩ’π–ÄÙÅâ…Ω≠ï»πùï—}ÖççΩ’π–†§(ÄÄÄÄÄÄÄÅ¡ΩÕ•—•ΩπÃÄÙÅâ…Ω≠ï»πùï—}¡ΩÕ•—•ΩπÃ†§(ÄÄÄÄÄÄÄÅ•òÅÖ…ùÃπï·ïç’—îË(ÄÄÄÄÄÄÄÄÄÄÄÅç±ïÖπ’¡}Ω…¡°Öπ}¡…Ω—ïç—•Ÿï}Õ—Ω¡Ã†(ÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÅµÖπÖùïë}ÕÂµâΩ±ÃımÕÂµâΩ∞ÅôΩ»ÅÕÂµâΩ∞∞Å|Å•∏ÅÖ±±ΩçÖ—•ΩπÕt∞(ÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÅ¡ΩÕ•—•ΩπÃı¡ΩÕ•—•ΩπÃ∞(ÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÅâ…Ω≠ï»ıâ…Ω≠ï»∞(ÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÅÕ—Ω…îıÕ—Ω…î∞(ÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÅÖ’ë•—}±Ωù}¡Ö—†ıÖ…ùÃπÖ’ë•—}±Ωú∞(ÄÄÄÄÄÄÄÄÄÄÄÄ§(ÄÄÄÄÄÄÄÅï≈’•—‰ÄÙÅô±ΩÖ–°ÖççΩ’π—lâï≈’•—‰ât§(ÄÄÄÄÄÄÄÅ±Ωç≠ïë}…ïÖÕΩ∏ÄÙÅÕ—Ω…îπΩâÕï…Ÿï}ï≈’•—‰°ï≈’•—‰∞Å±•µ•—Ã§(ÄÄÄÄÄÄÄÅµï—…•çÃÄÙÅÕ—Ω…îπµï—…•çÃ°ï≈’•—‰§(ÄÄÄÄÄÄÄÅ¡…•π–†(ÄÄÄÄÄÄÄÄÄÄÄÅòâççΩ’π–ËÅï≈’•—‰ÄëÌï≈’•—‰Ë∞∏…ôÙ∞ÅçÖÕ†ÄëÌô±ΩÖ–°ÖççΩ’π—lùçÖÕ†ùt§Ë∞∏…ôÙ∞Äà(ÄÄÄÄÄÄÄÄÄÄÄÅòâÅÌô±ΩÖ–°µï—…•çÕlùë…Ö›ëΩ›π}¡ç–ùt§Ë∏»ïÙ∞Äà(ÄÄÄÄÄÄÄÄÄÄÄÅòâëÖ•±‰Å±ΩÕÃÅÌô±ΩÖ–°µï—…•çÕlùëÖ•±Â}±ΩÕÕ}¡ç–ùt§Ë∏»ïÙà(ÄÄÄÄÄÄÄÄ§(ÄÄÄÄÄÄÄÅ•òÅ±Ωç≠ïë}…ïÖÕΩ∏Ë(ÄÄÄÄÄÄÄÄÄÄÄÅ¡…•π–°òãänPÅ%IU%PÅ	I-HËÅÌ±Ωç≠ïë}…ïÖÕΩπÙÏÅΩπ±‰Å…•Õ¨µ…ïë’ç•πúÅÕï±±ÃÅÖ…îÅÖ±±Ω›ïêà§((ÄÄÄÄÄÄÄÅÕ—Ö—ïÃËÅë•ç—mÕ—»∞Åë•ç—tÄÙÅÌÙ(ÄÄÄÄÄÄÄÅëÖ—Ö}ï……Ω…ÃËÅ±•Õ—mÕ—…tÄÙÅmt(ÄÄÄÄÄÄÄÅôΩ»Å¡Ö•»∞ÅÖ±±ΩçÖ—•Ω∏Å•∏ÅÖ±±ΩçÖ—•ΩπÃË(ÄÄÄÄÄÄÄÄÄÄÄÅ—…‰Ë(ÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÅÕ—Ö—îÄÙÅçΩµ¡’—ï}Õ—Ö—î°ùï—}…ïçïπ—}ëò°¡Ö•»§§(ÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÅÕ—Ö—ïÕm¡Ö•…tÄÙÅÕ—Ö—î(ÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÅ¡…•π–†(ÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÅòâÌ¡Ö•…ÙËÅÌÕ—Ö—ïlùÕ—Ö—îùuÙÅ ÄëÌÕ—Ö—ïlù¡…•çîùtË∞∏…ôÙ∞Äà(ÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÅòâ5ÅÌÕ—Ö—ïlùµÖ}ôÖÕ–ùtË∏…ôÙΩÌÕ—Ö—ïlùµÖ}Õ±Ω‹ùtË∏…ôÙ∞Äà(ÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÅòâ`ÅÌÕ—Ö—ïlùÖë‡ùtË∏≈ôÙ∞Å—Ö…ùï–ÅÌÖ±±ΩçÖ—•Ω∏Ë∏¿ïÙà(ÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄ§(ÄÄÄÄÄÄÄÄÄÄÄÅï·çï¡–Å·çï¡—•Ω∏ÅÖÃÅï·åË(ÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÅëÖ—Ö}ï……Ω…ÃπÖ¡¡ïπê°òâÌ¡Ö•…ÙËÅÌï·çÙà§(ÄÄÄÄÄÄÄÅ•òÅëÖ—Ö}ï……Ω…ÃË(ÄÄÄÄÄÄÄÄÄÄÄÅ…Ö•ÕîÅ1•ŸïMÖôï—Â……Ω»†âµÖ…≠ï–µëÖ—ÑÅâÖ—ç†ÅôÖ•±ïêÏÅπºÅΩ…ëï…ÃÅÕ’âµ•——ïêËÄàÄ¨ÄàÏÄàπ©Ω•∏°ëÖ—Ö}ï……Ω…Ã§§((ÄÄÄÄÄÄÄÅÖ¡¡±Â}Ö—…}¡…Ω—ïç—•Ω∏°Õ—Ö—ïÃıÕ—Ö—ïÃ∞Å¡ΩÕ•—•ΩπÃı¡ΩÕ•—•ΩπÃ∞ÅÕ—Ω…îıÕ—Ω…î§(ÄÄÄÄÄÄÄÅôΩ»Å¡Ö•»∞ÅÕ—Ö—îÅ•∏ÅÕ—Ö—ïÃπ•—ïµÃ†§Ë(ÄÄÄÄÄÄÄÄÄÄÄÅ•òÅÕ—Ö—îπùï–†â…•Õ≠}ï·•–à§Ë(ÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÅ¡…•π–°òâÌ¡Ö•…ÙËÅI%M,Åa%PÉäPÅÌÕ—Ö—ïlù…•Õ≠}ï·•–ùuÙà§((ÄÄÄÄÄÄÄÅëïç•Õ•ΩπÃÄÙÅâ’•±ë}ëïç•Õ•ΩπÃ†(ÄÄÄÄÄÄÄÄÄÄÄÅÖ±±ΩçÖ—•ΩπÃıÖ±±ΩçÖ—•ΩπÃ∞(ÄÄÄÄÄÄÄÄÄÄÄÅÕ—Ö—ïÃıÕ—Ö—ïÃ∞(ÄÄÄÄÄÄÄÄÄÄÄÅ¡ΩÕ•—•ΩπÃı¡ΩÕ•—•ΩπÃ∞(ÄÄÄÄÄÄÄÄÄÄÄÅï≈’•—‰ıï≈’•—‰∞(ÄÄÄÄÄÄÄÄÄÄÄÅ±Ωç≠ïë}…ïÖÕΩ∏ı±Ωç≠ïë}…ïÖÕΩ∏∞(ÄÄÄÄÄÄÄÄ§(ÄÄÄÄÄÄÄÅ•òÅÖ…ùÃπï·ïç’—îË(ÄÄÄÄÄÄÄÄÄÄÄÅïπÕ’…ï}¡…Ω—ïç—•Ÿï}Õ—Ω¡Ã†(ÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÅÕ—Ö—ïÃıÕ—Ö—ïÃ∞(ÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÅ¡ΩÕ•—•ΩπÃı¡ΩÕ•—•ΩπÃ∞(ÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÅâ…Ω≠ï»ıâ…Ω≠ï»∞(ÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÅÕ—Ω…îıÕ—Ω…î∞(ÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÅÕ≠•¡}ÕÂµâΩ±ÃıÏ(ÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÅëïç•Õ•ΩπlâµÖ…≠ï—}ÕÂµâΩ∞ât(ÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÅôΩ»Åëïç•Õ•Ω∏Å•∏Åëïç•Õ•ΩπÃ(ÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÅ•òÅëïç•Õ•ΩπlâÖç—•Ω∏âtÄÙÙÄâM10à(ÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÅÙ∞(ÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÅÖ’ë•—}±Ωù}¡Ö—†ıÖ…ùÃπÖ’ë•—}±Ωú∞(ÄÄÄÄÄÄÄÄÄÄÄÄ§(ÄÄÄÄÄÄÄÅ•òÅπΩ–Åëïç•Õ•ΩπÃË(ÄÄÄÄÄÄÄÄÄÄÄÅ¡…•π–†â9ºÅ—…ÖëïÃÅ—ºÅï·ïç’—îÉäPÅ¡ΩÕ•—•ΩπÃÅÖ±…ïÖë‰ÅµÖ—ç†Å—°îÅ¡ï…µ•——ïêÅ—Ö…ùï—Ãà§(ÄÄÄÄÄÄÄÄÄÄÄÅ…ï—’…∏Ä¿((ÄÄÄÄÄÄÄÅ¡…ï¡Ö…ïêÄÙÅ¡…ï¡Ö…ï}Ω…ëï…Ã†(ÄÄÄÄÄÄÄÄÄÄÄÅëïç•Õ•ΩπÃıëïç•Õ•ΩπÃ∞(ÄÄÄÄÄÄÄÄÄÄÄÅâ…Ω≠ï»ıâ…Ω≠ï»∞(ÄÄÄÄÄÄÄÄÄÄÄÅÖççΩ’π–ıÖççΩ’π–∞(ÄÄÄÄÄÄÄÄÄÄÄÅ¡ΩÕ•—•ΩπÃı¡ΩÕ•—•ΩπÃ∞(ÄÄÄÄÄÄÄÄÄÄÄÅ±•µ•—Ãı±•µ•—Ã∞(ÄÄÄÄÄÄÄÄÄÄÄÅ±Ωç≠ïë}…ïÖÕΩ∏ı±Ωç≠ïë}…ïÖÕΩ∏∞(ÄÄÄÄÄÄÄÄ§(ÄÄÄÄÄÄÄÅ¡…•π–†âqπaUQ%=8ÅA18à§(ÄÄÄÄÄÄÄÅôΩ»Å¡±ÖππïêÅ•∏Å¡…ï¡Ö…ïêË(ÄÄÄÄÄÄÄÄÄÄÄÅ¡…•π–†(ÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÅòàÄÅÌ¡±ÖππïëlùÖç—•Ω∏ùuÙÅÌ¡±Öππïëlù≈—‰ùtË∏·ôÙÅÌ¡±ÖππïëlùµÖ…≠ï—}ÕÂµâΩ∞ùuÙÄà(ÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÅòãä& ÄëÌ¡±ÖππïëlùπΩ—•ΩπÖ∞ùtË∞∏…ôÙÄ°Ì¡±Öππïëlù…ïÖÕΩ∏ùuÙ§à(ÄÄÄÄÄÄÄÄÄÄÄÄ§(ÄÄÄÄÄÄÄÅ•òÅπΩ–ÅÖ…ùÃπï·ïç’—îË(ÄÄÄÄÄÄÄÄÄÄÄÅ¡…•π–†âIdµIU8ÅçΩµ¡±ï—îÉäPÅπºÅΩ…ëï…ÃÅÕ’âµ•——ïêà§(ÄÄÄÄÄÄÄÄÄÄÄÅ…ï—’…∏Ä¿((ÄÄÄÄÄÄÄÅï·ïç’—ï}Ω…ëï…Ã†(ÄÄÄÄÄÄÄÄÄÄÄÅΩ…ëï…Ãı¡…ï¡Ö…ïê∞(ÄÄÄÄÄÄÄÄÄÄÄÅâ…Ω≠ï»ıâ…Ω≠ï»∞(ÄÄÄÄÄÄÄÄÄÄÄÅÕ—Ω…îıÕ—Ω…î∞(ÄÄÄÄÄÄÄÄÄÄÄÅ±•µ•—Ãı±•µ•—Ã∞(ÄÄÄÄÄÄÄÄÄÄÄÅÖ’ë•—}±Ωù}¡Ö—†ıÖ…ùÃπÖ’ë•—}±Ωú∞(ÄÄÄÄÄÄÄÄ§(ÄÄÄÄÄÄÄÅô•πÖ±}ÖççΩ’π–ÄÙÅâ…Ω≠ï»πùï—}ÖççΩ’π–†§(ÄÄÄÄÄÄÄÅô•πÖ±}¡ΩÕ•—•ΩπÃÄÙÅâ…Ω≠ï»πùï—}¡ΩÕ•—•ΩπÃ†§(ÄÄÄÄÄÄÄÅ¡…•π–†(ÄÄÄÄÄÄÄÄÄÄÄÅòâ•πÖ∞ËÅï≈’•—‰ÄëÌô±ΩÖ–°ô•πÖ±}ÖççΩ’π—lùï≈’•—‰ùt§Ë∞∏…ôÙ∞Äà(ÄÄÄÄÄÄÄÄÄÄÄÅòâçÖÕ†ÄëÌô±ΩÖ–°ô•πÖ±}ÖççΩ’π—lùçÖÕ†ùt§Ë∞∏…ôÙ∞Å¡ΩÕ•—•ΩπÃÅÌ±ï∏°ô•πÖ±}¡ΩÕ•—•ΩπÃ•Ùà(ÄÄÄÄÄÄÄÄ§(ÄÄÄÄÄÄÄÅ…ï—’…∏Ä¿(ÄÄÄÅô•πÖ±±‰Ë(ÄÄÄÄÄÄÄÅ›•—†ÅÕ’¡¡…ïÕÃ°·çï¡—•Ω∏§Ë(ÄÄÄÄÄÄÄÄÄÄÄÅÖÕÂπç•ºπ…’∏°ÖëÖ¡—ï»πë•ÕçΩππïç–†§§(()ëïòÅ…’∏°Ö…ùÃËÅÖ…ù¡Ö…Õîπ9ÖµïÕ¡Öçî§Ä¥¯Å•π–Ë(ÄÄÄÅ±•µ•—ÃÄÙÅ1•ŸïI•Õ≠1•µ•—Ãπô…Ωµ}ïπÿ†§(ÄÄÄÅÖ±±ΩçÖ—•ΩπÃÄÙÅ¡Ö…Õï}Ö±±ΩçÖ—•ΩπÃ°Ö…ùÃπÕÂµâΩ±Ã∞ÅÖ…ùÃπ›ï•ù°—Ã∞Å±•µ•—Ã§(ÄÄÄÅ…ï≈’•…ï}ï·ïç’—•Ωπ}Ö’—°Ω…•ÈÖ—•Ω∏†(ÄÄÄÄÄÄÄÅï·ïç’—îıÖ…ùÃπï·ïç’—î∞(ÄÄÄÄÄÄÄÅ—ïÕ—πï–ıÖ…ùÃπ—ïÕ—πï–∞(ÄÄÄÄÄÄÄÅç±•}çΩπô•…µÖ—•Ω∏ıÖ…ùÃπçΩπô•…µ}±•Ÿî∞(ÄÄÄÄ§(ÄÄÄÅÕ—Ö—ï}¡Ö—†ÄÙÅÖ…ùÃπÕ—Ö—ï}ô•±îÅΩ»Ä†(ÄÄÄÄÄÄÄÄâëÖ—ÑΩâ•πÖπçï}—ïÕ—πï—}…•Õ≠}Õ—Ö—îπ©ÕΩ∏à(ÄÄÄÄÄÄÄÅ•òÅÖ…ùÃπ—ïÕ—πï–(ÄÄÄÄÄÄÄÅï±ÕîÄâëÖ—ÑΩâ•πÖπçï}±•Ÿï}…•Õ≠}Õ—Ö—îπ©ÕΩ∏à(ÄÄÄÄ§(ÄÄÄÅ›•—†Å1•Ÿï·ïç’—•Ωπ1Ωç¨°òâÌÕ—Ö—ï}¡Ö—°Ùπ±Ωç¨à§Ë(ÄÄÄÄÄÄÄÅ…ï—’…∏Å…’π}±Ωç≠ïê†(ÄÄÄÄÄÄÄÄÄÄÄÅÖ…ùÃ∞(ÄÄÄÄÄÄÄÄÄÄÄÅ±•µ•—Ãı±•µ•—Ã∞(ÄÄÄÄÄÄÄÄÄÄÄÅÖ±±ΩçÖ—•ΩπÃıÖ±±ΩçÖ—•ΩπÃ∞(ÄÄÄÄÄÄÄÄÄÄÄÅÕ—Ö—ï}¡Ö—†ıÕ—Ö—ï}¡Ö—†∞(ÄÄÄÄÄÄÄÄ§(()ëïòÅµÖ•∏†§Ä¥¯Å•π–Ë(ÄÄÄÅÖ…ùÃÄÙÅâ’•±ë}¡Ö…Õï»†§π¡Ö…Õï}Ö…ùÃ†§(ÄÄÄÅÖ¡¡ïπë}±•Ÿï}Ö’ë•—}ïŸïπ–†(ÄÄÄÄÄÄÄÅÖ…ùÃπÖ’ë•—}±Ωú∞(ÄÄÄÄÄÄÄÄâ…’π}Õ—Ö…—ïêà∞(ÄÄÄÄÄÄÄÅÏ(ÄÄÄÄÄÄÄÄÄÄÄÄâï·ïç’—îàËÅâΩΩ∞°Ö…ùÃπï·ïç’—î§∞(ÄÄÄÄÄÄÄÄÄÄÄÄâ—ïÕ—πï–àËÅâΩΩ∞°Ö…ùÃπ—ïÕ—πï–§∞(ÄÄÄÄÄÄÄÄÄÄÄÄâÕÂµâΩ±ÃàËÅÖ…ùÃπÕÂµâΩ±Ã∞(ÄÄÄÄÄÄÄÅÙ∞(ÄÄÄÄ§(ÄÄÄÅ—…‰Ë(ÄÄÄÄÄÄÄÅ…ïÕ’±–ÄÙÅ…’∏°Ö…ùÃ§(ÄÄÄÄÄÄÄÅÖ¡¡ïπë}±•Ÿï}Ö’ë•—}ïŸïπ–°Ö…ùÃπÖ’ë•—}±Ωú∞Äâ…’π}çΩµ¡±ï—ïêà∞ÅÏâï·•—}çΩëîàËÅ…ïÕ’±—Ù§(ÄÄÄÄÄÄÄÅ…ï—’…∏Å…ïÕ’±–(ÄÄÄÅï·çï¡–Å·çï¡—•Ω∏ÅÖÃÅï·åË(ÄÄÄÄÄÄÄÅÖ¡¡ïπë}±•Ÿï}Ö’ë•—}ïŸïπ–†(ÄÄÄÄÄÄÄÄÄÄÄÅÖ…ùÃπÖ’ë•—}±Ωú∞(ÄÄÄÄÄÄÄÄÄÄÄÄâ…’π}ôÖ•±ïêà∞(ÄÄÄÄÄÄÄÄÄÄÄÅÏâï……Ω…}—Â¡îàËÅ—Â¡î°ï·å§π}}πÖµï}|∞Äâï……Ω»àËÅÕ—»°ï·å•lËƒ¿¿¡uÙ∞(ÄÄÄÄÄÄÄÄ§(ÄÄÄÄÄÄÄÅ¡…•π–°òâQ0ËÅÌï·çÙà∞Åô•±îıÕÂÃπÕ—ëï…»§(ÄÄÄÄÄÄÄÅ…ï—’…∏Äƒ(()•òÅ}}πÖµï}|ÄÙÙÄâ}}µÖ•π}|àË(ÄÄÄÅ…Ö•ÕîÅMÂÕ—ïµ·•–°µÖ•∏†§§