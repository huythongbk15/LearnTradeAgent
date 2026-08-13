"""Query Alpaca paper: orders gần nhất + equity + positions."""

import os
import sys

sys.path.insert(0, ".")
from dotenv import load_dotenv

load_dotenv()

from alpaca.trading.client import TradingClient  # noqa: E402
from alpaca.trading.enums import QueryOrderStatus  # noqa: E402
from alpaca.trading.requests import GetOrdersRequest  # noqa: E402

c = TradingClient(
    os.environ["ALPACA_API_KEY"], os.environ["ALPACA_API_SECRET"], paper=True
)
orders = c.get_orders(filter=GetOrdersRequest(status=QueryOrderStatus.ALL, limit=6))
print("== 6 lệnh gần nhất ==")
for o in orders:
    filled = o.filled_qty or 0
    print(
        f"  {o.symbol:10s} {o.side.value:4s} qty={o.qty} filled={filled} status={o.status.value}"
    )

acct = c.get_account()
print("\nEquity:", round(float(acct.equity), 2), "| Cash:", round(float(acct.cash), 2))
print("Positions:")
for p in c.get_all_positions():
    print(
        f"  {p.symbol:12s} qty={float(p.qty):.4f} avg={float(p.avg_entry_price):.2f} "
        f"mv={float(p.market_value):.2f} upnl={float(p.unrealized_pl):.2f} "
        f"({float(p.unrealized_plpc) * 100:.2f}%)"
    )
