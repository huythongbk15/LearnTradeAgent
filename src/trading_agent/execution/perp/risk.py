"""Account-wide caps of the perp demo account, shared by every book on it.

All books trade one Binance USD-M Demo Trading account at 1x. Two caps hold
across them, measured on the venue's positions at mark, not on what each book
thinks it holds:

- ``EQUITY_USE``: after any trade that adds exposure, the account's gross
  notional (sum over venue positions of |qty| x mark) stays at or under 90% of
  equity.
- ``ALT_SHORT_CAP``: after any trade that adds a short on a symbol other than
  BTCUSDT, the account's short notional on those symbols stays at or under 40%
  of equity. It bounds the short-alt group: the pair book's shorts, book LV's
  shorts and H-LEARN-11's shorts.

Exits and reductions are never blocked by a cap. A book that adds exposure
reads ``room(venue)`` after its own reductions, then asks the result:

- ``Room.binding(add_gross, add_alt_short)``: the reason the whole addition
  does not fit (``EQUITY`` first, then ``ALT_SHORT``), or None. The pair book,
  book LV, FLOW02 and FLOW01 skip on a reason and record it.
- ``Room.scale(add_gross, add_alt_short)``: the largest factor in [0, 1] by
  which the addition fits both caps. H-LEARN-11 scales its exposure-adding legs
  by it and records "capped".

A roll that replaces a book's current legs (book LV) passes them as
``release``: they are counted as gone, so the check sees the roll's net effect,
other positions plus the new book. The caps are constants: a change needs the
operator's ruling in docs/operations/PERP_DEMO_RUNNER.md. Values here are
notionals for a decision only; nothing in this module records or prints them.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from trading_agent.execution.perp import rules

EQUITY_USE = 0.9       # account gross notional at or under 90% of equity after an addition
ALT_SHORT_CAP = 0.4    # account short notional off BTCUSDT at or under 40% of equity after an alt short
EQUITY = "equity"            # recorded skip reason: the gross cap binds (stable since the pair book's first check)
ALT_SHORT = "alt_short_cap"  # recorded skip reason: the alt-short cap binds
QTY_TOL = 1e-9


def gross_notional(qty: Mapping[str, float], marks: Mapping[str, float]) -> float:
    """Sum over symbols of |signed quantity| x mark."""

    return sum(abs(q) * marks[s] for s, q in qty.items() if abs(q) > QTY_TOL)


def alt_short_notional(qty: Mapping[str, float], marks: Mapping[str, float]) -> float:
    """Short notional at mark on every symbol but BTCUSDT."""

    return sum(-q * marks[s] for s, q in qty.items() if s != rules.HEDGE and q < -QTY_TOL)


def account(venue, *, release: Mapping[str, float] | None = None) -> tuple[dict[str, float], dict[str, float]]:
    """Signed venue quantities and marks by symbol, less the quantities in ``release``.

    A position whose mark the venue reports as zero is priced from the ticker,
    so a missing mark never makes the account look smaller.
    """

    qty: dict[str, float] = {}
    marks: dict[str, float] = {}
    for s, pos in venue.positions().items():
        qty[s] = pos.qty
        marks[s] = pos.mark_price
    for s, q in (release or {}).items():
        qty[s] = qty.get(s, 0.0) - q
    for s, q in qty.items():
        if abs(q) > QTY_TOL and not marks.get(s, 0.0) > 0:
            marks[s] = venue.mark_price(s)
    return qty, marks


@dataclass(frozen=True)
class Room:
    """The account against both caps: equity and the notionals it holds now."""

    equity: float
    gross: float
    alt_short: float

    @property
    def gross_room(self) -> float:
        """Gross notional that can still be added (negative when already over)."""

        return EQUITY_USE * self.equity - self.gross

    @property
    def alt_short_room(self) -> float:
        """Alt short notional that can still be added (negative when already over)."""

        return ALT_SHORT_CAP * self.equity - self.alt_short

    def binding(self, add_gross: float, add_alt_short: float = 0.0) -> str | None:
        """Why adding this much does not fit, or None when it does. Adding nothing always fits."""

        if add_gross > 0 and add_gross > self.gross_room:
            return EQUITY
        if add_alt_short > 0 and add_alt_short > self.alt_short_room:
            return ALT_SHORT
        return None

    def scale(self, add_gross: float, add_alt_short: float = 0.0) -> float:
        """The largest factor in [0, 1] by which this addition fits both caps."""

        f = 1.0
        if add_gross > 0:
            f = min(f, self.gross_room / add_gross)
        if add_alt_short > 0:
            f = min(f, self.alt_short_room / add_alt_short)
        return max(0.0, f)


def room(venue, *, release: Mapping[str, float] | None = None) -> Room:
    """The account's room under both caps now, with ``release`` counted as gone."""

    qty, marks = account(venue, release=release)
    return Room(venue.equity_usdt(), gross_notional(qty, marks), alt_short_notional(qty, marks))
