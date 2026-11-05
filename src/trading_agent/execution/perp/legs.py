"""Trade a book's legs toward signed target quantities, through the pair book's order path.

Shared by every book on the perp demo account (book LV first). A leg is a dict
in the book's signed store with at least ``qty`` (signed, the book's own
quantity). A trade adds to it:

- ``fills``: one row per filled order, from ``LegTrader.row`` (default
  ``fill_record``: client key, side, filled quantity, average fill, fees and
  the mark the caller passed at send).
- ``closing_key``: the key of a reduce-only order while it is in flight, saved
  before the order is sent and cleared once its fill is recorded. A key left
  behind means the run died mid-close; the book's ``check()`` settles it from
  the order ledger.

A rebalance runs in two passes over the book's symbols, so freed margin comes
first and a flip goes through flat:

1. ``reduce_toward`` on every symbol: a leg whose target is flat or on the
   other side closes its exact quantity, reduce-only; a leg above a smaller
   target of the same side is cut to it, reduce-only.
2. ``add_toward`` on every target: open or add up to the target. A book reads
   ``risk.room(venue)`` between the passes and skips or scales its additions.

A difference that rounds below the venue minimum is not sent; ``skip_event``
is audited with the symbol and the reason. Keys come from
``Book.fresh_key(stem(symbol, kind))``, ``kind`` "c" for a reduction and "o"
for an addition, so the stem carries the book's own prefix.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable

from trading_agent.exchanges.models import OrderConstraintError
from trading_agent.execution.perp.book import QTY_TOL, PerpBookError

if TYPE_CHECKING:
    from trading_agent.execution.perp.book import Book

DECIMALS = 10      # a leg's summed fills are rounded here: float drift, never a venue step


def fill_record(key: str, side: str, res: dict, mark: float | None) -> dict:
    """A fill row with what a cost read needs: key, side, quantity, average, fees, mark at send."""

    return {"key": key, "side": side, "qty": res["filled_qty"], "avg": res["avg_fill_price"],
            "fees": res.get("fees") or {}, "mark": mark}


def ledger_price(book: "Book", key: str, rec: dict, mark: float | None = None) -> tuple[float, str]:
    """The average price of a fill read from the order ledger, and where it came from.

    Demo Trading can answer a filled market order with average 0 (seen
    2026-11-03). ``Book._market`` then raises after recording the order as
    filled with no price, and the record is never read again. A book that
    settles that fill from the ledger asks here: the ledger's own price
    (source ""), else the order read back by its key ("venue"), else its
    quote cost over its quantity ("quote_cost"), else the mark at send or now
    ("mark"). A fill row keeps a non-empty source as ``avg_from`` so a cost
    read can leave it out.
    """

    avg = float(rec.get("average_fill_price") or 0.0)
    if avg > 0:
        return avg, ""
    symbol = str(rec.get("symbol") or "")
    try:
        polled = book.venue.order_by_key(symbol, key)
    except Exception:      # a read for the record; the fallbacks below still price it
        polled = None
    if polled is not None and polled.get("status") == "filled" and float(polled.get("avg_fill_price") or 0.0) > 0:
        return float(polled["avg_fill_price"]), "venue"
    filled, cost = float(rec.get("filled_quantity") or 0.0), float(rec.get("quote_cost") or 0.0)
    if cost > 0 and filled > 0:
        return cost / filled, "quote_cost"
    if mark is not None and float(mark) > 0:
        return float(mark), "mark"
    return book.venue.mark_price(symbol), "mark"


@dataclass
class LegTrader:
    book: "Book"                                   # the pair book: order path, ledger, audit
    save: Callable[[], None]                       # saves the store that holds the legs
    stem: Callable[[str, str], str]                # (symbol, kind) -> client key stem
    skip_event: str                                # audited when a difference is below the venue minimum
    row: Callable[[str, str, dict, float | None], object] = fill_record

    def trade(self, symbol: str, delta: float, leg: dict, *, price: float | None = 0.0,
              exact: bool = False) -> dict | None:
        """Move ``leg["qty"]`` by ``delta`` (signed) at market; a reduction is reduce-only.

        ``exact`` sends ``|delta|`` as it is (a close of a quantity the venue
        already took), less float drift; otherwise it is normalized at
        ``price``. An exact close may pass ``price=None`` when the mark could
        not be read: it is only recorded. Returns the fill, or None when the
        difference was below the venue minimum.
        """

        reduce = abs(leg["qty"] + delta) < abs(leg["qty"]) - QTY_TOL
        if exact:
            # ccxt truncates an amount to the step: 0.19999999999999998 would go out as 0.199 and leave dust.
            qty = round(abs(delta), DECIMALS)
        else:
            try:
                qty = self.book.venue.normalize(symbol, abs(delta), price)
            except OrderConstraintError:
                self.book.audit(self.skip_event, symbol=symbol, reason="below_venue_minimum")
                return None
        key = self.book.fresh_key(self.stem(symbol, "c" if reduce else "o"))
        if reduce:
            leg["closing_key"] = key
            self.save()
        side = "buy" if delta > 0 else "sell"
        res = self.book._market(symbol, side, qty, key, reduce_only=reduce)
        leg["qty"] = round(leg["qty"] + (res["filled_qty"] if delta > 0 else -res["filled_qty"]), DECIMALS)
        if abs(leg["qty"]) <= QTY_TOL:
            leg["qty"] = 0.0
        leg.setdefault("fills", []).append(self.row(key, side, res, price))
        leg["closing_key"] = None     # recorded: only a close cut off before this line is left to check()
        self.save()
        return res

    def reduce_toward(self, symbol: str, leg: dict, target: float, *, price: float = 0.0) -> dict | None:
        """First pass: close a leg whose target is flat or the other side, or cut it to a smaller target."""

        cur = leg["qty"]
        if abs(cur) <= QTY_TOL:
            return None
        if abs(target) <= QTY_TOL or (cur > 0) != (target > 0):
            return self.trade(symbol, -cur, leg, price=price, exact=True)
        if abs(target) < abs(cur):
            return self.trade(symbol, target - cur, leg, price=price)
        return None

    def add_toward(self, symbol: str, leg: dict, target: float, *, price: float) -> dict | None:
        """Second pass: open or add up to a larger target on the leg's side."""

        cur = leg["qty"]
        if abs(cur) > QTY_TOL and abs(target) > QTY_TOL and (cur > 0) != (target > 0):
            raise PerpBookError(f"{symbol}: reduce to flat before adding on the other side")
        if abs(target) > abs(cur) + QTY_TOL:
            return self.trade(symbol, target - cur, leg, price=price)
        return None
