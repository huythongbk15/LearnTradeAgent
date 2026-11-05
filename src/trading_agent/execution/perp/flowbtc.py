"""Book FLOW02 on Binance Demo Trading: H-FLOW-BTC-02 as orders, long BTCUSDT or flat, daily.

The research rule (data/flow_btc_20261104_02, ridge on the FULL feature set,
long-only, 24 h; register row 4) as a book beside the pair book:

- **Decision:** the hourly bar opening 00:00 UTC (features through its close at
  01:00). The call comes from ``tools.flowbtc.signal.Signal``: long iff the
  quarter model's prediction is above its threshold, else flat. Research
  enters at the 01:00 open; the demo enters at the first run in
  [01:00, 03:00), normally the 01:15 run, so about 15 minutes later.
- **Trade:** long and flat buys ``usd / mark`` BTCUSDT, normalized. Long and
  already long keeps the position (no trade). Flat sells the book's own
  quantity, reduce-only. A day whose window passes without a call is
  recorded missed, and a held position is sold at that window end.
- **Shared BTCUSDT:** the book records its own quantity; the pair book checks
  that the venue holds the pair hedges plus every BTC book's quantity.
- **Caps:** a buy must fit ``risk.room``; when it does not, the day's entry is
  skipped and recorded (``equity``). Sells are never blocked.
- **Halt (register row 4, drawdown):** the sleeve value is ``usd`` plus
  realized and unrealized at mark less fees; funding is left out. When it
  falls below 65% of its high the book sells its quantity, trades nothing
  more and records ``halted``. It still appends public data to its data
  store once a day, so a resume finds no gap. Resuming is the operator's
  call: move ``flow02.json`` aside so a fresh store starts.
- **A new size is a new book:** the halt measures the ``usd`` stored at the
  book's first run. With a different ``--flow02-usd`` a long call on a flat
  book buys nothing and is recorded skipped (``size_changed``); sells still
  run. Move ``flow02.json`` aside to start at the new size.
- **Records** (signed ``PairStore``, ``flow02.json``): each day's call, the
  model, the prediction and threshold, the action and its key; each order's
  key, side, quantity, average fill, fees and the mark at send. The cost and
  futility rules of register row 4 are read from these later.
- **Blinding:** returns are sealed until the book's first look, after 365
  decisions. ``plan``, ``step`` and the audit events carry counts, statuses,
  quantities and keys only; the halt exposes only "halted" or "ok".

Rules: docs/operations/PERP_DEMO_RUNNER.md.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, ClassVar, Protocol

from trading_agent.exchanges.models import OrderConstraintError
from trading_agent.execution.live_safety import ORDER_LEDGER_TERMINAL_STATUSES
from trading_agent.execution.perp import risk, rules
from trading_agent.execution.perp.book import QTY_TOL, Book, PairStore, PerpBookError
from trading_agent.execution.perp.legs import DECIMALS, fill_record, ledger_price

HOUR_MS = rules.HOUR_MS
DAY_MS = 24 * HOUR_MS
ENTRY_H = 1             # the decision bar opens 00:00 and closes 01:00; research enters at the 01:00 open
WINDOW_H = 2            # the first run in [01:00, 03:00) trades; later the day is missed
USD_CAP = 3000.0        # a sleeve above this notional is refused
HALT_AT = 0.65          # halt when the sleeve value falls below 65% of its high (register row 4: 35% drawdown)
PREFIX = "fb2-"
BTC = rules.HEDGE
STATE = "book"          # the store's record of the sleeve; day records are "d<YYYY-MM-DD>"


class Signal(Protocol):
    """``tools.flowbtc.signal.Signal``: the store and the frozen model."""

    def update(self, now_ms: int) -> dict: ...
    def decide(self, day_ms: int, *, record: bool) -> dict: ...


def day_iso(day_ms: int) -> str:
    return datetime.fromtimestamp(day_ms / 1000, UTC).date().isoformat()


def _iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, UTC).strftime("%Y-%m-%dT%H:%MZ")


def window(day_ms: int) -> tuple[int, int]:
    start = day_ms + ENTRY_H * HOUR_MS
    return start, start + WINDOW_H * HOUR_MS


@dataclass
class FlowBtcBook:
    name: ClassVar[str] = "flow02"
    book: Book               # the pair book's order path and venue
    store: PairStore         # signed; "book" holds the sleeve, "d<day>" each day, ``seen`` the handled days
    usd: float
    signal: Signal

    @property
    def venue(self):
        return self.book.venue

    # ── state ──────────────────────────────────────────────────────────────

    def _state(self, now_ms: int | None = None) -> dict | None:
        st = self.store.pairs.get(STATE)
        if st is None and now_ms is not None:
            st = {"usd": self.usd, "started_ms": now_ms, "high": self.usd, "status": "ok",
                  "leg": {"qty": 0.0, "fills": [], "pending": None}}
            self.store.pairs[STATE] = st
            self.store.save()
        return st

    def held(self) -> dict[str, float]:
        """This book's BTCUSDT quantity; the pair book counts it in the venue's BTCUSDT sum."""

        st = self.store.pairs.get(STATE)
        q = float(st["leg"]["qty"]) if st else 0.0
        return {BTC: q} if abs(q) > QTY_TOL else {}

    def _qty(self) -> float:
        st = self._state()
        return float(st["leg"]["qty"]) if st else 0.0

    def _day(self, day: str) -> dict:
        return self.store.pairs.setdefault(f"d{day}", {"day": day, "status": "pending", "attempts": []})

    # ── orders ─────────────────────────────────────────────────────────────

    def _apply_fill(self, leg: dict, key: str, side: str, res: dict, mark: float) -> None:
        q = float(res["filled_qty"])
        # Rounded as legs.LegTrader does: 0.013 - 0.010 is 0.0029999999999999996, which ccxt would truncate
        # to 0.002 and leave a step behind that the book could never sell.
        leg["qty"] = round(float(leg["qty"]) + (q if side == "buy" else -q), DECIMALS)
        if abs(leg["qty"]) <= QTY_TOL:
            leg["qty"] = 0.0
        leg["fills"].append(fill_record(key, side, res, mark))

    def _trade(self, day: str, side: str, qty: float, mark: float, note: tuple[dict, str] | None = None) -> str:
        """One market order for this book's leg; a sell is reduce-only.

        The key is marked pending on the leg (and written to ``note``, a record
        and its field) before the order is sent, so ``check`` can settle a fill
        a crash left unrecorded.
        """

        st = self._state()
        leg = st["leg"]
        reduce = side == "sell"
        key = self.book.fresh_key(f"{PREFIX}{day[2:].replace('-', '')}-{'c' if reduce else 'o'}")
        leg["pending"] = {"key": key, "side": side, "mark": mark}
        if note is not None:
            note[0][note[1]] = key
        self.store.save()
        res = self.book._market(BTC, side, qty, key, reduce_only=reduce)
        self._apply_fill(leg, key, side, res, mark)
        leg["pending"] = None
        self.store.save()
        return key

    def _sell_all(self, day: str, note: tuple[dict, str] | None = None) -> str | None:
        qty = self._qty()
        if qty <= QTY_TOL:
            return None
        return self._trade(day, "sell", qty, self.venue.mark_price(BTC), note)

    def _close(self, st: dict, day: str, field: str) -> str | None:
        """Sell the book's quantity for ``day``'s flat call or miss, until it is flat.

        The duty (``close_due``: the day and the day record's key field) is
        saved before the sell. A sell that fails before its key is reserved
        (a ticker timeout, say), or that the venue answers expired or
        cancelled, leaves the duty, and the next run sells again; a
        reduce-only sell of the book's own quantity is safe to repeat. A sell
        cut off after its reserve is settled by ``check`` first. A sell the
        venue refuses with an error (Binance -2022 or -2019, say) is not
        retried: ccxt raises it, ``Book._send`` records an unknown outcome,
        and the next reconcile, not finding the order at the venue, stops
        every run for operator review, as for any order whose send raised.
        """

        if self._qty() <= QTY_TOL:
            st.pop("close_due", None)
            self.store.save()
            return None
        st["close_due"] = {"day": day, "field": field}
        self.store.save()
        key = self._sell_all(day, (self._day(day), field))
        if self._qty() <= QTY_TOL:
            st.pop("close_due", None)
            self.store.save()
        return key

    # ── each run, before the pair book's position check ────────────────────

    def check(self) -> None:
        """Settle an order cut off before its fill was recorded, from the order ledger reconcile() settled."""

        st = self._state()
        if st is None:
            return
        leg = st["leg"]
        p = leg.get("pending")
        if p:
            rec = self.book.orders.state.order_ledger.get(p["key"])
            status = str((rec or {}).get("status") or "")
            if rec is not None and status not in ORDER_LEDGER_TERMINAL_STATUSES:
                raise PerpBookError(f"FLOW02 order {p['key']} still {status}; operator review required")
            filled = float((rec or {}).get("filled_quantity") or 0.0)
            if filled > QTY_TOL:
                avg, src = ledger_price(self.book, p["key"], rec, p.get("mark"))
                res = {"filled_qty": filled, "avg_fill_price": avg, "fees": dict(rec.get("fees") or {})}
                self._apply_fill(leg, p["key"], p["side"], res, float(p.get("mark") or 0.0))
                if src:          # the ledger had no price: kept apart for the cost read
                    leg["fills"][-1]["avg_from"] = src
            leg["pending"] = None
            self.store.save()
            self.book.audit("perp_flow02_settled", key=p["key"], status=status or "never_reserved")
        if float(leg["qty"]) < -QTY_TOL:
            raise PerpBookError("FLOW02 holds a negative BTCUSDT quantity; operator review required")

    # ── halt ───────────────────────────────────────────────────────────────

    def _value(self, st: dict, mark: float) -> float:
        """Sleeve value: usd + cash from fills + quantity at mark - USDT fees. Funding is left out. Never exposed."""

        cash = fees = 0.0
        for f in st["leg"]["fills"]:
            amount = float(f["qty"]) * float(f["avg"])
            cash += amount if f["side"] == "sell" else -amount
            fees += sum(float(v) for k, v in (f.get("fees") or {}).items() if str(k).upper() == "USDT")
        return float(st["usd"]) + cash + float(st["leg"]["qty"]) * mark - fees

    def _breached(self, st: dict) -> bool:
        qty = float(st["leg"]["qty"])
        v = self._value(st, self.venue.mark_price(BTC) if qty > QTY_TOL else 0.0)
        if v > float(st["high"]):
            st["high"] = v
            self.store.save()
        return v < HALT_AT * float(st["high"])

    def _halt(self, st: dict, day: str, reason: str, now_ms: int) -> None:
        st.update(status="halted", halt_reason=reason, halted_at=_iso(now_ms))
        self.store.save()
        self.book.audit("perp_flow02_halted", reason=reason)
        self._sell_all(day)

    # ── the day ────────────────────────────────────────────────────────────

    def plan(self, now_ms: int) -> dict:
        """What a run would do now, without orders or writes: statuses and counts only."""

        st = self.store.pairs.get(STATE) or {}
        day_ms = now_ms // DAY_MS * DAY_MS
        day = day_iso(day_ms)
        start, end = window(day_ms)
        qty = float((st.get("leg") or {}).get("qty") or 0.0)
        out: dict[str, Any] = {"day": day, "status": st.get("status", "ok"), "in_window": start <= now_ms < end,
                               "decided": day in self.store.seen, "held": int(qty > QTY_TOL)}
        if st and abs(float(st["usd"]) - self.usd) > 1e-9:
            out["size_changed"] = True
        if out["in_window"] and not out["decided"] and out["status"] == "ok":
            sig = self.signal.decide(day_ms, record=False)
            if sig.get("ok"):
                out.update(target="long" if sig["long"] else "flat", model=sig["model"])
            else:
                out["missing"] = list(sig.get("missing") or [])
        return out

    def step(self, now_ms: int, *, entry_lock: str | None = None) -> dict:
        """Halt check, missed days, then today's call inside its window. Sells also run under the entry lock."""

        if not 0 < self.usd <= USD_CAP:
            raise PerpBookError(f"FLOW02 notional {self.usd} outside (0, {USD_CAP}]")
        st = self._state(now_ms)
        day_ms = now_ms // DAY_MS * DAY_MS
        day = day_iso(day_ms)
        out: dict[str, Any] = {"day": day}
        if st["status"] != "halted" and self._breached(st):
            self._halt(st, day, "drawdown", now_ms)
        if st["status"] == "halted":
            self._sell_all(day)          # a halt close cut off is finished here
            self._keep_store(st, day_ms, now_ms)
            out.update(status="halted", held=int(self._qty() > QTY_TOL))
            return out
        due = st.get("close_due")
        if due:                          # a sell an earlier run could not send is sent now
            out["closed"] = bool(self._close(st, due["day"], due["field"]))
        out["missed"] = self._miss_past(now_ms, st)
        start, end = window(day_ms)
        if start <= now_ms < end and day not in self.store.seen and end > int(st["started_ms"]):
            out.update(self._decide(day_ms, now_ms, entry_lock))
        out.update(status="ok", held=int(self._qty() > QTY_TOL), qty=self._qty())
        return out

    def _keep_store(self, st: dict, day_ms: int, now_ms: int) -> None:
        """A halted book still appends public data once a day, in its window, and never trades.

        The 5 m series keep about 30 days at Binance; without this a halt
        longer than that would leave a gap a resumed book's features and
        later fits would see.
        """

        start, end = window(day_ms)
        day = day_iso(day_ms)
        if not start <= now_ms < end or st.get("store_day") == day:
            return
        try:
            self.signal.update(now_ms)
        except Exception as exc:   # public data only; a later run in the window tries again
            self.book.audit("perp_flow02_update_failed", error=type(exc).__name__)
            return
        st["store_day"] = day
        self.store.save()

    def _miss_past(self, now_ms: int, st: dict) -> int:
        """Record every counted day whose window ended without a call; a held position is sold."""

        seen = set(self.store.seen)
        d = int(st["started_ms"]) // DAY_MS * DAY_MS
        n = 0
        while window(d)[1] <= now_ms:
            day = day_iso(d)
            if day not in seen and window(d)[1] > int(st["started_ms"]):
                rec = self._day(day)
                attempts = rec.get("attempts") or []
                reason = attempts[-1]["why"] if attempts else "no_run"
                rec.update(status="missed", reason=reason, missed_at=_iso(now_ms))
                self.store.seen.append(day)
                key = self._close(st, day, "close_key")      # saves the miss with the sell duty first
                self.book.audit("perp_flow02_day_missed", day=day, reason=reason, closed=bool(key))
                n += 1
            d += DAY_MS
        return n

    def _attempt(self, day: str, why: str, now_ms: int) -> None:
        self._day(day).setdefault("attempts", []).append({"at": _iso(now_ms), "why": why})
        self.store.save()

    def _decide(self, day_ms: int, now_ms: int, entry_lock: str | None) -> dict:
        day = day_iso(day_ms)
        try:
            self.signal.update(now_ms)
        except Exception as exc:   # public data only; the call below sees what the store has
            self.book.audit("perp_flow02_update_failed", error=type(exc).__name__)
        try:
            sig = self.signal.decide(day_ms, record=True)
        except Exception as exc:   # a data or model fault stays in this book: it waits, then misses the day
            self.book.audit("perp_flow02_signal_failed", error=type(exc).__name__)
            sig = {"ok": False, "missing": [f"error_{type(exc).__name__}"]}
        if not sig.get("ok"):
            missing = list(sig.get("missing") or [])
            self._attempt(day, "missing:" + ",".join(missing), now_ms)
            return {"waiting": "missing", "missing": missing}
        long = bool(sig["long"])
        held = self._qty() > QTY_TOL
        if long and not held and entry_lock:
            self._attempt(day, "entry_lock", now_ms)     # an addition waits; at the window end the day is missed
            return {"waiting": "entry_lock", "target": "long"}
        rec = self._day(day)
        rec.update(status="decided", target="long" if long else "flat", pred=float(sig["pred"]),
                   threshold=float(sig["threshold"]), model=sig["model"], nan=int(sig.get("nan", 0)),
                   decided_at=_iso(now_ms), bar_ms=day_ms)
        action, reason = "none", None
        if long and held:
            action = "hold"
        elif not long and held:
            action = "sell"
        elif long and abs(float(self._state()["usd"]) - self.usd) > 1e-9:
            # The halt measures the size stored at the book's first run. A new size is a new book:
            # the operator moves flow02.json aside. Sells still run.
            action, reason = "skipped", "size_changed"
        elif long:
            mark = self.venue.mark_price(BTC)
            try:
                qty = self.venue.normalize(BTC, self.usd / mark, mark)
            except OrderConstraintError:
                qty = 0.0
            if qty <= QTY_TOL:
                action, reason = "skipped", "below_venue_minimum"
            else:
                reason = risk.room(self.venue).binding(qty * mark)
                action = "skipped" if reason else "buy"
        rec.update(action=action)
        if reason:
            rec["reason"] = reason
        if action == "buy":
            self.venue.prepare(BTC)      # cross margin, 1x
        self.store.seen.append(day)
        if action == "sell":
            self._state()["close_due"] = {"day": day, "field": "key"}   # a sell that fails is sent again
        self.store.save()     # the day is handled before any order: a crash cannot buy it twice
        if action == "buy":
            self._trade(day, "buy", qty, mark, (rec, "key"))
        elif action == "sell":
            self._close(self._state(), day, "key")
        if action == "skipped":
            self.book.audit("perp_flow02_entry_skipped", day=day, reason=reason)
        self.book.audit("perp_flow02_decided", day=day, target=rec["target"], action=action, model=sig["model"])
        out: dict[str, Any] = {"target": rec["target"], "action": action}
        if reason:
            out["reason"] = reason
        return out


def status(store: PairStore) -> dict:
    """Counts for status.json: no return, price or USD amount."""

    st = store.pairs.get(STATE) or {}
    days = [r for k, r in store.pairs.items() if k.startswith("d")]
    return {
        "flow02_status": st.get("status", "ok"),
        "flow02_held": int(float((st.get("leg") or {}).get("qty") or 0.0) > QTY_TOL),
        "flow02_days": {k: sum(1 for r in days if r.get("status") == k) for k in ("decided", "missed", "pending")},
        "flow02_long_days": sum(1 for r in days if r.get("status") == "decided" and r.get("target") == "long"),
        "flow02_skipped": sum(1 for r in days if r.get("action") == "skipped"),
        "flow02_orders": len((st.get("leg") or {}).get("fills") or []),
    }
