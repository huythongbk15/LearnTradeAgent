#!/usr/bin/env python3
"""Close micro-dust positions on Alpaca paper trading via canonical lifecycle."""

import os
import sys
import asyncio
import threading
from datetime import UTC, datetime

sys.path.insert(0, ".")
from dotenv import load_dotenv

load_dotenv()

from alpaca.trading.client import TradingClient
from alpaca.trading.enums import OrderSide, TimeInForce

from trading_agent.execution.lifecycle import ExecutionEventStore
from trading_agent.execution.lifecycle.lifecycle import (
    ExecutionLifecycle,
    EmergencyReduceRequest,
    TrustedPrice,
)
from trading_agent.execution.canonical import BrokerGateway, AuthorizedOrder

ALPACA_MICRO_DUST_THRESHOLD_USD = 5.0


class _AlpacaSyncAdapter:
    """Synchronous wrapper around async AlpacaAdapter for canonical gateway."""

    def __init__(self, async_adapter):
        self._adapter = async_adapter
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, daemon=True)
        self._thread.start()

    def _run(self, coro):
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return future.result()

    def get_all_positions(self):
        return self._run(self._adapter.get_all_positions())

    def get_ticker(self, symbol):
        ticker = self._run(self._adapter.get_ticker(symbol))
        return {"last": getattr(ticker, "last", None) or getattr(ticker, "price", None)}

    def place_order(self, payload):
        side = OrderSide.BUY if payload["side"].lower() == "buy" else OrderSide.SELL
        from alpaca.trading.requests import OrderRequest

        order_req = OrderRequest(
            symbol=payload["symbol"],
            qty=float(payload["qty"]),
            side=side,
            type=payload.get("order_type", "market"),
            time_in_force=TimeInForce.IOC,
        )
        order = self._run(self._adapter.submit_order(order_data=order_req))
        return {"id": getattr(order, "id", None) or getattr(order, "client_order_id", None)}


def close_micro_dust_positions():
    client = TradingClient(
        os.environ["ALPACA_API_KEY"], os.environ["ALPACA_API_SECRET"], paper=True
    )

    closed = []
    skipped = []

    sync_adapter = _AlpacaSyncAdapter(client)
    store = ExecutionEventStore(":memory:").connect()
    lifecycle = ExecutionLifecycle(
        store,
        price_source=lambda s: TrustedPrice(
            price=float(sync_adapter.get_ticker(s).get("last") or 0.0),
            exchange_timestamp=datetime.now(UTC),
            received_at=datetime.now(UTC),
        )
        if sync_adapter.get_ticker(s).get("last")
        else None,
    )
    gateway = BrokerGateway(adapter=sync_adapter, store=store)

    for position in sync_adapter.get_all_positions():
        market_value = float(position.market_value)
        qty = float(position.qty)
        symbol = position.symbol

        if abs(market_value) <= ALPACA_MICRO_DUST_THRESHOLD_USD:
            try:
                ticker = sync_adapter.get_ticker(symbol)
                price = ticker.get("last")
                if not price:
                    skipped.append({"symbol": symbol, "reason": "no price"})
                    continue
                current_price = float(price)
                emergency = EmergencyReduceRequest(
                    intent_id=f"emergency-close-{symbol}-{int(datetime.now(UTC).timestamp())}",
                    symbol=symbol,
                    side="sell",
                    quantity=qty,
                    reason="micro_dust_cleanup",
                )
                auth_event = lifecycle.emergency_reduce(emergency)
                authorized = AuthorizedOrder(
                    token="__authorized__",
                    intent_id=emergency.intent_id,
                    symbol=symbol,
                    side="sell",
                    quantity=qty,
                    idempotency_key=f"emergency-{symbol}",
                    price_reference=current_price,
                    risk_decision_id=auth_event.payload.get("risk_decision_id", ""),
                    forecast_fingerprint="",
                    model_artifact_id="emergency_reduce",
                    permission_result="REDUCE_ONLY",
                    authorization_id=auth_event.payload.get("authorization_id", ""),
                    lifecycle_event_id=auth_event.event_id,
                    correlation_id=emergency.intent_id,
                    exposure_effect="reduce",
                    current_exposure=0.0,
                    resulting_exposure=0.0,
                    authorized_at=auth_event.payload.get("authorized_at", ""),
                    authorization_hash="",
                )
                result = gateway.submit(authorized, correlation_id=emergency.intent_id)
                if result.success and result.broker_order_id:
                    lifecycle.submit_order(
                        intent_id=emergency.intent_id,
                        exchange_order_id=result.broker_order_id,
                    )
                    lifecycle.receive_fill(
                        intent_id=emergency.intent_id,
                        size=qty,
                        price=current_price,
                    )
                    closed.append(
                        {
                            "symbol": symbol,
                            "qty": qty,
                            "market_value": market_value,
                        }
                    )
                else:
                    skipped.append(
                        {
                            "symbol": symbol,
                            "reason": result.error or "gateway submit failed",
                        }
                    )
            except Exception as e:
                skipped.append(
                    {
                        "symbol": symbol,
                        "reason": str(e),
                    }
                )
        else:
            skipped.append(
                {
                    "symbol": symbol,
                    "reason": f"above threshold ({market_value:.2f} USD)",
                }
            )

    print(f"Closed {len(closed)} micro-dust positions:")
    for p in closed:
        print(f"  {p['symbol']}: qty={p['qty']:.6f} value={p['market_value']:.2f} USD")

    if skipped:
        print(f"\nSkipped {len(skipped)} positions:")
        for p in skipped:
            print(f"  {p['symbol']}: {p['reason']}")

    return closed


if __name__ == "__main__":
    close_micro_dust_positions()
