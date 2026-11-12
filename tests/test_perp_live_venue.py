"""The mainnet perp venue of tier R0 (docs/operations/PERP_LIVE_R0.md) on real ccxt with its HTTP call faked: no
network. Every request ccxt would send is recorded with its host and whether it carries a signature."""

from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import urlsplit

import ccxt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519, rsa

import trading_agent.execution.perp.venue as venue_mod
from trading_agent.exchanges.ccxt_adapter import CCXTAdapter, ExchangeConfig
from trading_agent.execution.perp.venue import (
    MAINNET_HOST,
    PerpLiveVenue,
    PerpVenueError,
    demo_config,
    live_config,
    load_live_credentials,
)

T0 = 1_790_000_000_000


def _pem(key) -> str:
    return key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                             serialization.NoEncryption()).decode("ascii")


ED_PEM = _pem(ed25519.Ed25519PrivateKey.generate())


class Wire:
    """Answers ccxt's HTTP call from canned replies by path; records each request."""

    def __init__(self):
        self.sent: list[tuple[str, str, bool]] = []   # (method, url without query, signed)
        self.account = {"multiAssetsMargin": False, "feeBurn": False, "dual": False,
                        "assets": [{"asset": "USDT", "walletBalance": "100", "marginBalance": "100"}]}

    def __call__(self, ex, url, method="GET", headers=None, body=None):
        signed = "signature=" in url or "signature=" in str(body or "")
        path = urlsplit(url).path
        self.sent.append((method, url.split("?")[0], signed))
        a = self.account
        replies = {
            "/fapi/v1/time": {"serverTime": T0},
            "/fapi/v1/exchangeInfo": {"serverTime": T0, "rateLimits": [], "symbols": [
                {"symbol": "BTCUSDT", "pair": "BTCUSDT", "contractType": "PERPETUAL", "status": "TRADING",
                 "baseAsset": "BTC", "quoteAsset": "USDT", "marginAsset": "USDT", "pricePrecision": 1,
                 "quantityPrecision": 3, "onboardDate": 1, "timeInForce": ["GTC"],
                 "orderTypes": ["LIMIT", "MARKET", "STOP", "STOP_MARKET", "TAKE_PROFIT_MARKET"],
                 "filters": [{"filterType": "LOT_SIZE", "stepSize": "0.001", "minQty": "0.001", "maxQty": "1000"},
                             {"filterType": "MIN_NOTIONAL", "notional": "50"}]}]},
            "/fapi/v1/positionSide/dual": {"dualSidePosition": a["dual"]},
            "/fapi/v1/multiAssetsMargin": {"multiAssetsMargin": a["multiAssetsMargin"]},
            "/fapi/v1/feeBurn": {"feeBurn": a["feeBurn"]},
        }
        if path in replies:
            return replies[path]
        if path in ("/fapi/v2/account", "/fapi/v3/account"):
            return {"totalMarginBalance": "100", "totalWalletBalance": "100", "assets": a["assets"], "positions": []}
        raise ccxt.ExchangeError(f"unexpected request {method} {path}")


@pytest.fixture
def wire(monkeypatch):
    w = Wire()
    monkeypatch.setattr(ccxt.binanceusdm, "fetch", lambda self, *a, **k: w(self, *a, **k))
    return w


def live(allow_orders: bool = False) -> PerpLiveVenue:
    return PerpLiveVenue(CCXTAdapter(live_config("live-key", ED_PEM)), allow_orders=allow_orders)


def test_connect_talks_to_futures_mainnet_only_and_signs_nothing(wire):
    v = live()
    v.connect()
    hosts = {urlsplit(u).scheme + "://" + urlsplit(u).netloc + "/" for _, u, _ in wire.sent}
    assert hosts == {MAINNET_HOST}
    assert not any(signed for *_, signed in wire.sent)
    assert any(u.endswith("/fapi/v1/exchangeInfo") for _, u, _ in wire.sent)
    v.assert_mainnet_only()
    assert "BTCUSDT" in v.trading_symbols()


def test_default_ccxt_options_would_sign_on_the_spot_api_so_they_are_refused(wire):
    # The reason for live_config: with a key and ccxt's defaults, loading markets signs a spot API call.
    ex = ccxt.binanceusdm({"apiKey": "k", "secret": ED_PEM})
    with pytest.raises(ccxt.ExchangeError):
        ex.load_markets()
    assert wire.sent[-1][1].startswith("https://api.binance.com/sapi/") and wire.sent[-1][2]
    plain = ExchangeConfig(id="binanceusdm", name="x", api_key="k", secret=ED_PEM)
    with pytest.raises(PerpVenueError, match="live_config"):
        PerpLiveVenue(CCXTAdapter(plain))


@pytest.mark.parametrize("cfg", [
    demo_config("k", "s"),
    ExchangeConfig(id="binanceusdm", name="x", testnet=True, options={"fetchCurrencies": False, "fetchMargins": False}),
    ExchangeConfig(id="binance", name="x", options={"fetchCurrencies": False, "fetchMargins": False}),
])
def test_anything_but_usdm_mainnet_is_refused(cfg):
    with pytest.raises(PerpVenueError):
        PerpLiveVenue(CCXTAdapter(cfg))


def test_a_wrong_host_is_refused_before_the_first_request(wire, monkeypatch):
    monkeypatch.setattr(venue_mod, "MAINNET_HOST", "https://elsewhere.example/")
    with pytest.raises(PerpVenueError, match="not Binance USD-M mainnet"):
        live().connect()
    assert wire.sent == []


def test_a_request_to_another_host_never_leaves_the_process(wire):
    v = live(allow_orders=True)
    v.connect()
    n = len(wire.sent)
    for url in ("https://api.binance.com/sapi/v1/capital/config/getall", "https://demo-fapi.binance.com/fapi/v1/order",
                "https://fapi.binance.com.evil.example/fapi/v1/order"):
        with pytest.raises(PerpVenueError, match="refused"):
            v._ex.fetch(url, "GET", {}, None)
    assert len(wire.sent) == n


def test_read_only_refuses_every_write_before_the_network(wire):
    v = live()
    v.connect()
    n = len(wire.sent)
    with pytest.raises(PerpVenueError, match="read-only"):
        v.prepare("BTCUSDT")
    with pytest.raises(PerpVenueError, match="read-only"):
        v.market("BTCUSDT", "buy", 0.001, key="k1", reduce_only=False)
    with pytest.raises(PerpVenueError, match="read-only"):
        v.stop("BTCUSDT", "buy", 0.001, 90_000.0, key="k2")
    with pytest.raises(PerpVenueError, match="read-only"):
        v.cancel_stop("BTCUSDT", key="k2")
    assert len(wire.sent) == n


def test_an_order_goes_signed_to_futures_mainnet_when_allowed(wire):
    v = live(allow_orders=True)
    v.connect()
    with pytest.raises(Exception, match="unexpected request POST /fapi/v1/order"):
        v.market("BTCUSDT", "buy", 0.001, key="k1", reduce_only=False)
    method, url, signed = wire.sent[-1]
    assert (method, url, signed) == ("POST", MAINNET_HOST + "fapi/v1/order", True)


def test_account_shape_passes_for_one_way_single_asset_usdt(wire):
    v = live()
    v.connect()
    v.require_account_shape()
    assert all(u.startswith(MAINNET_HOST) for _, u, _ in wire.sent)


@pytest.mark.parametrize("change, match", [
    ({"dual": True}, "hedge mode"),
    ({"multiAssetsMargin": True}, "multi-assets"),
    ({"feeBurn": True}, "BNB"),
    ({"assets": [{"asset": "USDT", "walletBalance": "100"}, {"asset": "BNB", "walletBalance": "0.1"}]}, "besides USDT"),
])
def test_account_shape_stops_on_any_difference(wire, change, match):
    v = live()
    v.connect()
    wire.account.update(change)
    with pytest.raises(PerpVenueError, match=match):
        v.require_account_shape()


def test_an_empty_asset_row_besides_usdt_is_fine(wire):
    v = live()
    v.connect()
    wire.account["assets"] = [{"asset": "USDT", "walletBalance": "100"}, {"asset": "BNB", "walletBalance": "0.00000000"}]
    v.require_account_shape()


# ── credentials (D9, D10) ────────────────────────────────────────────────────

def _keyfile(tmp_path: Path, text: str, mode: int = 0o600) -> Path:
    p = tmp_path / "live.pem"
    p.write_text(text, encoding="ascii")
    p.chmod(mode)
    return p


def test_ed25519_key_file_is_read(tmp_path):
    p = _keyfile(tmp_path, ED_PEM)
    key, secret = load_live_credentials({"BINANCE_LIVE_FUTURES_API_KEY": "kk",
                                         "BINANCE_LIVE_FUTURES_PRIVATE_KEY_FILE": str(p)})
    assert key == "kk" and secret == ED_PEM.strip()


@pytest.mark.parametrize("case", ["hmac", "missing", "group_readable", "rsa", "not_pem", "symlink", "other_owner"])
def test_anything_but_an_owner_only_ed25519_file_is_refused(tmp_path, monkeypatch, case):
    p = _keyfile(tmp_path, ED_PEM)
    env = {"BINANCE_LIVE_FUTURES_API_KEY": "kk", "BINANCE_LIVE_FUTURES_PRIVATE_KEY_FILE": str(p)}
    if case == "hmac":
        env["BINANCE_LIVE_FUTURES_API_SECRET"] = "s"
    elif case == "missing":
        env["BINANCE_LIVE_FUTURES_PRIVATE_KEY_FILE"] = str(tmp_path / "none.pem")
    elif case == "group_readable":
        p.chmod(0o640)
    elif case == "rsa":
        p.write_text(_pem(rsa.generate_private_key(public_exponent=65537, key_size=2048)), encoding="ascii")
    elif case == "not_pem":
        p.write_text("-----BEGIN PRIVATE KEY-----\nnot a key\n-----END PRIVATE KEY-----\n", encoding="ascii")
    elif case == "symlink":
        link = tmp_path / "link.pem"
        link.symlink_to(p)
        env["BINANCE_LIVE_FUTURES_PRIVATE_KEY_FILE"] = str(link)
    elif case == "other_owner":
        monkeypatch.setattr(os, "getuid", lambda: os.stat(p).st_uid + 1)
    with pytest.raises(PerpVenueError) as err:
        load_live_credentials(env)
    assert "kk" not in str(err.value) and "BEGIN" not in str(err.value)
