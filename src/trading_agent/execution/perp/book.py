"""One run of the books on the Binance Demo Trading perp account.

The pair book (``Book``) is senior: it owns the order path, the order ledger,
the reconcile and the venue. Every other book on the account (book LV in
``lowvol.py``; H-LEARN-11, H-FLOW-BTC-02, H-FLOW-BTC-01 and H-KRW-FADE-16) is
an ``OtherBook`` that sends its orders through the pair book. One process, one
order ledger, one reconcile, one writer lock.

Order of a run (``run_cycle``): reconcile unfinished intents by client key;
each other book's ``check()``; the pair book's position check, with what the
other books hold counted as explained; pair exits; each other book's
``step()``; pair entries. Anything a book cannot explain stops the run before
a new entry (fail closed). Every intent is reserved in a signed ledger before
it is sent, so a crash cannot send it twice. A run of one book (``only``,
the 5-minute modes of books FLOW01 and KRW) does the same checks, then that
book's step alone.

Symbols on the shared account:

- A symbol other than BTCUSDT is exclusive. A book never enters one another
  book holds (``Book.taken``); it skips it and records ``held_by_<holder>``.
  Two books holding one such symbol stop the run for operator review.
- BTCUSDT is shared by long-only legs: the pair hedges, the BTC books and
  book KRW's hedges. Each book records its own quantity; the venue's BTCUSDT
  must equal the sum; every BTC exit is a reduce-only sell of the book's own
  quantity. A negative BTC quantity in any book is an error.

What another book must implement (``OtherBook``):

- ``name: str``: unique and short ("lv", "learn", "flow02", "flow01", "krw"); its
  report key in ``run_cycle`` and the holder in skip reasons.
- ``check() -> None``: verify its exclusive (non-BTC) legs against
  ``venue.positions()``, settle its own fired stops and cut-off closes, and
  raise ``PerpBookError`` on a mismatch. Its BTCUSDT leg is checked by the
  pair book, inside the sum.
- ``held() -> dict[str, float]``: signed quantity per symbol it holds now,
  BTCUSDT included, read from its own store. Cheap and free of side effects:
  the run calls it several times.
- ``plan(now_ms) -> dict``: a dry run. Nothing signed, sent or written.
- ``step(now_ms, *, entry_lock) -> dict``: exits and reductions always, also
  under ``entry_lock``; additions only without it. A step that raises ends
  the run: the books after it in the run order do not step in that run, and
  no pair entry runs. Their exits wait for a run that gets past it (book
  FLOW01's 5-minute mode steps FLOW01 alone). Isolating the steps from each
  other would need the operator's ruling.

``plan``, ``step`` and everything a book audits carry counts, statuses,
quantities and keys only: never a return, P&L, price change or USD amount.

How it must trade:

- Orders go through this book: ``Book._market`` for market orders (most
  easily through ``legs.LegTrader``), ``Book._send`` for an exchange stop.
- Client keys come from ``Book.fresh_key(stem)``, with the book's own prefix
  ("lv", "lrn-", "fb2-", "fb1-", "krw-"; "pp-" is the pair book's): unique in the
  ledger and at most 36 characters. A stop's key ends in "-st": reconcile
  leaves stops to their book's ``check()``.
- Before a symbol is added: ``Book.taken(name)``. Before exposure is added:
  ``risk.room(venue)``, read after the book's own reductions (risk.py).
- One signed ``PairStore`` per book, under the perp_demo root.
"""

from __future__ import annotations

import hmac
import json
import os
import tempfile
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable, ClassVar, Mapping, Protocol, Sequence

from trading_agent.execution.live_safety import (
    LiveRiskStateStore,
    LiveSafetyError,
    _payload_hmac,
    append_live_audit_event,
    validate_integrity_key,
)
from trading_agent.execution.perp import risk, rules
from trading_agent.execution.perp.risk import EQUITY_USE  # noqa: F401  (its home is risk.py)
from trading_agent.execution.perp.venue import PerpVenue

QTY_TOL = 1e-9
FILL_WAIT_S = 15.0
PAIR_USD_CAP = 1000.0   # a pair above this notional is refused
PAIR_BOOK = "pair_book"  # the pair book's name as a holder of a symbol
KEY_MAX = 36            # Binance's newClientOrderId limit


class PerpBookError(LiveSafetyError):
    pass


@dataclass
class PairStore:
    """Signed, atomically written record of open and closed pairs."""

    path: Path
    key: str
    pairs: dict[str, dict] = field(default_factory=dict)
    seen: list[str] = field(default_factory=list)

    @classmethod
    def load(cls, path: Path, key: str) -> "PairStore":
        key = validate_integrity_key(key)
        if not path.exists():
            return cls(path, key)
        raw = json.loads(path.read_text(encoding="utf-8"))
        sig = raw.pop("integrity", None)
        if not isinstance(sig, str) or not hmac.compare_digest(sig, _payload_hmac(raw, key)):
            raise PerpBookError(f"pair store integrity check failed: {path}")
        return cls(path, key, dict(raw.get("pairs") or {}), list(raw.get("seen") or []))

    @classmethod
    def peek(cls, path: Path) -> "PairStore":
        """Unverified read for a dry run, which never saves."""

        raw = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        return cls(path, "", dict(raw.get("pairs") or {}), list(raw.get("seen") or []))

    def save(self) -> None:
        validate_integrity_key(self.key)
        payload = {"version": 1, "pairs": self.pairs, "seen": sorted(set(self.seen))}
        payload["integrity"] = _payload_hmac({k: v for k, v in payload.items()}, self.key)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=f".{self.path.name}.", suffix=".tmp", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as h:
                json.dump(payload, h, indent=2, sort_keys=True)
                h.flush()
                os.fsync(h.fileno())
            os.chmod(tmp, 0o600)
            os.replace(tmp, self.path)
        finally:
            Path(tmp).unlink(missing_ok=True)

    def open_pairs(self) -> dict[str, dict]:
        return {k: v for k, v in self.pairs.items() if v.get("status") == "open"}


def _key(symbol: str, tag: int, leg: str) -> str:
    """A client key Binance accepts unchanged: at most 36 of [A-Za-z0-9_-]."""

    key = f"pp-{symbol[:18]}-{tag:x}-{leg}"
    if len(key) > KEY_MAX - 2:  # an exit key adds "x" and its number (Book.fresh_key)
        raise PerpBookError(f"client key too long: {key}")
    return key


@dataclass
class Book:
    name: ClassVar[str] = PAIR_BOOK
    venue: PerpVenue
    orders: LiveRiskStateStore
    pairs: PairStore
    audit_path: Path
    pair_usd: float
    sleep: Callable[[float], None] = time.sleep
    clock: Callable[[], float] = time.monotonic
    others: list["OtherBook"] = field(default_factory=list)   # set by run_cycle, in run order
    dry: bool = False         # set by run_cycle: a dry run's plans write nothing, not even the audit

    def audit(self, event: str, **details: object) -> None:
        append_live_audit_event(self.audit_path, event, details)

    def fresh_key(self, stem: str) -> str:
        """The first client key ``stem`` + n (n = 0, 1, ...) that the ledger has not seen."""

        ledger, reserved = self.orders.state.order_ledger, self.orders.state.reserved_orders
        for n in range(100):
            key = f"{stem}{n}"
            if len(key) > KEY_MAX:
                raise PerpBookError(f"client key too long: {key}")
            if key not in ledger and key not in reserved:
                return key
        raise PerpBookError(f"no free client key for {stem}")

    # ── books on one account ───────────────────────────────────────────────

    def _holders(self) -> tuple[dict[str, str], dict[str, float]]:
        """The holder of each non-BTC symbol (this book included), and what the other books hold.

        The second map has every other book's non-BTC legs and, under BTCUSDT,
        the sum of their BTC legs. Two holders of one non-BTC symbol, or a
        negative BTC leg, stop the run for operator review.
        """

        owners = {p["symbol"]: PAIR_BOOK for p in self.pairs.open_pairs().values()}
        merged: dict[str, float] = {}
        for o in self.others:
            for s, q in o.held().items():
                if abs(q) <= QTY_TOL:
                    continue
                if s == rules.HEDGE:
                    if q < 0:
                        self._conflict(symbol=s, books=[o.name], reason="negative_btc")
                        raise PerpBookError(f"book {o.name} holds a negative {s} quantity; operator review required")
                    merged[s] = merged.get(s, 0.0) + q
                    continue
                if s in owners:
                    self._conflict(symbol=s, books=[owners[s], o.name], reason="two_holders")
                    raise PerpBookError(f"{s} is held by {owners[s]} and {o.name}; operator review required")
                owners[s] = o.name
                merged[s] = q
        return owners, merged

    def _conflict(self, **details: object) -> None:
        """Audit a symbol conflict; a dry run only raises (its plans write nothing)."""

        if not self.dry:
            self.audit("perp_symbol_conflict", **details)

    def held_elsewhere(self) -> dict[str, float]:
        """What the other books hold: their non-BTC legs, and their BTC legs summed under BTCUSDT."""

        return self._holders()[1]

    def taken(self, by: str) -> dict[str, str]:
        """Non-BTC symbols held by a book other than ``by``, with the holder's name.

        A book never enters one of these: it skips it and records
        ``held_by_<holder>``. BTCUSDT is never in it; it is shared.
        """

        return {s: owner for s, owner in self._holders()[0].items() if owner != by}

    # ── intents ────────────────────────────────────────────────────────────

    def _record(self, key: str, res: dict) -> str:
        status = res["status"]
        rec = self.orders.state.order_ledger[key]
        if rec["status"] in ("submitted", "reconciling"):
            self.orders.update_order(key, status="acknowledged", exchange_order_id=str(res.get("exchange_order_id") or ""))
        if status in ("filled", "open", "partial", "cancelled", "rejected", "expired"):
            self.orders.update_order(
                key,
                status=status,
                filled_quantity=res["filled_qty"],
                average_fill_price=res["avg_fill_price"] or None,
                quote_cost=res["quote_cost"] or None,
                fees=res["fees"] or None,
                exchange_status=str(res.get("exchange_status") or ""),
            )
        return status

    def _send(self, symbol: str, side: str, qty: float, key: str, send: Callable[[], dict]) -> dict:
        self.orders.reserve_order(key, symbol=symbol, side=side, quantity=qty, venue_client_order_id=key)
        self.orders.update_order(key, status="submitted")
        try:
            res = send()
        except Exception as exc:
            self.orders.update_order(key, status="reconciling", error=type(exc).__name__)
            self.audit("perp_order_uncertain", key=key, symbol=symbol, error=type(exc).__name__)
            raise PerpBookError(f"order {key} outcome unknown; reconcile before the next entry") from exc
        self._record(key, res)
        return res

    def _market(self, symbol: str, side: str, qty: float, key: str, *, reduce_only: bool) -> dict:
        res = self._send(symbol, side, qty, key, lambda: self.venue.market(symbol, side, qty, key=key, reduce_only=reduce_only))
        deadline = self.clock() + FILL_WAIT_S
        while res["status"] not in ("filled", "cancelled", "rejected", "expired"):
            if self.clock() > deadline:
                self.orders.update_order(key, status="reconciling", error="fill_timeout")
                raise PerpBookError(f"market order {key} not filled in {FILL_WAIT_S:.0f} s")
            self.sleep(1.0)
            polled = self.venue.order_by_key(symbol, key)
            if polled is not None:
                res = polled
                self._record(key, res)
        if res["status"] != "filled":
            raise PerpBookError(f"market order {key} ended {res['status']}")
        if res["avg_fill_price"] <= 0:
            res = self._fill_price(symbol, key, res)
        return res

    def _fill_price(self, symbol: str, key: str, res: dict) -> dict:
        """A market order can come back filled before its average price is set
        (seen on Demo Trading, 2026-11-03); read the order back by its key."""

        deadline = self.clock() + FILL_WAIT_S
        while True:
            polled = self.venue.order_by_key(symbol, key)
            if polled is not None and polled["status"] == "filled" and polled["avg_fill_price"] > 0:
                self._record(key, polled)
                return polled
            if res["quote_cost"] > 0 and res["filled_qty"] > 0:
                return {**res, "avg_fill_price": res["quote_cost"] / res["filled_qty"]}
            if self.clock() > deadline:
                raise PerpBookError(f"market order {key} filled without a price")
            self.sleep(1.0)

    def reconcile(self) -> None:
        """Settle every unfinished intent from the venue before anything else."""

        for key, rec in self.orders.unfinished_orders().items():
            symbol = str(rec.get("symbol") or "")
            status = str(rec.get("status") or "")
            if key.endswith("-st"):
                continue  # stops are checked against open algo orders in check_positions
            found = self.venue.order_by_key(symbol, key)
            if found is None:
                if status == "reserved":
                    self.orders.update_order(key, status="rejected", error="never_submitted")
                    continue
                if status != "manual_intervention":
                    self.orders.update_order(key, status="manual_intervention", error="not_found_at_venue")
                self.audit("perp_manual_intervention", key=key, symbol=symbol)
                raise PerpBookError(f"intent {key} is not at the venue; operator review required")
            if status == "manual_intervention":
                self.orders.update_order(key, status="reconciling")
            self._record(key, found)
            if found["status"] not in ("filled", "cancelled", "rejected", "expired"):
                raise PerpBookError(f"intent {key} still {found['status']} at the venue")

    # ── positions ──────────────────────────────────────────────────────────

    def check_positions(self, elsewhere: Mapping[str, float] | None = None) -> None:
        """Close the hedge of a pair whose stop fired; refuse anything unexplained.

        ``elsewhere`` is what the other books on this account hold
        (``held_elsewhere``). Their non-BTC symbols are explained: each book
        checks those quantities itself. Their BTCUSDT quantity is added to the
        pair hedges: the venue's BTCUSDT must equal that sum.
        """

        rest = {s: q for s, q in (elsewhere or {}).items() if abs(q) > QTY_TOL}
        btc_elsewhere = rest.pop(rules.HEDGE, 0.0)
        if btc_elsewhere < 0:
            raise PerpBookError(f"other books hold a negative {rules.HEDGE} quantity; operator review required")
        clash = sorted({p["symbol"] for p in self.pairs.open_pairs().values()} & set(rest))
        if clash:
            self.audit("perp_symbol_conflict", symbols=clash, books=[PAIR_BOOK], reason="two_holders")
            raise PerpBookError(f"{clash} held by the pair book and another book; operator review required")
        held = self.venue.positions()
        hedge_expected = 0.0
        for pid, p in self.pairs.open_pairs().items():
            short = held.get(p["symbol"])
            short_qty = -short.qty if short is not None and short.qty < 0 else 0.0
            bought, bought_keys = self._exit_fills(p["short_key"] + "x")
            if short_qty <= QTY_TOL:
                if p["stop_key"] in self.venue.open_stop_keys(p["symbol"]):
                    raise PerpBookError(f"pair {pid}: short is gone but its stop is still open")
                if bought > QTY_TOL:
                    # The hold exit's buy-back filled and the run was cut off before the hedge: finish it.
                    p.setdefault("short_exit", self._ledger_avg(bought_keys[-1]))
                    self._close_hedge(pid, p, reason="hold")
                    continue
                self._close_hedge(pid, p, reason="stop")
                self.orders.update_order(p["stop_key"], status="filled", exchange_status="triggered")
                continue
            expected = round(p["short_qty"] - bought, 10)     # less what an exit cut off has bought back
            if abs(short_qty - expected) > QTY_TOL * max(1.0, p["short_qty"]):
                raise PerpBookError(f"pair {pid}: venue short {short_qty} differs from book {expected}")
            hedge_expected += p["hedge_qty"]
        hedge_expected += btc_elsewhere
        held = self.venue.positions()
        explained = {p["symbol"] for p in self.pairs.open_pairs().values()} | {rules.HEDGE} | set(rest)
        stray = sorted(s for s, pos in held.items() if s not in explained and abs(pos.qty) > QTY_TOL)
        hedge = held.get(rules.HEDGE)
        hedge_qty = hedge.qty if hedge is not None else 0.0
        if stray or abs(hedge_qty - hedge_expected) > QTY_TOL * max(1.0, hedge_expected):
            self.audit("perp_unexplained_position", stray=stray, hedge=hedge_qty, hedge_expected=hedge_expected,
                       btc_elsewhere=btc_elsewhere)
            raise PerpBookError("venue positions do not match the book; operator review required")

    def _settle_stop(self, p: dict) -> None:
        """After a failed open: cancel a stop the venue took, and close its intent."""

        rec = self.orders.state.order_ledger.get(p["stop_key"])
        if rec is None or rec.get("status") not in ("submitted", "reconciling", "acknowledged", "open"):
            return
        if p["stop_key"] in self.venue.open_stop_keys(p["symbol"]):
            self.venue.cancel_stop(p["symbol"], key=p["stop_key"])
            if p["stop_key"] in self.venue.open_stop_keys(p["symbol"]):
                raise PerpBookError(f"stop {p['stop_key']} could not be cancelled")
            final = "cancelled"
        else:
            final = "rejected"
        if rec["status"] in ("submitted", "reconciling"):
            self.orders.update_order(p["stop_key"], status="acknowledged")
        self.orders.update_order(p["stop_key"], status=final, error="pair_failed")

    def _exit_fills(self, stem: str) -> tuple[float, list[str]]:
        """What the ledger holds filled for an exit stem ("-sx" or "-hx"), every numbered try included.

        Exit keys come from ``fresh_key``, so a try cut off never blocks the next one; this sum keeps the
        next try from buying back or selling what an earlier one already did (a lost reply, a killed run).
        """

        ledger = self.orders.state.order_ledger
        keys = sorted((k for k, r in ledger.items() if k.startswith(stem)
                       and str(r.get("status") or "") in ("filled", "cancelled", "rejected", "expired")
                       and float(r.get("filled_quantity") or 0.0) > QTY_TOL),
                      key=lambda k: (str(ledger[k].get("created_at") or ""), k))
        return sum(float(ledger[k]["filled_quantity"]) for k in keys), keys

    def _ledger_avg(self, key: str) -> float:
        """The average fill of an order settled from the ledger, priced as every book does (legs.ledger_price)."""

        from trading_agent.execution.perp.legs import ledger_price   # legs imports this module

        return ledger_price(self, key, self.orders.state.order_ledger[key])[0]

    def _close_hedge(self, pid: str, p: dict, *, reason: str) -> None:
        stem = p["hedge_key"] + "x"
        sold, keys = self._exit_fills(stem)
        left = round(p["hedge_qty"] - sold, 10)
        if left > QTY_TOL:
            res = self._market(rules.HEDGE, "sell", left, self.fresh_key(stem), reduce_only=True)
            px = res["avg_fill_price"]
        else:      # its sell filled and the reply or the record was lost: nothing is left to sell
            px = self._ledger_avg(keys[-1])
        p.update(status="closed", close_reason=reason, closed_at=_now_iso(), hedge_exit=px)
        self.pairs.save()
        self.audit("perp_pair_closed", pair=pid, reason=reason)

    # ── exits and entries ──────────────────────────────────────────────────

    def exits(self, now_ms: int) -> None:
        for pid, p in self.pairs.open_pairs().items():
            if not rules.exit_due(p["entry_ms"], now_ms, p.get("hold_h", rules.HOLD_H)):
                continue
            self.venue.cancel_stop(p["symbol"], key=p["stop_key"])
            if p["stop_key"] in self.venue.open_stop_keys(p["symbol"]):
                raise PerpBookError(f"pair {pid}: stop could not be cancelled")
            self.orders.update_order(p["stop_key"], status="cancelled")
            stem = p["short_key"] + "x"
            left = round(p["short_qty"] - self._exit_fills(stem)[0], 10)    # less a cut-off try's fill
            if left > QTY_TOL:
                res = self._market(p["symbol"], "buy", left, self.fresh_key(stem), reduce_only=True)
                p["short_exit"] = res["avg_fill_price"]
            self._close_hedge(pid, p, reason="hold")

    def enter(self, symbol: str, tag: int, now_ms: int, *, hold_h: int = rules.HOLD_H, rehearsal: bool = False) -> str:
        if not 0 < self.pair_usd <= PAIR_USD_CAP:
            raise PerpBookError(f"pair notional {self.pair_usd} outside (0, {PAIR_USD_CAP}]")
        # Account-wide caps (risk.py): both legs add gross, the short adds an alt short.
        why = risk.room(self.venue).binding(2 * self.pair_usd, self.pair_usd)
        if why is not None:
            self.audit("perp_entry_skipped", symbol=symbol, reason=why)
            return why
        for s in (symbol, rules.HEDGE):
            self.venue.prepare(s)
        px = self.venue.mark_price(symbol)
        qty = self.venue.normalize(symbol, self.pair_usd / px, px)
        pid = f"{symbol}-{tag:x}"
        p: dict[str, Any] = {
            "symbol": symbol, "status": "opening", "rehearsal": rehearsal, "hold_h": hold_h,
            "short_key": _key(symbol, tag, "s"), "hedge_key": _key(symbol, tag, "h"), "stop_key": _key(symbol, tag, "st"),
        }
        self.pairs.pairs[pid] = p
        self.pairs.seen.append(symbol)
        self.pairs.save()
        try:
            short = self._market(symbol, "sell", qty, p["short_key"], reduce_only=False)
            p.update(short_qty=short["filled_qty"], short_avg=short["avg_fill_price"], entry_ms=now_ms)
            self.pairs.save()
            bpx = self.venue.mark_price(rules.HEDGE)
            hqty = self.venue.normalize_nearest(rules.HEDGE, short["filled_qty"] * short["avg_fill_price"] / bpx, bpx)
            hedge = self._market(rules.HEDGE, "buy", hqty, p["hedge_key"], reduce_only=False)
            p.update(hedge_qty=hedge["filled_qty"], hedge_avg=hedge["avg_fill_price"])
            stop_px = rules.stop_price(short["avg_fill_price"])
            self._send(symbol, "buy", short["filled_qty"], p["stop_key"],
                       lambda: self.venue.stop(symbol, "buy", short["filled_qty"], stop_px, key=p["stop_key"]))
            p.update(stop_price=stop_px, status="open")
            self.pairs.save()
        except Exception:
            # Never leave a naked or unprotected short: take it off, then stop.
            self.audit("perp_pair_unprotected", pair=pid)
            held = self.venue.positions().get(symbol)
            if held is not None and held.qty < 0:
                self._market(symbol, "buy", -held.qty, self.fresh_key(p["short_key"] + "x"), reduce_only=True)
            if p.get("hedge_qty"):
                self._market(rules.HEDGE, "sell", p["hedge_qty"], self.fresh_key(p["hedge_key"] + "x"), reduce_only=True)
            self._settle_stop(p)
            p.update(status="failed")
            self.pairs.save()
            raise
        self.audit("perp_pair_opened", pair=pid, rehearsal=rehearsal, short_qty=p["short_qty"], hedge_qty=p["hedge_qty"])
        return "opened"


class OtherBook(Protocol):
    """A book beside the pair book on this account; the module docstring has the contract."""

    name: str

    def check(self) -> None: ...
    def held(self) -> dict[str, float]: ...
    def plan(self, now_ms: int) -> dict: ...
    def step(self, now_ms: int, *, entry_lock: str | None = None) -> dict: ...


SecondBook = OtherBook   # its name while book LV was the only other book
# Names another book may not take: the pair book's and the run report's own keys.
REPORT_KEYS = frozenset({PAIR_BOOK, "execute", "entered", "skipped", "due", "open_before", "open_after", "entry_lock",
                         "lv_rehearsal", "only", "only_reason"})


def run_cycle(
    book: Book,
    *,
    now_ms: int,
    mainnet_info: dict | None,
    execute: bool,
    entry_lock: str | None = None,
    rehearse: str | None = None,
    rehearse_hold_h: int = 2,
    lv: OtherBook | None = None,
    others: Sequence[OtherBook] = (),
    only: str | None = None,
) -> dict:
    """One run. Without ``execute`` it only reads and reports what is due.

    ``others`` are the books beside the pair book, in run order (LV, LEARN,
    FLOW02, FLOW01, KRW). ``lv`` is the older way to pass book LV alone; it runs
    first. Each book's plan or step result is reported under its name.

    ``only`` names one other book for a run of that book alone (the 5-minute
    modes of books FLOW01 and KRW): the reconcile, every book's ``check()``
    and the pair book's position check as usual, then that book's ``step()``
    and nothing else. No pair entry, no scheduled pair exit and no other book's step;
    the checks still close a hedge whose pair's stop fired and flatten an
    interrupted LV roll, as in every run.
    """

    books = ([lv] if lv is not None else []) + list(others)
    names = [o.name for o in books]
    if len(set(names)) != len(names) or set(names) & REPORT_KEYS:
        raise PerpBookError(f"book names must be unique and not a report key: {names}")
    book.others = books
    book.dry = not execute
    report: dict = {"execute": execute, "entered": [], "skipped": [], "open_before": len(book.pairs.open_pairs())}
    if only is not None:
        return _run_only(book, books, only, report, now_ms=now_ms, execute=execute, entry_lock=entry_lock,
                         rehearse=rehearse)
    seen = set(book.pairs.seen)
    due, skipped = rules.due_entries(mainnet_info or {}, book.venue.trading_symbols(), now_ms, seen)
    report["due"] = [d.symbol for d in due]
    if not execute:
        report["skipped"] = skipped
        for o in books:
            report[o.name] = o.plan(now_ms)
        return report
    book.reconcile()
    for o in books:
        o.check()
    book.check_positions(book.held_elsewhere())
    book.exits(now_ms)
    for o in books:
        # Before the entry lock returns: exits and reductions still run under it.
        report[o.name] = o.step(now_ms, entry_lock=entry_lock)
    for s in skipped:
        book.pairs.seen.append(s["symbol"])
        report["skipped"].append(s)
        book.audit("perp_entry_skipped", **s)
    book.pairs.save()
    if entry_lock:
        report["entry_lock"] = entry_lock
        return report
    taken = book.taken(PAIR_BOOK)     # after the other books' steps; refuses a symbol two books hold
    if rehearse:
        if rehearse not in book.venue.trading_symbols() or rehearse == rules.HEDGE or rehearse in taken:
            raise PerpBookError(f"cannot rehearse on {rehearse}")
        if book.enter(rehearse, now_ms // 1000, now_ms, hold_h=rehearse_hold_h, rehearsal=True) == "opened":
            report["entered"].append(rehearse)
    for listing in due:
        if listing.symbol in taken:
            reason = f"held_by_{taken[listing.symbol]}"
            book.pairs.seen.append(listing.symbol)
            book.audit("perp_entry_skipped", symbol=listing.symbol, reason=reason)
            report["skipped"].append({"symbol": listing.symbol, "reason": reason})
            continue
        if len(book.pairs.open_pairs()) >= rules.MAX_OPEN:
            book.pairs.seen.append(listing.symbol)
            book.audit("perp_entry_skipped", symbol=listing.symbol, reason="max_open")
            report["skipped"].append({"symbol": listing.symbol, "reason": "max_open"})
            continue
        if book.enter(listing.symbol, listing.onboard_ms // 1000, now_ms) == "opened":
            report["entered"].append(listing.symbol)
    book.pairs.save()
    report["open_after"] = len(book.pairs.open_pairs())
    return report


def _run_only(book: Book, books: Sequence[OtherBook], only: str, report: dict, *, now_ms: int, execute: bool,
              entry_lock: str | None, rehearse: str | None) -> dict:
    """``run_cycle`` for one other book: every check as usual, then only that book's step."""

    target = next((o for o in books if o.name == only), None)
    if target is None:
        raise PerpBookError(f"only {only!r}: that book is not in this run")
    if rehearse:
        raise PerpBookError("a run of one book does not rehearse the pair book")
    report.update(only=only, due=[])
    if not execute:
        report[only] = target.plan(now_ms)
        return report
    book.reconcile()
    for o in books:
        o.check()
    book.check_positions(book.held_elsewhere())
    report[only] = target.step(now_ms, entry_lock=entry_lock)
    if entry_lock:
        report["entry_lock"] = entry_lock
    report["open_after"] = len(book.pairs.open_pairs())
    return report


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()
