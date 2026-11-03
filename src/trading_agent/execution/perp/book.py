"""One run of the new perp pair book on Binance Demo Trading.

Order of a run: reconcile unfinished intents by client key, check the venue's
positions against the book, close pairs whose stop fired or whose hold ended,
then enter due listings. Anything the book cannot explain stops the run before
a new entry (fail closed). Every intent is reserved in a signed ledger before
it is sent, so a crash cannot send it twice.
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
from typing import Any, Callable

from trading_agent.execution.live_safety import (
    LiveRiskStateStore,
    LiveSafetyError,
    _payload_hmac,
    append_live_audit_event,
    validate_integrity_key,
)
from trading_agent.execution.perp import rules
from trading_agent.execution.perp.venue import PerpDemoVenue

QTY_TOL = 1e-9
FILL_WAIT_S = 15.0
EQUITY_USE = 0.9        # both legs of every pair together stay under 90% of equity at 1x
PAIR_USD_CAP = 1000.0   # a pair above this notional is refused


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
    if len(key) > 35:  # an exit key adds one character
        raise PerpBookError(f"client key too long: {key}")
    return key


@dataclass
class Book:
    venue: PerpDemoVenue
    orders: LiveRiskStateStore
    pairs: PairStore
    audit_path: Path
    pair_usd: float
    sleep: Callable[[float], None] = time.sleep
    clock: Callable[[], float] = time.monotonic

    def audit(self, event: str, **details: object) -> None:
        append_live_audit_event(self.audit_path, event, details)

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

    def check_positions(self) -> None:
        """Close the hedge of a pair whose stop fired; refuse anything unexplained."""

        held = self.venue.positions()
        hedge_expected = 0.0
        for pid, p in self.pairs.open_pairs().items():
            short = held.get(p["symbol"])
            short_qty = -short.qty if short is not None and short.qty < 0 else 0.0
            if short_qty <= QTY_TOL:
                if p["stop_key"] in self.venue.open_stop_keys(p["symbol"]):
                    raise PerpBookError(f"pair {pid}: short is gone but its stop is still open")
                self._close_hedge(pid, p, reason="stop")
                self.orders.update_order(p["stop_key"], status="filled", exchange_status="triggered")
                continue
            if abs(short_qty - p["short_qty"]) > QTY_TOL * max(1.0, p["short_qty"]):
                raise PerpBookError(f"pair {pid}: venue short {short_qty} differs from book {p['short_qty']}")
            hedge_expected += p["hedge_qty"]
        held = self.venue.positions()
        explained = {p["symbol"] for p in self.pairs.open_pairs().values()} | {rules.HEDGE}
        stray = sorted(s for s, pos in held.items() if s not in explained and abs(pos.qty) > QTY_TOL)
        hedge = held.get(rules.HEDGE)
        hedge_qty = hedge.qty if hedge is not None else 0.0
        if stray or abs(hedge_qty - hedge_expected) > QTY_TOL * max(1.0, hedge_expected):
            self.audit("perp_unexplained_position", stray=stray, hedge=hedge_qty, hedge_expected=hedge_expected)
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

    def _close_hedge(self, pid: str, p: dict, *, reason: str) -> None:
        res = self._market(rules.HEDGE, "sell", p["hedge_qty"], p["hedge_key"] + "x", reduce_only=True)
        p.update(status="closed", close_reason=reason, closed_at=_now_iso(), hedge_exit=res["avg_fill_price"])
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
            res = self._market(p["symbol"], "buy", p["short_qty"], p["short_key"] + "x", reduce_only=True)
            p["short_exit"] = res["avg_fill_price"]
            self._close_hedge(pid, p, reason="hold")

    def enter(self, symbol: str, tag: int, now_ms: int, *, hold_h: int = rules.HOLD_H, rehearsal: bool = False) -> str:
        if not 0 < self.pair_usd <= PAIR_USD_CAP:
            raise PerpBookError(f"pair notional {self.pair_usd} outside (0, {PAIR_USD_CAP}]")
        need = 2 * self.pair_usd * (len(self.pairs.open_pairs()) + 1)
        if need > EQUITY_USE * self.venue.equity_usdt():
            self.audit("perp_entry_skipped", symbol=symbol, reason="equity")
            return "equity"
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
            hqty = self.venue.normalize(rules.HEDGE, short["filled_qty"] * short["avg_fill_price"] / bpx, bpx)
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
                self._market(symbol, "buy", -held.qty, p["short_key"] + "x", reduce_only=True)
            if p.get("hedge_qty"):
                self._market(rules.HEDGE, "sell", p["hedge_qty"], p["hedge_key"] + "x", reduce_only=True)
            self._settle_stop(p)
            p.update(status="failed")
            self.pairs.save()
            raise
        self.audit("perp_pair_opened", pair=pid, rehearsal=rehearsal, short_qty=p["short_qty"], hedge_qty=p["hedge_qty"])
        return "opened"


def run_cycle(
    book: Book,
    *,
    now_ms: int,
    mainnet_info: dict | None,
    execute: bool,
    entry_lock: str | None = None,
    rehearse: str | None = None,
    rehearse_hold_h: int = 2,
) -> dict:
    """One run. Without ``execute`` it only reads and reports what is due."""

    report: dict = {"execute": execute, "entered": [], "skipped": [], "open_before": len(book.pairs.open_pairs())}
    seen = set(book.pairs.seen)
    due, skipped = rules.due_entries(mainnet_info or {}, book.venue.trading_symbols(), now_ms, seen)
    report["due"] = [d.symbol for d in due]
    if not execute:
        report["skipped"] = skipped
        return report
    book.reconcile()
    book.check_positions()
    book.exits(now_ms)
    for s in skipped:
        book.pairs.seen.append(s["symbol"])
        report["skipped"].append(s)
        book.audit("perp_entry_skipped", **s)
    book.pairs.save()
    if entry_lock:
        report["entry_lock"] = entry_lock
        return report
    if rehearse:
        if rehearse not in book.venue.trading_symbols() or rehearse == rules.HEDGE:
            raise PerpBookError(f"cannot rehearse on {rehearse}")
        if book.enter(rehearse, now_ms // 1000, now_ms, hold_h=rehearse_hold_h, rehearsal=True) == "opened":
            report["entered"].append(rehearse)
    for listing in due:
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


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()
