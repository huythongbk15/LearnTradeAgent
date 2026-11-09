"""Binance clients stamp signed requests with the measured clock offset (2026-11-08: a PC 1.5 s fast failed every run).

Offline: a fake Binance sits under ccxt's requests.Session and checks every signed request the way Binance does
(-1021 when the timestamp is more than 1000 ms ahead of the server, or older than recvWindow). No request leaves
the process. The connect path is driven through the project's real ``CCXTAdapter.connect``.
"""
from __future__ import annotations

import asyncio
import importlib
import json
import time
import urllib.parse

import ccxt
import pytest
import requests
from requests.structures import CaseInsensitiveDict

from trading_agent.exchanges.ccxt_adapter import MAX_TIME_ADJUST_MS, CCXTAdapter, ExchangeConfig
from trading_agent.execution.demo_spot.venue import spot_demo_config
from trading_agent.execution.perp.venue import demo_config

STATE: dict = {"server_ahead_ms": 0, "log": []}     # server clock = PC clock + server_ahead_ms

SPOT_INFO = {"timezone": "UTC", "rateLimits": [], "exchangeFilters": [], "symbols": [{
    "symbol": "BTCUSDT", "status": "TRADING", "baseAsset": "BTC", "baseAssetPrecision": 8, "quoteAsset": "USDT",
    "quotePrecision": 8, "quoteAssetPrecision": 8, "orderTypes": ["LIMIT", "MARKET"], "isSpotTradingAllowed": True,
    "isMarginTradingAllowed": False, "permissions": ["SPOT"], "permissionSets": [["SPOT"]],
    "filters": [{"filterType": "PRICE_FILTER", "minPrice": "0.01", "maxPrice": "1000000", "tickSize": "0.01"},
                {"filterType": "LOT_SIZE", "minQty": "0.00001", "maxQty": "9000", "stepSize": "0.00001"}]}]}
FUT_INFO = {"timezone": "UTC", "rateLimits": [], "exchangeFilters": [], "assets": [], "symbols": [{
    "symbol": "BTCUSDT", "pair": "BTCUSDT", "contractType": "PERPETUAL", "deliveryDate": 4133404800000,
    "onboardDate": 1569398400000, "status": "TRADING", "baseAsset": "BTC", "quoteAsset": "USDT", "marginAsset": "USDT",
    "pricePrecision": 2, "quantityPrecision": 3, "baseAssetPrecision": 8, "quotePrecision": 8, "underlyingType": "COIN",
    "orderTypes": ["LIMIT", "MARKET"], "timeInForce": ["GTC"],
    "filters": [{"filterType": "PRICE_FILTER", "minPrice": "0.10", "maxPrice": "1000000", "tickSize": "0.10"},
                {"filterType": "LOT_SIZE", "minQty": "0.001", "maxQty": "1000", "stepSize": "0.001"},
                {"filterType": "MARKET_LOT_SIZE", "minQty": "0.001", "maxQty": "120", "stepSize": "0.001"},
                {"filterType": "MIN_NOTIONAL", "notional": "100"}]}]}


def server_ms() -> int:
    return int(time.time() * 1000) + STATE["server_ahead_ms"]


def _resp(url: str, status: int, payload) -> requests.Response:
    r = requests.Response()
    r.status_code, r.reason, r.url, r.encoding = status, "OK" if status == 200 else "Bad Request", url, "utf-8"
    r._content = json.dumps(payload).encode()
    r.headers = CaseInsensitiveDict({"Content-Type": "application/json"})
    return r


class FakeSession(requests.Session):
    def request(self, method, url, data=None, headers=None, **kw):     # never touches the network
        p = urllib.parse.urlparse(url)
        q = dict(urllib.parse.parse_qsl(p.query))
        if data:
            q.update(dict(urllib.parse.parse_qsl(data.decode() if isinstance(data, bytes) else data)))
        now = server_ms()
        entry = {"path": p.path, "signed": "signature" in q}
        if "signature" in q:
            ts, rw = int(q["timestamp"]), int(q.get("recvWindow", 5000))
            entry["ts_minus_server_ms"] = ts - now
            if ts > now + 1000 or now - ts > rw:
                STATE["log"].append({**entry, "result": "-1021"})
                return _resp(url, 400, {"code": -1021, "msg": "Timestamp for this request was 1000ms ahead of the "
                                                               "server's time."})
        STATE["log"].append({**entry, "result": "ok"})
        if p.path.endswith("/time"):
            return _resp(url, 200, {"serverTime": now})
        if p.path == "/api/v3/exchangeInfo":
            return _resp(url, 200, dict(SPOT_INFO, serverTime=now))
        if p.path == "/fapi/v1/exchangeInfo":
            return _resp(url, 200, dict(FUT_INFO, serverTime=now))
        if p.path == "/dapi/v1/exchangeInfo":
            return _resp(url, 200, {"timezone": "UTC", "serverTime": now, "symbols": []})
        if p.path.endswith("/positionSide/dual"):
            return _resp(url, 200, {"dualSidePosition": False})
        if p.path.startswith("/sapi/") or p.path.endswith("openOrders"):
            return _resp(url, 200, [])
        return _resp(url, 200, {})


@pytest.fixture
def fake_binance(monkeypatch):
    base = importlib.import_module("ccxt.base.exchange")
    monkeypatch.setattr(base, "Session", FakeSession)       # restored after the test
    STATE["log"].clear()
    return STATE


def connect(cfg: ExchangeConfig) -> CCXTAdapter:
    ad = CCXTAdapter(cfg)
    asyncio.run(ad.connect())
    return ad


def signed(log: list) -> list:
    return [e for e in log if e["signed"]]


@pytest.mark.parametrize("cfg, call", [
    (lambda: demo_config("k", "s"), lambda ex: ex.fetch_position_mode()),               # the perp demo books
    (lambda: spot_demo_config("k", "s"), lambda ex: ex.fetch_open_orders("BTC/USDT")),  # book HOLD
    (lambda: ExchangeConfig(id="binance", name="Binance", api_key="k", secret="s", testnet=True,
                            options={"defaultType": "spot"}), lambda ex: ex.fetch_open_orders("BTC/USDT")),  # testnet
])
def test_a_pc_clock_15_s_fast_no_longer_fails_signed_calls(fake_binance, cfg, call):
    fake_binance["server_ahead_ms"] = -1500                 # 2026-11-08: the PC 1.5 s ahead of Binance
    ad = connect(cfg())
    assert 1300 <= ad.exchange.options["timeDifference"] <= 1700
    call(ad.exchange)
    s = signed(fake_binance["log"])
    assert s and all(e["result"] == "ok" and abs(e["ts_minus_server_ms"]) < 200 for e in s)


def test_mainnet_with_keys_measures_the_offset_before_load_markets_signs(fake_binance):
    """ccxt measures at the end of fetch_markets, after it signed the currencies and margin-pair reads."""

    fake_binance["server_ahead_ms"] = -1500
    connect(ExchangeConfig(id="binance", name="Binance", api_key="k", secret="s", options={"defaultType": "spot"}))
    s = signed(fake_binance["log"])
    assert s and s[0]["path"] == "/sapi/v1/capital/config/getall"
    assert all(e["result"] == "ok" and abs(e["ts_minus_server_ms"]) < 200 for e in s)


def test_a_pc_behind_is_left_uncorrected_so_a_sync_mid_run_cannot_stamp_ahead(fake_binance):
    fake_binance["server_ahead_ms"] = 1500                  # the PC 1.5 s behind: Binance accepts that
    ad = connect(demo_config("k", "s"))
    assert ad.exchange.options["timeDifference"] == 0
    fake_binance["server_ahead_ms"] = 0                     # Windows syncs the PC during the run
    ad.exchange.fetch_position_mode()
    assert signed(fake_binance["log"])[-1]["result"] == "ok"


class _Ex:
    def __init__(self, td, adjust=True):
        self.options = {"adjustForTimeDifference": adjust, "timeDifference": td}


@pytest.mark.parametrize("td, bounded", [(1500, 1500), (-1500, 0), (11_000, MAX_TIME_ADJUST_MS), (0, 0)])
def test_the_offset_is_bounded_to_a_pc_ahead_by_at_most_5_s(td, bounded):
    ad = CCXTAdapter(demo_config("k", "s"))
    ad.exchange = _Ex(td)
    ad._bound_time_difference()
    assert ad.exchange.options["timeDifference"] == bounded
    ad.exchange = _Ex(-1500, adjust=False)                  # a config that turned the adjustment off is left alone
    ad._bound_time_difference()
    assert ad.exchange.options["timeDifference"] == -1500


def test_without_the_measurement_the_fast_clock_fails_again(fake_binance, monkeypatch):
    """The guard: if ccxt ever stopped measuring (an upgrade), the connect path would fail as on 2026-11-08."""

    monkeypatch.setattr(ccxt.binance, "load_time_difference", lambda self, params={}: None)
    fake_binance["server_ahead_ms"] = -1500
    ad = connect(demo_config("k", "s"))
    with pytest.raises(ccxt.InvalidNonce):
        ad.exchange.fetch_position_mode()
