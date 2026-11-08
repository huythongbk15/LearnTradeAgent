"""Book KRW on Binance Demo Trading: H-KRW-FADE-16 traded as tested, beside a shadow row of every event.

The research (``data/krw_fade_20261108_01``, git-ignored; verdict KEEP: a forward record and a Demo discussion, never
money): after an Upbit KRW-market listing notice, short the coin's Binance USDT perp and hold an equal long BTCUSDT
for 72 h, with a stop at 1.5x. The event rule is ``tools/krwfade/rules.py``; the research trade, recomputed on public
mainnet data after the hold, is ``tools/krwfade/shadow.py``.

- **Feed:** each run reads page 1 of Upbit's trade notices (``tools/krwfade/feed.py``). A KRW-listing notice whose
  token passes the rule becomes an event with its entry time E (notice + 10 min, up to the hour), kept in this book's
  signed store with every notice id seen and each token's KRW notices (time, id, ``amb``, ``cancelled``). Notices
  whose E + 72 h falls before 2026-11-02 belong to the research sample and are not events (``FORWARD_FROM_MS``). A
  feed failure is recorded; exits and the shadow still run.
- **Research read the final titles:** a notice already seen is read again on every page that holds it. A follow-up
  that cancels the token's listing, or a title that no longer lists it on KRW, marks its event ``excluded`` (a
  waiting event becomes ``cancelled`` and is never traded). A non-ambiguous KRW notice of a token marks the token's
  ambiguous events more than a day older ``excluded`` (classify.py's reading, ``rules.resolves``). Neither those
  notices nor cancelled listings chain the 7-day merge (``rules.chain``). K1 and the read leave excluded events out.
- **Entry window:** a run with E <= now <= E + 15 min trades the event on Demo when the alt perp trades on the demo
  venue, no other book holds it (``Book.taken``) and this book holds nothing on it, the venue lists no stop on it
  (another book's left-over stop would buy the short back), the book is not halted, fewer than ``KRW_MAX_OPEN``
  events hold legs, and ``risk.room`` takes a short of ``usd`` on the alt and a long of ``usd`` on BTCUSDT. Otherwise
  the event is ``shadow_only`` with the reason. A run under the entry lock leaves it waiting; an event no run decided
  by E + 15 min is ``missed`` (``shadow_only`` with ``halted`` while the book is halted: the 5-minute pre-check does
  not sign for it then). The shadow records every event either way.
- **Entry orders**, the pair book's pattern (``Book.enter``) in this order: sell ``usd / mark`` of the alt at market;
  an exchange STOP_MARKET buy, reduce-only, at 1.5x the alt's average fill (key ``krw-<notice>-<TOKEN>-st``); buy
  ``usd / BTC mark`` of BTCUSDT at market (to the nearest step). If anything fails after the alt sell went out, the
  run audits ``perp_krw_unprotected``, buys back the short the venue holds (reduce-only) before the stop is settled,
  sells the BTC it bought, marks the event ``failed`` and stops. A stop that fired before the take-off read the
  positions is recorded as check() records one. Legs a failure leaves are unwound by later runs, and a failed event
  is ``unwound`` once a signed run finds it flat.
- **Exit:** due from E + 72 h less 2.5 min (``EXIT_EARLY_MS``, half the 5-minute cadence), or at once after a fired
  stop; a late run exits at once. The alt is bought back reduce-only; its stop is cancelled only after that buy
  filled in full (a buy-back that fails, loses its reply or fills in part leaves the stop at the venue); then the
  book's own BTCUSDT quantity is sold reduce-only. Every try has a fresh key; a cut-off try is settled from the
  order ledger first (``legs.ledger_price``), and an event whose last order was settled that way is closed by the
  next step. Exits run under the entry lock and while halted.
- **Stop fired:** ``check()`` finds the alt flat at the venue with its stop gone, as book LV does; the event is
  ``stopped`` and its BTC leg is sold by the step. The stop's fill is read back by its key when the venue answers,
  else taken at 1.05x the trigger (the research's 1.575x entry), marked ``avg_from``.
- **Halt (K3):** cumulative realized net of the Demo round trips of the sleeve (events flat by their fills: closed,
  failed, or closing with a stop left at the venue), from fill prices and USDT fees (0.05% a side when a fill
  reports none), below -15% of the sleeve (4 x ``usd``, stored at the first run) halts new Demo entries. Funding is
  not included. Open events still exit at their time; the shadow goes on. Resuming is the operator's call: a run
  with ``resume`` (``--krw-resume``) while the book is flat starts a new sleeve (``usd`` stored again, the halt
  cleared, only events decided from then on counted), and keeps the forward record. A different ``--krw-usd`` is
  a new sleeve: new events are ``shadow_only`` (``size_changed``) until such a run.
- **Shadow:** from E + 73 h each event's research row is computed from public 1h klines and funding and stored on
  the event; a run that cannot is retried. It is ``shadow_failed``, with the reason, only after 3 failed tries and 7
  days from the first; a 418 or 429 (``FetchStop``) ends the run's shadow and never counts as a failure. Only the
  shadow rows and the fills hold prices; nothing reads them back into a summary before the K1 look.

It is an ``OtherBook`` (book.py) named "krw", last in the run order. Its alt shorts are exclusive; its BTCUSDT longs
are summed into the venue's BTCUSDT by the pair book. Orders go through the pair book's path with keys
"krw-<notice id>-<TOKEN>-" plus "o<n>" (alt sell), "st" (stop), "b<n>" (BTC buy), "c<n>" (alt buy-back) and "x<n>"
(BTC sell). ``plan``, ``step``, ``counts`` and the audit events carry counts, statuses, quantities and keys only.
Store: ``krw.json`` under the perp_demo root. Rules and the proposed kill rules: docs/operations/PERP_DEMO_RUNNER.md,
"Book KRW". Research description, not advice.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ClassVar

from trading_agent.exchanges.models import OrderConstraintError
from trading_agent.execution.live_safety import LiveSafetyError
from trading_agent.execution.perp import risk, rules
from trading_agent.execution.perp.book import QTY_TOL, Book, PairStore, PerpBookError, _now_iso
from trading_agent.execution.perp.legs import DECIMALS, fill_record, ledger_price

BTC = rules.HEDGE
MIN_MS = 60_000
HOUR_MS = rules.HOUR_MS
DAY_MS = 24 * HOUR_MS
HOLD_MS = 72 * HOUR_MS                # research exit: the 1h open at E + 72 h
ENTRY_WINDOW_MS = 15 * MIN_MS         # Demo enters at a run with E <= now <= E + 15 min
EXIT_EARLY_MS = 150_000               # an exit is due from 2.5 min before E + 72 h: half the 5-minute cadence
STOP_X = 1.5                          # exchange stop at 1.5x the alt's average fill
STOP_FILL_X = 1.05                    # a fired stop's fill when the venue does not report it: the research's 1.575 / 1.5
KRW_MAX_OPEN = 4                      # Demo events holding legs at once
USD_CAP = 2000.0                      # --krw-usd above this is refused
SLEEVE_EVENTS = 4                     # the halt's sleeve: 4 x usd
HALT_NET = -0.15                      # halt new entries when realized net < -15% of the sleeve
FEE_FALLBACK = 0.0005                 # taker rate assumed for the halt when a fill reports no USDT fee
SHADOW_AFTER_MS = 73 * HOUR_MS        # the shadow row is computed from E + 73 h (the exit bar has closed)
SHADOW_GIVE_UP_MS = 7 * DAY_MS        # ... and marked shadow_failed 7 days after its first failed try,
SHADOW_MIN_FAILS = 3                  # ... once it has failed on at least this many runs
FORWARD_FROM_MS = 1_793_577_600_000   # 2026-11-02 00:00 UTC: research events end with E + 72 h before it
STORE = "krw.json"
PREFIX = "krw-"
STATE = "book"                        # the store's state record; events are keyed "e<notice id>-<TOKEN>"
TERMINAL = ("filled", "cancelled", "rejected", "expired")
STOP_OPEN = ("submitted", "reconciling", "acknowledged", "open")    # a stop intent sent and not closed
STOP_PENDING = ("reserved",) + STOP_OPEN                            # ... or reserved and never sent (a killed run)
LIVE = ("opening", "open", "stopped", "closing", "failed")          # statuses that can hold legs
# An order intent a signed run's reconcile settles. "manual_intervention" is left out: no run can settle it, only
# the operator, and every signed run stops at the reconcile until then.
UNSETTLED = ("reserved", "submitted", "acknowledged", "open", "partial", "reconciling", "unknown")


def _rules():
    """The frozen event rule. Imported where used: the repository root must be on sys.path."""

    from tools.krwfade import rules as R

    return R


def _iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, UTC).isoformat(timespec="seconds")


def event_id(notice_id: int, token: str) -> str:
    return f"e{int(notice_id)}-{token}"


def stem(ev: Mapping) -> str:
    """The client key stem of an event: "krw-<notice id>-<TOKEN>-"."""

    key = f"{PREFIX}{int(ev['notice_id'])}-{ev['token']}-"
    if len(key) + 4 > 36:          # "st", or a kind and up to two digits from Book.fresh_key
        raise PerpBookError(f"client key too long: {key}")
    return key


def stop_key(ev: Mapping) -> str:
    return stem(ev) + "st"


def in_window(ev: Mapping, now_ms: int) -> bool:
    return int(ev["entry_ms"]) <= now_ms <= int(ev["entry_ms"]) + ENTRY_WINDOW_MS


def exit_due(ev: Mapping, now_ms: int) -> bool:
    """Is the hold over? From 2.5 min before E + 72 h (a run's start-up must not push it a cadence later)."""

    return now_ms + EXIT_EARLY_MS >= int(ev["entry_ms"]) + HOLD_MS


def _qty(ev: Mapping, symbol: str, sign_sell: int) -> float:
    q = 0.0
    for f in ev.get("fills") or []:
        if f.get("symbol") == symbol:
            q += float(f["qty"]) * (sign_sell if f["side"] == "sell" else -sign_sell)
    return round(q, DECIMALS)


def alt_short(ev: Mapping) -> float:
    """The alt quantity this event holds short, from its fills (stop fill included)."""

    return _qty(ev, str(ev.get("perp") or ""), 1)


def btc_long(ev: Mapping) -> float:
    """The BTCUSDT quantity this event holds long, from its fills."""

    return _qty(ev, BTC, -1)


def has_legs(ev: Mapping) -> bool:
    return ev.get("status") in LIVE and (alt_short(ev) > QTY_TOL or btc_long(ev) > QTY_TOL)


def _state(store: PairStore) -> dict:
    return store.pairs.get(STATE) or {}


def _halted_by(st: Mapping, deadline_ms: int) -> bool:
    """Whether the book was halted by ``deadline_ms`` (an event's window end): only then is 'halted' the reason it
    was not traded. A halt recorded without its time (``at_ms``) counts as earlier."""

    h = st.get("halted")
    if not h:
        return False
    at = h.get("at_ms") if isinstance(h, Mapping) else None
    return at is None or int(at) <= deadline_ms


def _events(store: PairStore) -> list[tuple[str, dict]]:
    out = [(k, v) for k, v in store.pairs.items() if k.startswith("e") and v.get("kind") == "event"]
    return sorted(out, key=lambda kv: (int(kv[1]["entry_ms"]), int(kv[1]["notice_ms"]), kv[0]))


def _stop_pending(ev: Mapping, ledger: Mapping) -> bool:
    rec = ledger.get(stop_key(ev)) if ev.get("stop_sent") else None
    return rec is not None and str(rec.get("status") or "") in STOP_PENDING


def peek_ledger(path: Path) -> dict:
    """The order ledger of ``order_state.json`` read unverified and without a key, for the keyless pre-check (the
    signed run verifies it). A missing file is an empty ledger."""

    if not path.exists():
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    led = raw.get("order_ledger") if isinstance(raw, dict) else None
    return led if isinstance(led, dict) else {}


def unsettled(store: PairStore, ledger: Mapping[str, Mapping]) -> bool:
    """Has this book an order a signed run would settle? A non-stop "krw-" intent still in flight or left open
    (a lost reply, a fill timeout, a killed run), or a fill the ledger holds that its event's record does not yet
    (a market order filled without a price)."""

    for key, rec in ledger.items():
        if key.startswith(PREFIX) and not key.endswith("-st") and str((rec or {}).get("status") or "") in UNSETTLED:
            return True
    for _k, ev in _events(store):
        if ev.get("status") not in LIVE:
            continue
        recorded = {f.get("key") for f in ev.get("fills") or []}
        prefix = stem(ev)
        for key, rec in ledger.items():
            if (key.startswith(prefix) and not key.endswith("-st") and key not in recorded
                    and str((rec or {}).get("status") or "") in TERMINAL
                    and float((rec or {}).get("filled_quantity") or 0.0) > QTY_TOL):
                return True
    return False


def signed_run_reason(now_ms: int, store: PairStore, notices: Callable[[], Sequence],
                      symbols: Callable[[], Mapping[str, Mapping]], ledger: Mapping[str, Mapping] | None = None
                      ) -> str | None:
    """Why book KRW's 5-minute mode needs a signed run now, or None. No key: the store and the order ledger (both
    read unverified, the signed run verifies them), Upbit's public notice list and Binance's public exchangeInfo,
    each read only when needed.

    "settle": an order or an unwind left unfinished: an event opening or closing, a failed event with legs or not
    yet found flat by a signed run (its take-off cut off), or an order of this book the ledger has not settled
    (``unsettled``: a naked short a lost reply left is seen here); "exit_due": a fired stop's BTC leg, or a hold at
    its end; "entry_window": an event, recorded or new on the notice list, inside E <= now <= E + 15 min. A fired
    stop is seen by a signed run only (it needs the venue's positions): the hourly run finds it.
    """

    st = _state(store)
    evs = _events(store)
    for _k, ev in evs:
        s = ev["status"]
        if (s == "opening" or (s in ("closing", "failed") and has_legs(ev))
                or (s == "closing" and not ev.get("stale_stop"))       # a stale stop is tried again by the hourly run
                or (s == "failed" and ev.get("unwound") is not True)):
            return "settle"
    if ledger is not None and unsettled(store, ledger):
        return "settle"
    for _k, ev in evs:
        if ev["status"] == "stopped" or (ev["status"] == "open" and exit_due(ev, now_ms)):
            return "exit_due"
    if st.get("halted"):
        return None
    if any(ev["status"] == "waiting" and in_window(ev, now_ms) for _k, ev in evs):
        return "entry_window"
    R = _rules()
    seen = set(store.seen)
    earlier = st.get("krw_notices") or {}
    syms: Mapping[str, Mapping] | None = None
    for n in sorted(notices(), key=lambda n: (n.first_listed_at_ms, n.id)):
        if f"n{n.id}" in seen:
            continue
        t = n.first_listed_at_ms
        for tk in R.krw_tokens(n.title):
            ev = {"entry_ms": R.entry_ms(t)}
            if not in_window(ev, now_ms) or ev["entry_ms"] + HOLD_MS < FORWARD_FROM_MS:
                continue
            if not R.first_within(t, R.chain(t, tk.ambiguous, earlier.get(tk.token) or [])):
                continue
            syms = symbols() if syms is None else syms
            if R.verdict(tk.token, t, syms).ok:
                return "entry_window"
    return None


@dataclass
class KrwFadeBook:
    name: ClassVar[str] = "krw"
    book: Book                                          # the pair book's order path and venue
    store: PairStore                                    # signed; pairs[STATE] the book, pairs["e..."] the events
    usd: float                                          # --krw-usd: each leg's notional
    notices: Callable[[], Sequence]                     # page 1 of Upbit's trade notices (tools.krwfade.feed)
    symbols: Callable[[], Mapping[str, Mapping]]        # mainnet exchangeInfo by symbol (public)
    shadow_row: Callable[[str, int, int], dict]         # (perp, E, notice) -> the research row (tools.krwfade.shadow)
    wall: Callable[[], float] = time.time               # the wall clock just before an order goes out
    resume: bool = False                                # --krw-resume: a new sleeve once the book is flat (``_resume``)

    @property
    def venue(self):
        return self.book.venue

    @property
    def ledger(self) -> dict:
        return self.book.orders.state.order_ledger

    # ── state ──────────────────────────────────────────────────────────────

    def _st(self, now_ms: int | None = None) -> dict:
        st = self.store.pairs.get(STATE)
        if st is None:
            st = {"started_ms": now_ms, "usd": self.usd, "halted": None, "krw_notices": {}, "not_events": {},
                  "feed": {"fails": 0, "last_error": None, "last_ok_ms": None, "newest_listed_ms": None}}
            if now_ms is not None:
                self.store.pairs[STATE] = st
        return st

    def _sleeve(self) -> float:
        """The halt's base: 4 x the size stored at the book's first run. A new size is a new book."""

        return SLEEVE_EVENTS * float(_state(self.store).get("usd") or self.usd)

    def _size_changed(self) -> bool:
        return abs(float(_state(self.store).get("usd") or self.usd) - self.usd) > 1e-9

    def held(self) -> dict[str, float]:
        """The alt shorts (negative) and the BTCUSDT longs (summed) this book holds by its record."""

        out: dict[str, float] = {}
        for _k, ev in _events(self.store):
            if ev.get("status") not in LIVE:
                continue
            a, b = alt_short(ev), btc_long(ev)
            if a > QTY_TOL:
                out[ev["perp"]] = round(out.get(ev["perp"], 0.0) - a, DECIMALS)
            if b > QTY_TOL:
                out[BTC] = round(out.get(BTC, 0.0) + b, DECIMALS)
        return out

    def _holding(self) -> list[dict]:
        return [ev for _k, ev in _events(self.store) if has_legs(ev)]

    def counts(self) -> dict[str, Any]:
        """For status.json: counts and statuses only."""

        evs = [ev for _k, ev in _events(self.store)]
        by: dict[str, int] = {}
        sh: dict[str, int] = {}
        for ev in evs:
            by[ev["status"]] = by.get(ev["status"], 0) + 1
            s = (ev.get("shadow") or {}).get("status", "due")
            sh[s] = sh.get(s, 0) + 1
        st = _state(self.store)
        return {"krw_events": by, "krw_open": len(self._holding()), "krw_halted": bool(st.get("halted")),
                "krw_shadow": sh, "krw_feed_fails": int((st.get("feed") or {}).get("fails") or 0),
                "krw_gaps": sum(1 for k in self.store.pairs if k.startswith("g")),
                "krw_excluded": sum(1 for ev in evs if ev.get("excluded")),
                "krw_stale_stops": sum(1 for ev in evs if ev.get("stale_stop"))}

    # ── fills ──────────────────────────────────────────────────────────────

    def _fill(self, ev: dict, symbol: str, key: str, side: str, res: dict, mark: float | None, now_ms: int,
              sent: int) -> None:
        row = fill_record(key, side, res, mark)
        row.update(symbol=symbol, ms=now_ms, sent_ms=sent)
        ev.setdefault("fills", []).append(row)
        self.store.save()

    def _settle(self, eid: str, ev: dict) -> int:
        """Record from the order ledger every fill of this event's orders that it does not hold yet (stops aside).

        A lost reply, a part fill that expired, a run killed after an order: after the reconcile each is a terminal
        ledger record under the event's key stem with a filled quantity. Priced as every book prices a ledger fill
        (``legs.ledger_price``); a non-empty source is kept as ``avg_from``.
        """

        recorded = {f["key"] for f in ev.get("fills") or []}
        prefix = stem(ev)
        keys = sorted((k for k in self.ledger if k.startswith(prefix) and not k.endswith("-st") and k not in recorded),
                      key=lambda k: (str(self.ledger[k].get("created_at") or ""), k))
        n = 0
        for key in keys:
            rec = self.ledger[key]
            status = str(rec.get("status") or "")
            filled = float(rec.get("filled_quantity") or 0.0)
            if status not in TERMINAL:
                raise PerpBookError(f"KRW order {key} still {status}; operator review required")
            if filled <= QTY_TOL:
                continue
            symbol = str(rec.get("symbol") or "")
            avg, src = ledger_price(self.book, key, rec)
            row = {"key": key, "side": str(rec.get("side") or "").lower(), "qty": filled, "avg": avg,
                   "fees": dict(rec.get("fees") or {}), "mark": None, "symbol": symbol, "from_ledger": True}
            if src:
                row["avg_from"] = src
            ev.setdefault("fills", []).append(row)
            n += 1
            self.book.audit("perp_krw_fill_settled", id=eid, key=key, status=status, qty=filled)
        if ev.get("closing_key") and str((self.ledger.get(ev["closing_key"]) or {}).get("status") or "") in TERMINAL:
            ev["closing_key"] = None
            n += 1
        if n:
            self.store.save()
        return n

    def _close_stop_intent(self, ev: dict, final: str, **extra: Any) -> None:
        key = stop_key(ev)
        rec = self.ledger.get(key)
        if rec is not None and rec.get("status") == "reserved":      # a run killed before it was sent
            self.book.orders.update_order(key, status="rejected", error="never_submitted")
            return
        if rec is None or rec.get("status") not in STOP_OPEN:
            return
        if rec["status"] in ("submitted", "reconciling"):
            self.book.orders.update_order(key, status="acknowledged")
        self.book.orders.update_order(key, status=final, **extra)

    def _stop_filled_at_venue(self, ev: dict) -> bool:
        """Whether the venue answers the stop's key with a fill: a stop whose send raised may still have been taken.
        A read error or no answer counts as not filled (the caller then closes the intent as rejected, and a short
        the record still holds stops the next run for operator review)."""

        try:
            got = self.venue.order_by_key(ev["perp"], stop_key(ev))
        except Exception:           # a read for the record
            return False
        return got is not None and got.get("status") == "filled"

    def _stop_fill(self, ev: dict, qty: float) -> tuple[float, str]:
        """A fired stop's average fill: read back by its key when the venue answers, else 1.05x the trigger."""

        try:
            got = self.venue.order_by_key(ev["perp"], stop_key(ev))
        except Exception:           # a read for the record
            got = None
        if got is not None and got.get("status") == "filled" and float(got.get("avg_fill_price") or 0.0) > 0:
            return float(got["avg_fill_price"]), "venue"
        return STOP_FILL_X * float(ev["stop_price"]), "stop_trigger"

    # ── each run, before the pair book's position check ────────────────────

    def check(self) -> None:
        """Settle cut-off orders, finish a cut-off entry, record fired stops, refuse a mismatch (operator review).

        After the reconcile every order of the book has its outcome in the ledger. Per event holding legs: its
        fills are completed from the ledger; an entry cut off mid-run becomes ``open`` when its three orders are
        all in place, else ``failed`` (later runs unwind it). Then its alt leg is compared with the venue:

        - flat at the venue with its stop gone while the record holds a short: the stop fired. The event is
          ``stopped`` (its BTC leg is sold by the step) and the stop intent closed as filled, as book LV does;
        - flat while its stop is still listed, a quantity that differs, or a short without its stop (an ``open``
          or ``closing`` event): ``PerpBookError``, nothing sent.

        BTCUSDT is checked by the pair book, in the sum of every book's BTC.
        """

        positions: dict | None = None          # read once, when an event holds a short
        for eid, ev in _events(self.store):
            if ev["status"] not in LIVE and not ev.get("stale_stop"):
                continue
            if ev["status"] in LIVE:
                self._settle(eid, ev)          # first: a stale stop's retry closes the event once its last fill is in
            if ev.get("stale_stop"):
                self._retry_stale_stop(eid, ev)
            if ev["status"] not in LIVE:
                continue
            if ev["status"] == "opening":
                self._finish_opening(eid, ev)
            a = alt_short(ev)
            if a < -QTY_TOL:
                raise PerpBookError(f"KRW {eid}: the record holds more bought back than sold; operator review required")
            if a > QTY_TOL:                    # a record without a short: the pair book refuses what no book explains
                positions = self.venue.positions() if positions is None else positions
                self._check_alt(eid, ev, a, positions)

    def _finish_opening(self, eid: str, ev: dict) -> None:
        stops = self.venue.open_stop_keys(ev["perp"]) if ev.get("stop_sent") else set()
        b = self.ledger.get(str(ev.get("btc_key") or ""))
        whole = (alt_short(ev) > QTY_TOL and stop_key(ev) in stops and b is not None and b.get("status") == "filled"
                 and btc_long(ev) > QTY_TOL)
        if whole:
            ev["status"] = "open"
            if self.ledger.get(stop_key(ev), {}).get("status") in ("submitted", "reconciling"):
                self.book.orders.update_order(stop_key(ev), status="acknowledged")
        else:
            ev.update(status="failed", reason="entry_cut_off")
        self.store.save()
        self.book.audit("perp_krw_settled", id=eid, status=ev["status"])

    def _check_alt(self, eid: str, ev: dict, a: float, positions: Mapping) -> None:
        perp = ev["perp"]
        pos = positions.get(perp)
        q = pos.qty if pos is not None else 0.0
        stops = self.venue.open_stop_keys(perp)
        key = stop_key(ev)
        if abs(q) <= QTY_TOL:
            if key in stops:
                self.book.audit("perp_krw_mismatch", id=eid, symbol=perp)
                raise PerpBookError(f"KRW {perp}: short is gone but its stop is still open")
            if self.ledger.get(key, {}).get("status") not in ("acknowledged", "open"):
                self.book.audit("perp_krw_mismatch", id=eid, symbol=perp)
                raise PerpBookError(f"KRW {perp}: short is gone without a stop the venue took; operator review required")
            self._record_stop(eid, ev, a, status="stopped")
            return
        if abs(-q - a) > QTY_TOL * max(1.0, a):
            self.book.audit("perp_krw_mismatch", id=eid, symbol=perp)
            raise PerpBookError(f"KRW {perp}: venue {q} differs from book {-a}")
        if ev["status"] in ("open", "closing", "stopped") and key not in stops:
            self.book.audit("perp_krw_unprotected", id=eid, symbol=perp)
            raise PerpBookError(f"KRW {perp}: short has no open stop; operator review required")

    def _record_stop(self, eid: str, ev: dict, a: float, *, status: str) -> None:
        """A fired stop, as book LV records one: its fill on the event (``_stop_fill``), the event saved with
        ``status`` before the stop intent is closed as filled."""

        perp, key = ev["perp"], stop_key(ev)
        avg, src = self._stop_fill(ev, a)
        ev.setdefault("fills", []).append({"key": key, "side": "buy", "qty": a, "avg": avg, "fees": {}, "mark": None,
                                           "symbol": perp, "from_stop": True, "avg_from": src})
        ev.update(status=status, stopped_at=_now_iso())
        self.store.save()       # the leg is saved before the intent is closed
        self._close_stop_intent(ev, "filled", exchange_status="triggered")
        self.book.audit("perp_krw_stop", id=eid, symbol=perp, qty=a)

    def _retry_stale_stop(self, eid: str, ev: dict) -> None:
        """A stop an unwind could not cancel: cancel it again; close its intent once the venue no longer lists it."""

        try:
            if stop_key(ev) in self.venue.open_stop_keys(ev["perp"]):
                self.venue.cancel_stop(ev["perp"], key=stop_key(ev))
            if stop_key(ev) in self.venue.open_stop_keys(ev["perp"]):
                return
        except Exception as exc:    # the alt is flat: a stop it cannot cancel yet never stops the run
            self.book.audit("perp_krw_stop_not_cancelled", id=eid, error=type(exc).__name__)
            return
        self._close_stop_intent(ev, "cancelled")
        ev.pop("stale_stop", None)
        if ev["status"] == "closing" and not has_legs(ev):
            ev.update(status="closed", closed_at=_now_iso())
        self.store.save()

    # ── the run ────────────────────────────────────────────────────────────

    def plan(self, now_ms: int) -> dict:
        """What a run would do now. Nothing signed, sent or written; counts and statuses only."""

        st = _state(self.store)
        evs = [ev for _k, ev in _events(self.store)]
        out: dict[str, Any] = {"events": self.counts()["krw_events"], "open": len(self._holding()),
                               "halted": bool(st.get("halted")),
                               "exit_due": sum(1 for ev in evs if ev["status"] == "stopped"
                                               or (ev["status"] == "open" and exit_due(ev, now_ms))),
                               "in_window": sum(1 for ev in evs if ev["status"] == "waiting" and in_window(ev, now_ms)),
                               "shadow_due": sum(1 for ev in evs if (ev.get("shadow") or {}).get("status") == "due"
                                                 and now_ms >= int(ev["entry_ms"]) + SHADOW_AFTER_MS)}
        if st and self._size_changed():
            out["size_changed"] = True
        try:
            R = _rules()
            seen = set(self.store.seen)
            out["new_krw_notices"] = sum(len(R.krw_tokens(n.title)) for n in self.notices() if f"n{n.id}" not in seen)
        except Exception as exc:    # a dry run reports, it never stops
            out["feed"] = type(exc).__name__
        return out

    def step(self, now_ms: int, *, entry_lock: str | None = None) -> dict:
        """Exits and unwinds; the halt; the feed; entries in their window (not under the entry lock); the shadow."""

        if not 0 < self.usd <= USD_CAP:
            raise PerpBookError(f"KRW notional {self.usd} outside (0, {USD_CAP}]")
        st = self._st(now_ms)
        self._fetch_stopped = False         # set by a 418/429 from Binance's public API: nothing more asked this run
        out: dict[str, Any] = {"closed": 0, "entered": 0, "shadow_only": 0, "missed": 0, "new_events": 0,
                               "shadow_rows": 0}
        errors: list[Exception] = []
        for eid, ev in _events(self.store):
            if not self._exit_wanted(ev, now_ms):
                continue
            try:
                if self._unwind(eid, ev, now_ms):
                    out["closed"] += 1
            except Exception as exc:          # one event's exit never holds up another's; the run stops after
                errors.append(exc)
                self.book.audit("perp_krw_exit_failed", id=eid, error=type(exc).__name__)
        if self.resume:
            out["resume"] = "exit_failed" if errors else self._resume(st, now_ms)
        if not st.get("halted") and self._breached():
            st["halted"] = {"reason": "realized_net_below_limit", "at": _now_iso(), "at_ms": now_ms}
            self.store.save()
            self.book.audit("perp_krw_halted", reason="realized_net_below_limit")
        out["new_events"] = self._read_feed(st, now_ms)
        for eid, ev in _events(self.store):
            if ev["status"] != "waiting":
                continue
            got = self._decide(eid, ev, now_ms, entry_lock, blocked=bool(errors))
            if got:
                out[got] = out.get(got, 0) + 1
        out["shadow_rows"] = self._shadow(now_ms)
        self.store.save()
        out.update(open=len(self._holding()), halted=bool(st.get("halted")))
        if errors:
            raise errors[0]
        return out

    # ── exits ──────────────────────────────────────────────────────────────

    def _exit_wanted(self, ev: dict, now_ms: int) -> bool:
        """Does the step unwind this event now? A closing event without a stale stop always does, legs or not: when
        its last order was settled from the ledger (a lost reply, a killed run) it holds nothing and ``_unwind``
        only closes it. A failed event does until it is ``unwound`` (``_unwind`` marks it once it holds nothing)."""

        s = ev["status"]
        if s == "open":
            return exit_due(ev, now_ms)
        if s == "stopped":
            return True
        if s == "closing":
            return has_legs(ev) or not ev.get("stale_stop")
        if s == "failed":
            return (has_legs(ev) or ev.get("unwound") is not True
                    or (_stop_pending(ev, self.ledger) and not ev.get("stale_stop")))
        return False

    def _unwind(self, eid: str, ev: dict, now_ms: int) -> bool:
        """Buy the alt back reduce-only, cancel its stop after a full fill, sell this event's BTC reduce-only.

        Returns True when the event ended closed in this call. An order that fails, loses its reply or fills in
        part raises: the stop stays while any short is left, and the next run settles the fill and goes on.
        """

        if ev["status"] in ("open", "stopped"):
            ev.update(status="closing", close_reason="stop" if ev["status"] == "stopped" else "hold",
                      exit_ms=now_ms)
            self.store.save()
            self.book.audit("perp_krw_closing", id=eid, reason=ev["close_reason"])
        perp = ev["perp"]
        a = alt_short(ev)
        if a > QTY_TOL:
            key = self.book.fresh_key(stem(ev) + "c")
            mark = self._mark(perp)
            ev["closing_key"] = key
            self.store.save()
            sent = self._sent_ms()
            res = self.book._market(perp, "buy", a, key, reduce_only=True)    # raises unless filled in full
            ev["closing_key"] = None
            self._fill(ev, perp, key, "buy", res, mark, now_ms, sent)
        if alt_short(ev) <= QTY_TOL and _stop_pending(ev, self.ledger) and not ev.get("stale_stop"):
            self._cancel_stop(eid, ev)          # a stale stop is tried again by check()
        b = btc_long(ev)
        if b > QTY_TOL:
            key = self.book.fresh_key(stem(ev) + "x")
            mark = self._mark(BTC)
            sent = self._sent_ms()
            res = self.book._market(BTC, "sell", b, key, reduce_only=True)
            self._fill(ev, BTC, key, "sell", res, mark, now_ms, sent)
        if has_legs(ev):
            return False
        if ev["status"] == "closing" and not ev.get("stale_stop"):
            ev.update(status="closed", closed_at=_now_iso())
            self.store.save()
            self.book.audit("perp_krw_closed", id=eid, reason=ev.get("close_reason"))
            return True
        if ev["status"] == "failed":
            ev["unwound"] = True
            self.store.save()
        return False

    def _cancel_stop(self, eid: str, ev: dict) -> None:
        """After the alt is flat by this book's own buy-back: cancel its stop; a stop the venue keeps is stale."""

        perp, key = ev["perp"], stop_key(ev)
        if key in self.venue.open_stop_keys(perp):
            self.venue.cancel_stop(perp, key=key)
            if key in self.venue.open_stop_keys(perp):
                ev["stale_stop"] = True
                self.store.save()
                self.book.audit("perp_krw_stop_not_cancelled", id=eid, symbol=perp)
                return
        self._close_stop_intent(ev, "cancelled")
        self.store.save()

    def _mark(self, symbol: str) -> float | None:
        try:
            return self.venue.mark_price(symbol)
        except Exception:      # a record; never a reason to keep a leg
            return None

    def _sent_ms(self) -> int:
        return int(self.wall() * 1000)

    # ── halt (K3) ──────────────────────────────────────────────────────────

    def _breached(self) -> bool:
        """Cumulative realized net of the sleeve's Demo round trips from fill prices and USDT fees, against the sleeve.

        A round trip is an event flat by its fills: closed, failed, or closing with a stop the venue still lists.
        The sleeve's events are those decided from its start (``sleeve_from_ms``, set by ``_resume``). Funding is not
        included. Never exposed: only "halted" leaves the book.
        """

        since = int(_state(self.store).get("sleeve_from_ms") or 0)
        net = 0.0
        for _k, ev in _events(self.store):
            if ev["status"] not in ("closed", "failed", "closing") or not ev.get("fills"):
                continue
            if int(ev.get("decided_ms") or 0) < since:
                continue        # an earlier sleeve's
            if abs(alt_short(ev)) > QTY_TOL or abs(btc_long(ev)) > QTY_TOL:
                continue        # legs left, or a fill not settled yet (a lost reply): not a round trip yet
            for f in ev["fills"]:
                cash = float(f["qty"]) * float(f["avg"])
                fees = f.get("fees") or {}
                fee = float(fees["USDT"]) if "USDT" in fees else FEE_FALLBACK * cash
                net += (cash if f["side"] == "sell" else -cash) - fee
        return net < HALT_NET * self._sleeve()

    def _busy(self) -> int:
        """Events that keep the book from being flat: legs, an entry or a stop not settled, a stop left at the venue,
        or an order of the book the ledger has not settled."""

        n = sum(1 for _k, ev in _events(self.store)
                if has_legs(ev) or ev["status"] == "opening" or ev.get("stale_stop") or _stop_pending(ev, self.ledger))
        return n + int(unsettled(self.store, self.ledger))

    def _resume(self, st: dict, now_ms: int) -> str:
        """``--krw-resume``, the operator's call after the K3 halt or a new ``--krw-usd``: once the book is flat, a new
        sleeve starts now. The size is stored again, the halt is cleared and only events decided from now on count
        for K3. The forward record (events, shadow rows, notices) stays as it is. Nothing to resume, or a book that is
        not flat, is refused and audited."""

        if not (st.get("halted") or self._size_changed()):
            why = "nothing_to_resume"
        elif self._busy():
            why = "not_flat"
        else:
            st.setdefault("resumes", []).append({"at_ms": now_ms, "usd": self.usd, "usd_before": st.get("usd"),
                                                 "was_halted": bool(st.get("halted"))})
            st.update(usd=self.usd, halted=None, sleeve_from_ms=now_ms)
            self.store.save()
            self.book.audit("perp_krw_resumed", size_changed=bool(st["resumes"][-1]["usd_before"] != self.usd))
            return "resumed"
        self.book.audit("perp_krw_resume_refused", reason=why)
        return why

    # ── events ─────────────────────────────────────────────────────────────

    def _read_feed(self, st: dict, now_ms: int) -> int:
        """Record each new KRW-listing notice: an event when the rule takes it, else its reason. Returns new events."""

        feed = st.setdefault("feed", {"fails": 0})
        try:
            notes = list(self.notices())
        except Exception as exc:     # public data: a failed read adds nothing and never stops the exits
            feed.update(fails=int(feed.get("fails") or 0) + 1, last_error=type(exc).__name__, last_fail_ms=now_ms)
            self.store.save()
            self.book.audit("perp_krw_feed_failed", error=type(exc).__name__)
            return 0
        R = _rules()
        newest = feed.get("newest_listed_ms")
        if notes and newest is not None and min(n.listed_at_ms for n in notes) > int(newest):
            # Nothing on this page was listed before the last page's newest: notices in between may be missed.
            self.store.pairs[f"g{now_ms // MIN_MS:x}"] = {"kind": "gap", "from_ms": int(newest),
                                                          "to_ms": min(n.listed_at_ms for n in notes)}
            self.book.audit("perp_krw_feed_gap")
        if notes:
            feed["newest_listed_ms"] = max([n.listed_at_ms for n in notes] + ([int(newest)] if newest is not None else []))
        feed.update(last_ok_ms=now_ms)
        seen = set(self.store.seen)
        self._read_again([n for n in notes if f"n{n.id}" in seen], now_ms)
        made = 0
        syms: Mapping[str, Mapping] | None = None
        krw_notices = st.setdefault("krw_notices", {})
        for n in sorted(notes, key=lambda n: (n.first_listed_at_ms, n.id)):
            if f"n{n.id}" in seen:
                continue
            t = n.first_listed_at_ms
            rows = [x for x in R.classify(n.title) or [] if x.krw]     # krw_tokens, and the cancelled ones
            verdicts = []
            for tk in rows:
                E = R.entry_ms(t)
                if tk.cancelled:
                    verdicts.append((tk, None, "listing_cancelled"))
                elif not R.first_within(t, R.chain(t, tk.ambiguous, krw_notices.get(tk.token) or [])):
                    verdicts.append((tk, None, "merged_within_7d"))
                elif E + HOLD_MS < FORWARD_FROM_MS:
                    verdicts.append((tk, None, "research_sample"))
                else:
                    if syms is None:
                        try:
                            syms = self.symbols()
                        except Exception as exc:   # judged at a later run; the notice is not marked seen
                            if type(exc).__name__ == "FetchStop":   # 418/429: the shadow asks nothing this run
                                self._fetch_stopped = True
                            feed.update(fails=int(feed.get("fails") or 0) + 1, last_error=type(exc).__name__,
                                        last_fail_ms=now_ms)
                            self.store.save()
                            self.book.audit("perp_krw_exchange_info_failed", error=type(exc).__name__)
                            return made
                    v = R.verdict(tk.token, t, syms)
                    verdicts.append((tk, v, None if v.ok else v.reason))
            for tk, v, why in verdicts:
                krw_notices.setdefault(tk.token, []).append({"t": t, "id": int(n.id), "amb": tk.ambiguous,
                                                             "cancelled": tk.cancelled})
                if not tk.ambiguous:
                    self._resolve_ambiguous(tk.token, t, int(n.id), now_ms)
                if why is not None:
                    st.setdefault("not_events", {})[f"{n.id}-{tk.token}"] = why
                    self.book.audit("perp_krw_notice", notice=n.id, token=tk.token, status="not_event", reason=why)
                    continue
                eid = event_id(n.id, tk.token)
                E = R.entry_ms(t)
                ev = {"kind": "event", "notice_id": int(n.id), "token": tk.token, "perp": v.perp, "notice_ms": t,
                      "notice": _iso(t), "listed_ms": n.listed_at_ms, "entry_ms": E, "seen_ms": now_ms,
                      "age_d": v.age_d, "ambiguous": tk.ambiguous, "title": n.title, "status": "waiting", "fills": [],
                      "attempts": [], "shadow": {"status": "due", "tries": 0}}
                stem(ev)                 # refuses a key that would not fit
                if now_ms > E + ENTRY_WINDOW_MS:
                    if _halted_by(st, E + ENTRY_WINDOW_MS):   # the pre-check signs for no window while halted
                        ev.update(status="shadow_only", reason="halted", decided_ms=now_ms)
                    else:
                        ev.update(status="missed", reason="seen_late")
                self.store.pairs[eid] = ev
                made += 1
                self.book.audit("perp_krw_notice", notice=n.id, token=tk.token, status=ev["status"])
            self.store.seen.append(f"n{n.id}")
            seen.add(f"n{n.id}")
        self.store.save()       # once a page: a run killed before it reads the same notices again
        return made

    def _read_again(self, notes: Sequence, now_ms: int) -> None:
        """Notices seen before, read again: research classified the final titles.

        A follow-up that cancels a token's listing, or a title that no longer lists the token on KRW, takes the
        token's notice out of its 7-day chain and excludes its event (``_exclude``).
        """

        R = _rules()
        entries: dict[int, list[tuple[str, dict]]] = {}
        for tok, rows in (_state(self.store).get("krw_notices") or {}).items():
            for e in rows:
                if isinstance(e, dict) and e.get("id") is not None:
                    entries.setdefault(int(e["id"]), []).append((tok, e))
        events: dict[int, list[tuple[str, dict]]] = {}
        for eid, ev in _events(self.store):
            events.setdefault(int(ev["notice_id"]), []).append((eid, ev))
        for n in notes:
            if int(n.id) not in entries and int(n.id) not in events:
                continue
            now = {x.token: x for x in R.classify(n.title) or [] if x.krw}
            for tok, e in entries.get(int(n.id), []):
                if not e.get("cancelled") and (tok not in now or now[tok].cancelled):
                    e["cancelled"] = True
                    self.store.save()
            for eid, ev in events.get(int(n.id), []):
                x = now.get(ev["token"])
                if ev.get("excluded") or (x is not None and not x.cancelled):
                    continue
                self._exclude(eid, ev, "listing_cancelled" if x is not None else "no_longer_krw", now_ms,
                              notice=int(n.id), title=n.title)

    def _resolve_ambiguous(self, token: str, t: int, notice_id: int, now_ms: int) -> None:
        """A non-ambiguous KRW notice of ``token`` at ``t``: research rules the token's ambiguous notices more than a
        day older not KRW (classify.py lines 352-362). Their events are excluded."""

        R = _rules()
        for eid, ev in _events(self.store):
            if (ev["token"] == token and ev.get("ambiguous") and not ev.get("excluded")
                    and R.resolves(t, int(ev["notice_ms"]))):
                self._exclude(eid, ev, "ambiguous_not_krw", now_ms, notice=notice_id)

    def _exclude(self, eid: str, ev: dict, reason: str, now_ms: int, **detail: Any) -> None:
        """Take an event out of K1 and the read. A waiting event is ``cancelled`` (never traded); one traded on Demo
        keeps its legs to their exit by the rule. A shadow row not computed yet is not computed (``excluded``)."""

        ev["excluded"] = {"reason": reason, "at_ms": now_ms, **detail}
        if ev["status"] == "waiting":
            ev.update(status="cancelled", reason=reason, decided_ms=now_ms)
        sh = ev.setdefault("shadow", {"status": "due", "tries": 0})
        if sh.get("status") == "due":
            sh["status"] = "excluded"
        self.store.save()
        self.book.audit("perp_krw_event", id=eid, status=ev["status"], excluded=reason)

    def _decide(self, eid: str, ev: dict, now_ms: int, entry_lock: str | None, *, blocked: bool) -> str | None:
        """A waiting event: missed past its window; in it, traded or shadow-only with the reason."""

        if now_ms > int(ev["entry_ms"]) + ENTRY_WINDOW_MS:
            if _halted_by(_state(self.store), int(ev["entry_ms"]) + ENTRY_WINDOW_MS):   # halted in its window
                ev.update(status="shadow_only", reason="halted", decided_ms=now_ms)
                self.store.save()
                self.book.audit("perp_krw_event", id=eid, status="shadow_only", reason="halted")
                return "shadow_only"
            tries = ev.get("attempts") or []
            ev.update(status="missed", reason=tries[-1]["why"] if tries else "no_run_in_window")
            self.store.save()
            self.book.audit("perp_krw_event", id=eid, status="missed", reason=ev["reason"])
            return "missed"
        if not in_window(ev, now_ms):
            return None
        if entry_lock or blocked:
            ev.setdefault("attempts", []).append({"at": _iso(now_ms), "why": "entry_lock" if entry_lock else "exit_failed"})
            self.store.save()
            return None
        try:
            why = self._why_not(ev)
        except LiveSafetyError:
            raise
        except Exception as exc:    # a venue read; nothing sent yet: a later run inside the window tries again
            ev.setdefault("attempts", []).append({"at": _iso(now_ms), "why": f"error_{type(exc).__name__}"})
            self.store.save()
            self.book.audit("perp_krw_entry_error", id=eid, error=type(exc).__name__)
            return None
        if why is not None:
            ev.update(status="shadow_only", reason=why, decided_ms=now_ms)
            self.store.save()
            self.book.audit("perp_krw_event", id=eid, status="shadow_only", reason=why)
            return "shadow_only"
        return self._enter(eid, ev, now_ms)

    def _why_not(self, ev: dict) -> str | None:
        """The first condition that keeps a Demo entry out, in the rule's order, or None."""

        perp = ev["perp"]
        if self._size_changed():
            return "size_changed"
        if perp not in self.venue.trading_symbols():
            return "not_on_demo"
        taken = self.book.taken(self.name)
        if perp in taken:
            return f"held_by_{taken[perp]}"
        if any(x.get("perp") == perp and (has_legs(x) or x.get("stale_stop") or _stop_pending(x, self.ledger))
               for _k, x in _events(self.store) if x is not ev):
            return "held_by_krw"
        if self.venue.open_stop_keys(perp):
            return "stop_listed"    # a stop another book left (LV's stale_stops): it would buy this short back
        if _state(self.store).get("halted"):
            return "halted"
        if len(self._holding()) >= KRW_MAX_OPEN:
            return "max_open"
        return risk.room(self.venue).binding(2 * self.usd, self.usd)

    def _enter(self, eid: str, ev: dict, now_ms: int) -> str | None:
        perp = ev["perp"]
        try:
            mark = self.venue.mark_price(perp)
            try:
                qty = self.venue.normalize(perp, self.usd / mark, mark)
            except OrderConstraintError:
                qty = 0.0
            if qty > QTY_TOL:
                for s in (perp, BTC):
                    self.venue.prepare(s)       # cross margin at 1x
        except Exception as exc:    # nothing sent yet: a later run inside the window tries again
            ev.setdefault("attempts", []).append({"at": _iso(now_ms), "why": f"error_{type(exc).__name__}"})
            self.store.save()
            self.book.audit("perp_krw_entry_error", id=eid, error=type(exc).__name__)
            return None
        if qty <= QTY_TOL:
            ev.update(status="shadow_only", reason="below_venue_minimum", decided_ms=now_ms)
            self.store.save()
            self.book.audit("perp_krw_event", id=eid, status="shadow_only", reason="below_venue_minimum")
            return "shadow_only"
        key = self.book.fresh_key(stem(ev) + "o")
        ev.update(status="opening", decided_ms=now_ms, entry_run_ms=now_ms, entry_key=key, entry_mark=mark)
        self.store.save()
        try:
            sent = self._sent_ms()
            res = self.book._market(perp, "sell", qty, key, reduce_only=False)
            self._fill(ev, perp, key, "sell", res, mark, now_ms, sent)
            short, px = float(res["filled_qty"]), float(res["avg_fill_price"])
            stop_px = STOP_X * px
            skey = stop_key(ev)
            ev.update(stop_sent=True, stop_price=stop_px)
            self.store.save()
            self.book._send(perp, "buy", short, skey, lambda: self.venue.stop(perp, "buy", short, stop_px, key=skey))
            bmark = self.venue.mark_price(BTC)
            bqty = self.venue.normalize_nearest(BTC, self.usd / bmark, bmark)
            bkey = self.book.fresh_key(stem(ev) + "b")
            ev.update(btc_key=bkey)
            self.store.save()
            sent = self._sent_ms()
            res = self.book._market(BTC, "buy", bqty, bkey, reduce_only=False)
            self._fill(ev, BTC, bkey, "buy", res, bmark, now_ms, sent)
            ev["status"] = "open"
            self.store.save()
        except Exception:
            # Never leave a naked or unprotected short: buy back what the venue holds, before the stop is settled.
            self.book.audit("perp_krw_unprotected", id=eid, symbol=perp)
            ev.update(status="failed", reason="entry_failed")
            self.store.save()
            self._take_off(eid, ev, now_ms)
            raise
        self.book.audit("perp_krw_opened", id=eid, symbol=perp, short_qty=alt_short(ev), btc_qty=btc_long(ev))
        return "entered"

    def _take_off(self, eid: str, ev: dict, now_ms: int) -> None:
        """After a failed entry: the venue's alt short bought back first, then the BTC this event bought, then the stop.

        A stop the venue took that it no longer lists, while the venue held no short and the record holds one, has
        fired (the listing squeeze this book trades into): it is recorded as ``check`` records one, and the event
        stays failed. ``unwound`` is set last, so a take-off cut off leaves it unset for the pre-check.
        """

        perp = ev["perp"]
        held = self.venue.positions().get(perp)
        short = round(-held.qty, DECIMALS) if held is not None and held.qty < -QTY_TOL else 0.0
        if short > QTY_TOL:
            key = self.book.fresh_key(stem(ev) + "c")
            sent = self._sent_ms()
            res = self.book._market(perp, "buy", short, key, reduce_only=True)
            self._fill(ev, perp, key, "buy", res, None, now_ms, sent)
        b = btc_long(ev)
        if b > QTY_TOL:
            key = self.book.fresh_key(stem(ev) + "x")
            sent = self._sent_ms()
            res = self.book._market(BTC, "sell", b, key, reduce_only=True)
            self._fill(ev, BTC, key, "sell", res, None, now_ms, sent)
        if ev.get("stop_sent"):
            rec = self.ledger.get(stop_key(ev))
            if rec is not None and rec.get("status") in STOP_PENDING:
                if stop_key(ev) in self.venue.open_stop_keys(perp):
                    self._cancel_stop(eid, ev)
                elif rec.get("status") in ("acknowledged", "open") and short <= QTY_TOL and alt_short(ev) > QTY_TOL:
                    self._record_stop(eid, ev, alt_short(ev), status="failed")    # taken, gone, the short with it
                elif rec.get("status") in ("acknowledged", "open"):
                    self._close_stop_intent(ev, "cancelled")        # taken, and no longer listed
                elif short <= QTY_TOL and alt_short(ev) > QTY_TOL and self._stop_filled_at_venue(ev):
                    # its send raised, yet the venue took it and it fired before the take-off read the positions
                    self._record_stop(eid, ev, alt_short(ev), status="failed")
                else:
                    self._close_stop_intent(ev, "rejected", error="entry_failed")   # its send raised; never listed
        ev["unwound"] = not has_legs(ev)
        self.store.save()

    # ── shadow ─────────────────────────────────────────────────────────────

    def _shadow(self, now_ms: int) -> int:
        """Compute each due event's research row from public data; retry later.

        A row is given up (``shadow_failed``) only after ``SHADOW_MIN_FAILS`` failed tries and 7 days from the first,
        so neither a long stop of the runs nor one burst of errors gives it up. A 418 or 429 (``FetchStop``) is the
        exchange asking to stop: nothing more this run, and it never counts as a failed try.
        """

        made = 0
        if getattr(self, "_fetch_stopped", False):     # Binance said stop (418/429) earlier in this run
            return made
        for eid, ev in _events(self.store):
            sh = ev.setdefault("shadow", {"status": "due", "tries": 0})
            if sh.get("status") != "due" or now_ms < int(ev["entry_ms"]) + SHADOW_AFTER_MS:
                continue
            try:
                row = self.shadow_row(ev["perp"], int(ev["entry_ms"]), int(ev["notice_ms"]))
            except Exception as exc:   # public data: a later run tries again
                stop = type(exc).__name__ == "FetchStop"
                sh.update(tries=int(sh.get("tries") or 0) + 1, last_error=f"{type(exc).__name__}: {str(exc)[:120]}",
                          last_try_ms=now_ms)
                if not stop:
                    sh["fails"] = int(sh.get("fails") or 0) + 1
                    sh.setdefault("first_fail_ms", now_ms)
                    if sh["fails"] >= SHADOW_MIN_FAILS and now_ms >= int(sh["first_fail_ms"]) + SHADOW_GIVE_UP_MS:
                        sh.update(status="shadow_failed", reason=sh["last_error"])
                        self.book.audit("perp_krw_shadow_failed", id=eid, error=type(exc).__name__)
                self.store.save()
                if stop:                  # the exchange said stop (418/429): nothing more this run
                    self._fetch_stopped = True
                    break
                continue
            sh.update(status="done", row=row, at_ms=now_ms, tries=int(sh.get("tries") or 0) + 1)
            self.store.save()
            self.book.audit("perp_krw_shadow", id=eid, pass_0a=bool(row.get("pass_0a")))
            made += 1
        return made
