"""Book FLOW01 on Binance Demo Trading: H-FLOW-BTC-01's primary cell as demo orders.

The cell (rule and provenance in ``tools/flowflush/rules.py``): at a 5-minute open interest stamp t, when
BTCUSDT's 60-minute log return and its 60-minute open interest change are both at or below their frozen
train 1% thresholds (a long flush), go long BTCUSDT and hold 4 h. After an event the next one needs
t > previous t + 5 min + 4 h. Research enters at the 1m open at t + 5 min; the demo enters at the first run
with now >= t + 5 min and now - t <= 20 min (``ENTRY_WINDOW_MIN``), otherwise the event is recorded stale.
The OI of stamp t is the snapshot the public endpoint takes at t + 5 min and publishes seconds later
(``tools/flowflush/feed.py``), so the demo always enters after the research price. A keyless 5-minute poll
(``needs_signed_run``, ``tools/flowflush/poll.py``) asks for a signed run only when one is needed.

States: idle -> open -> idle. Each run (``step``):

1. exit: an open leg is sold, its own quantity, reduce-only, at the first run with now >= entry + 4 h less
   ``EXIT_EARLY_MS`` (2.5 min, half the 5-minute cadence), also under the entry lock. The tolerance keeps a
   run's own start-up from pushing the exit to the next run: the 5-minute pre-check reads its clock seconds
   before the signed run's ``at``, which set the entry time;
2. halt: cumulative realized net after fees below -10% of the sleeve (-1,000 bps; register row 6) halts the
   book. A halted book trades nothing more until the operator moves its store aside. The sleeve is the size
   stored at the book's first run; with a different ``--flow01-usd`` a fresh event is skipped and recorded
   (``size_changed``) until the operator moves the store aside;
3. scan: when idle, not halted and without the entry lock, the stamps since the store's cursor are scanned
   (public data through ``feed``). The first unclustered event is entered when fresh, sized ``usd`` / mark,
   if the account-wide caps of ``risk.py`` allow it (otherwise skipped and recorded), or recorded stale.

Every event is recorded in the signed store (``flow01.json`` under the perp_demo root): t, ret60, doi60, the
detection delay, and for each order its client key, side, quantity, average fill, fees and mark at send,
with the mainnet 1m opens at t + 5 min and t + 5 min + 4 h (research entry and exit) when available. Each
fill made in this run also has ``sent_ms``, the wall clock just before its order went out (``ms`` is the
run's start), and the event keeps the 1m open of that minute for the entry and the exit.

Only BTCUSDT is traded; the pair book checks the venue's BTCUSDT against the pair hedges plus every BTC
book's leg. Orders go through the pair book's path with client keys "fb1-<t in minutes, hex>-o<n>"
(entry) and "-c<n>" (exit). ``plan``, ``step``, the audit events and ``counts`` carry counts, statuses,
quantities and keys only: the book's returns are sealed until its first look at 20 events. Rules:
docs/operations/PERP_DEMO_RUNNER.md. Research description, not advice.
"""

from __future__ import annotations

import time
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Callable, ClassVar, Protocol

from trading_agent.exchanges.models import OrderConstraintError
from trading_agent.execution.perp import risk, rules
from trading_agent.execution.perp.book import QTY_TOL, Book, PairStore, PerpBookError, _now_iso
from trading_agent.execution.perp.legs import DECIMALS, ledger_price

SYMBOL = rules.HEDGE                 # BTCUSDT, shared by the long-only BTC legs
MIN_MS = 60_000
STAMP_MS = 5 * MIN_MS
HOLD_MS = 4 * 60 * MIN_MS            # hold 4 h from the demo entry
EXIT_EARLY_MS = 150_000              # an exit is due from 2.5 min before entry + 4 h: half the 5-minute cadence
ENTRY_DELAY_MS = 5 * MIN_MS          # research enters at the 1m open at t + 5 min
ENTRY_WINDOW_MIN = 20                # the demo enters while now - t <= 20 min
ENTRY_WINDOW_MS = ENTRY_WINDOW_MIN * MIN_MS
CLUSTER_MS = STAMP_MS + HOLD_MS      # next event needs t > previous t + 5 min + 4 h
USD_CAP = 3000.0                     # --flow01-usd above this is refused
HALT_NET = -0.10                     # halt when cumulative realized net after fees < -10% of the sleeve
FEE_FALLBACK = 0.0005                # taker rate assumed for the halt when a fill reports no USDT fee
STORE = "flow01.json"
PREFIX = "fb1-"
STATE = "book"                       # the store's state record; events are keyed "e<t in minutes, hex>"
TERMINAL = ("filled", "cancelled", "rejected", "expired")


class Stamp(Protocol):
    t: int
    ret60: float
    doi60: float
    event: bool


class Look(Protocol):
    stamps: Sequence[Stamp]
    covered_from: int


class Feed(Protocol):
    """Public mainnet data (``tools.flowflush.feed.Feed``): no key."""

    def look(self, now_ms: int) -> Look: ...
    def open_at(self, t_ms: int) -> float | None: ...


def _iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, UTC).isoformat(timespec="seconds")


def event_id(t: int) -> str:
    return f"e{t // MIN_MS:x}"


def key_stem(eid: str, kind: str) -> str:
    """Client key stem of an event's entry ("o") or exit ("c"): "fb1-<t in minutes, hex>-o"; a rehearsal keeps its "r"."""

    return f"{PREFIX}{eid[1:] if eid.startswith('e') else eid}-{kind}"


def clustered(t: int, anchor: int | None) -> bool:
    return anchor is not None and t <= anchor + CLUSTER_MS


def unclustered(stamps: Sequence[Stamp], cursor: int | None, anchor: int | None) -> Iterator[Stamp]:
    """Events after the cursor that the previous event does not cluster, oldest first; each anchors the next."""

    for s in stamps:
        if cursor is not None and s.t <= cursor:
            continue
        if not s.event or clustered(s.t, anchor):
            continue
        yield s
        anchor = s.t


def fresh(t: int, now_ms: int) -> bool:
    return t + ENTRY_DELAY_MS <= now_ms and now_ms - t <= ENTRY_WINDOW_MS


def exit_due(ev: dict, now_ms: int) -> bool:
    """Is an open leg's exit due? From ``EXIT_EARLY_MS`` before entry + 4 h.

    ``exit_due_ms`` is the entry run's ``at`` + 4 h. The run four hours later starts at the same phase, but
    the 5-minute pre-check reads its clock before the signed start-up that set ``at``, and two hourly runs
    differ by seconds of start-up. Without the tolerance the exit would wait one cadence (4 h 05 min, or
    5 h on hourly runs alone).
    """

    return now_ms + EXIT_EARLY_MS >= int(ev.get("exit_due_ms") or 0)


def _state(store: PairStore) -> dict:
    return store.pairs.get(STATE) or {}


def _fee_usdt(fill: dict) -> float:
    fees = fill.get("fees") or {}
    if "USDT" in fees:
        return float(fees["USDT"])
    return FEE_FALLBACK * float(fill["qty"]) * float(fill["avg"])


def signed_run_reason(now_ms: int, store: PairStore, feed: Feed) -> str | None:
    """Why the 5-minute mode needs a signed run now, or None. Reads the store (unverified is fine) and
    public data only; no key. An exit due, an order left unsettled, or a fresh event."""

    st = _state(store)
    eid = st.get("event")
    if eid:
        ev = store.pairs.get(eid) or {}
        if ev.get("status") in ("opening", "closing"):
            return "settle"
        if ev.get("status") == "open" and exit_due(ev, now_ms):
            return "exit_due"
        if ev.get("status") == "open" and st.get("halted"):
            return "halted_open"
        return None
    if st.get("halted"):
        return None
    look = feed.look(now_ms)
    for s in unclustered(look.stamps, st.get("cursor"), st.get("last_event_t")):
        if fresh(s.t, now_ms):
            return "fresh_event"
    return None


def needs_signed_run(now_ms: int, store: PairStore, feed: Feed) -> bool:
    """True when an exit is due, an order is unsettled or a fresh event is present (``signed_run_reason``)."""

    return signed_run_reason(now_ms, store, feed) is not None


@dataclass
class FlowFlushBook:
    name: ClassVar[str] = "flow01"
    book: Book                    # the pair book's order path and venue
    store: PairStore              # signed; pairs[STATE] the book state, pairs[e...] the events, pairs[g...] gaps
    usd: float                    # --flow01-usd, the sleeve
    feed: Feed
    wall: Callable[[], float] = time.time    # the wall clock read just before an order goes out (``sent_ms``)

    @property
    def venue(self):
        return self.book.venue

    # ── state ──────────────────────────────────────────────────────────────

    def _st(self, now_ms: int | None = None) -> dict:
        st = self.store.pairs.get(STATE)
        if st is None:
            st = {"started_ms": now_ms, "usd": self.usd, "cursor": None, "last_event_t": None, "event": None,
                  "halted": None}
            if now_ms is not None:
                self.store.pairs[STATE] = st
        return st

    def _current(self) -> tuple[str, dict] | None:
        eid = _state(self.store).get("event")
        return (eid, self.store.pairs[eid]) if eid else None

    def held(self) -> dict[str, float]:
        """BTCUSDT this book holds at the venue."""

        cur = self._current()
        if cur is None or cur[1].get("status") not in ("open", "closing") or cur[1].get("qty", 0.0) <= QTY_TOL:
            return {}
        return {SYMBOL: float(cur[1]["qty"])}

    def counts(self) -> dict[str, Any]:
        """For status.json: counts and statuses only."""

        evs = [v for k, v in self.store.pairs.items() if k.startswith("e")]
        by: dict[str, int] = {}
        for v in evs:
            by[v["status"]] = by.get(v["status"], 0) + 1
        st = _state(self.store)
        return {"flow01_events": by, "flow01_open_legs": 1 if self.held() else 0,
                "flow01_halted": bool(st.get("halted")),
                "flow01_gaps": sum(1 for k in self.store.pairs if k.startswith("g"))}

    # ── each run, before the pair book's position check ────────────────────

    def check(self) -> None:
        """Settle an entry or exit cut off mid-order from the order ledger (after the reconcile)."""

        cur = self._current()
        if cur is None:
            return
        eid, ev = cur
        ledger = self.book.orders.state.order_ledger
        if ev["status"] == "opening":
            rec = ledger.get(ev["entry_key"])
            if rec is not None and rec.get("status") not in TERMINAL:
                raise PerpBookError(f"FLOW01 entry {ev['entry_key']} still {rec.get('status')}")
            filled = float((rec or {}).get("filled_quantity") or 0.0)
            if filled > QTY_TOL:
                ev["fills"].append(self._ledger_fill(ev["entry_key"], "buy", rec, ev.get("entry_mark")))
                ev.update(qty=filled, status="open")
                self.book.audit("perp_flow01_settled", id=eid, key=ev["entry_key"], status="open", qty=filled)
                if ev.get("kind") == "event" and ev.get("ref_entry_open") is None:
                    ev["ref_entry_open"] = self._ref(ev["t"] + ENTRY_DELAY_MS)
            else:
                ev.update(status="failed", reason="entry_not_filled")
                self._st()["event"] = None
                self.book.audit("perp_flow01_settled", id=eid, key=ev["entry_key"], status="failed")
            self.store.save()
        elif ev["status"] == "closing":
            rec = ledger.get(ev["exit_key"])
            if rec is not None and rec.get("status") not in TERMINAL:
                raise PerpBookError(f"FLOW01 exit {ev['exit_key']} still {rec.get('status')}")
            filled = float((rec or {}).get("filled_quantity") or 0.0)
            if filled > QTY_TOL:
                ev["fills"].append(self._ledger_fill(ev["exit_key"], "sell", rec, ev.get("exit_mark")))
                ev["qty"] = max(0.0, round(ev["qty"] - filled, DECIMALS))   # float drift would leave an unsellable step
            self._after_exit(eid, ev)
            self.book.audit("perp_flow01_settled", id=eid, key=ev["exit_key"], status=ev["status"], qty=filled)
            if ev["status"] == "closed" and ev.get("kind") == "event":
                ev["ref_exit_open"] = self._ref(ev["t"] + ENTRY_DELAY_MS + HOLD_MS)
            self.store.save()

    def _ledger_fill(self, key: str, side: str, rec: dict, mark: float | None) -> dict:
        avg, src = ledger_price(self.book, key, rec, mark)
        row = {"key": key, "side": side, "qty": float(rec.get("filled_quantity") or 0.0), "avg": avg,
               "fees": dict(rec.get("fees") or {}), "mark": mark, "from_ledger": True}
        if src:          # the ledger had no price: kept apart for the cost read
            row["avg_from"] = src
        return row

    def _after_exit(self, eid: str, ev: dict) -> None:
        if ev["qty"] <= QTY_TOL:
            ev.update(qty=0.0, status="closed")
            self._st()["event"] = None
        else:
            ev["status"] = "open"     # a partial or failed exit is retried at the next run

    # ── the run ────────────────────────────────────────────────────────────

    def plan(self, now_ms: int) -> dict:
        """What a run would do now. Nothing signed, sent or written."""

        st = _state(self.store)
        cur = self._current()
        out: dict[str, Any] = {"state": cur[1]["status"] if cur else "idle", "halted": bool(st.get("halted")),
                               "events": self.counts()["flow01_events"]}
        if st and self._size_changed():
            out["size_changed"] = True
        if cur is not None:
            out["exit_due"] = cur[1].get("status") == "open" and exit_due(cur[1], now_ms)
        elif not st.get("halted"):
            try:
                look = self.feed.look(now_ms)
                out["fresh_event"] = any(fresh(s.t, now_ms) for s in
                                         unclustered(look.stamps, st.get("cursor"), st.get("last_event_t")))
            except Exception as exc:   # a dry run reports, it never stops
                out["feed"] = type(exc).__name__
        return out

    def step(self, now_ms: int, *, entry_lock: str | None = None) -> dict:
        st = self._st(now_ms)
        out: dict[str, Any] = {"entered": 0, "closed": 0, "stale": 0, "skipped": 0, "gap_stamps": 0}
        cur = self._current()
        if cur is not None and cur[1]["status"] == "open" and (exit_due(cur[1], now_ms) or st.get("halted")):
            self._exit(*cur, now_ms, reason="halt" if st.get("halted") else "hold")
            out["closed"] = 1
        if not st.get("halted") and self._breached():
            st["halted"] = {"reason": "realized_net_below_limit", "at": _now_iso()}
            self.store.save()
            self.book.audit("perp_flow01_halted", reason="realized_net_below_limit")
            cur = self._current()
            if cur is not None and cur[1]["status"] == "open":
                self._exit(*cur, now_ms, reason="halt")
                out["closed"] += 1
        if self._current() is None and not st.get("halted") and not entry_lock:
            out.update(self._scan(st, now_ms))
        self.store.save()
        cur = self._current()
        out.update(state=cur[1]["status"] if cur else "idle", halted=bool(st.get("halted")))
        return out

    def _breached(self) -> bool:
        """Cumulative realized net after fees of the closed events, against the sleeve. Never exposed."""

        net = 0.0
        for k, ev in self.store.pairs.items():
            if not k.startswith("e") or ev.get("status") != "closed":
                continue
            for f in ev["fills"]:
                cash = float(f["qty"]) * float(f["avg"])
                net += (cash if f["side"] == "sell" else -cash) - _fee_usdt(f)
        return net < HALT_NET * self._sleeve()

    def _sleeve(self) -> float:
        """The size stored at the book's first run: the halt's base. A new size is a new book."""

        return float(_state(self.store).get("usd") or self.usd)

    def _size_changed(self) -> bool:
        return abs(self._sleeve() - self.usd) > 1e-9

    # ── events ─────────────────────────────────────────────────────────────

    def _scan(self, st: dict, now_ms: int) -> dict:
        out = {"entered": 0, "stale": 0, "skipped": 0, "gap_stamps": 0}
        try:
            look = self.feed.look(now_ms)
        except Exception as exc:   # public data: a failed look adds nothing and never stops the other books
            self.book.audit("perp_flow01_feed_failed", error=type(exc).__name__)
            return {**out, "feed_failed": 1}
        cursor, anchor = st.get("cursor"), st.get("last_event_t")
        if not look.stamps:
            self.book.audit("perp_flow01_feed_empty")
            return out
        if cursor is not None:
            lower = max(cursor, anchor + CLUSTER_MS if anchor is not None else cursor)
            if look.covered_from > lower + STAMP_MS:
                n = (look.covered_from - lower) // STAMP_MS - 1
                self.store.pairs[f"g{lower // MIN_MS:x}"] = {"kind": "gap", "from": lower + STAMP_MS,
                                                              "to": look.covered_from - STAMP_MS, "stamps": int(n)}
                self.book.audit("perp_flow01_gap", stamps=int(n))
                out["gap_stamps"] = int(n)
        stop: int | None = None
        for s in unclustered(look.stamps, cursor, anchor):
            if now_ms < s.t + ENTRY_DELAY_MS:
                stop = s.t        # not yet: the next run sees it again
                break
            st["last_event_t"] = s.t
            status = self._event(s, st, now_ms)
            out[status] = out.get(status, 0) + 1
            if status == "entered":
                break
        done = [s.t for s in look.stamps if stop is None or s.t < stop]
        if done:
            st["cursor"] = max(done + ([cursor] if cursor is not None else []))
        self.store.save()
        return out

    def _event(self, s: Stamp, st: dict, now_ms: int) -> str:
        eid = event_id(s.t)
        ev: dict[str, Any] = {"kind": "event", "t": s.t, "t_iso": _iso(s.t), "ret60": s.ret60, "doi60": s.doi60,
                              "seen_ms": now_ms, "delay_s": (now_ms - s.t) / 1000,
                              "lag_s": (now_ms - s.t - ENTRY_DELAY_MS) / 1000, "qty": 0.0, "fills": [],
                              "usd": self.usd, "status": "seen"}
        self.store.pairs[eid] = ev
        self.store.seen.append(eid)
        if not fresh(s.t, now_ms):
            started = st.get("started_ms") or now_ms
            ev["status"] = "before_start" if s.t + ENTRY_WINDOW_MS < started else "stale"
            self.store.save()
            self.book.audit("perp_flow01_event", id=eid, status=ev["status"])
            return "stale"
        if not 0 < self.usd <= USD_CAP:
            raise PerpBookError(f"FLOW01 sleeve {self.usd} outside (0, {USD_CAP}]")
        if self._size_changed():      # the halt measures the stored size: the operator moves flow01.json aside
            return self._skip(eid, ev, "size_changed")
        mark = self.venue.mark_price(SYMBOL)
        try:
            qty = self.venue.normalize(SYMBOL, self.usd / mark, mark)
        except OrderConstraintError:
            return self._skip(eid, ev, "below_venue_minimum")
        why = risk.room(self.venue).binding(qty * mark)
        if why is not None:
            return self._skip(eid, ev, why)
        self.venue.prepare(SYMBOL)
        key = self.book.fresh_key(key_stem(eid, "o"))
        ev.update(status="opening", entry_key=key, entry_mark=mark, entry_ms=now_ms, exit_due_ms=now_ms + HOLD_MS)
        st["event"] = eid
        self.store.save()
        sent = self._sent_ms()
        res = self.book._market(SYMBOL, "buy", qty, key, reduce_only=False)
        ev["fills"].append({"key": key, "side": "buy", "qty": res["filled_qty"], "avg": res["avg_fill_price"],
                            "fees": res.get("fees") or {}, "mark": mark, "ms": now_ms, "sent_ms": sent})
        ev.update(qty=res["filled_qty"], status="open")
        self.store.save()
        self.book.audit("perp_flow01_entered", id=eid, key=key, qty=res["filled_qty"])
        # research references, read after the order so a slow call never delays it
        ev["ref_entry_open"] = self._ref(s.t + ENTRY_DELAY_MS)
        ev["ref_send_minute_open"] = self._ref(sent // MIN_MS * MIN_MS)
        self.store.save()
        return "entered"

    def _sent_ms(self) -> int:
        """The wall clock now, in ms: the minute an order goes out, which can be minutes after the run's ``at``."""

        return int(self.wall() * 1000)

    def _skip(self, eid: str, ev: dict, reason: str) -> str:
        ev.update(status="skipped", reason=reason)
        self.store.save()
        self.book.audit("perp_flow01_event", id=eid, status="skipped", reason=reason)
        return "skipped"

    def _exit(self, eid: str, ev: dict, now_ms: int, *, reason: str) -> None:
        qty = float(ev["qty"])
        key = self.book.fresh_key(key_stem(eid, "c"))
        try:
            mark = self.venue.mark_price(SYMBOL)
        except Exception:      # a record; never a reason to keep a leg
            mark = None
        ev.update(status="closing", exit_key=key, exit_mark=mark, exit_reason=reason, exit_ms=now_ms)
        self.store.save()
        sent = self._sent_ms()
        res = self.book._market(SYMBOL, "sell", qty, key, reduce_only=True)
        ev["fills"].append({"key": key, "side": "sell", "qty": res["filled_qty"], "avg": res["avg_fill_price"],
                            "fees": res.get("fees") or {}, "mark": mark, "ms": now_ms, "sent_ms": sent})
        ev["qty"] = max(0.0, round(qty - res["filled_qty"], DECIMALS))
        self._after_exit(eid, ev)
        self.store.save()
        self.book.audit("perp_flow01_closed", id=eid, reason=reason, key=key, qty=res["filled_qty"])
        if ev.get("kind") == "event":
            ev["ref_exit_open"] = self._ref(ev["t"] + ENTRY_DELAY_MS + HOLD_MS)
            ev["ref_exit_send_minute_open"] = self._ref(sent // MIN_MS * MIN_MS)
            self.store.save()

    def _ref(self, t_ms: int) -> float | None:
        """A mainnet 1m open for the record; None when it cannot be read."""

        try:
            return self.feed.open_at(t_ms)
        except Exception as exc:
            self.book.audit("perp_flow01_ref_unread", error=type(exc).__name__)
            return None

    # ── rehearsal ──────────────────────────────────────────────────────────

    def rehearse(self, now_ms: int, hold_min: int = 10) -> dict:
        """Buy the sleeve now and sell it after ``hold_min`` minutes, to test the mechanics before an event.

        Recorded with kind "rehearsal": never an event of the read and never in the halt.
        """

        st = self._st(now_ms)
        if self._current() is not None or st.get("halted"):
            raise PerpBookError("FLOW01 rehearses only when idle and not halted")
        if not 0 < self.usd <= USD_CAP:
            raise PerpBookError(f"FLOW01 sleeve {self.usd} outside (0, {USD_CAP}]")
        mark = self.venue.mark_price(SYMBOL)
        qty = self.venue.normalize(SYMBOL, self.usd / mark, mark)
        why = risk.room(self.venue).binding(qty * mark)
        if why is not None:
            return {"rehearsal": "skipped", "reason": why}
        rid = f"r{now_ms // MIN_MS:x}"
        self.venue.prepare(SYMBOL)
        key = self.book.fresh_key(key_stem(rid, "o"))
        ev = {"kind": "rehearsal", "t": now_ms, "qty": 0.0, "fills": [], "status": "opening", "entry_key": key,
              "entry_mark": mark, "entry_ms": now_ms, "exit_due_ms": now_ms + hold_min * MIN_MS}
        self.store.pairs[rid] = ev
        st["event"] = rid
        self.store.save()
        sent = self._sent_ms()
        res = self.book._market(SYMBOL, "buy", qty, key, reduce_only=False)
        ev["fills"].append({"key": key, "side": "buy", "qty": res["filled_qty"], "avg": res["avg_fill_price"],
                            "fees": res.get("fees") or {}, "mark": mark, "ms": now_ms, "sent_ms": sent})
        ev.update(qty=res["filled_qty"], status="open")
        self.store.save()
        self.book.audit("perp_flow01_entered", id=rid, key=key, qty=res["filled_qty"], rehearsal=True)
        return {"rehearsal": rid, "qty": res["filled_qty"]}
