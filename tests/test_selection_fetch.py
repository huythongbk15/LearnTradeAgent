"""The fetch layer against a fake exchange: the allowlist, pacing, retries, stops, closed bars only, pages. No test here can reach the network."""
from __future__ import annotations

import http.client
import io
import json
import urllib.error
import urllib.request
from datetime import date, timedelta

import numpy as np
import polars as pl
import pytest

from tests.test_selection_exchange import RULE_FIRST, T0_MS, FakeExchange, blank, client_of, hour_ms, kline, refuse_network, without
from tests.test_selection_fixture import market
from tools.selection import config, fetch
from tools.selection import panel as pn
from tools.selection.config import hour_of

REAL_TRANSPORT = fetch.urllib_transport
CUT = "2020-09-07"
ORIGIN = "https://data-api.binance.vision"


@pytest.fixture(autouse=True)
def no_network(monkeypatch) -> None:
    refuse_network(monkeypatch)
    monkeypatch.setattr(config, "RULE_FIRST_WEEK", RULE_FIRST)      # the first daily fetch starts from it, and the market is short


def bars(root, coin: str = "AAA") -> pl.DataFrame:
    return pl.read_parquet(config.bars_dir(root) / f"{coin}.parquet")


def days(root, coin: str = "AAA") -> pl.DataFrame:
    return pl.read_parquet(config.daily_dir(root) / f"{coin}.parquet")


def scripted(*answers):
    """A transport that plays its answers in order, an exception raises. The URLs it saw are on .urls."""
    queue = list(answers)

    def transport(url, timeout):
        transport.urls.append(url)
        a = queue.pop(0)
        if isinstance(a, Exception):
            raise a
        return a

    transport.urls = []
    return transport


def test_the_two_public_calls_have_exact_urls() -> None:
    c = fetch.Client()
    assert c.url(fetch.EXCHANGE_INFO, {}) == f"{ORIGIN}/api/v3/exchangeInfo"
    assert c.url(fetch.KLINES, {"symbol": "BTCUSDT", "interval": "1h", "startTime": 0, "endTime": 9, "limit": 1000}) == f"{ORIGIN}/api/v3/klines?symbol=BTCUSDT&interval=1h&startTime=0&endTime=9&limit=1000"


@pytest.mark.parametrize("path", ["/api/v3/order", "/api/v3/account", "/api/v3/myTrades", "/api/v3/userDataStream", "/sapi/v1/capital/config/getall", "/fapi/v1/klines", "/api/v3/klines/", "/api/v3/ticker/price"])
def test_any_other_path_is_refused(path: str) -> None:
    with pytest.raises(ValueError):
        fetch.Client().url(path, {})


@pytest.mark.parametrize("param", ["signature", "timestamp", "apiKey", "recvWindow", "quantity", "side"])
def test_any_other_parameter_is_refused(param: str) -> None:
    with pytest.raises(ValueError):
        fetch.Client().url(fetch.KLINES, {"symbol": "BTCUSDT", param: "1"})
    with pytest.raises(ValueError):
        fetch.Client().url(fetch.EXCHANGE_INFO, {param: "1"})


@pytest.mark.parametrize("url", [
    "https://api.binance.com/api/v3/klines",
    "https://testnet.binance.vision/api/v3/klines",
    "http://data-api.binance.vision/api/v3/klines",
    "https://data-api.binance.vision.example.com/api/v3/klines",
    "https://x:y@data-api.binance.vision/api/v3/klines",
    "https://data-api.binance.vision:8443/api/v3/klines",
    "https://data-api.binance.vision/api/v3/order",
    "https://data-api.binance.vision/api/v3/klines#x",
])
def test_the_allowlist_refuses_every_other_url(url: str) -> None:
    with pytest.raises(ValueError):
        fetch.check_url(url)


def test_the_real_transport_checks_the_url_before_it_opens_anything() -> None:
    with pytest.raises(ValueError):
        REAL_TRANSPORT("https://api.binance.com/api/v3/account", 1.0)


def test_a_redirect_is_not_followed() -> None:
    assert fetch._NoRedirect().redirect_request(None, None, 302, "", {}, "https://example.com") is None


class Answer:
    """What an opener hands back: a context manager with a status and a body."""

    status = 200
    headers: dict = {}

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, n: int) -> bytes:
        return b"[1]"


def opener_that(answer, monkeypatch) -> list:
    """Replace urllib's opener, keep what the real transport asks of it."""
    seen = []

    class Opener:
        def open(self, url, timeout):
            seen.append((url, timeout))
            if isinstance(answer, Exception):
                raise answer
            return answer

    def build(*handlers):
        seen.append(handlers)
        return Opener()

    monkeypatch.setattr(fetch.urllib.request, "build_opener", build)
    return seen


def test_the_real_transport_returns_status_and_body_with_no_header_and_no_redirect(monkeypatch) -> None:
    seen = opener_that(Answer(), monkeypatch)
    url = f"{ORIGIN}/api/v3/exchangeInfo"
    assert REAL_TRANSPORT(url, 3.0) == (200, b"[1]")
    (proxy, redirect), call = seen
    assert proxy.proxies == {} and redirect is fetch._NoRedirect and call == (url, 3.0)


def test_the_real_transport_takes_no_proxy_from_the_environment(monkeypatch) -> None:
    """The allowlist is one host: a proxy in the environment would be a second one."""
    monkeypatch.setenv("https_proxy", "http://127.0.0.1:9")
    real = urllib.request.build_opener
    assert any(getattr(h, "proxies", {}).get("https") for h in real().handlers)     # what a plain opener does here
    built = []

    def build(*handlers):
        built.append(real(*handlers))
        built[-1].open = lambda url, timeout: Answer()
        return built[-1]

    monkeypatch.setattr(fetch.urllib.request, "build_opener", build)
    assert REAL_TRANSPORT(f"{ORIGIN}/api/v3/exchangeInfo", 3.0) == (200, b"[1]")
    assert not [h for h in built[0].handlers if isinstance(h, urllib.request.ProxyHandler)]


def response_of(raw: bytes) -> http.client.HTTPResponse:
    """A real response over bytes, with no socket behind it."""

    class Sock:
        def makefile(self, *args, **kwargs):
            return io.BytesIO(raw)

    r = http.client.HTTPResponse(Sock())
    r.begin()
    return r


def test_a_connection_cut_short_is_counted_since_read_raises_nothing(monkeypatch) -> None:
    """Nine bytes of a thousand. read with a size hands back what came, so the transport must compare."""
    cut = response_of(b'HTTP/1.1 200 OK\r\nContent-Length: 1000\r\n\r\n[{"a":1},')
    assert cut.read(fetch.MAX_BYTES) == b'[{"a":1},'
    opener_that(response_of(b'HTTP/1.1 200 OK\r\nContent-Length: 1000\r\n\r\n[{"a":1},'), monkeypatch)
    with pytest.raises(http.client.IncompleteRead):
        REAL_TRANSPORT(f"{ORIGIN}/api/v3/exchangeInfo", 3.0)


def test_a_whole_body_passes_the_count(monkeypatch) -> None:
    opener_that(response_of(b"HTTP/1.1 200 OK\r\nContent-Length: 3\r\n\r\n[1]"), monkeypatch)
    assert REAL_TRANSPORT(f"{ORIGIN}/api/v3/exchangeInfo", 3.0) == (200, b"[1]")


def test_the_real_transport_hands_an_http_error_back_as_a_status(monkeypatch) -> None:
    error = urllib.error.HTTPError(ORIGIN, 429, "slow down", {}, io.BytesIO(b"{}"))
    opener_that(error, monkeypatch)
    assert REAL_TRANSPORT(f"{ORIGIN}/api/v3/exchangeInfo", 3.0) == (429, b"{}")


@pytest.mark.parametrize("symbol", ["AAABTC", "aaausdt", "AAA", "AAA/USDT", "AAA_USDT"])
def test_only_usdt_pairs_in_their_exact_spelling_are_asked_for(symbol: str) -> None:
    with pytest.raises(ValueError):
        fetch.Client().url(fetch.KLINES, {"symbol": symbol})


def test_every_call_waits_the_pace() -> None:
    slept = []
    c = fetch.Client(transport=scripted((200, b"[]"), (200, b"[]")), pace=.5, sleep=slept.append)
    c.get(fetch.EXCHANGE_INFO)
    c.get(fetch.EXCHANGE_INFO)
    assert slept == [.5, .5] and c.calls == 2


def test_the_default_pace_is_under_ten_calls_a_second() -> None:
    assert fetch.Client().pace >= .1


def test_a_5xx_is_retried_with_backoff_then_answered() -> None:
    slept = []
    t = scripted((500, b""), (503, b""), (200, b'{"a": 1}'))
    c = fetch.Client(transport=t, pace=.1, sleep=slept.append)
    assert c.get(fetch.EXCHANGE_INFO) == {"a": 1}
    assert len(t.urls) == 3 and slept == [.1, 2.0, .1, 4.0, .1]


@pytest.mark.parametrize("error", [TimeoutError("t"), urllib.error.URLError("no route"), ConnectionResetError("r"), http.client.IncompleteRead(b"")])
def test_a_dropped_connection_is_retried(error: Exception) -> None:
    t = scripted(error, (200, b"[1]"))
    assert fetch.Client(transport=t, pace=0.0, sleep=lambda s: None).get(fetch.EXCHANGE_INFO) == [1]


def test_after_the_last_attempt_it_gives_up_and_says_why() -> None:
    t = scripted((500, b""), TimeoutError("t"), (502, b""), (200, b"[]"))
    with pytest.raises(fetch.FetchError, match="status 502"):
        fetch.Client(transport=t, pace=0.0, sleep=lambda s: None).get(fetch.EXCHANGE_INFO)
    assert len(t.urls) == 3


@pytest.mark.parametrize("status", [418, 429])
def test_a_rate_limit_stops_at_once(status: int) -> None:
    t = scripted((status, b""), (200, b"[]"))
    with pytest.raises(fetch.FetchStop, match=str(status)):
        fetch.Client(transport=t, pace=0.0, sleep=lambda s: None).get(fetch.EXCHANGE_INFO)
    assert len(t.urls) == 1


@pytest.mark.parametrize("status", [302, 400, 403, 404])
def test_a_refused_call_is_not_retried(status: int) -> None:
    t = scripted((status, b"{}"), (200, b"[]"))
    with pytest.raises(fetch.FetchError):
        fetch.Client(transport=t, pace=0.0, sleep=lambda s: None).get(fetch.EXCHANGE_INFO)
    assert len(t.urls) == 1


def test_a_body_that_is_not_json_is_a_failure_after_its_retries_not_a_crash() -> None:
    t = scripted(*[(200, b"<html>")] * fetch.ATTEMPTS)
    with pytest.raises(fetch.FetchError, match="not JSON"):
        fetch.Client(transport=t, pace=0.0, sleep=lambda s: None).get(fetch.EXCHANGE_INFO)
    assert len(t.urls) == fetch.ATTEMPTS


def test_a_body_cut_short_is_asked_for_again() -> None:
    t = scripted((200, b'{"symbols": [{"sta'), (200, b'{"symbols": []}'))
    assert fetch.Client(transport=t, pace=0.0, sleep=lambda s: None).get(fetch.EXCHANGE_INFO) == {"symbols": []}
    assert len(t.urls) == 2


def test_a_symbol_the_exchange_does_not_have_is_named_so() -> None:
    t = scripted((400, b'{"code":-1121,"msg":"Invalid symbol."}'))
    with pytest.raises(fetch.UnknownSymbol):
        fetch.Client(transport=t, pace=0.0, sleep=lambda s: None).get(fetch.KLINES, {"symbol": "OLDUSDT"})


def test_only_bars_closed_before_the_cutoff_enter_even_if_the_exchange_sends_more(tmp_path) -> None:
    ex = FakeExchange(market(), ignore_end=True)
    fetch.refresh_coin(client_of(ex), "AAA", CUT, tmp_path)
    assert bars(tmp_path)["hour"].max() == hour_of(CUT) - 1


def test_a_bar_that_closes_at_the_cutoff_is_partial(tmp_path) -> None:
    cut = hour_ms(hour_of(CUT))
    closed = kline(cut - config.HOUR_MS, config.HOUR_MS, 1.0, 2.0, 3.0)
    partial = kline(cut - config.HOUR_MS * 2, config.HOUR_MS, 1.0, 2.0, 3.0)
    partial[6] = cut
    rows = [partial, closed]
    t = scripted((200, json.dumps(rows).encode()))
    got = list(fetch.pages(client_of(t), "AAAUSDT", "1h", config.HOUR_MS, cut - 3 * config.HOUR_MS, cut))
    assert got == [[closed]]


def test_a_day_enters_only_after_it_closed(tmp_path) -> None:
    ex = FakeExchange(market(), ignore_end=True)
    fetch.refresh_pair(client_of(ex), "AAA", CUT, tmp_path)
    last = days(tmp_path)["day"].max()
    assert date(1970, 1, 1) + timedelta(days=int(last)) == date(2020, 9, 6)


def test_a_new_coin_fetches_its_whole_history_in_pages(tmp_path) -> None:
    mk = market()
    ex = FakeExchange(mk)
    added = fetch.refresh_coin(client_of(ex), "AAA", CUT, tmp_path)
    T = hour_of(CUT)
    calls = ex.kline_calls("AAAUSDT")
    assert added == T and len(calls) == T // 1000 + 1
    assert [int(q["startTime"]) for q in calls] == [T0_MS + k * 1000 * config.HOUR_MS for k in range(len(calls))]
    assert all(q["limit"] == "1000" and int(q["endTime"]) == hour_ms(T) - 1 for q in calls)
    j = mk.coins.index("AAA")
    f = bars(tmp_path)
    assert f["hour"].to_list() == list(range(T))
    assert (f["o"].to_numpy() == mk.opens[:T, j]).all() and (f["c"].to_numpy() == mk.closes[:T, j]).all() and (f["q"].to_numpy() == mk.quotes[:T, j]).all()


def test_a_late_listing_pages_from_its_first_bar(tmp_path) -> None:
    mk = market()
    fetch.refresh_coin(client_of(FakeExchange(mk)), "LATE", CUT, tmp_path)
    assert bars(tmp_path, "LATE")["hour"][0] == 3100


def test_a_full_page_that_reaches_the_cutoff_asks_no_more(tmp_path) -> None:
    ex = FakeExchange(market())
    fetch.refresh_coin(client_of(ex), "AAA", "2020-02-12T16:00:00", tmp_path)       # exactly 1000 hours
    assert bars(tmp_path).height == 1000 and len(ex.kline_calls("AAAUSDT")) == 1


def test_a_full_page_short_of_the_cutoff_asks_on(tmp_path) -> None:
    ex = FakeExchange(market())
    fetch.refresh_coin(client_of(ex), "AAA", "2020-02-12T17:00:00", tmp_path)
    assert bars(tmp_path).height == 1001 and len(ex.kline_calls("AAAUSDT")) == 2


def test_a_short_page_is_the_last_page(tmp_path) -> None:
    ex = FakeExchange(market())
    fetch.refresh_coin(client_of(ex), "GONE", "2020-10-20", tmp_path)       # the coin stops at hour 5300, a long way before the cutoff
    assert bars(tmp_path, "GONE").height == 5300 and len(ex.kline_calls("GONEUSDT")) == 6


def test_a_known_coin_asks_from_its_last_stored_bar(tmp_path) -> None:
    mk = market()
    x = np.array([1.0, 2.0])
    pn.append_bars("AAA", [10, 11], x, x, x, CUT, tmp_path)
    ex = FakeExchange(mk)
    added = fetch.refresh_coin(client_of(ex), "AAA", "2020-01-04", tmp_path)
    assert int(ex.kline_calls("AAAUSDT")[0]["startTime"]) == hour_ms(12)
    assert added == 48 - 12 and bars(tmp_path)["hour"].to_list() == [10, *range(11, 48)]


def test_a_failed_page_leaves_a_shorter_history_with_no_hole_and_the_next_run_goes_on(tmp_path) -> None:
    ex = FakeExchange(market())
    ex.hook = lambda n, path, q: (500, b"") if n > 1 else None
    with pytest.raises(fetch.FetchError):
        fetch.refresh_coin(client_of(ex), "AAA", CUT, tmp_path)
    assert bars(tmp_path)["hour"].to_list() == list(range(1000))
    fetch.refresh_coin(client_of(FakeExchange(market())), "AAA", CUT, tmp_path)
    assert bars(tmp_path)["hour"].to_list() == list(range(hour_of(CUT)))


def test_a_coin_with_a_hole_keeps_it_and_the_store_stays_sorted(tmp_path) -> None:
    mk = blank(market(), "AAA", 100, 110)
    fetch.refresh_coin(client_of(FakeExchange(mk)), "AAA", "2020-01-10", tmp_path)
    h = bars(tmp_path)["hour"].to_numpy()
    assert (np.diff(h) > 0).all() and 99 in h and 110 in h and not set(range(100, 110)) & set(h.tolist())


def test_luna_is_asked_by_its_exchange_name_and_stored_by_its_own(tmp_path) -> None:
    mk = market()
    ex = FakeExchange(mk)
    fetch.refresh_coin(client_of(ex), "LUNA2", "2020-01-04", tmp_path)
    fetch.refresh_pair(client_of(ex), "LUNA", CUT, tmp_path)
    assert ex.kline_calls("LUNAUSDT") and not ex.kline_calls("LUNA2USDT")
    assert (config.bars_dir(tmp_path) / "LUNA2.parquet").exists() and (config.daily_dir(tmp_path) / "LUNA.parquet").exists()


def test_a_first_daily_fetch_starts_120_days_before_the_first_rule_week_and_takes_one_call(tmp_path) -> None:
    ex = FakeExchange(market())
    config_first = date.fromisoformat(config.RULE_FIRST_WEEK) - timedelta(days=120)
    ms = (config_first - date(1970, 1, 1)).days * config.DAY_MS
    t = scripted((200, b"[]"))
    fetch.refresh_pair(client_of(t), "AAA", CUT, tmp_path)
    assert f"startTime={ms}&" in t.urls[0]
    fetch.refresh_pair(client_of(ex), "AAA", CUT, tmp_path)
    assert len(ex.kline_calls("AAAUSDT", "1d")) == 1


def test_a_weekly_daily_fetch_asks_from_the_day_after_the_last_stored_day(tmp_path) -> None:
    pn.append_daily("AAA", [date(2020, 8, 20)], [1.0], [2.0], CUT, tmp_path)
    ex = FakeExchange(market())
    assert fetch.refresh_pair(client_of(ex), "AAA", CUT, tmp_path) == 17       # 21 August to 6 September
    assert int(ex.kline_calls("AAAUSDT", "1d")[0]["startTime"]) == (date(2020, 8, 21) - date(1970, 1, 1)).days * config.DAY_MS


def test_the_report_counts_what_failed_and_share_of_pairs() -> None:
    assert fetch.Report().daily_fail_share == 0.0
    assert fetch.Report(attempted=True).daily_fail_share == 1.0
    assert fetch.Report(attempted=True, tradable=[]).daily_fail_share == 1.0
    assert fetch.Report(attempted=True, tradable=list("abcd"), daily_failed=["a"]).daily_fail_share == .25


def test_a_stop_in_the_daily_step_marks_every_pair_left_and_ends_all_fetching(tmp_path) -> None:
    ex = FakeExchange(market())
    ex.hook = lambda n, path, q: (429, b"") if n == 4 else None
    report = fetch.Report(attempted=True)
    fetch.refresh_pairs(client_of(ex), CUT, tmp_path, report)
    pairs = len(report.tradable)
    assert report.stop == "status 429" and len(report.daily_failed) == pairs - 2 and report.daily_fail_share == (pairs - 2) / pairs
    asked = len(ex.calls)
    fetch.refresh_hourly(client_of(ex), ["BTC", "AAA"], CUT, tmp_path, report)
    assert len(ex.calls) == asked and report.hourly_failed == ["BTC", "AAA"] and not report.stopped


def test_exchange_info_that_fails_leaves_no_pairs_and_asks_nothing_more(tmp_path) -> None:
    ex = FakeExchange(market())
    ex.hook = lambda n, path, q: (500, b"")
    report = fetch.Report(attempted=True)
    fetch.refresh_pairs(client_of(ex), CUT, tmp_path, report)
    assert report.tradable is None and report.trading is None and report.daily_fail_share == 1.0
    assert len(ex.calls) == fetch.ATTEMPTS * (len(fetch.OUTAGE_WAITS) + 1) and report.errors == ["exchangeInfo: FetchError"]


def test_a_run_waits_out_an_outage_at_its_first_call(tmp_path) -> None:
    ex = FakeExchange(market())
    ex.hook = lambda n, path, q: (503, b"") if n <= 3 * fetch.ATTEMPTS else None
    slept = []
    report = fetch.Report(attempted=True)
    fetch.refresh_pairs(fetch.Client(transport=ex, pace=0.0, sleep=slept.append), CUT, tmp_path, report)
    assert report.tradable and report.trading and report.errors == [] and report.daily_fail_share == 0.0
    assert [s for s in slept if s in fetch.OUTAGE_WAITS] == list(fetch.OUTAGE_WAITS[:3])


def test_the_wait_at_the_first_call_is_a_quarter_of_an_hour_inside_the_jobs_limit() -> None:
    worst = (len(fetch.OUTAGE_WAITS) + 1) * fetch.ATTEMPTS * fetch.TIMEOUT_SECONDS
    assert 15 * 60 <= sum(fetch.OUTAGE_WAITS) <= 16 * 60 and sum(fetch.OUTAGE_WAITS) + worst < 30 * 60


def test_the_exchange_saying_stop_is_not_waited_out(tmp_path) -> None:
    ex = FakeExchange(market())
    ex.hook = lambda n, path, q: (503, b"") if n <= fetch.ATTEMPTS else (429, b"")
    slept = []
    report = fetch.Report(attempted=True)
    fetch.refresh_pairs(fetch.Client(transport=ex, pace=0.0, sleep=slept.append), CUT, tmp_path, report)
    assert report.stop == "status 429" and len(ex.calls) == fetch.ATTEMPTS + 1 and slept.count(fetch.OUTAGE_WAITS[1]) == 0


def test_a_rate_limit_on_exchange_info_is_a_stop(tmp_path) -> None:
    ex = FakeExchange(market())
    ex.hook = lambda n, path, q: (418, b"")
    report = fetch.Report(attempted=True)
    fetch.refresh_pairs(client_of(ex), CUT, tmp_path, report)
    assert report.stop == "status 418" and len(ex.calls) == 1


def test_a_page_that_does_not_parse_fails_that_coin_and_the_next_goes_on(tmp_path) -> None:
    ex = FakeExchange(market())
    ex.hook = lambda n, path, q: (200, b'[["x"]]') if q.get("symbol") == "AAAUSDT" else None
    report = fetch.Report(attempted=True)
    fetch.refresh_hourly(client_of(ex), ["AAA", "CCC"], "2020-01-04", tmp_path, report)
    assert report.hourly_failed == ["AAA"] and report.stop is None and bars(tmp_path, "CCC").height


def test_five_failures_in_a_row_stop_the_run_instead_of_walking_every_name(tmp_path) -> None:
    ex = FakeExchange(market())
    ex.hook = lambda n, path, q: (500, b"")
    names = ["AAA", "CCC", "DDD", "EEE", "FFF", "GGG", "HHH"]
    report = fetch.Report(attempted=True)
    fetch.refresh_hourly(client_of(ex), names, "2020-01-04", tmp_path, report)
    assert len(ex.calls) == 5 * fetch.ATTEMPTS and report.hourly_failed == names and report.stop == "too many failures in a row"


def test_a_good_name_resets_the_count_of_failures(tmp_path) -> None:
    ex = FakeExchange(market())
    bad = {"AAAUSDT", "DDDUSDT", "FFFUSDT", "HHHUSDT", "JJJUSDT"}
    ex.hook = lambda n, path, q: (500, b"") if q.get("symbol") in bad else None
    names = ["AAA", "CCC", "DDD", "EEE", "FFF", "GGG", "HHH", "III", "JJJ", "KKK"]
    report = fetch.Report(attempted=True)
    fetch.refresh_hourly(client_of(ex), names, "2020-01-04", tmp_path, report)
    assert report.stop is None and report.hourly_failed == ["AAA", "DDD", "FFF", "HHH", "JJJ"]


def test_a_coin_the_exchange_dropped_is_stopped_not_failed(tmp_path) -> None:
    mk = market()
    x = np.array([1.0])
    pn.append_bars("OLD1", [5], x, x, x, CUT, tmp_path)
    report = fetch.Report(attempted=True)
    fetch.refresh_hourly(client_of(FakeExchange(mk)), ["OLD1"], CUT, tmp_path, report)
    assert report.stopped == {"OLD1"} and not report.hourly_failed and not report.new_coins


def test_a_coin_off_the_trading_pairs_is_stopped_and_a_new_coin_is_named(tmp_path) -> None:
    mk = market()
    report = fetch.Report(attempted=True, trading={"BTC"})
    fetch.refresh_hourly(client_of(FakeExchange(mk)), ["GONE"], "2020-02-01", tmp_path, report)
    assert report.new_coins == ["GONE"]
    fetch.refresh_hourly(client_of(FakeExchange(mk)), ["GONE"], CUT, tmp_path, report)
    fetch.refresh_hourly(client_of(FakeExchange(mk)), ["GONE"], "2020-10-20", tmp_path, report)
    assert report.stopped == {"GONE"} and report.new_coins == ["GONE"]
    assert bars(tmp_path, "GONE")["hour"].max() == 5299


@pytest.mark.parametrize("body", [b"[]", None])
def test_a_coin_that_still_trades_is_not_stopped_by_an_empty_answer_or_a_short_page(tmp_path, body) -> None:
    """A fetch that ended is not the exchange saying the coin ended. None: the page stops 30 hours short of the cutoff."""
    ex = FakeExchange(market())
    real = ex.klines
    cut = hour_ms(hour_of(CUT) - 30)
    ex.klines = lambda q: (200, body if body else json.dumps([r for r in json.loads(real(q)[1]) if r[0] < cut]).encode())
    report = fetch.Report(attempted=True, trading={"BTC", "AAA"})
    fetch.refresh_hourly(client_of(ex), ["AAA"], CUT, tmp_path, report)
    assert report.stopped == set() and report.hourly_failed == []


def test_luna_trades_under_its_exchange_name(tmp_path) -> None:
    report = fetch.Report(attempted=True, trading={"BTC", "LUNA"})
    fetch.refresh_hourly(client_of(FakeExchange(market())), ["LUNA2"], "2020-01-04", tmp_path, report)
    assert report.stopped == set()


def test_a_coin_off_the_pairs_whose_fetch_failed_is_late_not_stopped(tmp_path) -> None:
    """It may have bars we did not get, so its week waits."""
    ex = FakeExchange(market())
    ex.hook = lambda n, path, q: (500, b"")
    report = fetch.Report(attempted=True, trading={"BTC"})
    fetch.refresh_hourly(client_of(ex), ["GONE"], CUT, tmp_path, report)
    assert report.stopped == set() and report.hourly_failed == ["GONE"]


def test_with_the_pairs_unknown_nothing_is_stopped_but_a_pair_the_exchange_refuses(tmp_path) -> None:
    report = fetch.Report(attempted=True)
    fetch.refresh_hourly(client_of(FakeExchange(without(market(), "LATE"))), ["GONE", "LATE"], CUT, tmp_path, report)
    assert report.trading is None and report.stopped == {"LATE"}


def test_the_trading_pairs_are_read_raw(tmp_path) -> None:
    report = fetch.Report(attempted=True)
    fetch.refresh_pairs(client_of(FakeExchange(market())), CUT, tmp_path, report)
    assert "BTC" in report.trading and "AAA" in report.trading and "BTC" not in report.tradable
    assert "GONE" not in report.trading and not config.sealed_hits(report.trading)


@pytest.mark.parametrize("body", [b"{}", b'{"symbols": []}', b'{"symbols": [{"status": "TRADING", "quoteAsset": "USDT", "isSpotTradingAllowed": true, "baseAsset": "AAA"}]}'])
def test_an_answer_without_btc_says_nothing_about_who_stopped(tmp_path, body: bytes) -> None:
    ex = FakeExchange(market())
    ex.hook = lambda n, path, q: (200, body) if path == fetch.EXCHANGE_INFO else None
    report = fetch.Report(attempted=True)
    fetch.refresh_pairs(client_of(ex), CUT, tmp_path, report)
    assert report.trading is None


def test_a_coin_the_exchange_never_had_stores_nothing(tmp_path) -> None:
    report = fetch.Report(attempted=True)
    fetch.refresh_hourly(client_of(FakeExchange(without(market(), "LATE"))), ["LATE"], CUT, tmp_path, report)
    assert not (config.bars_dir(tmp_path) / "LATE.parquet").exists()
    assert report.stopped == {"LATE"} and not report.new_coins


def test_a_coin_with_a_broken_store_fails_alone(tmp_path) -> None:
    config.bars_dir(tmp_path).mkdir(parents=True)
    (config.bars_dir(tmp_path) / "AAA.parquet").write_bytes(b"not parquet")
    report = fetch.Report(attempted=True)
    fetch.refresh_hourly(client_of(FakeExchange(market())), ["AAA", "CCC"], "2020-01-04", tmp_path, report)
    assert report.hourly_failed == ["AAA"] and bars(tmp_path, "CCC").height


def test_tradable_pairs_drop_btc_and_masked_coins_before_the_daily_step(tmp_path) -> None:
    ex = FakeExchange(market())
    report = fetch.Report(attempted=True)
    fetch.refresh_pairs(client_of(ex), CUT, tmp_path, report)
    assert "BTC" not in report.tradable and not config.sealed_hits(report.tradable)
    assert ex.masked_requests == [] and not ex.kline_calls("BTCUSDT", "1d")
