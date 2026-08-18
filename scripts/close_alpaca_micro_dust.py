#!/usr/bin/env python3
"""Close micro-dust positions on Alpaca paper trading."""

import os
import sys

sys.path.insert(0, ".")
from dotenv import load_dotenv

load_dotenv()

from alpaca.trading.client import TradingClient
from alpaca.trading.enums import OrderSide, TimeInForce

ALPACA_MICRO_DUST_THRESHOLD_USD = 5.0


def close_micro_dust_positions():
    client = TradingClient(
        os.environ["ALPACA_API_KEY"], os.environ["ALPACA_API_SECRET"], paper=True
    )

    closed = []
    skipped = []

    for position in client.get_all_positions():
        market_value = float(position.market_value)
        qty = float(position.qty)
        symbol = position.symbol

        if abs(market_value) <= ALPACA_MICRO_DUST_THRESHOLD_USD:
            try:
                from alpaca.trading.requests import OrderRequest

                order_req = OrderRequest(
                    symbol=symbol,
                    qty=qty,
                    side=OrderSide.SELL,
                    type="market",
                    time_in_force=TimeInForce.IOC,
                )
                client.submit_order(order_data=order_req)
                closed.append({
                    "symbol": symbol,
                    "qty": qty,
                    "market_value": market_value,
                })
            except Exception as e:
                skipped.append({
                    "symbol": symbol,
                    "reason": str(e),
                })
        else:
            skipped.append({
                "symbol": symbol,
                "reason": f"above threshold ({market_value:.2f} USD)",
            })

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
