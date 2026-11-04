"""Book HOLD on Binance Demo Trading: option B of the 10k discussion as orders.

Buy BTC on the demo spot wallet once with ``hold_usd`` USDT at market and hold
it: no leverage, no timing, no rebalancing, no exit rule. The rest of the
sleeve stays in USDT (option A, cash). The demo wallet came funded with some
BTC, so the book records the wallet's BTC before its buy and checks every run
that the wallet still holds at least that plus what it bought.

Every intent is reserved in a signed order ledger before it is sent, and an
unknown outcome is settled by its client key before anything else. Rules:
docs/operations/PERP_DEMO_RUNNER.md, section "Book HOLD".
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

from trading_agent.execution.live_safety import (
    LiveRiskStateStore,
    LiveSafetyError,
    append_live_audit_event,
)
from trading_agent.execution.perp.book import PairStore

BASE = "BTC"
QUOTE = "USDT"
KEY_PREFIX = "hold-btc-b"
HOLD_USD_CAP = 4500.0   # the demo spot wallet holds 5,000 USDT
QTY_TOL = 1e-8
FILL_WAIT_S = 15.0
TERMINAL = ("filled", "cancelled", "rejected", "expired")


class HoldBookError(LiveSafetyError):
    pass


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


@dataclass
class HoldBook:
    venue: Any                       # SpotDemoVenue
    orders: LiveRiskStateStore
    store: PairStore                 # ``pairs["btc"]`` is the holding
    audit_path: Path
    hold_usd: float
    sleep: Callable[[float], None] = time.sleep
    clock: Callable[[], float] = time.monotonic

    def audit(self, event: str, **details: object) -> None:
        append_live_audit_event(self.audit_path, event, details)

    @property
    def holding(self) -> dict | None:
        return self.store.pairs.get("btc")

    # ── intents ────────────────────────────────────────────────────────────

    def _record(self, key: str, res: dict) -> None:
        rec = self.orders.state.order_ledger[key]
        if rec["status"] in ("submitted", "reconciling", "manual_intervention"):
            self.orders.update_order(key, status="acknowledged", exchange_order_id=str(res.get("exchange_order_id") or ""))
        if res["status"] in ("filled", "open", "partial") + TERMINAL[1:]:
            self.orders.update_order(
                key,
                status=res["status"],
                filled_quantity=res["filled_qty"],
                average_fill_price=res["avg_fill_price"] or None,
                quote_cost=res["quote_cost"] or None,
                fees=res["fees"] or None,
                exchange_status=str(res.get("exchange_status") or ""),
            )

    def _settle(self, res: dict) -> None:
        """Move the holding from ``buying`` to ``held`` (or ``failed``) from the venue's view of the buy."""

        h = self.holding
        if h is None or h.get("status") != "buying":
            return
        if res["status"] != "filled":
            h.update(status="failed", reason=f"buy {res['status']}", closed=_now_iso())
            self.store.save()
            self.audit("spot_hold_failed", status=res["status"])
            raise HoldBookError(f"BTC buy ended {res['status']}; operator review required")
        fee_btc = float((res.get("fees") or {}).get(BASE) or 0.0)
        h.update(status="held", filled_qty=res["filled_qty"], fee_btc=fee_btc,
                 qty=res["filled_qty"] - fee_btc, held_since=_now_iso())
        self.store.save()
        self.audit("spot_hold_bought", key=h["key"])

    def reconcile(self) -> None:
        for key, rec in self.orders.unfinished_orders().items():
            status = str(rec.get("status") or "")
            found = self.venue.order_by_key(BASE, key)
            if found is None:
                if status == "reserved":
                    self.orders.update_order(key, status="rejected", error="never_submitted")
                    h = self.holding
                    if h is not None and h.get("status") == "buying" and h.get("key") == key:
                        del self.store.pairs["btc"]       # nothing was sent: the next run buys afresh
                        self.store.save()
                    continue
                if status != "manual_intervention":
                    self.orders.update_order(key, status="manual_intervention", error="not_found_at_venue")
                self.audit("spot_hold_manual_intervention", key=key)
                raise HoldBookError(f"intent {key} is not at the venue; operator review required")
            self._record(key, found)
            if found["status"] not in TERMINAL:
                raise HoldBookError(f"intent {key} still {found['status']} at the venue")
            if self.holding and self.holding.get("key") == key:
                self._settle(self._priced(key, found))

    def _priced(self, key: str, res: dict) -> dict:
        """A filled market order can come back before its fills are priced; read it back by key."""

        deadline = self.clock() + FILL_WAIT_S
        while res["status"] == "filled" and not (res["filled_qty"] > 0 and (res["avg_fill_price"] > 0 or res["quote_cost"] > 0)):
            if self.clock() > deadline:
                raise HoldBookError(f"market order {key} filled without a price")
            self.sleep(1.0)
            res = self.venue.order_by_key(BASE, key) or res
        self._record(key, res)
        return res

    # ── a run ──────────────────────────────────────────────────────────────

    def check(self) -> None:
        h = self.holding
        if h is None or h.get("status") != "held":
            return
        _, total = self.venue.balance(BASE)
        if total < float(h["baseline_btc"]) + float(h["qty"]) - QTY_TOL:
            self.audit("spot_hold_short_of_book")
            raise HoldBookError("the demo spot wallet holds less BTC than the book; operator review required")

    def plan(self) -> dict:
        h = self.holding
        return {"status": h.get("status") if h else "none", "would_buy": h is None}

    def step(self, *, execute: bool, entry_lock: str | None = None) -> dict:
        if not execute:
            return self.plan()
        self.reconcile()
        self.check()
        if self.holding is not None:
            return {"status": self.holding["status"], "bought": False}
        if entry_lock:
            self.audit("spot_hold_entry_locked", reason=entry_lock)
            return {"status": "none", "bought": False, "entry_lock": True}
        return self.buy()

    def buy(self) -> dict:
        if not 0 < self.hold_usd <= HOLD_USD_CAP:
            raise HoldBookError(f"hold_usd must be in (0, {HOLD_USD_CAP:g}]")
        free_usdt, _ = self.venue.balance(QUOTE)
        if free_usdt < self.hold_usd:
            raise HoldBookError("the demo spot wallet has too little free USDT for the buy")
        price = self.venue.price(BASE)
        qty = self.venue.normalize(BASE, self.hold_usd / price, price)
        _, baseline = self.venue.balance(BASE)
        key = self._fresh_key()
        self.store.pairs["btc"] = {"status": "buying", "key": key, "baseline_btc": baseline,
                                   "usd": self.hold_usd, "opened": _now_iso()}
        self.store.save()
        self.orders.reserve_order(key, symbol=BASE, side="buy", quantity=qty, venue_client_order_id=key)
        self.orders.update_order(key, status="submitted")
        try:
            res = self.venue.market(BASE, "buy", qty, key=key)
        except Exception as exc:
            self.orders.update_order(key, status="reconciling", error=type(exc).__name__)
            self.audit("spot_hold_order_uncertain", key=key, error=type(exc).__name__)
            raise HoldBookError(f"order {key} outcome unknown; the next run reconciles it") from exc
        self._record(key, res)
        if res["status"] not in TERMINAL:
            raise HoldBookError(f"order {key} still {res['status']}; the next run reconciles it")
        self._settle(self._priced(key, res))
        return {"status": "held", "bought": True}

    def _fresh_key(self) -> str:
        ledger = self.orders.state.order_ledger
        for n in range(100):
            if f"{KEY_PREFIX}{n}" not in ledger:
                return f"{KEY_PREFIX}{n}"
        raise HoldBookError("no free client key for the BTC buy")
