#!/usr/bin/env python3
"""Close micro-dust positions on Alpaca paper trading via canonical lifecycle."""

import os
import sys
import asyncio
import math
import threading
import uuid
from datetime import UTC, datetime

sys.path.insert(0, ".")
from dotenv import load_dotenv

load_dotenv()

from trading_agent.exchanges.alpaca_adapter import AlpacaAdapter, AlpacaConfig
from trading_agent.exchanges.models import AssetClass, MarketType, Symbol
from trading_agent.execution.lifecycle import ExecutionEventStore
from trading_agent.execution.lifecycle.lifecycle import (
    ExecutionLifecycle,
    EmergencyReduceRequest,
    PortfolioRiskSnapshot,
    TrustedPrice,
)
from trading_agent.execution.canonical import BrokerGateway
from trading_agent.execution.canonical.adapters import (
    AlpacaExecutionAdapter,
    BrokerSubmitState,
)
from trading_agent.execution.application import CanonicalExecutionService

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
        return self._run(self._adapter.fetch_positions())

    def get_ticker(self, symbol):
        if isinstance(symbol, str):
            base, _, quote = symbol.partition("/")
            symbol = Symbol(
                base,
                quote or "USD",
                AssetClass.STOCK,
                MarketType.SPOT,
                "alpaca",
            )
        ticker = self._run(self._adapter.fetch_ticker(symbol))
        return {
            "last": getattr(ticker, "last", None) or getattr(ticker, "price", None),
            "timestamp": getattr(ticker, "timestamp", None),
            "received_at": datetime.now(UTC),
        }

    def get_account(self):
        return self._adapter.get_account_info()


def close_micro_dust_positions():
    adapter = AlpacaAdapter(
        AlpacaConfig(
            api_key=os.environ["ALPACA_API_KEY"],
            secret_key=os.environ["ALPACA_API_SECRET"],
            paper=True,
        )
    )
    asyncio.run(adapter.connect())

    closed = []
    skipped = []

    sync_adapter = _AlpacaSyncAdapter(adapter)
    store = ExecutionEventStore("data/execution/events.db").connect()

    def _price_source(symbol):
        ticker = sync_adapter.get_ticker(symbol)
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

    def _portfolio_source(symbol):
        account = sync_adapter.get_account()
        equity = float(account.get("equity") or 0.0)
        cash = float(account.get("cash") or 0.0)
        if (
            not math.isfinite(equity)
            or equity <= 0
            or not math.isfinite(cash)
            or cash < 0
        ):
            return None
        quantity = 0.0
        for position in sync_adapter.get_all_positions():
            if str(position.symbol) == str(symbol):
                quantity = max(0.0, float(position.size))
                break
        return PortfolioRiskSnapshot(
            symbol=str(symbol),
            position_quantity=quantity,
            available_quantity=quantity,
            equity=equity,
            available_cash=cash,
            observed_at=datetime.now(UTC),
            source="alpaca_paper",
        )

    lifecycle = ExecutionLifecycle(
        store,
        price_source=_price_source,
        inventory_source=lambda symbol, side: (
            snapshot.available_quantity
            if (snapshot := _portfolio_source(symbol)) is not None
            else 0.0
        ),
        portfolio_source=_portfolio_source,
    )
    execution_adapter = AlpacaExecutionAdapter(adapter)
    gateway = BrokerGateway(
        adapter=execution_adapter,
        store=store,
        lifecycle=lifecycle,
    )
    execution_service = CanonicalExecutionService(
        lifecycle=lifecycle,
        gateway=gateway,
    )

    for position in sync_adapter.get_all_positions():
        market_value = float(position.notional)
        qty = max(0.0, float(position.size))
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
                    intent_id=f"emergency-close-{symbol}-{uuid.uuid4().hex}",
                    symbol=str(symbol),
                    side="sell",
                    quantity=qty,
                    reason="micro_dust_cleanup",
                    metadata={"order_type": "market", "time_in_force": "ioc"},
                )
                result = execution_service.emergency_close(emergency).result
                if result.state == BrokerSubmitState.FILLED:
                    closed.append(
                        {
                            "symbol": str(symbol),
                            "qty": qty,
                            "market_value": market_value,
                        }
                    )
                elif result.success:
                    skipped.append(
                        {
                            "symbol": str(symbol),
                            "reason": "order accepted but fill is not yet confirmed",
                        }
                    )
                else:
                    skipped.append(
                        {
                            "symbol": str(symbol),
                            "reason": result.error or "gateway submit failed",
                        }
                    )
            except Exception as e:
                skipped.append(
                    {
                        "symbol": str(symbol),
                        "reason": str(e),
                    }
                )
        else:
            skipped.append(
                {
                    "symbol": str(symbol),
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
