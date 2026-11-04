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
explained. Fills and funding recorded here are returns of the LV forward read:
nothing computes them before that read (2028-11-13). Rules:
docs/operations/PERP_DEMO_RUNNER.md.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

from trading_agent.exchanges.models import OrderConstraintError
from trading_agent.execution.perp import rules
from trading_agent.execution.perp.book import (
    EQUITY_USE,
    QTY_TOL,
    Book,
    PairStore,
    PerpBookError,
    _now_iso,
)

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


def first_week(path: Path) -> str | None:
    """The paper book's first Monday: weeks before it are not missed, they never were."""

    return min((str(r["monday"]) for r in _picks(path) if r.get("monday")), default=None)


@dataclass
class LowVolBook:
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
        ledger = self.book.orders.state.order_ledger
        for n in range(100):
            key = f"lv{tag}-{symbol[:18]}-{kind}{n}"
            if key not in ledger and key not in self.book.orders.state.reserved_orders:
                return key
        raise PerpBookError(f"no free client key for {symbol}")

    def _close_stop_intent(self, key: str, final: str, **extra: Any) -> None:
        rec = self.book.orders.state.order_ledger.get(key)
        if rec is None or rec.get("status") not in ("submitted", "reconciling", "acknowledged", "open"):
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

    # ── each run, before the pair book's position check ────────────────────

    def check(self) -> None:
        """Flatten an interrupted roll, settle fired stops and closes, refuse a mismatch."""

        for bid, b in list(self.blocks.pairs.items()):
            if b.get("status") == "rolling":
                self.book.audit("perp_lv_roll_interrupted", block=bid)
                self._flatten(bid, b, reason="interrupted")
        act = self.active()
        if act is None:
            return
        bid, b = act
        positions = self.venue.positions()
        for s, leg in b["legs"].items():
            if leg["state"] != "held":
                continue
            pos = positions.get(s)
            q = pos.qty if pos is not None else 0.0
            closing = self.book.orders.state.order_ledger.get(leg.get("closing_key") or "")
            if abs(q) <= QTY_TOL and closing is not None and closing.get("status") == "filled":
                leg.update(state="closed", exit=closing.get("average_fill_price"))
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
        trading = self.venue.trading_symbols()
        taken = {p["symbol"] for p in self.book.pairs.open_pairs().values()} | {rules.HEDGE}
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
                    skipped[coin] = "held_by_pair_book"
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
        elif self.book_usd + 2 * self.book.pair_usd * len(self.book.pairs.open_pairs()) > EQUITY_USE * self.venue.equity_usdt():
            reason = "equity"
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
        if act is not None:
            act[1].update(status="closed", close_reason="roll", closed_at=_now_iso())
        self.blocks.pairs[bid] = nb
        self.blocks.seen.append(week)
        self.blocks.save()
        tag = bid
        try:
            if act is not None:
                for s, leg in act[1]["legs"].items():
                    if leg["state"] == "held" and leg["qty"] < 0:
                        self._cancel_stop(s, leg["stop_key"])
                    if leg["state"] == "held":
                        leg["state"] = "rolled"
            legs = nb["legs"]
            for s in nb["touched"]:
                legs[s] = {"qty": current.get(s, 0.0), "state": "held"}
            for s in target:
                if s not in current:
                    self.venue.prepare(s)
            # Reductions first: they free margin, and a flip goes through flat.
            for s in nb["touched"]:
                cur, tgt = legs[s]["qty"], target.get(s, 0.0)
                if abs(cur) <= QTY_TOL:
                    continue
                if tgt == 0.0 or (cur > 0) != (tgt > 0):
                    self._trade(tag, s, -cur, legs[s], exact=True)
                elif abs(tgt) < abs(cur):
                    self._trade(tag, s, tgt - cur, legs[s], price=marks[s])
            for s, tgt in target.items():
                cur = legs[s]["qty"]
                if abs(tgt) > abs(cur) + QTY_TOL:
                    self._trade(tag, s, tgt - cur, legs[s], price=marks[s])
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

    def _trade(self, tag: str, s: str, delta: float, leg: dict, *, price: float = 0.0, exact: bool = False) -> None:
        """Move one leg by ``delta`` (signed) at market; a reduction is reduce-only."""

        reduce = abs(leg["qty"] + delta) < abs(leg["qty"]) - QTY_TOL
        if not exact:
            try:
                qty = self.venue.normalize(s, abs(delta), price)
            except OrderConstraintError:
                self.book.audit("perp_lv_delta_skipped", symbol=s, reason="below_venue_minimum")
                return
        else:
            qty = abs(delta)
        key = self._fresh_key(tag, s, "c" if reduce else "o")
        if reduce:
            leg["closing_key"] = key
            self.blocks.save()
        res = self.book._market(s, "buy" if delta > 0 else "sell", qty, key, reduce_only=reduce)
        leg["qty"] += res["filled_qty"] if delta > 0 else -res["filled_qty"]
        if abs(leg["qty"]) <= QTY_TOL:
            leg["qty"] = 0.0
        leg.setdefault("fills", []).append([key, res["filled_qty"], res["avg_fill_price"]])
        leg["closing_key"] = None     # recorded: only a close cut off before this line is left to check()
        self.blocks.save()

    def _close(self, bid: str, b: dict, *, reason: str) -> None:
        """Both legs off at market, reduce-only, after each short's stop is cancelled."""

        for s, leg in b["legs"].items():
            if leg["state"] != "held":
                continue
            if leg["qty"] < 0:
                self._cancel_stop(s, leg["stop_key"])
            self._trade(bid, s, -leg["qty"], leg, exact=True)
            leg["state"] = "closed"
            self.blocks.save()
        b.update(status="closed", close_reason=reason, closed_at=_now_iso(), funding=self._funding(b))
        self.blocks.save()
        self.book.audit("perp_lv_closed", block=bid, reason=reason)

    def _flatten(self, bid: str, b: dict, *, reason: str) -> None:
        """Take off whatever the venue holds on the block's symbols, then mark it failed."""

        for s in b.get("touched") or list(b.get("legs") or {}):
            open_keys = self.venue.open_stop_keys(s)
            for key in open_keys:
                if key.startswith("lv"):
                    self.venue.cancel_stop(s, key=key)
                    self._close_stop_intent(key, "cancelled")
            mine = f"lv{bid}-{s[:18]}-st"
            if mine not in open_keys:   # sent with an unknown outcome, or never taken
                self._close_stop_intent(mine, "rejected", error="block_failed")
            pos = self.venue.positions().get(s)
            if pos is not None and abs(pos.qty) > QTY_TOL:
                key = self._fresh_key(bid, s, "f")
                self.book._market(s, "buy" if pos.qty < 0 else "sell", abs(pos.qty), key, reduce_only=True)
        for leg in (b.get("legs") or {}).values():
            leg["state"] = "closed"
        b.update(status="failed", close_reason=reason, closed_at=_now_iso())
        self.blocks.save()
        self.book.audit("perp_lv_flattened", block=bid, reason=reason)

    def _funding(self, b: dict) -> float | None:
        """Funding the venue booked on the block's symbols over its life, USDT; None if unreadable."""

        try:
            return self.venue.funding_paid(sorted(b["legs"]), int(b["entry_ms"]), int(datetime.now(UTC).timestamp() * 1000))
        except Exception as exc:  # a record, never a reason to stop a close
            self.book.audit("perp_lv_funding_unread", error=type(exc).__name__)
            return None
