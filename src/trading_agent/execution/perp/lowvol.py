"""Book LV on Binance Demo Trading: the frozen low volatility paper book as orders.

Each Monday the weekly selection run (``tools/selection/lv_book.py``, rules in
docs/operations/SELECTION_PAPER_RUN.md) picks the calmest fifth of the masked
list to hold long and the most volatile fifth to hold short. This book trades
that pick on the demo venue from 04:00 UTC that Monday: half the book a side,
equal weight over the names the venue lists, held a week. A roll trades only
the difference to the new targets. Each short gets an exchange-side
STOP_MARKET at 1.5x its price at the roll, and a stopped short stays flat until
the next roll. A week without a pick in its window ends flat.

Orders go through the pair book's path (one order ledger, one reconcile, one
venue), and the pair book's position check counts this book's legs as
explained. It is an ``book.OtherBook`` named "lv": a coin any other book holds,
or BTCUSDT, is skipped and recorded ``held_by_<holder>``, and a roll must fit
the account-wide caps of ``risk.py`` with its current legs counted as gone, or
the week is missed. Fills and funding recorded here are returns of the LV
forward read: nothing computes them before that read (2028-11-13). Rules:
docs/operations/PERP_DEMO_RUNNER.md.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable, ClassVar

from trading_agent.execution.perp import risk, rules
from trading_agent.execution.perp.book import (
    PAIR_BOOK,
    QTY_TOL,
    Book,
    PairStore,
    PerpBookError,
    _now_iso,
)
from trading_agent.execution.perp.legs import DECIMALS, LegTrader, ledger_price

TERMINAL = ("filled", "cancelled", "rejected", "expired")
STOP_OPEN = ("submitted", "reconciling", "acknowledged", "open")   # a stop intent sent and not closed
HOUR_MS = rules.HOUR_MS
DAY_MS = 24 * HOUR_MS
ENTRY_H = 4            # the paper block enters at the 4 h open after Monday 00:00 UTC
WINDOW_H = 2           # a run that first sees the pick later than this misses the week
HOLD_H = 168
SIDE = 0.5             # each leg holds half the book
STOP_X = 1.5           # exchange-side stop on each short at 1.5x its price at the roll
BOOK_USD_CAP = 5000.0  # a book above this notional is refused
PREFIXES = ("", "1000", "10000", "1000000", "1M")   # rebased perps: 1000PEPEUSDT


def week_of(now_ms: int) -> tuple[str, int]:
    """The Monday at or before ``now_ms``: its ISO date and its 00:00 UTC in ms."""

    day = now_ms // DAY_MS
    monday = (day - (day + 3) % 7) * DAY_MS     # 1970-01-01 was a Thursday
    return datetime.fromtimestamp(monday / 1000, UTC).date().isoformat(), monday


def perp_symbol(coin: str, trading: set[str]) -> str | None:
    """The demo USDT perp of a list coin, rebased units included."""

    for prefix in PREFIXES:
        if f"{prefix}{coin}USDT" in trading:
            return f"{prefix}{coin}USDT"
    return None


def _picks(path: Path) -> list[dict]:
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []


def load_pick(path: Path, monday: str) -> dict | None:
    """The selection run's LV pick of one Monday, None before it exists."""

    return next((r for r in _picks(path) if r.get("monday") == monday), None)


def pick_symbols(path: Path, now_ms: int, trading: set[str]) -> set[str]:
    """Demo symbols of the LV pick of the week holding ``now_ms``, both sides.

    Another book leaves them free for the Monday 04:00 roll. Empty
    before the selection run writes the pick (Monday 00:07 UTC).
    """

    pick = load_pick(path, week_of(now_ms)[0])
    coins = [str(c) for c in (pick or {}).get("long") or []] + [str(c) for c in (pick or {}).get("short") or []]
    return {s for s in (perp_symbol(c, trading) for c in coins) if s is not None}


def first_week(path: Path) -> str | None:
    """The paper book's first Monday: weeks before it are not missed, they never were."""

    return min((str(r["monday"]) for r in _picks(path) if r.get("monday")), default=None)


def _lv_fill(key: str, side: str, res: dict, mark: float) -> list:
    return [key, res["filled_qty"], res["avg_fill_price"]]


def _add_fill(leg: dict, row: list, side: str) -> None:
    """Record one fill row on a leg and move its signed quantity by it, rounded as ``LegTrader`` rounds."""

    leg.setdefault("fills", []).append(row)
    q = float(row[1])
    leg["qty"] = round(leg["qty"] + (q if side == "buy" else -q), DECIMALS)
    if abs(leg["qty"]) <= QTY_TOL:
        leg["qty"] = 0.0


@dataclass
class LowVolBook:
    name: ClassVar[str] = "lv"
    book: Book                       # the pair book's order path and venue
    blocks: PairStore                # signed; ``pairs`` holds blocks by id, ``seen`` the handled weeks
    book_usd: float
    picks_path: Path
    is_sealed: Callable[[str], bool]

    @property
    def venue(self):
        return self.book.venue

    # ── state ──────────────────────────────────────────────────────────────

    def active(self) -> tuple[str, dict] | None:
        live = [(k, b) for k, b in self.blocks.pairs.items() if b.get("status") in ("open", "rolling")]
        if len(live) > 1:
            raise PerpBookError(f"more than one LV block is live: {sorted(k for k, _ in live)}")
        return live[0] if live else None

    def held(self) -> dict[str, float]:
        """Signed quantities this book holds at the venue, by symbol."""

        act = self.active()
        if act is None or act[1]["status"] != "open":
            return {}
        return {s: leg["qty"] for s, leg in act[1]["legs"].items() if leg["state"] == "held"}

    def _fresh_key(self, tag: str, symbol: str, kind: str) -> str:
        return self.book.fresh_key(f"lv{tag}-{symbol[:18]}-{kind}")

    def _trader(self, tag: str) -> LegTrader:
        return LegTrader(self.book, self.blocks.save, lambda s, kind: f"lv{tag}-{s[:18]}-{kind}",
                         "perp_lv_delta_skipped", row=_lv_fill)

    def _stop_live(self, key: str) -> bool:
        """The stop's intent was sent and is not closed yet."""

        rec = self.book.orders.state.order_ledger.get(key)
        return rec is not None and rec.get("status") in STOP_OPEN

    def _close_stop_intent(self, key: str, final: str, **extra: Any) -> None:
        rec = self.book.orders.state.order_ledger.get(key)
        if rec is None or rec.get("status") not in STOP_OPEN:
            return
        if rec["status"] in ("submitted", "reconciling"):
            self.book.orders.update_order(key, status="acknowledged")
        self.book.orders.update_order(key, status=final, **extra)

    def _cancel_stop(self, symbol: str, key: str) -> None:
        if key in self.venue.open_stop_keys(symbol):
            self.venue.cancel_stop(symbol, key=key)
            if key in self.venue.open_stop_keys(symbol):
                raise PerpBookError(f"stop {key} could not be cancelled")
        self._close_stop_intent(key, "cancelled")

    def _unsettled(self, bid: str, b: dict) -> int:
        """Orders of this block, stops aside, whose fill no leg holds yet: outcome unknown, or a fill not priced."""

        ledger = self.book.orders.state.order_ledger
        legs = b.get("legs") or {}
        recorded = {r[0] for leg in legs.values() for r in leg.get("fills") or []}
        prefix = f"lv{bid}-"
        n = 0
        for k, r in ledger.items():
            if not k.startswith(prefix) or k.endswith("-st") or k in recorded:
                continue
            if str(r.get("status") or "") not in TERMINAL or (
                    str(r.get("symbol") or "") in legs and float(r.get("filled_quantity") or 0.0) > QTY_TOL):
                n += 1
        return n

    def _settle(self, bid: str, b: dict, *, lenient: bool = False) -> dict[str, tuple[float, str]]:
        """Record the fills of this block's orders that no leg holds yet, from the order ledger.

        A close, cut or flatten that the venue filled in part and then expired
        or cancelled, a reply lost and read back by the reconcile, or a run
        killed before it recorded a fill: each leaves a terminal order under
        the block's key prefix with a filled quantity and no row. Its row goes
        on its symbol's leg as a trade records it, and the leg's quantity
        moves by it, rounded to ``legs.DECIMALS``. A row priced by a fallback
        of ``legs.ledger_price`` carries that source as a fourth field. A
        ``closing_key`` whose order is settled, or was never reserved, is
        cleared. Returns the price and source of each symbol's last row
        settled here.

        ``lenient`` (a failed block, and the flatten): a fill that cannot be
        priced (its last fallback, the mark, unreadable) is left for a later
        run and audited ``perp_lv_fill_unpriced``; ``_unsettled`` still
        counts it. Without it the error stops the run: the live block's legs
        must hold every fill before they are compared with the venue.
        """

        ledger = self.book.orders.state.order_ledger
        legs = b.get("legs") or {}
        recorded = {r[0] for leg in legs.values() for r in leg.get("fills") or []}
        prefix = f"lv{bid}-"
        keys = sorted((k for k in ledger if k.startswith(prefix) and not k.endswith("-st") and k not in recorded),
                      key=lambda k: (str(ledger[k].get("created_at") or ""), k))
        out: dict[str, tuple[float, str]] = {}
        changed = False
        for key in keys:
            rec = ledger[key]
            s, filled, status = str(rec.get("symbol") or ""), float(rec.get("filled_quantity") or 0.0), str(rec.get("status") or "")
            if s not in legs or status not in TERMINAL or filled <= QTY_TOL:
                continue
            # Priced as every book prices a fill it settles from the ledger: Demo can leave average 0 there.
            try:
                px, src = ledger_price(self.book, key, rec)
            except Exception as exc:
                if not lenient:
                    raise
                self.book.audit("perp_lv_fill_unpriced", block=bid, symbol=s, key=key, error=type(exc).__name__)
                continue
            _add_fill(legs[s], [key, filled, px] + ([src] if src else []), str(rec.get("side") or "").lower())
            out[s] = (px, src)
            changed = True
            self.book.audit("perp_lv_fill_settled", block=bid, symbol=s, key=key, status=status, qty=filled)
        for leg in legs.values():
            key = leg.get("closing_key")
            rec = ledger.get(key) if key else None
            if key and (rec is None or str(rec.get("status") or "") in TERMINAL):
                leg["closing_key"] = None
                changed = True
        if changed:
            self.blocks.save()
        return out

    # ── each run, before the pair book's position check ────────────────────

    def check(self) -> None:
        """Flatten an interrupted roll, record cut-off fills, settle fired stops and closes, refuse a mismatch.

        Runs after the reconcile, so every order of the book has its outcome in the ledger here. A failed
        block's ``unsettled`` fills are recorded and its ``stale_stops`` cancelled again (``_flatten``).
        """

        for bid, b in list(self.blocks.pairs.items()):
            if b.get("status") == "rolling":
                self.book.audit("perp_lv_roll_interrupted", block=bid)
                self._flatten(bid, b, reason="interrupted")
            elif b.get("status") == "failed" and (b.get("unsettled") or b.get("stale_stops")):
                if b.get("unsettled"):
                    # Flattened while an order's outcome was unknown, or a fill could not be priced: the reconcile
                    # has read the outcome back by now. The block is flat, so a price it still cannot read never
                    # stops the run.
                    self._settle(bid, b, lenient=True)
                    left = self._unsettled(bid, b)
                    if left:
                        b["unsettled"] = left
                    else:
                        del b["unsettled"]
                if b.get("stale_stops"):
                    self._cancel_stale_stops(bid, b)
                self.blocks.save()
        act = self.active()
        if act is None:
            return
        bid, b = act
        settled = self._settle(bid, b)
        positions = self.venue.positions()
        for s, leg in b["legs"].items():
            if leg["state"] != "held":
                continue
            pos = positions.get(s)
            q = pos.qty if pos is not None else 0.0
            if abs(leg["qty"]) <= QTY_TOL and abs(q) <= QTY_TOL:
                # Its close filled (a short's stop was cancelled before it) and the run was cut off before it
                # marked the leg closed.
                if leg.get("stop_key"):
                    if leg["stop_key"] in self.venue.open_stop_keys(s):
                        raise PerpBookError(f"LV {s}: short is gone but its stop is still open")
                    # A run cut off after the venue took the cancel, before the intent was closed, left it open.
                    self._close_stop_intent(leg["stop_key"], "cancelled")
                leg["state"] = "closed"
                if s in settled:
                    leg["exit"] = settled[s][0]
                    if settled[s][1]:
                        leg["exit_from"] = settled[s][1]
                continue
            if leg["qty"] < 0:
                stops = self.venue.open_stop_keys(s)
                if abs(q) <= QTY_TOL:
                    if leg["stop_key"] in stops:
                        raise PerpBookError(f"LV {s}: short is gone but its stop is still open")
                    leg.update(state="stopped", stopped_at=_now_iso())
                    self._close_stop_intent(leg["stop_key"], "filled", exchange_status="triggered")
                    self.book.audit("perp_lv_stop", block=bid, symbol=s)
                    continue
                if leg["stop_key"] not in stops and leg.get("closing") and abs(q - leg["qty"]) <= QTY_TOL * max(1.0, abs(q)):
                    # Its close cancelled the stop, then was cut off before the buy-back or bought back only the
                    # part recorded above: the venue holds exactly the rest, so buy that back now.
                    self._trader(bid).trade(s, -leg["qty"], leg, exact=True)
                    self._close_stop_intent(leg["stop_key"], "cancelled")
                    leg["state"] = "closed"
                    self.blocks.save()
                    self.book.audit("perp_lv_close_finished", block=bid, symbol=s)
                    continue
                if leg["stop_key"] not in stops:
                    self.book.audit("perp_lv_unprotected", block=bid, symbol=s)
                    raise PerpBookError(f"LV {s}: short has no open stop; operator review required")
            if abs(q - leg["qty"]) > QTY_TOL * max(1.0, abs(leg["qty"])):
                self.book.audit("perp_lv_mismatch", block=bid, symbol=s)
                raise PerpBookError(f"LV {s}: venue {q} differs from book {leg['qty']}")
        self.blocks.save()

    # ── the week ───────────────────────────────────────────────────────────

    def plan(self, now_ms: int) -> dict:
        """What a run would do now, without orders."""

        monday, start = self._window(now_ms)
        pick = load_pick(self.picks_path, monday)
        out: dict[str, Any] = {"week": monday, "in_window": start[0] <= now_ms < start[1], "pick": pick is not None}
        if pick is not None:
            longs, shorts, skipped = self._names(pick)
            out.update(long=len(longs), short=len(shorts), skipped=skipped)
        return out

    @staticmethod
    def _window(now_ms: int) -> tuple[str, tuple[int, int]]:
        monday, m = week_of(now_ms)
        start = m + ENTRY_H * HOUR_MS
        return monday, (start, start + WINDOW_H * HOUR_MS)

    def step(self, now_ms: int, *, entry_lock: str | None = None) -> dict:
        """Roll into this week's pick inside its window; end the live block at its hold end."""

        monday, (start, end) = self._window(now_ms)
        out: dict[str, Any] = {"week": monday}
        first = first_week(self.picks_path)
        if first is not None and monday >= first and monday not in self.blocks.seen and now_ms >= start:
            pick = load_pick(self.picks_path, monday) if now_ms < end and not entry_lock else None
            if pick is not None:
                out.update(self.roll(f"w{monday[2:].replace('-', '')}", pick, now_ms, start + (HOLD_H + WINDOW_H) * HOUR_MS))
            elif now_ms >= end:
                reason = "entry_lock" if entry_lock else "no_pick"
                self.blocks.pairs[f"w{monday[2:].replace('-', '')}"] = {"monday": monday, "status": "missed", "reason": reason, "legs": {}}
                self.blocks.seen.append(monday)
                self.blocks.save()
                self.book.audit("perp_lv_week_missed", week=monday, reason=reason)
                out["missed"] = reason
        act = self.active()
        if act is not None and now_ms >= act[1]["exit_ms"]:
            self._close(*act, reason="hold")
            out["closed"] = "hold"
        return out

    def rehearse(self, longs: list[str], shorts: list[str], now_ms: int, hold_h: int) -> dict:
        """Roll into a hand-given pick now, to test the mechanics before a real week."""

        act = self.active()
        if act is not None and not act[1].get("rehearsal"):
            raise PerpBookError("a weekly LV block is live; rehearse only on a flat or rehearsal book")
        pick = {"monday": None, "long": longs, "short": shorts}
        return self.roll(f"r{now_ms // 60_000:x}", pick, now_ms, now_ms + hold_h * HOUR_MS, rehearsal=True)

    def _names(self, pick: dict) -> tuple[list[str], list[str], dict[str, str]]:
        """The pick's demo symbols to hold long and short, and the coins skipped with their reasons.

        Skipped: a coin without a demo perp (``not_on_demo``), one another
        book holds, or BTCUSDT (``held_by_<holder>``), and one whose symbol
        still carries a stop a failed LV block could not cancel
        (``stale_stop``, the block's ``stale_stops``): that reduce-only buy
        would sit under a new short as a second stop.
        """

        trading = self.venue.trading_symbols()
        taken = {**self.book.taken(self.name), rules.HEDGE: PAIR_BOOK}   # BTCUSDT stays the pair hedges' and the BTC books'
        stale = {s for b in self.blocks.pairs.values() if b.get("status") == "failed"
                 for s in (b.get("stale_stops") or {}).values()}
        skipped: dict[str, str] = {}
        out: tuple[list[str], list[str]] = ([], [])
        for side, coins in ((0, pick.get("long") or []), (1, pick.get("short") or [])):
            for coin in coins:
                coin = str(coin)
                if self.is_sealed(coin):
                    raise PerpBookError(f"a masked coin reached the LV book: {coin}")
                s = perp_symbol(coin, trading)
                if s is None:
                    skipped[coin] = "not_on_demo"
                elif s in taken:
                    skipped[coin] = f"held_by_{taken[s]}"
                elif s in stale:
                    skipped[coin] = "stale_stop"
                else:
                    out[side].append(s)
        return out[0], out[1], skipped

    def roll(self, bid: str, pick: dict, now_ms: int, exit_ms: int, *, rehearsal: bool = False) -> dict:
        if not 0 < self.book_usd <= BOOK_USD_CAP:
            raise PerpBookError(f"LV book notional {self.book_usd} outside (0, {BOOK_USD_CAP}]")
        week = pick.get("monday") or bid
        longs, shorts, skipped = self._names(pick)
        act = self.active()
        current = self.held()
        nb: dict[str, Any] = {"monday": pick.get("monday"), "rehearsal": rehearsal, "entry_ms": now_ms,
                              "exit_ms": exit_ms, "skipped": skipped, "legs": {}, "prev": act[0] if act else None}
        reason = None
        if not longs or not shorts:
            reason = "too_few_names"
        else:
            # The roll's net effect on the account: its current legs go, the new book comes.
            reason = risk.room(self.venue, release=current).binding(self.book_usd, self.book_usd * SIDE)
        if reason is not None:
            if act is not None:
                self._close(*act, reason="flat")
            nb.update(status="missed", reason=reason)
            self.blocks.pairs[bid] = nb
            self.blocks.seen.append(week)
            self.blocks.save()
            self.book.audit("perp_lv_week_missed", week=week, reason=reason)
            return {"missed": reason, "skipped": len(skipped)}

        marks = {s: self.venue.mark_price(s) for s in longs + shorts}
        target = {s: self.book_usd * SIDE / len(longs) / marks[s] for s in longs}
        target.update({s: -self.book_usd * SIDE / len(shorts) / marks[s] for s in shorts})
        nb.update(status="rolling", touched=sorted(set(target) | set(current)), marks=marks)
        # The new block takes the current legs over in the same save, before any cancel or order: whatever the
        # roll or its flatten fills from here on lands on a leg of this block.
        legs = nb["legs"]
        for s in nb["touched"]:
            legs[s] = {"qty": current.get(s, 0.0), "state": "held"}
        uncover: list[tuple[str, str]] = []      # the current shorts' stops, cancelled first
        if act is not None:
            for s, leg in act[1]["legs"].items():
                if leg["state"] == "held":
                    if leg["qty"] < 0 and leg.get("stop_key"):
                        uncover.append((s, leg["stop_key"]))
                    leg["state"] = "rolled"
            act[1].update(status="closed", close_reason="roll", closed_at=_now_iso())
        self.blocks.pairs[bid] = nb
        self.blocks.seen.append(week)
        self.blocks.save()
        tag = bid
        try:
            for s, key in uncover:
                self._cancel_stop(s, key)
            for s in target:
                if s not in current:
                    self.venue.prepare(s)
            # Reductions first: they free margin, and a flip goes through flat.
            trader = self._trader(tag)
            for s in nb["touched"]:
                trader.reduce_toward(s, legs[s], target.get(s, 0.0), price=marks.get(s, 0.0))
            for s, tgt in target.items():
                trader.add_toward(s, legs[s], tgt, price=marks[s])
            for s in shorts:
                qty = -legs[s]["qty"]
                if qty <= QTY_TOL:
                    continue          # its open was below the venue minimum: nothing to protect
                key = f"lv{tag}-{s[:18]}-st"
                stop_px = STOP_X * marks[s]
                self.book._send(s, "buy", qty, key, lambda: self.venue.stop(s, "buy", qty, stop_px, key=key))
                legs[s].update(stop_key=key, stop_price=stop_px)
            for leg in legs.values():
                if abs(leg["qty"]) <= QTY_TOL:
                    leg["state"] = "closed"
            nb["status"] = "open"
            self.blocks.save()
        except Exception:
            self.book.audit("perp_lv_roll_failed", block=bid)
            self._flatten(bid, nb, reason="failed")
            raise
        self.book.audit("perp_lv_rolled", block=bid, rehearsal=rehearsal, long=len(longs), short=len(shorts),
                        skipped=len(skipped))
        return {"rolled": bid, "long": len(longs), "short": len(shorts), "skipped": len(skipped)}

    def _close(self, bid: str, b: dict, *, reason: str) -> None:
        """Both legs off at market, reduce-only, after each short's stop is cancelled."""

        trader = self._trader(bid)
        for s, leg in b["legs"].items():
            if leg["state"] != "held":
                continue
            if leg["qty"] < 0:
                # Saved before the stop goes: a run cut off between the cancel and the buy-back leaves a short
                # without its stop, and the next check() finishes this close instead of stopping every run.
                leg["closing"] = True
                self.blocks.save()
                self._cancel_stop(s, leg["stop_key"])
            trader.trade(s, -leg["qty"], leg, exact=True)
            leg["state"] = "closed"
            self.blocks.save()
        b.update(status="closed", close_reason=reason, closed_at=_now_iso(), funding=self._funding(b))
        self.blocks.save()
        self.book.audit("perp_lv_closed", block=bid, reason=reason)

    def _flatten(self, bid: str, b: dict, *, reason: str) -> None:
        """Take off whatever the venue holds on the block's symbols, then mark it failed.

        Symbols the venue holds short go first, then the rest in the block's
        order: a roll cancels the current shorts' stops before anything else,
        so a short is what a flatten cut short would leave unprotected. Each
        symbol is taken on its own: its LV stops are listed, then its position
        is read.

        - A short is bought back, reduce-only at market, before any of its
          stops is cancelled, and they are cancelled only once that order has
          filled in full. A position read that fails, or a buy-back that
          fails, whose reply is lost, or that fills only in part, leaves every
          stop of the symbol at the venue: it keeps protecting what is left,
          and a reduce-only stop larger than the position cannot open
          anything.
        - A short with no stop listed whose own position read fails is bought
          back by the quantity of the read of all positions that set the
          order, but only when no stop of this block or the previous one on
          the symbol that the venue took is still open in the record
          (``_stop_taken``): the roll cancelled the old stop, and the new one
          was never sent or its send raised. Nothing but this flatten moves
          that symbol then, and it would otherwise wait for the next run
          without a stop. A stop the venue took that it no longer lists may
          have fired since that read, and a reduce-only buy on a flat symbol
          is rejected: such a symbol waits for the next pass. A stop whose
          send raised although the venue took it is not seen this way; if it
          fires in that same window, the buy-back is rejected and the next
          reconcile stops every book for operator review.
        - A long carries no LV stop of its own: any LV stop listed on it is
          cancelled, then the long is sold, reduce-only. An error while those
          stops are cancelled or listed again does not hold up the sale.
        - On a flat symbol the listed stops are cancelled.

        An error on one symbol (a read, an order whose reply is lost, filled
        in part or filled without a price) does not stop the others. A cancel
        is not such an error: ``venue.cancel_stop`` answers False when the
        venue refuses one or the call fails, and the list read after it
        decides. Once every symbol was tried, the first error is raised with
        the block still ``rolling``: the next run's check() records what the
        venue filled, takes off what it still holds, and lists and cancels
        the stops this pass left.

        Once a symbol's stops are cancelled, the list is read again and the
        intents are closed (``_close_flatten_stops``). A stop the venue still
        lists keeps its intent open and goes to the block's ``stale_stops``.
        On a failed block each later check() tries the cancel again and
        closes the intent once the stop is gone; a block left ``rolling``
        lists and cancels its symbols' stops again in the next flatten. The
        position is taken off either way. A failed block's ``stale_stops``
        keep their symbols out of the next roll (``_names``).

        Each flatten fill is recorded on its leg as it comes. After the
        positions are off, what a cut-off order filled is recorded from the
        order ledger (``_settle``, lenient: a price read for the record never
        holds up the flatten). An order whose outcome is unknown here, or a
        fill that could not be priced, leaves the failed block ``unsettled``,
        and the next check() records it after the reconcile.
        """

        legs = b.get("legs") or {}
        prev = (self.blocks.pairs.get(str(b.get("prev") or "")) or {}).get("legs") or {}
        was_stale = set(b.get("stale_stops") or {})
        order = list(b.get("touched") or legs)
        try:
            held = {s: p.qty for s, p in self.venue.positions().items()}
            read = True
        except Exception as exc:     # sets the order only: each symbol reads its position again below
            self.book.audit("perp_lv_flatten_error", block=bid, step="order", error=type(exc).__name__)
            held = {s: float((legs.get(s) or {}).get("qty") or 0.0) for s in order}
            read = False
        order.sort(key=lambda s: held.get(s, 0.0) >= -QTY_TOL)     # shorts first, the block's order kept in each group
        errors: list[Exception] = []
        stale: dict[str, str] = {}
        for s in order:
            leg, was = legs.get(s), prev.get(s) or {}
            before: set[str] | None = None
            try:
                before = self.venue.open_stop_keys(s)
            except Exception as exc:     # the position is still taken off; its stops wait for the next pass
                errors.append(exc)
                self.book.audit("perp_lv_flatten_error", block=bid, symbol=s, step="stops", error=type(exc).__name__)
            try:
                try:
                    pos = self.venue.positions().get(s)
                    q = pos.qty if pos is not None else 0.0
                except Exception as exc:
                    if not (read and before == set() and held.get(s, 0.0) < -QTY_TOL
                            and not self._stop_taken(bid, s, was)):
                        raise
                    # A short with no stop listed and none the venue took (the roll cancelled the old one, the new one
                    # never reached it): nothing but this flatten moves it, so the read that set the order still holds.
                    # It is bought back now, not left without a stop until the next run; the error keeps the block
                    # rolling, and the next pass reads the venue again.
                    errors.append(exc)
                    self.book.audit("perp_lv_flatten_error", block=bid, symbol=s, step="position",
                                    error=type(exc).__name__)
                    self._flatten_order(bid, s, leg, held[s])
                    continue
                if q < -QTY_TOL:
                    # Bought back before its stops go: ``_market`` returns only once the order has filled in full, and
                    # until then the stops protect the short.
                    self._flatten_order(bid, s, leg, q)
            except Exception as exc:
                errors.append(exc)
                self.book.audit("perp_lv_flatten_error", block=bid, symbol=s, step="position", error=type(exc).__name__)
                continue
            if before is not None:
                try:
                    self._cancel_flatten_stops(bid, s, leg, was, q, before, stale=stale, was_stale=was_stale)
                except Exception as exc:     # a long is still sold below
                    errors.append(exc)
                    self.book.audit("perp_lv_flatten_error", block=bid, symbol=s, step="stops", error=type(exc).__name__)
            if q > QTY_TOL:      # a long: no LV stop of its own
                try:
                    self._flatten_order(bid, s, leg, q)
                except Exception as exc:
                    errors.append(exc)
                    self.book.audit("perp_lv_flatten_error", block=bid, symbol=s, step="position",
                                    error=type(exc).__name__)
        if stale:
            b["stale_stops"] = stale
        else:
            b.pop("stale_stops", None)
        if errors:
            self.blocks.save()
            self.book.audit("perp_lv_flatten_unfinished", block=bid, reason=reason, errors=len(errors))
            raise errors[0]
        self._settle(bid, b, lenient=True)
        for leg in legs.values():
            if leg["state"] != "stopped":
                leg["state"] = "closed"
        b.update(status="failed", close_reason=reason, closed_at=_now_iso())
        left = self._unsettled(bid, b)
        if left:
            b["unsettled"] = left
        self.blocks.save()
        self.book.audit("perp_lv_flattened", block=bid, reason=reason)

    def _flatten_order(self, bid: str, s: str, leg: dict | None, q: float) -> None:
        """The flatten's reduce-only market order for the venue's ``q`` on ``s``, its fill recorded on the leg.

        Raises unless the venue filled it in full (``Book._market``).
        """

        key = self._fresh_key(bid, s, "f")
        side = "buy" if q < 0 else "sell"
        res = self.book._market(s, side, abs(q), key, reduce_only=True)
        if leg is not None:
            _add_fill(leg, _lv_fill(key, side, res, 0.0), side)
            self.blocks.save()

    def _stop_taken(self, bid: str, s: str, was: dict) -> bool:
        """Whether the venue took a stop of this block or the previous one on ``s`` whose intent is still open.

        Taken: its send was answered (``acknowledged`` or ``open``). A stop
        the roll cancelled is closed, and one whose send raised may never have
        reached the venue: neither counts. ``was`` is the previous block's leg.
        """

        ledger = self.book.orders.state.order_ledger
        old = was.get("stop_key") if was.get("state") == "rolled" else None
        return any(str((ledger.get(k) or {}).get("status") or "") in ("acknowledged", "open")
                   for k in (f"lv{bid}-{s[:18]}-st", old) if k)

    def _cancel_flatten_stops(self, bid: str, s: str, leg: dict | None, was: dict, q: float, before: set[str], *,
                              stale: dict[str, str], was_stale: set[str]) -> None:
        """Cancel the LV stops ``before`` lists on ``s``, read the list again and close their intents."""

        tried = {k: self.venue.cancel_stop(s, key=k) for k in sorted(before) if k.startswith("lv")}
        after = self.venue.open_stop_keys(s) if tried else before
        self._close_flatten_stops(bid, s, leg, was, q, before, tried, after, stale=stale, was_stale=was_stale)

    def _close_flatten_stops(self, bid: str, s: str, leg: dict | None, was: dict, q: float, before: set[str],
                             tried: dict[str, bool], after: set[str], *, stale: dict[str, str],
                             was_stale: set[str]) -> None:
        """Close the intents of one symbol's LV stops in a flatten, from what the venue lists (``_flatten``).

        ``before`` and ``after`` are the stops the venue listed before and after
        this flatten's cancels, ``tried`` each cancel's answer, ``q`` the venue
        position read before this flatten's own order on the symbol (a short
        is bought back in full before its stops are cancelled), ``was`` the
        previous block's leg, ``was_stale`` the block's ``stale_stops`` when
        this flatten began.

        A stop the venue still lists keeps its intent open and goes to
        ``stale``. One it no longer lists has its intent closed:

        - cancelled, when this flatten's cancel took it off;
        - filled (``exchange_status`` triggered), when it went without that
          cancel while ``q`` is flat and the leg still holds a short once the
          fills of the block's own orders on the symbol that the leg does not
          hold yet are counted (``_rest``; an order whose outcome is unknown
          rules it out): it fired. The leg is saved ``stopped`` before the
          intent is closed, as check() records a fired stop;
        - rejected, for this block's own stop that the venue never
          acknowledged (its send raised) and that no earlier pass found
          listed (``was_stale``);
        - otherwise cancelled: the previous block's stop (cancelled by the
          roll, its read-back cut off), or a stop of this block that the venue
          took and no longer lists.

        A pass cut off after it saved a leg ``stopped`` and before it closed
        that stop's intent left the intent open: it is closed here first, as
        filled.
        """

        mine = f"lv{bid}-{s[:18]}-st"
        old = was.get("stop_key") if was.get("state") == "rolled" else None
        if leg is not None and leg.get("state") == "stopped" and leg.get("stop_key"):
            self._close_stop_intent(leg["stop_key"], "filled", exchange_status="triggered")
        rest = self._rest(bid, s, leg) if leg is not None and leg.get("state") == "held" and abs(q) <= QTY_TOL else None
        short_gone = rest is not None and rest < -QTY_TOL
        ledger = self.book.orders.state.order_ledger
        for key in sorted({k for k in before if k.startswith("lv")} | {mine} | ({old} if old else set())):
            if key in after:
                stale[key] = s
                self.book.audit("perp_lv_stop_not_cancelled", block=bid, symbol=s, key=key)
            elif tried.get(key):
                self._close_stop_intent(key, "cancelled")
            elif short_gone and leg is not None and key in (mine, old) and self._stop_live(key):
                # Gone without this flatten's cancel, with the venue flat and the leg still short once the block's own
                # fills are counted: it fired. The leg is saved first: a run cut off before the intent is closed leaves
                # a stopped leg, whose intent the next pass closes (above).
                src = was if key == old else leg
                leg.update(state="stopped", stopped_at=_now_iso(), stop_key=key)
                if src.get("stop_price"):
                    leg["stop_price"] = src["stop_price"]
                self.blocks.save()
                self._close_stop_intent(key, "filled", exchange_status="triggered")
                self.book.audit("perp_lv_stop", block=bid, symbol=s)
                short_gone = False
            elif (key == mine and key not in before and key not in was_stale
                  and str((ledger.get(key) or {}).get("status") or "") in ("submitted", "reconciling")):
                self._close_stop_intent(key, "rejected", error="block_failed")     # its send raised; never listed
            else:
                self._close_stop_intent(key, "cancelled")

    def _rest(self, bid: str, s: str, leg: dict) -> float | None:
        """The leg's quantity with the fills of this block's orders on ``s`` that it does not hold yet, stops aside.

        Counts filled quantities only, so it needs no price. A flatten calls it
        before ``_settle``, which would read prices: a buy-back that filled
        while its reply was lost, or filled without a price, is not on the leg
        yet, and must not be taken for a fired stop. None when one of those
        orders has no known outcome.
        """

        ledger = self.book.orders.state.order_ledger
        recorded = {r[0] for r in leg.get("fills") or []}
        prefix = f"lv{bid}-"
        qty = float(leg["qty"])
        for k, r in ledger.items():
            if not k.startswith(prefix) or k.endswith("-st") or k in recorded or str(r.get("symbol") or "") != s:
                continue
            if str(r.get("status") or "") not in TERMINAL:
                return None
            filled = float(r.get("filled_quantity") or 0.0)
            qty += filled if str(r.get("side") or "").lower() == "buy" else -filled
        return qty

    def _cancel_stale_stops(self, bid: str, b: dict) -> None:
        """Cancel again the stops a flatten left at the venue; close each intent once its stop is gone."""

        left: dict[str, str] = {}
        for key, s in sorted(b["stale_stops"].items()):
            try:
                self._cancel_stop(s, key)
            except Exception as exc:     # the block is flat: a stop it cannot cancel yet never stops the run
                left[key] = s
                self.book.audit("perp_lv_stop_not_cancelled", block=bid, symbol=s, key=key, error=type(exc).__name__)
        if left:
            b["stale_stops"] = left
        else:
            del b["stale_stops"]

    def _funding(self, b: dict) -> float | None:
        """Funding the venue booked on the block's symbols over its life, USDT; None if unreadable."""

        try:
            return self.venue.funding_paid(sorted(b["legs"]), int(b["entry_ms"]), int(datetime.now(UTC).timestamp() * 1000))
        except Exception as exc:  # a record, never a reason to stop a close
            self.book.audit("perp_lv_funding_unread", error=type(exc).__name__)
            return None
