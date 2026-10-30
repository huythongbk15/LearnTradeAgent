"""M3 rehearsal: the operational track over many scheduled cycles, offline.

The real runner runs cycle after cycle on a simulated 2 h schedule against a
local three-symbol spot venue whose prices move between cycles and whose
resting stops fill when the price trades through them. After every cycle the
books must agree: venue holdings = signed order ledger = lifecycle, every held
position carries exactly one venue stop that the ledger knows, no flat symbol
keeps a stop, nothing is left reserved or open. At the end the M3 gate of
scripts/ops/daily_check.py must pass. No network IO.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import ROUND_DOWN, Decimal
import json
import math
import os
import random
import sys
import time
import types

import polars as pl
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts", "ops"))
import daily_check
import live_enhanced_ma_binance as runner

from tests.test_operational_track import KEY, authorization
from tests.test_promoted_live_admission import install_exchange_boundary
from trading_agent.exchanges.models import OrderConstraintError
from trading_agent.execution.canonical.instrument_registry import get_instrument_rules
from trading_agent.execution.live_safety import LiveRiskStateStore
from trading_agent.execution.operational_authorization import write_authorization
from trading_agent.execution.operational_driver import flat_below_usd

SYMBOLS = ("BTC/USDT", "SOL/USDT", "AVAX/USDT")
START = {"BTC/USDT": 77_000.0, "SOL/USDT": 95.0, "AVAX/USDT": 8.2}
HOURLY_VOL = {"BTC/USDT": 0.005, "SOL/USDT": 0.008, "AVAX/USDT": 0.009}
CADENCE = timedelta(hours=2)
VENUE_MIN_NOTIONAL = 5.0  # Binance testnet NOTIONAL filter


# ── Simulated clock ─────────────────────────────────────────────────────────

_REAL_DATETIME = datetime
_REAL_TIME = time


class SimClock:
    """Wall time shifted so that cycle k starts k cadences after the first."""

    def __init__(self) -> None:
        self.offset = timedelta(0)
        self.base = _REAL_DATETIME.now(UTC)

    def begin_cycle(self, k: int) -> None:
        self.offset = self.base + k * CADENCE - _REAL_DATETIME.now(UTC)

    def now(self) -> datetime:
        return _REAL_DATETIME.now(UTC) + self.offset


class _SimMeta(type):
    def __instancecheck__(cls, obj):
        return isinstance(obj, _REAL_DATETIME)

    def __subclasscheck__(cls, sub):
        return issubclass(sub, _REAL_DATETIME)


def _sim_datetime(clock: SimClock):
    class SimDatetime(_REAL_DATETIME, metaclass=_SimMeta):
        @classmethod
        def now(cls, tz=None):
            return _REAL_DATETIME.now(tz) + clock.offset

        @classmethod
        def utcnow(cls):
            return _REAL_DATETIME.utcnow() + clock.offset

    return SimDatetime


class _SimTime(types.ModuleType):
    def __init__(self, clock: SimClock) -> None:
        super().__init__("time")
        self._clock = clock

    def time(self):
        return _REAL_TIME.time() + self._clock.offset.total_seconds()

    def __getattr__(self, name):
        return getattr(_REAL_TIME, name)


def install_sim_clock(monkeypatch, clock: SimClock) -> None:
    """Point every loaded runtime module's wall clock at the simulated one."""
    sim_datetime, sim_time = _sim_datetime(clock), _SimTime(clock)
    prefixes = ("trading_agent", "live_enhanced_ma_binance", "check_live_audit", "daily_check")
    for name, module in list(sys.modules.items()):
        if module is None or not name.startswith(prefixes):
            continue
        if getattr(module, "datetime", None) is _REAL_DATETIME:
            monkeypatch.setattr(module, "datetime", sim_datetime)
        if getattr(module, "time", None) is _REAL_TIME:
            monkeypatch.setattr(module, "time", sim_time)


# ── Venue ───────────────────────────────────────────────────────────────────


@dataclass
class Bar:
    open: float
    high: float
    low: float
    close: float


@dataclass
class Venue:
    """Spot venue with Binance-like filters: lot step, minimum notional, free balance.

    Market orders fill at the last price. Stops rest, lock their quantity and
    fill when a bar trades through the stop (at the stop, or the open on a gap).
    """

    clock: SimClock
    seed: int = 7
    cash: Decimal = Decimal("90000")
    held: dict = field(default_factory=dict)
    bars: dict = field(default_factory=dict)
    orders: dict = field(default_factory=dict)
    fills: list = field(default_factory=list)  # (symbol, side, qty, price, kind)
    base_fee_rate: Decimal = Decimal("0")
    # Fault hooks.
    vol_scale: float = 1.0
    stop_fills_on_cancel: bool = False  # the venue fills a stop as it is cancelled
    timeout_after_accept: set = field(default_factory=set)  # symbols, one shot
    ticker_skew: dict = field(default_factory=dict)  # symbol -> quote / last close

    def __post_init__(self) -> None:
        self.rng = random.Random(self.seed)
        for symbol in SYMBOLS:
            self.held[symbol] = Decimal(0)
            price, history = START[symbol], []
            for _ in range(400):
                history.append(self._bar(symbol, price))
                price = history[-1].close
            self.bars[symbol] = history

    # Prices
    def _bar(self, symbol: str, open_: float) -> Bar:
        vol = HOURLY_VOL[symbol] * self.vol_scale
        close = open_ * math.exp(self.rng.gauss(0.0, vol))
        high = max(open_, close) * (1 + abs(self.rng.gauss(0.0, vol / 2)))
        low = min(open_, close) * (1 - abs(self.rng.gauss(0.0, vol / 2)))
        return Bar(open_, high, low, close)

    def price(self, symbol: str) -> Decimal:
        return Decimal(str(round(self.bars[symbol][-1].close, 8)))

    def frame(self, symbol: str) -> pl.DataFrame:
        bars = self.bars[symbol][-400:]
        end = self.clock.now().replace(minute=0, second=0, microsecond=0)
        return pl.DataFrame(
            {
                "timestamp": [end - timedelta(hours=len(bars) - i) for i in range(len(bars))],
                "open": [b.open for b in bars],
                "high": [b.high for b in bars],
                "low": [b.low for b in bars],
                "close": [b.close for b in bars],
                "volume": [10_000.0] * len(bars),
            }
        )

    def advance(self, hours: int = 2) -> None:
        """Trade ``hours`` bars; resting stops fill when a bar trades through them."""
        for symbol in SYMBOLS:
            for _ in range(hours):
                bar = self._bar(symbol, self.bars[symbol][-1].close)
                self.bars[symbol].append(bar)
                for order in self._open_stops(symbol):
                    if bar.low <= order["stop_price"]:
                        self._fill_stop(order, min(order["stop_price"], bar.open))

    def shock(self, change: float) -> None:
        """Every price gaps by ``change`` in one bar; stops fill at the gap open."""
        for symbol in SYMBOLS:
            open_ = self.bars[symbol][-1].close * (1 + change)
            bar = Bar(open_, open_ * 1.001, open_ * 0.999, open_)
            self.bars[symbol].append(bar)
            for order in self._open_stops(symbol):
                if bar.low <= order["stop_price"]:
                    self._fill_stop(order, min(order["stop_price"], bar.open))

    def _fill_stop(self, order: dict, price: float) -> None:
        quantity, fill = Decimal(str(order["qty"])), Decimal(str(round(price, 8)))
        self.held[order["symbol"]] -= quantity
        self.cash += quantity * fill
        order.update(status="filled", filled_qty=float(quantity), avg_fill_price=float(fill),
                     quote_cost=float(quantity * fill))
        self.fills.append((order["symbol"], "sell", quantity, fill, "stop"))

    # Exchange boundary
    def _open_stops(self, symbol: str) -> list[dict]:
        return [o for o in self.orders.values()
                if o["symbol"] == symbol and o["type"] == "stop" and o["status"] == "open"]

    def _locked(self, symbol: str) -> Decimal:
        return sum((Decimal(str(o["qty"])) for o in self._open_stops(symbol)), Decimal(0))

    def get_account(self):
        value = sum(self.held[s] * self.price(s) for s in SYMBOLS)
        return {"equity": float(self.cash + value), "cash": float(self.cash)}

    def get_positions(self):
        return [
            {
                "symbol": symbol,
                "qty": float(self.held[symbol]),
                "free_qty": float(self.held[symbol] - self._locked(symbol)),
                "locked_qty": float(self._locked(symbol)),
                "market_value": float(self.held[symbol] * self.price(symbol)),
                "current_price": float(self.price(symbol)),
            }
            for symbol in SYMBOLS
            if self.held[symbol] > 0
        ]

    def get_ticker(self, symbol):
        started = time.monotonic()
        price = float(self.price(symbol.pair)) * self.ticker_skew.get(symbol.pair, 1.0)
        return {"timestamp": self.clock.now(), "request_started_at": started,
                "received_at": time.monotonic(), "last": price, "bid": price, "ask": price}

    def get_order_book(self, symbol, limit=50):
        started = time.monotonic()
        price = float(self.price(symbol.pair))
        return {"timestamp": self.clock.now(), "request_started_at": started,
                "received_at": time.monotonic(), "bids": [(price, 1e6)], "asks": [(price, 1e6)]}

    def _step(self, symbol: str) -> Decimal:
        return Decimal(str(get_instrument_rules(symbol).qty_step))

    def normalize_order_amount(self, symbol, amount, *, reference_price):
        """As CCXTAdapter.normalize_order_amount: precision, then hard filters."""
        step = self._step(symbol.pair)
        normalized = (Decimal(str(amount)) / step).to_integral_value(ROUND_DOWN) * step
        if normalized <= 0:
            raise OrderConstraintError(f"amount rounds to zero for {symbol.pair}",
                                       constraint="amount_zero")
        if normalized * Decimal(str(reference_price)) < Decimal(str(VENUE_MIN_NOTIONAL)):
            raise OrderConstraintError(f"order notional is below market minimum for {symbol.pair}",
                                       constraint="minimum_notional")
        return float(normalized)

    def place_order(self, order):
        symbol = order.symbol.pair
        qty = Decimal(str(order.size))
        side, kind = order.side.value, order.type.value
        price = self.price(symbol)
        if qty <= 0 or qty % self._step(symbol) != 0:
            raise RuntimeError(f"binance -1013 LOT_SIZE: {symbol} {qty}")
        if kind != "stop" and qty * price < Decimal(str(VENUE_MIN_NOTIONAL)):
            raise RuntimeError(f"binance -1013 NOTIONAL: {symbol} {qty * price}")
        free = self.held[symbol] - self._locked(symbol)
        if side == "sell" and qty > free:
            raise RuntimeError(f"binance -2010 insufficient balance: {symbol} {qty} > {free}")
        if side == "buy" and qty * price > self.cash:
            raise RuntimeError("binance -2010 insufficient balance: USDT")
        result = {
            "id": f"venue-{len(self.orders) + 1}",
            "client_order_id": order.client_order_id,
            "symbol": symbol,
            "side": side,
            "type": kind,
            "qty": float(qty),
            "status": "open" if kind == "stop" else "filled",
            "filled_qty": 0.0,
            "avg_fill_price": 0.0,
            "fees": {"USDT": 0.0},
            "quote_cost": 0.0,
            "stop_price": float(order.stop_price) if kind == "stop" else None,
            "error": None,
        }
        if kind != "stop":
            fee = qty * self.base_fee_rate if side == "buy" else Decimal(0)
            self.held[symbol] += (qty - fee) if side == "buy" else -qty
            self.cash += -qty * price if side == "buy" else qty * price
            result.update(filled_qty=float(qty), avg_fill_price=float(price),
                          quote_cost=float(qty * price))
            if fee:
                result["fees"] = {symbol.split("/")[0]: float(fee)}
            self.fills.append((symbol, side, qty, price, "market"))
        self.orders[order.client_order_id] = result
        if kind != "stop" and symbol in self.timeout_after_accept:
            self.timeout_after_accept.discard(symbol)
            raise TimeoutError("venue accepted the order; the response was lost")
        return dict(result)

    def get_order_by_client_id(self, client_order_id, symbol):
        value = self.orders.get(client_order_id)
        return dict(value) if value else None

    def cancel_order(self, order_id, symbol):
        for order in self.orders.values():
            if order["id"] == order_id:
                if self.stop_fills_on_cancel and order["type"] == "stop" and order["status"] == "open":
                    self.stop_fills_on_cancel = False
                    self._fill_stop(order, order["stop_price"])
                if order["status"] != "open":
                    raise RuntimeError("binance -2011 Unknown order sent")
                order["status"] = "cancelled"
                return True
        raise RuntimeError("binance -2011 Unknown order sent")


# ── Rehearsal ───────────────────────────────────────────────────────────────


def _argv(tmp_path):
    """The scheduled cycle's command line (scripts/ops/run_binance_testnet_cycle.sh)."""
    return [
        "live_enhanced_ma_binance.py", "--testnet", "--profile", "testnet", "--execute",
        "--state-file", str(tmp_path / "risk.json"),
        "--audit-log", str(tmp_path / "audit.jsonl"),
    ]


def _audit(tmp_path) -> list[dict]:
    path = tmp_path / "audit.jsonl"
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


class Rehearsal:
    def __init__(self, tmp_path, monkeypatch, *, seed=7, venue=None, **terms):
        self.tmp_path, self.monkeypatch = tmp_path, monkeypatch
        self.clock = SimClock()
        self.venue = Venue(self.clock, seed=seed, **(venue or {}))
        install_exchange_boundary(monkeypatch, tmp_path, self.venue, self.venue.frame(SYMBOLS[0]))
        monkeypatch.setattr(
            runner.ServerClock, "_fetch_server_time", lambda _self: self.clock.now().timestamp() * 1000
        )
        monkeypatch.setattr(runner, "get_recent_df", lambda pair, monitor=None: self.venue.frame(pair))
        issued = self.clock.now() - timedelta(minutes=5)
        values = dict(
            symbols=SYMBOLS, max_open_positions=len(SYMBOLS),
            issued_at=issued, expires_at=issued + timedelta(days=14),
        )
        values.update(terms)
        self.authorization = authorization(**values)
        write_authorization(tmp_path / "operational_authorization.json", self.authorization, key=KEY)
        self.cycle = 0
        self.stop_prices: dict[tuple[str, int], list[float]] = {}

    def store(self) -> LiveRiskStateStore:
        return LiveRiskStateStore(self.tmp_path / "risk.json", integrity_key=KEY)

    def run_cycle(self) -> int:
        self.clock.begin_cycle(self.cycle)
        install_sim_clock(self.monkeypatch, self.clock)
        # The entrypoint, so that every run is tied to its terminal audit event.
        self.monkeypatch.setattr(sys, "argv", _argv(self.tmp_path))
        try:
            return runner.main()
        finally:
            self.cycle += 1

    def outcomes(self) -> list[str]:
        return [e["event"] for e in _audit(self.tmp_path)
                if e["event"] in ("run_completed", "run_failed", "run_interrupted")]

    def check_books(self) -> None:
        """Venue = ledger = lifecycle after a cycle; every held position protected."""
        store = self.store()
        where = f"after cycle {self.cycle - 1}"
        assert store.unfinished_orders() == {}, where
        assert store.state.locked_reason is None, where
        assert daily_check.open_lifecycle_intents(
            self.tmp_path / "accounts", daily_check.resting_stops(store)
        ) == [], where
        for symbol in SYMBOLS:
            held = self.venue.held[symbol]
            base = symbol.split("/")[0]
            ledger = Decimal(0)
            for record in store.state.order_ledger.values():
                if record.get("symbol") != symbol:
                    continue
                filled = Decimal(str(record.get("filled_quantity") or 0))
                ledger += filled if record.get("side") == "BUY" else -filled
                # A fee charged in the base asset leaves the account with less.
                ledger -= Decimal(str((record.get("fees") or {}).get(base) or 0))
            stopped = sum(
                (q for s, side, q, _p, kind in self.venue.fills if s == symbol and kind == "stop"),
                Decimal(0),
            )
            assert ledger - stopped == held, f"{where}: {symbol} ledger {ledger} - stops {stopped} != venue {held}"
            stops = self.venue._open_stops(symbol)
            active = store.protective_order_state(symbol).get("active")
            if float(held * self.venue.price(symbol)) >= flat_below_usd(symbol):
                step = self.venue._step(symbol)
                assert len(stops) == 1, f"{where}: {symbol} held {held} with stops {stops}"
                # The stop covers every whole lot; a sub-lot remainder cannot be sold.
                assert Decimal(str(stops[0]["qty"])) == (held / step).to_integral_value(ROUND_DOWN) * step, \
                    f"{where}: {symbol} stop qty"
                assert active and active["client_order_id"] == stops[0]["client_order_id"], where
                # Within one lifecycle (since its BUY) the stop only tightens.
                lifecycle = sum(1 for f in self.venue.fills if f[0] == symbol and f[1] == "buy")
                trail = self.stop_prices.setdefault((symbol, lifecycle), [])
                trail.append(stops[0]["stop_price"])
                assert trail == sorted(trail), f"{where}: {symbol} stop widened {trail}"
            else:
                assert stops == [], f"{where}: {symbol} flat with a resting stop {stops}"
                assert not active, f"{where}: {symbol} flat but ledger keeps stop {active}"

    def m3_line(self, capsys) -> tuple[str, list[str]]:
        from check_live_audit import _runs, load_events

        events = load_events(self.tmp_path / "audit.jsonl")
        runs = [r for r in _runs(sorted(events, key=lambda e: e["_timestamp"])) if r["execute"]]
        capsys.readouterr()
        issues = daily_check.m3_progress(events, runs, now=self.clock.now())
        return capsys.readouterr().out, issues


def test_one_cycle_opens_and_protects_every_flat_symbol(tmp_path, monkeypatch):
    """A protected BUY fill must not block the next BUY in the same batch."""
    rehearsal = Rehearsal(tmp_path, monkeypatch)
    assert rehearsal.run_cycle() == 0
    rehearsal.check_books()
    assert sorted(f[0] for f in rehearsal.venue.fills if f[1] == "buy") == sorted(SYMBOLS)
    lifecycle, _ = runner.build_canonical_execution_stack(
        rehearsal.venue,
        event_store_path=str(next((tmp_path / "accounts").glob("*/events.db"))),
    )
    assert "protection_required" not in {
        state.value for state in lifecycle.state.protection_state.values()
    }


@pytest.mark.slow
@pytest.mark.serial
def test_rehearsal_three_symbols_forty_cycles_passes_m3(tmp_path, monkeypatch, capsys):
    rehearsal = Rehearsal(tmp_path, monkeypatch)
    for _ in range(40):
        assert rehearsal.run_cycle() == 0, f"cycle {rehearsal.cycle - 1} failed"
        rehearsal.check_books()
        rehearsal.venue.advance()
    assert set(rehearsal.outcomes()) == {"run_completed"}
    buys = [f for f in rehearsal.venue.fills if f[1] == "buy"]
    assert buys and all(q * p <= Decimal("20.0001") for _s, _side, q, p, _k in buys)
    line, issues = rehearsal.m3_line(capsys)
    print(line)
    assert issues == []
    assert "PASSED" in line
    # Every exit is the track's own: its sell after the hold, or the venue stop.
    assert "other 0;" in line


# ── Faults: each must leave the books consistent ───────────────────────────

CALM = {"vol_scale": 0.05}  # stops stay out of reach unless a fault moves the price


def _blocked(tmp_path) -> list[str | None]:
    return [e["details"]["entries_blocked"] for e in _audit(tmp_path)
            if e["event"] == "operational_track_active"]


def test_a_stop_filled_while_the_exit_cancels_it_is_booked_not_failed(tmp_path, monkeypatch):
    rehearsal = Rehearsal(tmp_path, monkeypatch, venue=CALM, tight_stop_every=0)
    for _ in range(2):  # buy, then hold
        assert rehearsal.run_cycle() == 0
        rehearsal.check_books()
        rehearsal.venue.advance()
    rehearsal.venue.stop_fills_on_cancel = True  # the exit cycle races its stop
    assert rehearsal.run_cycle() == 0
    rehearsal.check_books()
    raced = [e for e in _audit(tmp_path) if e["event"] == "exit_superseded_by_protective_stop"]
    assert len(raced) == 1
    assert [f[4] for f in rehearsal.venue.fills if f[0] == raced[0]["details"]["symbol"]] == [
        "market", "stop"  # bought, closed by the stop, never sold twice
    ]
    assert set(rehearsal.outcomes()) == {"run_completed"}


def test_a_lost_buy_response_fails_the_run_then_entries_stop_and_books_hold(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("LIVE_ORDER_RECONCILE_TIMEOUT_SECONDS", "1")
    rehearsal = Rehearsal(
        tmp_path, monkeypatch, venue={**CALM, "timeout_after_accept": {"BTC/USDT"}},
        tight_stop_every=0,
    )
    assert rehearsal.run_cycle() == 1  # reconciled as filled, then the batch stops
    for _ in range(3):  # entries blocked; the held positions still exit
        rehearsal.check_books()
        rehearsal.venue.advance()
        assert rehearsal.run_cycle() == 0
    rehearsal.check_books()
    assert rehearsal.outcomes()[0] == "run_failed"
    assert all("run_failed" in reason for reason in _blocked(tmp_path)[1:])
    assert all(rehearsal.venue.held[s] == 0 for s in SYMBOLS)
    assert len([f for f in rehearsal.venue.fills if f[1] == "buy"]) == 2


def test_a_run_killed_between_a_fill_and_its_stop_is_protected_next_cycle(
    tmp_path, monkeypatch
):
    from tests.test_promoted_live_admission import SimulatedProcessDeath

    rehearsal = Rehearsal(tmp_path, monkeypatch, venue=CALM, tight_stop_every=0)
    original, calls = runner.ensure_protective_stop, []

    def killed_before_the_first_stop(**kwargs):
        calls.append(kwargs["pair"])
        if len(calls) == 1:
            raise SimulatedProcessDeath("killed after the fill, before its stop")
        return original(**kwargs)

    monkeypatch.setattr(runner, "ensure_protective_stop", killed_before_the_first_stop)
    with pytest.raises(SimulatedProcessDeath):
        rehearsal.run_cycle()
    (unprotected,) = [s for s in SYMBOLS if rehearsal.venue.held[s] > 0]
    assert rehearsal.venue._open_stops(unprotected) == []
    rehearsal.venue.advance()
    assert rehearsal.run_cycle() == 0
    rehearsal.check_books()  # protected now, and the lifecycle knows it
    assert "no terminal event" in _blocked(tmp_path)[-1]


def test_a_crash_past_the_daily_loss_limit_stops_entries_and_the_stops_close_out(
    tmp_path, monkeypatch
):
    rehearsal = Rehearsal(tmp_path, monkeypatch, venue=CALM, tight_stop_every=0)
    assert rehearsal.run_cycle() == 0
    rehearsal.venue.shock(-0.30)
    assert all(rehearsal.venue.held[s] == 0 for s in SYMBOLS)  # every stop filled
    for _ in range(2):
        assert rehearsal.run_cycle() == 0
        rehearsal.check_books()
        rehearsal.venue.advance()
    assert all("daily loss" in reason for reason in _blocked(tmp_path)[1:])
    assert len([f for f in rehearsal.venue.fills if f[1] == "buy"]) == len(SYMBOLS)


def test_fees_in_the_base_asset_leave_dust_that_never_blocks_the_track(tmp_path, monkeypatch):
    """Mainnet charges spot fees in the base asset: every exit leaves a sub-lot of dust."""
    rehearsal = Rehearsal(
        tmp_path, monkeypatch, venue={**CALM, "base_fee_rate": Decimal("0.001")},
        tight_stop_every=0,
    )
    for _ in range(6):
        assert rehearsal.run_cycle() == 0, f"cycle {rehearsal.cycle - 1} failed"
        rehearsal.check_books()
        rehearsal.venue.advance()
    assert set(rehearsal.outcomes()) == {"run_completed"}
    sold = {f[0] for f in rehearsal.venue.fills if f[1] == "sell" and f[4] == "market"}
    assert {"BTC/USDT", "SOL/USDT"} <= sold  # the track's own exits still happen
    dust = [e for e in _audit(tmp_path) if e["event"] == "position_dust_classified"]
    assert dust and all(e["details"]["estimated_notional"] < 1 for e in dust)
    # Dust joins the next lifecycle and is sold with it; it never accumulates.
    for symbol in SYMBOLS:
        bought = sum((f[2] for f in rehearsal.venue.fills if f[0] == symbol and f[1] == "buy"), Decimal(0))
        assert rehearsal.venue.held[symbol] <= bought  # bounded by what was bought


def _held_back(tmp_path) -> list[dict]:
    return [e["details"] for e in _audit(tmp_path) if e["event"] == "order_held_back_by_market"]


def test_a_quote_off_the_candle_holds_back_only_that_entry(tmp_path, monkeypatch, capsys):
    rehearsal = Rehearsal(tmp_path, monkeypatch, venue=CALM)
    rehearsal.venue.ticker_skew["AVAX/USDT"] = 1.015
    assert rehearsal.run_cycle() == 0
    rehearsal.check_books()
    assert sorted(f[0] for f in rehearsal.venue.fills) == ["BTC/USDT", "SOL/USDT"]
    (held,) = _held_back(tmp_path)
    assert (held["symbol"], held["side"]) == ("AVAX/USDT", "BUY")
    assert "quote deviation" in held["error"]
    rehearsal.venue.ticker_skew.clear()  # the market settles: the entry is planned again
    rehearsal.venue.advance()
    assert rehearsal.run_cycle() == 0
    rehearsal.check_books()
    assert "AVAX/USDT" in {f[0] for f in rehearsal.venue.fills if f[1] == "buy"}
    assert set(rehearsal.outcomes()) == {"run_completed"}
    line, issues = rehearsal.m3_line(capsys)
    assert issues == [] and "held back by the market 1)" in line


def test_an_exit_the_market_holds_back_keeps_its_stop_and_sells_next_cycle(
    tmp_path, monkeypatch
):
    rehearsal = Rehearsal(tmp_path, monkeypatch, venue=CALM, tight_stop_every=0)
    for _ in range(2):  # buy, then hold
        assert rehearsal.run_cycle() == 0
        rehearsal.check_books()
        rehearsal.venue.advance()
    rehearsal.venue.ticker_skew["BTC/USDT"] = 0.985  # the exit cycle: BTC's bid is off
    assert rehearsal.run_cycle() == 0
    rehearsal.check_books()  # BTC still held, under its venue stop
    assert rehearsal.venue.held["BTC/USDT"] > 0
    assert [(h["symbol"], h["side"]) for h in _held_back(tmp_path)] == [("BTC/USDT", "SELL")]
    rehearsal.venue.ticker_skew.clear()
    rehearsal.venue.advance()
    assert rehearsal.run_cycle() == 0
    rehearsal.check_books()
    assert [(f[1], f[4]) for f in rehearsal.venue.fills if f[0] == "BTC/USDT"] == [
        ("buy", "market"), ("sell", "market")  # sold by the track, a cycle late
    ]
    assert set(rehearsal.outcomes()) == {"run_completed"}
