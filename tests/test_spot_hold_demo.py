"""Book HOLD on Binance spot Demo Trading: buy BTC once, hold, fail closed."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from trading_agent.execution.demo_spot.hold import HOLD_USD_CAP, HoldBook, HoldBookError
from trading_agent.execution.demo_spot.venue import SpotDemoVenue, SpotVenueError, spot_demo_config
from trading_agent.execution.live_safety import LiveRiskStateStore
from trading_agent.execution.perp.book import PairStore

KEY = "k" * 64


class FakeSpot:
    def __init__(self, *, price=50_000.0, usdt=5000.0, btc=0.05, fail=None, status="filled"):
        self.px, self.usdt, self.btc = price, usdt, btc
        self.fail, self.status = fail, status
        self.orders: dict[str, dict] = {}
        self.sent = 0

    def price(self, base):
        return self.px

    def balance(self, asset):
        v = self.usdt if asset == "USDT" else self.btc
        return v, v

    def normalize(self, base, qty, price):
        return round(qty, 5)

    def order_by_key(self, base, key):
        return self.orders.get(key)

    def market(self, base, side, qty, *, key):
        self.sent += 1
        res = {"client_order_id": key, "exchange_order_id": "1", "status": self.status, "exchange_status": "",
               "qty": qty, "filled_qty": qty if self.status == "filled" else 0.0, "avg_fill_price": self.px,
               "quote_cost": qty * self.px, "fees": {"BTC": qty * 0.001}}
        self.orders[key] = res
        if self.status == "filled":
            self.btc += qty * 0.999
            self.usdt -= qty * self.px
        if self.fail:
            raise self.fail
        return res


def make(tmp_path, fake, usd=2500.0):
    orders = LiveRiskStateStore(tmp_path / "order_state.json", integrity_key=KEY)
    store = PairStore.load(tmp_path / "hold.json", KEY)
    return HoldBook(fake, orders, store, tmp_path / "audit.jsonl", usd, sleep=lambda s: None)


def reload(tmp_path, book):
    return HoldBook(book.venue, LiveRiskStateStore(tmp_path / "order_state.json", integrity_key=KEY),
                    PairStore.load(tmp_path / "hold.json", KEY), tmp_path / "audit.jsonl", book.hold_usd,
                    sleep=lambda s: None)


def test_buys_once_then_holds(tmp_path):
    fake = FakeSpot()
    book = make(tmp_path, fake)
    assert book.step(execute=True) == {"status": "held", "bought": True}
    h = book.holding
    assert h["baseline_btc"] == 0.05 and h["qty"] == pytest.approx(0.05 * 0.999)
    again = reload(tmp_path, book)
    assert again.step(execute=True) == {"status": "held", "bought": False}
    assert fake.sent == 1


def test_dry_run_sends_nothing(tmp_path):
    fake = FakeSpot()
    assert make(tmp_path, fake).step(execute=False) == {"status": "none", "would_buy": True}
    assert fake.sent == 0


def test_entry_lock_blocks_the_buy(tmp_path):
    fake = FakeSpot()
    assert make(tmp_path, fake).step(execute=True, entry_lock="kill")["entry_lock"] is True
    assert fake.sent == 0


def test_size_above_cap_or_wallet_is_refused(tmp_path):
    with pytest.raises(HoldBookError):
        make(tmp_path, FakeSpot(), usd=HOLD_USD_CAP + 1).step(execute=True)
    with pytest.raises(HoldBookError):
        make(tmp_path / "b", FakeSpot(usdt=100.0)).step(execute=True)


def test_wallet_short_of_the_book_stops_the_run(tmp_path):
    fake = FakeSpot()
    book = make(tmp_path, fake)
    book.step(execute=True)
    fake.btc -= 0.01
    with pytest.raises(HoldBookError, match="less BTC"):
        reload(tmp_path, book).step(execute=True)


def test_unknown_outcome_is_settled_by_key_next_run(tmp_path):
    fake = FakeSpot(fail=TimeoutError())
    book = make(tmp_path, fake)
    with pytest.raises(HoldBookError, match="unknown"):
        book.step(execute=True)
    fake.fail = None
    again = reload(tmp_path, book)
    assert again.step(execute=True) == {"status": "held", "bought": False}
    assert fake.sent == 1 and again.holding["status"] == "held"


def test_order_never_at_venue_needs_the_operator(tmp_path):
    fake = FakeSpot(fail=TimeoutError())
    book = make(tmp_path, fake)
    with pytest.raises(HoldBookError):
        book.step(execute=True)
    fake.orders.clear()
    with pytest.raises(HoldBookError, match="operator"):
        reload(tmp_path, book).step(execute=True)


def test_rejected_buy_marks_failed_and_never_retries(tmp_path):
    fake = FakeSpot(status="rejected")
    book = make(tmp_path, fake)
    with pytest.raises(HoldBookError):
        book.step(execute=True)
    assert reload(tmp_path, book).step(execute=True) == {"status": "failed", "bought": False}
    assert fake.sent == 1


def _adapter(api, id_="binance", demo=True):
    return SimpleNamespace(config=SimpleNamespace(id=id_, demo=demo), exchange=SimpleNamespace(urls={"api": api}))


def test_venue_refuses_any_host_but_demo_api():
    demo = {"public": "https://demo-api.binance.com/api/v3", "private": "https://demo-api.binance.com/api/v3"}
    SpotDemoVenue(_adapter(demo)).assert_demo_only()
    for api in (
        {"public": "https://api.binance.com/api/v3", "private": "https://api.binance.com/api/v3"},
        {"public": "https://testnet.binance.vision/api/v3", "private": "https://testnet.binance.vision/api/v3"},
        {**demo, "sapi": "https://api.binance.com/sapi/v1"},
    ):
        with pytest.raises(SpotVenueError):
            SpotDemoVenue(_adapter(api)).assert_demo_only()
    with pytest.raises(SpotVenueError):
        SpotDemoVenue(_adapter(demo, demo=False))


def test_config_is_demo_spot():
    cfg = spot_demo_config("a", "b")
    assert cfg.id == "binance" and cfg.demo and not cfg.sandbox
