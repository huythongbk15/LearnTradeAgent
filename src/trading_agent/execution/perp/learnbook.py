"""Book LEARN on Binance Demo Trading: the H-LEARN-11 daily decisions as orders.

The frozen paper book (``tools/learnpaper``, docs/operations/LEARN_PAPER_RUN.md)
decides once a day from the hourly bar that opens at 23:00 UTC: a quarterly
refit model predicts each of 20 alt coins' next 24 h, and a coin is long above
+20 bps, short below -20 bps, else flat. This book trades the same decision on
the demo venue. It imports the paper's own functions (bar store, model, features,
positions) and never changes them or writes the paper's ledger.

- **When:** the first run in [00:00, 02:00) UTC trades the day (``WINDOW_H``).
  The paper enters at the 00:00 open; the hourly run at :15 enters about 15
  minutes later. The position is held until the next day's decision.
- **Missed day:** a day whose window ends without a decision closes every leg
  reduce-only at the first run after the window and is recorded missed.
- **Size:** ``learn_usd / 20`` a coin, whatever coins are present; the quantity
  is normalized to the venue step at the mark price at send.
- **Skips:** a coin without its 23:00 bar is flat. A coin is not traded when it
  has no demo perp, when another book holds it, or when it is in this week's
  LV pick (``lowvol.pick_symbols``), so the Monday 04:00 roll finds it free.
  Each skip is recorded with its reason.
- **Monday:** the selection run starts at 00:07 and writes the week's LV pick
  about ten minutes later, after the 00:15 run. A Monday run before 01:00
  (``LV_PICK_WAIT_H``) whose pick is not written yet waits (``lv_pick_wait``),
  so the 01:15 run decides with the pick. From 01:00 the book decides without
  it. There is no wait before the selection run has written any pick.
- **Trades:** only the difference to the targets, through ``legs.LegTrader``:
  reductions first, reduce-only; a flip goes through flat; additions last.
  Additions must fit the account caps of ``risk.py``; when they do not, all
  adding legs are scaled down pro rata and the day is recorded "capped".
  Under the entry lock only reductions run.
- **Reserves:** before the trades the whole day is sized to
  ``risk.short_budget``, which keeps the alt-short room of book LV's next
  roll and of one KRW event free. Every leg, held or new, gets the same
  factor (``reserve_scale``, and the recorded target is the scaled one), so
  held shorts above the budget are reduced that day; the day is recorded
  "capped".
- **Halt:** each run values the book from its own fills (average entry per
  leg, realized on reductions, USDT fees, unrealized at the venue mark;
  funding is not counted). Below ``HALT_AT`` of its high the book records
  "halted", then closes every leg reduce-only, and trades nothing more. A
  close cut off is finished at the next run; a rebound does not undo the
  halt. Resuming is the operator's call: move the store aside, so a fresh one
  starts.
- **A run killed midway:** each order batch is an op record saved as
  "running" before its first order. The next run's ``check()`` reads the
  order ledger (after the pair book's reconcile) for that op's keys, records
  every fill the store missed, and checks the legs against the venue. An
  interrupted decision is decided again if its window is still open.
- **A decision that fails:** the day is recorded "failed" and the run stops,
  with a duty to go flat saved first (``flat_due``). No order is sent from
  the failure: the order that raised may have filled with its reply lost.
  The next run closes every leg reduce-only after the reconcile and
  ``check()`` have recorded every fill, and keeps the duty until the book is
  flat. The day is not retried.

Blinding: the fills, the book value and its high are returns of the H-LEARN-11
read (first look 2027-04-26). They stay in the signed store. ``plan``,
``step``, ``counts`` and every audit event carry counts, statuses, quantities
and keys only. Rules: docs/operations/PERP_DEMO_RUNNER.md.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Callable, ClassVar

import numpy as np
import pandas as pd

from trading_agent.exchanges.models import OrderConstraintError
from trading_agent.execution.perp import lowvol, risk, rules
from trading_agent.execution.perp.book import QTY_TOL, Book, PairStore, PerpBookError
from trading_agent.execution.perp.legs import LegTrader, ledger_price

HOUR_MS = rules.HOUR_MS
DAY_MS = 24 * HOUR_MS
WINDOW_H = 2              # the first run in [00:00, 02:00) UTC trades the day
LV_PICK_WAIT_H = 1        # on Monday a run before 01:00 UTC waits for the week's LV pick
SLOTS = 20                # a coin gets learn_usd / 20, whatever coins are present
LEARN_USD_CAP = 4000.0    # a book above this notional is refused
HALT_AT = 0.75            # halt when the book value falls below 75% of its high
BOOK = "book"             # the store entry with the legs and the halt state
PREFIX = "lrn-"           # client key prefix of this book
TERMINAL = ("filled", "cancelled", "rejected", "expired")
DONE = ("done", "failed", "missed")   # a day with one of these is in ``seen``


def _paper():
    """The frozen paper book's modules. Imported where used: the repo root must be on sys.path."""

    from tools.learnpaper import model as M
    from tools.learnpaper import run as R

    return M, R


# ── days ───────────────────────────────────────────────────────────────────

def day_iso(d: int) -> str:
    return datetime.fromtimestamp(d * DAY_MS / 1000, UTC).date().isoformat()


def day_tag(d: int) -> str:
    return datetime.fromtimestamp(d * DAY_MS / 1000, UTC).strftime("%y%m%d")


def day_index(iso: str) -> int:
    return (date.fromisoformat(iso) - date(1970, 1, 1)).days


def window(d: int) -> tuple[int, int]:
    """Day ``d``'s trading window in ms: [00:00, 02:00) UTC."""

    return d * DAY_MS, d * DAY_MS + WINDOW_H * HOUR_MS


def decision_bar(d: int) -> pd.Timestamp:
    """The hourly bar that decides day ``d``: it opens at 23:00 UTC the day before."""

    M, _ = _paper()
    return pd.Timestamp(d * DAY_MS - (24 - M.DECISION_HOUR) * HOUR_MS, unit="ms", tz="UTC")


def has_bar(b: pd.DataFrame, t: pd.Timestamp) -> bool:
    return t in b.index and bool(np.isfinite(b.at[t, "close"]))


def predict(model, feats: dict[str, pd.DataFrame], cols: list[str], t: pd.Timestamp, coins) -> dict[str, float]:
    """The model's prediction for each coin at decision bar ``t``, as the paper's ``decide`` makes it."""

    return {c: float(model.predict(feats[c].loc[[t], cols])[0]) for c in coins}


# ── accounting (for the halt only; nothing here is printed) ───────────────

def _signed(row: dict) -> float:
    return float(row["qty"]) if row["side"] == "buy" else -float(row["qty"])


def fold_leg(leg: dict, totals: dict) -> list[dict]:
    """Fold the leg's unfolded fills into its average entry and the book's realized and fees.

    ``leg["qty"]`` already includes the fills; the position before them is
    ``qty`` less their signed sum. Fees count in USDT only. Returns the rows.
    """

    rows = list(leg.get("fills") or [])
    pos = leg["qty"] - sum(_signed(r) for r in rows)
    avg = float(leg.get("avg") or 0.0)
    for r in rows:
        if abs(pos) <= QTY_TOL:
            pos, avg = 0.0, 0.0
        q, px, d = float(r["qty"]), float(r["avg"]), _signed(r)
        new = pos + d
        if pos == 0.0 or (pos > 0) == (d > 0):
            avg = (abs(pos) * avg + q * px) / abs(new) if abs(new) > QTY_TOL else 0.0
        else:
            closed = min(q, abs(pos))
            totals["realized"] += closed * (px - avg) * (1.0 if pos > 0 else -1.0)
            if abs(new) <= QTY_TOL:
                new, avg = 0.0, 0.0
            elif (new > 0) != (pos > 0):
                avg = px
        pos = new
        totals["fees"] += float((r.get("fees") or {}).get("USDT", 0.0))
    leg["avg"] = avg
    leg["fills"] = []
    return rows


def book_value(state: dict, marks: dict[str, float]) -> float:
    """learn_usd at the start + realized + unrealized at mark - fees. Funding is not counted."""

    v = state["base_usd"] + state["realized"] - state["fees"]
    for s, leg in state["legs"].items():
        if abs(leg["qty"]) > QTY_TOL:
            v += leg["qty"] * (marks[s] - leg["avg"])
    return v


def _new_leg() -> dict:
    return {"qty": 0.0, "avg": 0.0, "fills": [], "closing_key": None}


def _tidy(leg: dict) -> dict:
    """Round away float drift from summed fills, so an exact close sends the venue's own quantity.

    ccxt truncates an amount to the step: 0.19999999999999998 would go out as
    0.199 and leave dust behind.
    """

    leg["qty"] = round(leg["qty"], 10) if abs(leg["qty"]) > QTY_TOL else 0.0
    return leg


@dataclass
class LearnBook:
    name: ClassVar[str] = "learn"
    book: Book                              # the pair book: order path, ledger, venue, audit, other books
    store: PairStore                        # signed learn_book.json; ``pairs`` holds BOOK and the op records, ``seen`` the handled days
    learn_usd: float
    paper_root: Path                        # the paper book's bars/ and models/; its ledger is never written here
    picks_path: Path                        # the selection run's lv_book/picks.jsonl
    client_factory: Callable[[], Any]       # public mainnet client (tools/newperp/fetch.py) for update_bars
    is_sealed: Callable[[str], bool]        # the operator's mask; no LEARN coin is masked

    @property
    def venue(self):
        return self.book.venue

    # ── state ──────────────────────────────────────────────────────────────

    def _state(self) -> dict | None:
        return self.store.pairs.get(BOOK)

    def held(self) -> dict[str, float]:
        """Signed quantities this book holds at the venue, by symbol."""

        st = self._state()
        if st is None:
            return {}
        return {s: leg["qty"] for s, leg in st["legs"].items() if abs(leg["qty"]) > QTY_TOL}

    def counts(self) -> dict:
        """For status.json: counts and statuses only."""

        st = self._state() or {}
        days = [v for k, v in self.store.pairs.items() if k != BOOK and v.get("kind") == "decision"]
        return {"status": st.get("status", "new"), "legs": len(self.held()),
                "days": {k: sum(1 for v in days if v.get("status") == k) for k in DONE},
                "capped": sum(1 for v in days if v.get("capped"))}

    def _ensure(self, now_ms: int) -> dict:
        st = self._state()
        if st is None:
            d = now_ms // DAY_MS
            since = d if now_ms < window(d)[1] else d + 1     # the first window this book sees open
            st = {"kind": "book", "status": "ok", "base_usd": self.learn_usd, "high": self.learn_usd,
                  "realized": 0.0, "fees": 0.0, "since": day_iso(since), "created_ms": now_ms, "legs": {}}
            self.store.pairs[BOOK] = st
            self.store.save()
            self.book.audit("perp_learn_started", since=st["since"])
        return st

    def _trader(self, tag: str) -> LegTrader:
        return LegTrader(self.book, self.store.save, lambda s, kind: f"{PREFIX}{tag}-{s[:18]}-{kind}",
                         "perp_learn_delta_skipped")

    def _fold(self) -> None:
        """Fold every leg's new fills into the accounting and into the op record named in each key."""

        st, pairs = self._state(), self.store.pairs
        for s, leg in list(st["legs"].items()):
            for r in fold_leg(leg, st):
                tag = r["key"][len(PREFIX):].split("-")[0]
                pairs.setdefault(tag, {"kind": "orphan"}).setdefault("fills", []).append({**r, "symbol": s})
            if abs(leg["qty"]) <= QTY_TOL and not leg.get("closing_key"):
                del st["legs"][s]

    # ── each run, before the pair book's position check ────────────────────

    def check(self) -> None:
        """Record fills an interrupted op left behind, then check every leg against the venue."""

        st = self._state()
        if st is None:
            return
        changed = self._settle()
        if any(leg.get("fills") for leg in st["legs"].values()):
            self._fold()
            changed = True
        if changed:
            self.store.save()
        if not st["legs"]:
            return
        positions = self.venue.positions()
        for s, leg in st["legs"].items():
            pos = positions.get(s)
            q = pos.qty if pos is not None else 0.0
            if abs(q - leg["qty"]) > QTY_TOL * max(1.0, abs(leg["qty"])):
                self.book.audit("perp_learn_mismatch", symbol=s)
                raise PerpBookError(f"LEARN {s}: venue {q} differs from book {leg['qty']}")

    def _settle(self) -> bool:
        """Fills of a killed or failed op, read from the order ledger by the op's key prefix."""

        pairs, st = self.store.pairs, self._state()
        ops = [(tag, op) for tag, op in pairs.items() if tag != BOOK and (
            op.get("status") == "running" or (op.get("status") == "failed" and not op.get("settled")))]
        if not ops:
            return False
        ledger = self.book.orders.state.order_ledger
        recorded = {r["key"] for k, op in pairs.items() if k != BOOK for r in op.get("fills") or []}
        recorded |= {r["key"] for leg in st["legs"].values() for r in leg.get("fills") or []}
        for tag, op in ops:
            prefix = f"{PREFIX}{tag}-"
            keys = sorted((k for k in ledger if k.startswith(prefix) and k not in recorded),
                          key=lambda k: (str(ledger[k].get("created_at") or ""), k))
            found = 0
            for key in keys:
                rec = ledger[key]
                filled = float(rec.get("filled_quantity") or 0.0)
                if str(rec.get("status") or "") not in TERMINAL or filled <= QTY_TOL:
                    continue
                s, side = str(rec["symbol"]), str(rec.get("side") or "").lower()
                avg, src = ledger_price(self.book, key, rec)
                row: dict[str, Any] = {"key": key, "side": side, "qty": filled, "avg": avg,
                                       "fees": dict(rec.get("fees") or {}), "mark": None, "from": "ledger"}
                if src:
                    row["avg_from"] = src
                leg = st["legs"].setdefault(s, _new_leg())
                leg["qty"] += filled if side == "buy" else -filled
                _tidy(leg).setdefault("fills", []).append(row)
                recorded.add(key)
                found += 1
            for leg in st["legs"].values():
                if str(leg.get("closing_key") or "").startswith(prefix):
                    leg["closing_key"] = None
            if op["status"] == "running":
                op["status"] = "interrupted"
                self.book.audit("perp_learn_interrupted", op=tag, recorded=found)
            op["settled"] = True
        return True

    # ── the day ────────────────────────────────────────────────────────────

    def step(self, now_ms: int, *, entry_lock: str | None = None) -> dict:
        """Halt check; close a missed day; decide today inside its window."""

        if not 0 < self.learn_usd <= LEARN_USD_CAP:
            raise PerpBookError(f"LEARN book notional {self.learn_usd} outside (0, {LEARN_USD_CAP}]")
        st = self._ensure(now_ms)
        d = now_ms // DAY_MS
        out: dict[str, Any] = {"day": day_iso(d)}
        due = st.get("flat_due")
        if due:
            # A decision that failed: every leg off, reduce-only, now that the reconcile and ``check()`` have
            # recorded every fill its orders left. Kept until the book is flat, so a close that fails is retried.
            n = self._close_all(due, "failed") if self.held() else 0
            if not self.held():
                st.pop("flat_due", None)
                self.store.save()
            self.book.audit("perp_learn_failed_closed", op=due, closed=n)
            out["closed"] = n
        if st["status"] == "halted":
            if self.held():      # a halt close cut off is finished here, reduce-only
                n = self._close_all(f"{day_tag(d)}h", "halt")
                self.book.audit("perp_learn_halted", reason=str(st.get("halt_reason") or "drawdown"), closed=n)
                out["closed"] = n
            out.update(status="halted", held=len(self.held()))
            return out
        if self._breach():
            # The halt is saved before the first close, so a close cut off cannot undo it.
            st.update(status="halted", halt_reason="drawdown", halted_ms=now_ms)
            self.store.save()
            n = self._close_all(f"{day_tag(d)}h", "halt")
            self.book.audit("perp_learn_halted", reason="drawdown", closed=n)
            out.update(status="halted", closed=n, held=len(self.held()))
            return out
        missed = self._missed_days(d, now_ms)
        if missed:
            out.update(self._miss(missed))
        start, end = window(d)
        if start <= now_ms < end and day_iso(d) not in self.store.seen and day_iso(d) >= st["since"]:
            out.update(self._decide(d, now_ms, entry_lock))
        out.update(status=st["status"], held=len(self.held()))
        return out

    def _breach(self) -> bool:
        """Is the book value below HALT_AT of its high? Updates the high. Only 'halted' or 'ok' leaves here."""

        st = self._state()
        held = self.held()
        marks: dict[str, float] = {}
        if held:
            _, marks = risk.account(self.venue)
            for s in held:
                if not marks.get(s, 0.0) > 0:
                    marks[s] = self.venue.mark_price(s)
        value = book_value(st, marks)
        if value > st["high"]:
            st["high"] = value
            self.store.save()
        return value < HALT_AT * st["high"]

    def _missed_days(self, d: int, now_ms: int) -> list[int]:
        last = d if now_ms >= window(d)[1] else d - 1
        seen = set(self.store.seen)
        return [x for x in range(day_index(self._state()["since"]), last + 1) if day_iso(x) not in seen]

    def _miss(self, days: list[int]) -> dict:
        n = self._close_all(f"{day_tag(days[0])}m", "missed") if self.held() else 0
        reasons: set[str] = set()
        for x in days:
            tag = day_tag(x)
            rec = self.store.pairs.get(tag) or {"kind": "decision", "day": day_iso(x)}
            reason = rec.get("wait") or ("interrupted" if rec.get("status") == "interrupted" else "no_run")
            rec.update(status="missed", reason=reason)
            self.store.pairs[tag] = rec
            self.store.seen.append(day_iso(x))
            reasons.add(reason)
        self.store.save()
        self.book.audit("perp_learn_day_missed", days=[day_iso(x) for x in days], reasons=sorted(reasons), closed=n)
        return {"missed": len(days), "closed": n}

    def _close_all(self, tag: str, reason: str) -> int:
        """Every leg off at market, reduce-only, its exact quantity. Never blocked by a cap."""

        st = self._state()
        op = self.store.pairs.setdefault(tag, {"kind": "close", "reason": reason})
        op.update(status="running", settled=False)
        self.store.save()
        trader = self._trader(tag)
        n = 0
        for s, leg in list(st["legs"].items()):
            if abs(_tidy(leg)["qty"]) > QTY_TOL:
                trader.trade(s, -leg["qty"], leg, price=self._mark_or_none(s), exact=True)
                _tidy(leg)
                n += 1
        self._fold()
        op["status"] = "done"
        self.store.save()
        return n

    def _mark_or_none(self, symbol: str) -> float | None:
        """The mark at send, for the fill row only: an exact close never needs it, so a failed read is None."""

        try:
            return self.venue.mark_price(symbol)
        except Exception:
            return None

    def _wait(self, d: int, reason: str) -> dict:
        """No decision this run; a later run inside the window tries again."""

        tag = day_tag(d)
        rec = self.store.pairs.setdefault(tag, {"kind": "decision", "day": day_iso(d)})
        if rec.get("status") != "interrupted":
            rec["status"] = "waiting"
        rec["wait"] = reason
        self.store.save()
        self.book.audit("perp_learn_wait", day=day_iso(d), reason=reason)
        return {"wait": reason}

    def _lv_pick_pending(self, d: int, now_ms: int) -> bool:
        """Is it Monday before ``LV_PICK_WAIT_H`` with this week's LV pick not written yet?

        Only once the selection run has written a pick: a book without the LV paper never waits.
        """

        monday, monday_ms = lowvol.week_of(now_ms)
        if monday_ms != d * DAY_MS or now_ms >= monday_ms + LV_PICK_WAIT_H * HOUR_MS:
            return False
        return lowvol.first_week(self.picks_path) is not None and lowvol.load_pick(self.picks_path, monday) is None

    def _bars_ready(self) -> bool:
        M, R = _paper()
        return all(R.bars_path(self.paper_root, c).exists() for c in M.COINS)

    def _model(self, bars: dict[str, pd.DataFrame], t: pd.Timestamp, *, fit: bool):
        """The quarter's model through the paper's ``model_for``, or the reason there is none.

        A missing model is fitted, and recorded for the paper too, only when
        every coin's bars reach the quarter start: then every label the fit
        uses has closed, and the paper's own fit would be the same. A dry
        run never fits.
        """

        M, R = _paper()
        q = M.quarter_of(t)
        tag = R.quarter_tag(q)
        if not (self.paper_root / "models" / f"{tag}.pkl").exists():
            if not fit:
                return "model_missing"
            if min(b.index[-1] for b in bars.values()) < q:
                return "model_wait"
        try:
            return R.model_for(self.paper_root, bars, q,
                               log=lambda msg: self.book.audit("perp_learn_model_fitted", quarter=tag))
        except SystemExit:      # the paper refuses a model whose hash differs, or bars that end too early
            if fit:
                self.book.audit("perp_learn_model_refused", quarter=tag)
            return "model_refused"

    def _targets(self, preds: dict[str, float], now_ms: int) -> tuple[dict[str, int], dict[str, int], dict[str, str]]:
        """Model positions; the side to trade per demo symbol; skipped coins with their reasons."""

        M, _ = _paper()
        pos = M.positions(preds)
        trading = self.venue.trading_symbols()
        taken = self.book.taken(self.name)
        pick = lowvol.pick_symbols(self.picks_path, now_ms, trading)
        want: dict[str, int] = {}
        skipped: dict[str, str] = {}
        for c in M.COINS:
            if c not in pos:
                skipped[c] = "missing_bar"
                continue
            if pos[c] == 0:
                continue
            s = f"{c}USDT"
            if s not in trading:
                skipped[c] = "not_on_demo"
            elif s in taken:
                skipped[c] = f"held_by_{taken[s]}"
            elif s in pick:
                skipped[c] = "lv_pick"
            else:
                want[s] = pos[c]
        return pos, want, skipped

    def _decide(self, d: int, now_ms: int, entry_lock: str | None) -> dict:
        M, R = _paper()
        if any(self.is_sealed(c) for c in M.COINS):
            raise PerpBookError("a masked coin reached the LEARN book")
        st = self._state()
        if abs(st["base_usd"] - self.learn_usd) > 1e-9:
            return self._wait(d, "size_changed")     # a new size is a new book: move the store aside
        if not self._bars_ready():
            return self._wait(d, "no_bar_store")
        if self._lv_pick_pending(d, now_ms):
            return self._wait(d, "lv_pick_wait")
        try:
            R.update_bars(self.client_factory(), self.paper_root, now_ms)
        except Exception as exc:  # a fetch that failed leaves the day to a later run in the window
            self.book.audit("perp_learn_bars_failed", error=type(exc).__name__)
            return self._wait(d, "bars_failed")
        t = decision_bar(d)
        bars = R.load_bars(self.paper_root)
        have = [c for c in M.COINS if has_bar(bars[c], t)]
        if not have:
            return self._wait(d, "no_bars")
        got = self._model(bars, t, fit=True)
        if isinstance(got, str):
            return self._wait(d, got)
        mdl, cols, mtag = got
        preds = predict(mdl, M.features(bars), cols, t, have)
        pos, want, skipped = self._targets(preds, now_ms)

        legs = st["legs"]
        marks = {s: self.venue.mark_price(s) for s in sorted(set(want) | set(self.held()))}
        f = self._reserve_scale(want)
        target: dict[str, float] = {}
        for s, side in want.items():
            try:
                target[s] = side * self.venue.normalize(s, f * self.learn_usd / SLOTS / marks[s], marks[s])
            except OrderConstraintError:
                skipped[s[:-4]] = "below_venue_minimum"
        tag = day_tag(d)
        rec = self.store.pairs.setdefault(tag, {"kind": "decision", "day": day_iso(d)})
        rec.pop("wait", None)
        rec.update(status="running", settled=False, bar=R.iso(t), model=mtag, learn_usd=self.learn_usd,
                   pred=preds, pos=pos, missing=[c for c in M.COINS if c not in have], skipped=skipped,
                   target=target, capped=f < 1.0, entry_lock=entry_lock or None, decided_ms=now_ms)
        if f < 1.0:
            rec["reserve_scale"] = round(f, 6)
        rec["attempts"] = int(rec.get("attempts") or 0) + 1
        self.store.save()
        try:
            n = self._trade(tag, rec, legs, target, marks, entry_lock)
            self._fold()
            rec["status"] = "done"
            self.store.seen.append(day_iso(d))
            self.store.save()
        except Exception:
            # No order from here: the order that raised may have an unknown outcome, so the legs are not
            # known yet. The close is a duty the next run carries out after the reconcile and ``check()``.
            self.book.audit("perp_learn_decision_failed", day=day_iso(d))
            rec["status"] = "failed"
            self.store.seen.append(day_iso(d))
            st["flat_due"] = f"{tag}f"
            self.store.save()
            raise
        out = {"decided": day_iso(d), "long": sum(1 for v in pos.values() if v > 0),
               "short": sum(1 for v in pos.values() if v < 0), "flat": sum(1 for v in pos.values() if v == 0),
               "skipped": len(skipped), "missing": len(rec["missing"]), "capped": rec["capped"], "orders": n}
        if entry_lock:
            out["held_back"] = len(rec.get("held_back") or [])
        self.book.audit("perp_learn_decided", **out)
        return out

    def _reserve_scale(self, want: dict[str, int]) -> float:
        """The factor in [0, 1] that sizes the whole day inside ``risk.short_budget``.

        The budget leaves the reserves of the books beside this one free (book
        LV's roll, book KRW's event); the day's shorts are counted
        ``SHORT_DRIFT`` above the mark. Every leg, held or new, is sized by it,
        so a held short above the budget is reduced at this decision. With no
        reserving book in the run it is 1: the adding legs still meet the caps.
        """

        shorts = sum(1 for s, side in want.items() if side < 0 and s != rules.HEDGE)
        reserves = [(o.alt_short_reserve(), o.held()) for o in self.book.others
                    if o is not self and hasattr(o, "alt_short_reserve")]
        if not shorts or not reserves:
            return 1.0
        budget = risk.short_budget(self.venue, self.held(), reserves)
        need = risk.SHORT_DRIFT * shorts * self.learn_usd / SLOTS
        return min(1.0, max(0.0, budget) / need)

    def _trade(self, tag: str, rec: dict, legs: dict, target: dict[str, float], marks: dict[str, float],
               entry_lock: str | None) -> int:
        """Reductions, then the caps, then additions. Returns the number of orders sent."""

        trader = self._trader(tag)
        n = 0
        for s in sorted(set(target) | set(self.held())):
            leg = _tidy(legs.setdefault(s, _new_leg()))
            if trader.reduce_toward(s, leg, target.get(s, 0.0), price=marks[s]) is not None:
                n += 1
            _tidy(leg)
        adds = {s: tgt for s, tgt in target.items() if abs(tgt) > abs(legs[s]["qty"]) + QTY_TOL}
        if adds and entry_lock:
            rec["held_back"] = sorted(adds)
            return n
        if adds:
            add_gross = sum((abs(tgt) - abs(legs[s]["qty"])) * marks[s] for s, tgt in adds.items())
            add_short = sum((abs(tgt) - abs(legs[s]["qty"])) * marks[s] for s, tgt in adds.items() if tgt < 0)
            f = risk.room(self.venue).scale(add_gross, add_short)
            if f < 1.0:
                rec.update(capped=True, scale=round(f, 6))
                adds = {s: legs[s]["qty"] + f * (tgt - legs[s]["qty"]) for s, tgt in adds.items()}
        for s, tgt in sorted(adds.items()):
            if abs(tgt) <= abs(legs[s]["qty"]) + QTY_TOL:
                continue
            if abs(legs[s]["qty"]) <= QTY_TOL:
                self.venue.prepare(s)
            if trader.add_toward(s, legs[s], tgt, price=marks[s]) is not None:
                n += 1
            _tidy(legs[s])
        return n

    # ── dry run ────────────────────────────────────────────────────────────

    def plan(self, now_ms: int) -> dict:
        """What a decision now would do. Nothing is fetched, fitted, signed, sent or written; counts only."""

        M, R = _paper()
        st = self._state()
        d = now_ms // DAY_MS
        start, end = window(d)
        out: dict[str, Any] = {"day": day_iso(d), "in_window": start <= now_ms < end,
                               "status": st["status"] if st else "new", "decided": day_iso(d) in self.store.seen,
                               "held": len(self.held())}
        if st is not None and st.get("flat_due"):
            out["flat_due"] = True
        if st is not None and st["status"] == "halted":
            return out
        if st is not None and abs(st["base_usd"] - self.learn_usd) > 1e-9:
            out["size_changed"] = True
        if not self._bars_ready():
            out["bars"] = "no_bar_store"
            return out
        if out["in_window"] and self._lv_pick_pending(d, now_ms):
            out["lv_pick_wait"] = True
        t = decision_bar(d)
        bars = R.load_bars(self.paper_root)
        have = [c for c in M.COINS if has_bar(bars[c], t)]
        out.update(bar=R.iso(t), missing=len(M.COINS) - len(have), model=R.quarter_tag(M.quarter_of(t)))
        if not have:
            return out
        got = self._model(bars, t, fit=False)
        if isinstance(got, str):
            out["model_state"] = got
            return out
        mdl, cols, _ = got
        pos, want, skipped = self._targets(predict(mdl, M.features(bars), cols, t, have), now_ms)
        reasons: dict[str, int] = {}
        for r in skipped.values():
            key = "held_by_other" if r.startswith("held_by_") else r
            reasons[key] = reasons.get(key, 0) + 1
        out.update(model_state="ok", long=sum(1 for v in want.values() if v > 0),
                   short=sum(1 for v in want.values() if v < 0), flat=sum(1 for v in pos.values() if v == 0),
                   skipped=reasons)
        return out
