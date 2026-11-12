"""The way out of a stuck perp ledger (PERP_LIVE_R0.md section 5, item 4).

- A send that raises is ``rejected`` when nothing left the process or the venue refused it outright, else its outcome
  stays unknown. Run on real ccxt with only ``requests``' session faked, so ccxt's own HTTP call, error mapping and
  response hook all run; no network.
- ``scripts/perp_resolve_intent.py`` closes an intent parked in ``manual_intervention`` under the evidence rule.
"""

from __future__ import annotations

import importlib.util
import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import ccxt
import pytest
import requests

import trading_agent.exchanges.ccxt_adapter as ca
from tests.test_perp_demo import KEY, FakeUsdm, events
from tests.test_perp_live_venue import ED_PEM, T0, Wire
from trading_agent.exchanges.ccxt_adapter import CCXTAdapter
from trading_agent.execution.live_safety import LiveRiskStateStore
from trading_agent.execution.perp.book import Book, PairStore, PerpBookError
from trading_agent.execution.perp.venue import (
    PerpDemoVenue,
    PerpLiveVenue,
    PerpVenueError,
    demo_config,
    live_config,
)

REPO = Path(__file__).resolve().parents[1]
DAY = 24 * 3_600_000
FILLED = {"orderId": 7, "clientOrderId": "x", "symbol": "BTCUSDT", "status": "FILLED", "side": "SELL", "type": "MARKET",
          "origQty": "0.001", "executedQty": "0.001", "avgPrice": "80000", "cumQuote": "80", "price": "0",
          "reduceOnly": True, "positionSide": "BOTH", "timeInForce": "GTC", "updateTime": T0}


def response(url: str, status: int, payload) -> requests.Response:
    r = requests.Response()
    r.status_code, r.reason, r.url = status, "status", url
    r._content = json.dumps(payload).encode()
    r.headers["Content-Type"] = "application/json"
    return r


class Http:
    """``requests.Session.request`` for ccxt: canned GET replies (test_perp_live_venue's Wire), the account's trades,
    and one answer for every write."""

    def __init__(self):
        self.wire = Wire()
        self.write: tuple[int, dict] | BaseException = (200, FILLED)
        self.trades: list[dict] = []
        self.algo: dict[str, dict] = {}                  # GET /fapi/v1/algoOrder answers, by clientAlgoId
        self.sent: list[tuple[str, str, dict]] = []      # (method, path, query)

    def __call__(self, session, method, url, data=None, headers=None, **kw):
        path, query = urlsplit(url).path, {k: v[0] for k, v in parse_qs(urlsplit(url).query).items()}
        if isinstance(data, (str, bytes)) and data:      # a signed write carries its parameters in the body
            body = data.decode() if isinstance(data, bytes) else data
            query.update({k: v[0] for k, v in parse_qs(body).items()})
        self.sent.append((method, path, query))
        if method != "GET":
            if isinstance(self.write, BaseException):
                raise self.write
            return response(url, *self.write)
        if path == "/fapi/v1/algoOrder" and method == "GET":
            got = self.algo.get(query.get("clientAlgoId", ""))
            if got is None:
                return response(url, 400, {"code": -2013, "msg": "Order does not exist."})
            return response(url, 200, got)
        if path == "/fapi/v1/userTrades" and "orderId" in query:
            return response(url, 200, [t for t in self.trades if str(t["orderId"]) == query["orderId"]])
        if path == "/fapi/v1/userTrades":
            lo, hi = int(query["startTime"]), int(query["endTime"])
            return response(url, 200, [t for t in self.trades if lo <= t["time"] <= hi])
        return response(url, 200, self.wire(None, url, method))

    def writes(self) -> list[str]:
        return [p for m, p, _ in self.sent if m != "GET"]


@pytest.fixture
def http(monkeypatch):
    h = Http()
    monkeypatch.setattr(requests.Session, "request", lambda session, *a, **k: h(session, *a, **k))
    monkeypatch.setattr(os, "fsync", lambda fd: None)
    return h


def venue(kind: str, allow_orders: bool = True):
    v = (PerpLiveVenue(CCXTAdapter(live_config("live-key", ED_PEM)), allow_orders=allow_orders) if kind == "live"
         else PerpDemoVenue(CCXTAdapter(demo_config("demo-key", ED_PEM))))
    v.connect()
    return v


def pair_book(v, tmp: Path) -> Book:
    orders = LiveRiskStateStore(tmp / "order_state.json", integrity_key=KEY)
    return Book(v, orders, PairStore.load(tmp / "pairs.json", KEY), tmp / "audit.jsonl", 100.0, sleep=lambda s: None)


def sell(book: Book, key: str = "pp-t-h", qty: float = 0.001) -> dict:
    return book._market("BTCUSDT", "sell", qty, key, reduce_only=True)


def rec(book: Book, key: str = "pp-t-h") -> dict:
    return book.orders.state.order_ledger[key]


# ── a send that raises ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("kind", ["demo", "live"])
def test_an_order_the_venue_takes_is_recorded_as_before(http, tmp_path, kind):
    book = pair_book(venue(kind), tmp_path)
    assert sell(book)["status"] == "filled"
    assert rec(book)["status"] == "filled" and http.writes() == ["/fapi/v1/order"]


@pytest.mark.parametrize("kind", ["demo", "live"])
def test_a_definite_refusal_is_rejected_and_the_next_reconcile_passes(http, tmp_path, kind):
    book = pair_book(venue(kind), tmp_path)
    http.write = (400, {"code": -2022, "msg": "ReduceOnly Order is rejected."})
    with pytest.raises(PerpBookError, match="refused -2022: InvalidOrder"):
        sell(book)
    assert rec(book)["status"] == "rejected" and rec(book)["error"] == "refused:-2022:InvalidOrder"
    (ev,) = [e for e in events(tmp_path) if e["event"].startswith("perp_order")]
    assert ev["event"] == "perp_order_rejected" and ev["details"]["how"] == "refused:-2022"
    book.reconcile()                # nothing left for the operator: the key is closed
    assert book.orders.unfinished_orders() == {}


@pytest.mark.parametrize("answer", [
    (503, {"code": -1001, "msg": "Internal error; unable to process your request."}),
    (408, {"code": -1007, "msg": "Timeout waiting for response from backend server."}),
    (400, {"code": -1006, "msg": "An unexpected response was received from the message bus."}),
    (500, {}),
    (400, ["not", "an", "error object"]),
    requests.exceptions.ReadTimeout("read timed out"),
    requests.exceptions.ConnectionError("connection reset"),
], ids=["503", "408", "-1006", "500-no-code", "no-code", "timeout", "connection"])
def test_a_write_that_left_without_a_definite_answer_stays_unknown(http, tmp_path, answer):
    book = pair_book(venue("live"), tmp_path)
    http.write = answer
    with pytest.raises(PerpBookError, match="outcome unknown"):
        sell(book)
    assert rec(book)["status"] == "reconciling" and http.writes() == ["/fapi/v1/order"]
    assert [e["event"] for e in events(tmp_path)] == ["perp_order_uncertain"]


def test_a_write_the_read_only_venue_refuses_is_rejected_as_never_sent(http, tmp_path):
    book = pair_book(venue("live", allow_orders=False), tmp_path)
    with pytest.raises(PerpBookError, match="not_sent: PerpVenueError"):
        sell(book)
    assert rec(book)["status"] == "rejected" and rec(book)["error"] == "not_sent:PerpVenueError"
    assert http.writes() == []
    book.reconcile()
    assert book.orders.unfinished_orders() == {}


@pytest.mark.parametrize("kind", ["demo", "live"])
def test_an_amount_ccxt_refuses_locally_is_rejected_as_never_sent(http, tmp_path, kind):
    book = pair_book(venue(kind), tmp_path)
    with pytest.raises(PerpBookError, match="not_sent: InvalidOrder"):
        sell(book, qty=0.0001)      # under BTCUSDT's 0.001 step: ccxt refuses it before any request
    assert rec(book)["status"] == "rejected" and http.writes() == []


def test_a_venue_without_http_knows_nothing_and_keeps_the_old_way(monkeypatch, tmp_path):
    fake = FakeUsdm({"BTCUSDT": 80_000.0})
    monkeypatch.setattr(ca.ccxt, "binanceusdm", lambda cfg: fake)
    v = PerpDemoVenue(CCXTAdapter(demo_config("demo-key", "demo-secret")))
    v.connect()
    assert v.wire_mark() is None and v.wire_since(None) is None


def test_only_a_later_write_counts_against_the_mark(http):
    v = venue("live")
    mark = v.wire_mark()
    assert v.wire_since(mark) == "not_sent"
    http.write = (400, {"code": -4164, "msg": "Order's notional must be no smaller than 50"})
    with pytest.raises(ccxt.InvalidOrder):
        v.market("BTCUSDT", "buy", 0.001, key="k1", reduce_only=False)
    assert v.wire_since(mark) == "refused:-4164"
    assert v.wire_since(v.wire_mark()) == "not_sent"


# ── the account's trades, read in windows ─────────────────────────────────────

def trade(oid: int, at: int) -> dict:
    return {"symbol": "BTCUSDT", "id": oid, "orderId": oid, "side": "BUY", "price": "1", "qty": "0.001",
            "realizedPnl": "0", "quoteQty": "1", "commission": "0", "commissionAsset": "USDT", "time": at,
            "buyer": True, "maker": False, "positionSide": "BOTH"}


def test_trades_are_read_in_seven_day_windows_and_a_full_page_fails_closed(http):
    v = venue("live", allow_orders=False)
    http.trades = [trade(1, T0 + DAY), trade(2, T0 + 9 * DAY), trade(3, T0 + 20 * DAY)]
    assert v.trade_order_ids("BTCUSDT", T0, T0 + 15 * DAY) == ["1", "2"]
    spans = [(int(q["startTime"]), int(q["endTime"])) for m, p, q in http.sent if p == "/fapi/v1/userTrades"]
    assert spans == [(T0, T0 + 7 * DAY - 1), (T0 + 7 * DAY, T0 + 14 * DAY - 1), (T0 + 14 * DAY, T0 + 15 * DAY)]
    http.trades = [trade(i, T0 + DAY) for i in range(1000)]
    with pytest.raises(PerpVenueError, match="full page"):
        v.trade_order_ids("BTCUSDT", T0, T0 + DAY)


@pytest.mark.parametrize("kind", ["demo", "live"])
def test_an_orders_fee_and_fee_asset_are_read_from_its_trades(http, kind):
    """PERP_LIVE_R0.md item 6: Binance answers an order without its fee; ccxt reads userTrades by orderId."""

    v = venue(kind, allow_orders=False)
    http.trades = [{**trade(7, T0), "commission": "0.0125", "commissionAsset": "USDT"},
                   {**trade(7, T0 + 1), "id": 70, "commission": "0.0025", "commissionAsset": "USDT"},
                   {**trade(8, T0), "commission": "9", "commissionAsset": "BNB"}]
    assert v.order_fees("BTCUSDT", "7") == {"USDT": pytest.approx(0.015)}
    assert [q.get("orderId") for m, p, q in http.sent if p == "/fapi/v1/userTrades"] == ["7"]
    assert v.order_fees("BTCUSDT", "8") == {"BNB": pytest.approx(9.0)}
    with pytest.raises(PerpVenueError, match="no trade"):
        v.order_fees("BTCUSDT", "9")


@pytest.mark.parametrize("kind", ["demo", "live"])
def test_a_stop_goes_to_the_algo_api_triggered_on_the_last_price(http, tmp_path, kind):
    """D12 of PERP_LIVE_R0.md: the trigger type is set explicitly, never left to the venue's default."""

    v = venue(kind)
    try:
        v.stop("BTCUSDT", "buy", 0.001, 150_000.0, key="krw-1-AAA-st")
    except Exception:          # the canned reply is a plain order's; only what was sent matters here
        pass
    ((method, path, sent),) = [(m, p, q) for m, p, q in http.sent if m != "GET"]
    assert (method, path) == ("POST", "/fapi/v1/algoOrder")
    assert sent["workingType"] == "CONTRACT_PRICE" and sent["algoType"] == "CONDITIONAL"
    assert sent["clientAlgoId"] == "krw-1-AAA-st" and sent["reduceOnly"] == "true"


@pytest.mark.parametrize("kind", ["demo", "live"])
def test_a_fired_stop_is_read_back_from_its_algo_order_by_client_id(http, kind):
    v = venue(kind, allow_orders=False)
    http.algo["krw-1-AAA-st"] = {
        "algoId": 2146760, "clientAlgoId": "krw-1-AAA-st", "algoType": "CONDITIONAL", "orderType": "STOP_MARKET",
        "symbol": "BTCUSDT", "side": "BUY", "positionSide": "BOTH", "timeInForce": "GTC", "quantity": "0.001",
        "algoStatus": "FINISHED", "actualOrderId": "7781", "actualPrice": "150123.4", "actualType": "MARKET",
        "actualQty": "0.001", "triggerPrice": "150000.0", "price": "0", "workingType": "CONTRACT_PRICE",
        "priceProtect": False, "reduceOnly": True, "createTime": T0, "updateTime": T0 + 5, "triggerTime": T0 + 3,
        "goodTillDate": 0}
    got = v.stop_by_key("BTCUSDT", "krw-1-AAA-st")
    assert got == {"status": "FINISHED", "actual_order_id": "7781", "actual_price": 150123.4, "actual_qty": 0.001,
                   "trigger_ms": T0 + 3, "trigger_price": 150000.0, "working_type": "CONTRACT_PRICE"}
    (query,) = [q for m, p, q in http.sent if p == "/fapi/v1/algoOrder"]
    assert query["clientAlgoId"] == "krw-1-AAA-st"
    assert v.stop_by_key("BTCUSDT", "krw-9-ZZZ-st") is None          # the venue does not know it


# ── the resolve command ────────────────────────────────────────────────────────

NOW = datetime(2026, 11, 12, 12, 0, tzinfo=UTC)


@pytest.fixture
def cmd():
    spec = importlib.util.spec_from_file_location("perp_resolve_intent_test", REPO / "scripts" / "perp_resolve_intent.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class Reads:
    """The venue reads the command makes."""

    def __init__(self):
        self.found: dict | None = None
        self.open: set[str] = set()
        self.stops: set[str] = set()
        self.trades: list[str] = []
        self.asked: list[tuple] = []

    def order_by_key(self, exchange_id, key):
        return self.found

    def open_order_keys(self, exchange_id):
        return set(self.open)

    def open_stop_keys(self, exchange_id):
        return set(self.stops)

    def trade_order_ids(self, exchange_id, since_ms, until_ms):
        self.asked.append((exchange_id, since_ms, until_ms))
        return list(self.trades)


def stuck(tmp: Path, key: str = "krw-101-AAA-c0", *, at: datetime = NOW - timedelta(minutes=30),
          status: str = "manual_intervention") -> LiveRiskStateStore:
    store = LiveRiskStateStore(tmp / "order_state.json", integrity_key=KEY)
    store.reserve_order(key, symbol="AAAUSDT", side="buy", quantity=250.0, venue_client_order_id=key, now=at)
    if status != "reserved":
        store.update_order(key, status="submitted", now=at)
        store.update_order(key, status="reconciling", error="NetworkError", now=at)
    if status == "manual_intervention":
        store.update_order(key, status="manual_intervention", error="not_found_at_venue", now=at)
    return store


def go(cmd, store, reads, tmp, key="krw-101-AAA-c0", *, apply=True, checked=True, min_age=10.0):
    return cmd.resolve(store, reads, key, min_age=timedelta(minutes=min_age), operator_checked=checked, apply=apply,
                       audit=tmp / "audit.jsonl", now=NOW)


def test_an_intent_the_venue_never_saw_is_closed_with_the_evidence_and_the_operators_word(cmd, tmp_path):
    store, reads = stuck(tmp_path), Reads()
    rep = go(cmd, store, reads, tmp_path)
    assert rep["applied"] and rep["eligible"] and rep["refusals"] == []
    again = LiveRiskStateStore(tmp_path / "order_state.json", integrity_key=KEY)      # signed and saved
    r = again.state.order_ledger["krw-101-AAA-c0"]
    assert (r["status"], r["resolution"]) == ("rejected", "operator_unsubmitted")
    assert again.unfinished_orders() == {}
    (ev,) = events(tmp_path)
    assert ev["event"] == "perp_intent_resolved"
    assert ev["details"]["operator_checked_app"] is True and ev["details"]["venue_lookup"] == "not_found"
    assert ev["details"]["trades_since_reservation"] == 0
    # trades read from a minute before the reservation to now
    (asked,) = reads.asked
    assert asked == ("AAAUSDT", int((NOW - timedelta(minutes=30)).timestamp() * 1000) - 60_000,
                     int(NOW.timestamp() * 1000))
    assert "250" not in json.dumps(rep)          # no amount in the report
    assert go(cmd, again, reads, tmp_path)["already_resolved"]


@pytest.mark.parametrize("case, words", [
    ("found", "venue knows this client key"),
    ("open", "open order at the venue"),
    ("stop_listed", "open order at the venue"),
    ("unknown_trade", "1 trade(s) on AAAUSDT"),
    ("young", "reserved 5 min ago"),
    ("not_checked", "--operator-checked-app"),
    ("short_age", "below the rule's 10"),
    ("filled", "venue evidence"),
    ("stop_key", "settled by its book"),
    ("missing", "not in this root's order ledger"),
])
def test_every_part_of_the_evidence_rule_is_needed(cmd, tmp_path, case, words):
    key = "krw-101-AAA-st" if case == "stop_key" else "krw-101-AAA-c0"
    store = stuck(tmp_path, key, at=NOW - timedelta(minutes=5) if case == "young" else NOW - timedelta(minutes=30))
    reads = Reads()
    kw: dict = {}
    if case == "found":
        reads.found = {"status": "cancelled"}
    elif case == "open":
        reads.open = {key}
    elif case == "stop_listed":
        reads.stops = {key}
    elif case == "unknown_trade":
        reads.trades = ["991"]
    elif case == "not_checked":
        kw["checked"] = False
    elif case == "short_age":
        kw["min_age"] = 5.0
    elif case == "filled":
        store.state.order_ledger[key]["filled_quantity"] = 1.0
    rep = go(cmd, store, reads, tmp_path, "krw-9-ZZZ-c0" if case == "missing" else key, **kw)
    assert not rep["applied"] and not rep["eligible"]
    assert any(words in r for r in rep["refusals"]), rep["refusals"]
    assert not (tmp_path / "audit.jsonl").exists()


def test_trades_of_orders_the_ledger_knows_do_not_count(cmd, tmp_path):
    store = stuck(tmp_path)
    store.reserve_order("krw-101-AAA-o0", symbol="AAAUSDT", side="sell", quantity=1.0, now=NOW - timedelta(hours=2))
    store.update_order("krw-101-AAA-o0", status="submitted")
    store.update_order("krw-101-AAA-o0", status="acknowledged", exchange_order_id="555")
    reads = Reads()
    reads.trades = ["555", "555"]
    assert go(cmd, store, reads, tmp_path)["applied"]


def test_a_report_without_apply_changes_nothing(cmd, tmp_path):
    store = stuck(tmp_path, status="reserved")
    before = (tmp_path / "order_state.json").read_bytes()
    rep = go(cmd, store, Reads(), tmp_path, apply=False, checked=False)
    assert rep["eligible"] and not rep["applied"]
    assert (tmp_path / "order_state.json").read_bytes() == before and not (tmp_path / "audit.jsonl").exists()


def test_the_command_stops_without_a_ledger_and_never_builds_a_writing_live_venue(cmd, tmp_path, capsys, monkeypatch):
    assert cmd.main(["--venue", "demo", "--key", "k", "--root", str(tmp_path)]) == 1
    assert "no order ledger" in capsys.readouterr().err
    built = []

    class Spy:
        def __init__(self, adapter, *, allow_orders=False):
            built.append(allow_orders)
            raise PerpVenueError("stop here")

    import trading_agent.execution.perp.venue as pv

    monkeypatch.setattr(pv, "PerpLiveVenue", Spy)
    monkeypatch.setattr(pv, "load_live_credentials", lambda env: ("k", ED_PEM))
    stuck(tmp_path)
    env = tmp_path / "live.env"
    env.write_text("LIVE_SAFETY_HMAC_KEY=x\n")
    env.chmod(0o600)
    assert cmd.main(["--venue", "live", "--env-file", str(env), "--key", "k", "--root", str(tmp_path)]) == 1
    assert built == [False]
