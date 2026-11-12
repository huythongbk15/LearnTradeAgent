"""Book KRW (H-KRW-FADE-16) on the fake Binance USD-M demo venue, with fake public data. No network."""

from __future__ import annotations

import collections
import json
import os
import urllib.request

import ccxt
import pytest

import trading_agent.exchanges.ccxt_adapter as ca
from tests.test_perp_demo import VENUES, KEY, FakeBook, FakeUsdm, Killed, events, hold, make_venue, orders_sent, pos
from tools.krwfade import feed as kfeed
from tools.krwfade import rules as R
from tools.krwfade import shadow as S
from trading_agent.execution.live_safety import LiveRiskStateStore
from trading_agent.execution.perp import krwfade as kx
from trading_agent.execution.perp.book import KEY_MAX, PAIR_BOOK, Book, PairStore, PerpBookError, run_cycle

MIN = 60_000
H = 3_600_000
DAY = 24 * H
FWD = kx.FORWARD_FROM_MS
N0 = FWD + 30 * DAY + 5 * H + 25 * MIN         # a notice at 05:25 UTC
E0 = FWD + 30 * DAY + 6 * H                    # its entry: the 06:00 open


def sym(name: str, onboard: int, status: str = "TRADING", delivery: int = 4_133_404_800_000) -> dict:
    return {"symbol": name, "contractType": "PERPETUAL", "quoteAsset": "USDT", "status": status, "onboardDate": onboard,
            "deliveryDate": delivery}


SYMS = {s: sym(s, N0 - 100 * DAY) for s in ("AAAUSDT", "BBBUSDT", "CCCUSDT", "DDDUSDT", "EEEUSDT", "ZZZUSDT")}


def note(nid: int, tok: str, at: int = N0, title: str | None = None, listed: int | None = None) -> kfeed.Notice:
    return kfeed.Notice(nid, title or f"테스트({tok}) KRW 마켓 디지털 자산 추가", at, at if listed is None else listed)


class Public:
    """Upbit's notice list, mainnet exchangeInfo and the shadow rows, as the book asks for them."""

    def __init__(self, *notes: kfeed.Notice, symbols: dict | None = None):
        self.notes = list(notes)
        self.syms = dict(SYMS if symbols is None else symbols)
        self.fail: set[str] = set()
        self.calls: collections.Counter = collections.Counter()

    def notices(self):
        self.calls["notices"] += 1
        if "notices" in self.fail:
            raise kfeed.FeedError("status 503")
        return list(self.notes)

    def symbols(self):
        self.calls["symbols"] += 1
        if "symbols" in self.fail:
            raise RuntimeError("exchangeInfo down")
        return dict(self.syms)

    def shadow_row(self, perp: str, entry_ms: int, notice_ms: int) -> dict:
        self.calls["shadow"] += 1
        if "shadow" in self.fail:
            raise S.NoData("no alt bar in the hold")
        return {"pass_0a": True, "entry_ms": entry_ms, "g_bps": 1.0, "n_bps": 0.5}


@pytest.fixture(params=VENUES)
def world(tmp_path, monkeypatch, request):
    """The fake venue (BTC at 80,000, equity 10,000), Demo or mainnet, and the pair book."""

    fake = FakeUsdm({"BTCUSDT": 80_000.0, "AAAUSDT": 2.0, "BBBUSDT": 10.0, "CCCUSDT": 5.0, "DDDUSDT": 1.0,
                     "EEEUSDT": 4.0, "OLDUSDT": 10.0})
    monkeypatch.setattr(ca.ccxt, "binanceusdm", lambda cfg: fake)
    # These runs make far more than the adapter's 100 free calls; past them it paces 0.6 s a call. Not here.
    monkeypatch.setattr(ca.RateLimitManager, "REFILL_PER_S", 1e9)
    # Durability is not under test: each store save's fsync costs tens of ms on this disk, hundreds a test.
    monkeypatch.setattr(os, "fsync", lambda fd: None)
    venue = make_venue(request.param)
    venue.connect()
    orders = LiveRiskStateStore(tmp_path / "order_state.json", integrity_key=KEY)
    book = Book(venue, orders, PairStore.load(tmp_path / "pairs.json", KEY), tmp_path / "audit.jsonl", 100.0,
                sleep=lambda s: None)
    return fake, book, tmp_path


def krw(book: Book, tmp, pub: Public, usd: float = 500.0, **kw) -> kx.KrwFadeBook:
    """Book KRW as a new process makes it: the store read again from disk."""

    return kx.KrwFadeBook(book, PairStore.load(tmp / kx.STORE, KEY), usd, pub.notices, pub.symbols, pub.shadow_row,
                          wall=lambda: 1.0, **kw)


def fresh(book: Book, tmp) -> Book:
    """The pair book of a new process."""

    return Book(book.venue, LiveRiskStateStore(tmp / "order_state.json", integrity_key=KEY),
                PairStore.load(tmp / "pairs.json", KEY), tmp / "audit.jsonl", 100.0, sleep=lambda s: None)


def cycle(book: Book, kb: kx.KrwFadeBook, at: int, *, others=(), **kw) -> dict:
    return run_cycle(book, now_ms=at, mainnet_info={}, execute=True, others=[*others, kb], **kw)


def stored(tmp) -> PairStore:
    return PairStore.load(tmp / kx.STORE, KEY)


def event(tmp, nid: int = 101, tok: str = "AAA") -> dict:
    return stored(tmp).pairs[kx.event_id(nid, tok)]


def krw_events(tmp) -> list[dict]:
    return [e for e in events(tmp) if e["event"].startswith("perp_krw")]


def no_floats(x) -> bool:
    if isinstance(x, dict):
        return all(no_floats(v) for v in x.values())
    if isinstance(x, (list, tuple)):
        return all(no_floats(v) for v in x)
    return not isinstance(x, float)


def enter(world, *notes: kfeed.Notice, at: int = E0 + 2 * MIN) -> tuple[Public, kx.KrwFadeBook]:
    fake, book, tmp = world
    pub = Public(*(notes or (note(101, "AAA"),)))
    kb = krw(book, tmp, pub)
    cycle(book, kb, at)
    return pub, kb


# ── the rule ────────────────────────────────────────────────────────────────────

AUDIT_TITLES = [
    # a follow-up appended to the original title: the market spec stays, the note goes
    ("액시스로보틱스(AXIS) 신규 거래지원 안내 (KRW, BTC, USDT 마켓) (매도 최저가 기준 가격 안내)", ["AXIS"]),
    ("카이아(KAIA) 신규 거래지원 안내 (KRW, BTC, USDT 마켓) (KAIA/BTC 초기 매수 제한 안내)", ["KAIA"]),
    # several tickers in one bracket
    ("원화 마켓 디지털 자산 추가 (AXS, STX, FLOW, DAWN)", ["AXS", "STX", "FLOW", "DAWN"]),
    ("KRW, BTC 마켓 디지털 자산 추가 (AVAX, IMX) (거래지원 개시 시점 연장 안내)", ["AVAX", "IMX"]),
    ("원화 마켓 신규 상장 (컴파운드 COMP, 스와이프 SXP)", ["COMP", "SXP"]),
    # single-letter tickers; a swap warning inside a listing notice is no listing
    ("웜홀(W) 신규 거래지원 안내 (KRW, BTC, USDT 마켓)", ["W"]),
    ("신퓨처스(F) 신규 거래지원 안내 (KRW, USDT 마켓)", ["F"]),
    ("오원익스체인지(O) 신규 거래지원 안내 (KRW, BTC, USDT 마켓)", ["O"]),
    ("KRW, BTC 마켓 디지털 자산 추가 (T) 및 이에 따른 NU 유의 종목 지정 안내 (NU <> T 스왑 사이트 추가 안내)", ["T"]),
    # a market list per token
    ("셀레스티아(TIA)(KRW, BTC, USDT 마켓), 아이오넷(IO)(BTC, USDT 마켓) 신규 거래지원 안내 (주문 타입 제한 관련 안내)", ["TIA"]),
    ("라이브피어(LPT)(KRW, USDT 마켓), 포켓네트워크(POKT)(KRW 마켓) 디지털 자산 추가", ["LPT", "POKT"]),
    # BTC- or USDT-only listings are not KRW
    ("인피닛(IN) BTC, USDT 마켓, 비쓰리(B3) USDT 마켓 신규 거래지원 안내", []),
    ("BTC, USDT 마켓 신규 거래지원 안내 (BICO, BMT, NIL, GWEI) (BICO, BMT, NIL, GWEI 거래지원 개시 시점 추가 변경 안내)", []),
    ("넥소(NEXO) 신규 거래지원 안내 (USDT 마켓) (거래지원 개시 시점 변경 안내)", []),
    # a KRW-market addition for a coin Upbit already lists is KRW
    ("닐리온(NIL) KRW 마켓 디지털 자산 추가", ["NIL"]),
    ("커브(CRV) KRW, USDT 마켓 디지털 자산 추가", ["CRV"]),
    ("퍼퍼(PUFFER) KRW 마켓 디지털 자산 추가", ["PUFFER"]),
    # a ticker cancelled in a follow-up is dropped
    ("헤미(HEMI), 유즈리스(USELESS) 신규 거래지원 안내 (KRW, BTC, USDT 마켓) (헤미(HEMI) 거래지원 취소 안내)", ["USELESS"]),
    # never listings
    ("블러(BLUR) 거래지원 종료 안내 (11/30 15:00)", []),
    ("이니시아(INIT) 거래 유의 종목 지정 안내", []),
    ("왁스(WAXP) 유의 촉구 안내", []),
    ("멀티버스엑스(EGLD) 거래 유의 종목 지정 기간 연장 안내", []),
]


@pytest.mark.parametrize("title,want", AUDIT_TITLES, ids=[t[:24] for t, _ in AUDIT_TITLES])
def test_the_classifier_reads_the_audited_titles(title, want):
    assert [t.token for t in R.krw_tokens(title)] == want


def test_a_shared_market_list_is_krw_for_every_token_and_flagged_ambiguous():
    (shib, gal) = R.krw_tokens("KRW, BTC 마켓 디지털 자산 추가 (SHIB, GAL)")
    assert (shib.token, shib.ambiguous, gal.token, gal.ambiguous) == ("SHIB", True, "GAL", True)
    (one,) = R.krw_tokens("닐리온(NIL) KRW 마켓 디지털 자산 추가")
    assert not one.ambiguous
    assert R.classify("블러(BLUR) 거래지원 종료 안내 (11/30 15:00)") is None


def test_entry_is_the_first_hour_open_at_or_after_notice_plus_ten_minutes():
    day = FWD + 30 * DAY
    assert R.entry_ms(day + 5 * H + 25 * MIN) == day + 6 * H
    assert R.entry_ms(day + 5 * H + 50 * MIN) == day + 6 * H              # 06:00 exactly: that open
    assert R.entry_ms(day + 5 * H + 50 * MIN + 1) == day + 7 * H
    assert R.entry_ms(day + 6 * H) == day + 7 * H
    assert R.entry_ms(1793086200000) == 1793088000000                     # NIL 2026-10-27 07:30 -> 08:00 (events.json)


def test_eligibility_age_perp_status_aliases_deny_sealed_and_pegged():
    t = N0
    syms = {
        "AAAUSDT": sym("AAAUSDT", t - 35 * DAY),
        "YNGUSDT": sym("YNGUSDT", t - 35 * DAY + 1),
        "SETUSDT": sym("SETUSDT", t - 300 * DAY, "SETTLING", t - DAY),
        "1000SHIBUSDT": sym("1000SHIBUSDT", t - 600 * DAY),
        "1MBABYUSDT": sym("1MBABYUSDT", t - 600 * DAY),
        "BEAMXUSDT": sym("BEAMXUSDT", t - 200 * DAY),
        "BEAMUSDT": sym("BEAMUSDT", t - 900 * DAY),
        "AIUSDT": sym("AIUSDT", t - 900 * DAY),
        "AIGENSYNUSDT": sym("AIGENSYNUSDT", t - 60 * DAY),
        "RAYUSDT": sym("RAYUSDT", t - 900 * DAY, "SETTLING", t - 500 * DAY),
        "RAYSOLUSDT": sym("RAYSOLUSDT", t - 190 * DAY),
        "METUSDT": sym("METUSDT", t - 40 * DAY),
        "PROSUSDT": sym("PROSUSDT", t - 400 * DAY),
        "ETHUSDT": sym("ETHUSDT", t - 2000 * DAY),
        "QUARTUSDT_261225": {**sym("QUARTUSDT_261225", t - 90 * DAY), "contractType": "CURRENT_QUARTER"},
    }
    v = lambda tok: R.verdict(tok, t, syms)   # noqa: E731
    assert v("AAA") == R.Verdict("AAA", "AAAUSDT", True, None, 35.0)
    assert (v("YNG").ok, v("YNG").reason) == (False, "perp_younger_than_35d")
    assert (v("SET").perp, v("SET").reason) == ("SETUSDT", "not_trading")
    assert (v("NONE").perp, v("NONE").reason) == (None, "no_perp")
    assert v("SHIB").perp == "1000SHIBUSDT" and v("BABY").perp == "1MBABYUSDT"
    assert v("BEAM").perp == "BEAMXUSDT" and v("AI").perp == "AIGENSYNUSDT" and v("MET2").perp == "METUSDT"
    assert v("RAY").perp == "RAYSOLUSDT"
    assert R.candidates("AI") == ["AIGENSYNUSDT"]           # an alias stands alone: never Binance's AIUSDT
    assert (v("PROS").ok, v("PROS").reason) == (False, "deny_collision")
    for tok in ("ETH", "SOL", "BNSOL", "BTC", "XRP"):
        assert v(tok).reason == "sealed_coin"
    for tok in ("USDT", "USDC", "PYUSD", "JPYC", "USDS", "XAUT"):
        assert v(tok).reason == "pegged"
    assert v("QUART").reason == "no_perp"                     # only USDT perpetuals count


def test_only_the_first_krw_notice_of_a_token_within_seven_days_is_an_event():
    t = N0
    assert R.first_within(t, []) and R.first_within(t, [t - 7 * DAY - 1]) and R.first_within(t, [t + DAY])
    assert not R.first_within(t, [t - 7 * DAY]) and not R.first_within(t, [t - 3 * DAY, t - 30 * DAY])


def test_the_book_records_events_merges_within_seven_days_and_leaves_the_research_sample(world):
    fake, book, tmp = world
    young = {**SYMS, "YNGUSDT": sym("YNGUSDT", N0 - 10 * DAY)}
    old = FWD - 72 * H - 2 * H                       # E + 72 h before 2026-11-02: the research sample
    pub = Public(note(101, "AAA"), note(102, "AAA", at=N0 + 3 * DAY), note(103, "YNG"), note(104, "ETH"),
                 note(105, "USDC"), note(99, "BBB", at=old), note(106, "NOPE"),
                 kfeed.Notice(107, "블러(BLUR) 거래지원 종료 안내 (11/30 15:00)", N0, N0), symbols=young)
    kb = krw(book, tmp, pub)
    rep = cycle(book, kb, N0 + 5 * MIN)["krw"]
    assert rep["new_events"] == 1
    st = stored(tmp)
    assert [k for k in st.pairs if k.startswith("e")] == ["e101-AAA"]
    assert st.pairs["book"]["not_events"] == {"102-AAA": "merged_within_7d", "103-YNG": "perp_younger_than_35d",
                                              "104-ETH": "sealed_coin", "105-USDC": "pegged",
                                              "99-BBB": "research_sample", "106-NOPE": "no_perp"}
    assert set(st.seen) == {f"n{i}" for i in (99, 101, 102, 103, 104, 105, 106, 107)}
    assert pub.calls["symbols"] == 1                          # exchangeInfo read once a run
    cycle(book, kb, N0 + 6 * MIN)                             # seen notices are not read again
    assert pub.calls["symbols"] == 1 and len([k for k in stored(tmp).pairs if k.startswith("e")]) == 1


def test_a_notice_waits_when_exchange_info_cannot_be_read(world):
    fake, book, tmp = world
    pub = Public(note(101, "AAA"))
    pub.fail.add("symbols")
    kb = krw(book, tmp, pub)
    assert cycle(book, kb, N0 + 5 * MIN)["krw"]["new_events"] == 0
    assert "n101" not in stored(tmp).seen and stored(tmp).pairs["book"]["feed"]["fails"] == 1
    pub.fail.clear()
    assert cycle(book, kb, N0 + 10 * MIN)["krw"]["new_events"] == 1


# ── the entry window ────────────────────────────────────────────────────────────

def test_an_event_waits_for_e_and_enters_up_to_fifteen_minutes_after_it(world):
    fake, book, tmp = world
    pub = Public(note(101, "AAA"))
    kb = krw(book, tmp, pub)
    cycle(book, kb, N0 + 2 * MIN)
    assert event(tmp)["status"] == "waiting" and event(tmp)["entry_ms"] == E0
    assert cycle(book, kb, E0 - 1)["krw"]["entered"] == 0 and not orders_sent(fake)
    assert cycle(book, kb, E0 + kx.ENTRY_WINDOW_MS)["krw"]["entered"] == 1          # E + 15 min is still inside
    assert event(tmp)["status"] == "open"


def test_an_event_no_run_decides_by_e_plus_fifteen_minutes_is_missed_and_still_shadowed(world):
    fake, book, tmp = world
    pub = Public(note(101, "AAA"))
    kb = krw(book, tmp, pub)
    cycle(book, kb, N0 + 2 * MIN)                                  # AAA waits
    rep = cycle(book, kb, E0 + kx.ENTRY_WINDOW_MS + 1)["krw"]
    assert rep["missed"] == 1 and rep["entered"] == 0 and not orders_sent(fake)
    assert (event(tmp)["status"], event(tmp)["reason"]) == ("missed", "no_run_in_window")
    # first seen after its window: recorded missed at once
    pub.notes.append(note(102, "BBB", at=N0 + DAY))
    cycle(book, kb, E0 + DAY + kx.ENTRY_WINDOW_MS + 1)
    assert (event(tmp, 102, "BBB")["status"], event(tmp, 102, "BBB")["reason"]) == ("missed", "seen_late")
    # the shadow records both
    rep = cycle(book, kb, E0 + DAY + 73 * H)["krw"]
    assert rep["shadow_rows"] == 2 and all(event(tmp, *k)["shadow"]["status"] == "done" for k in ((101, "AAA"), (102, "BBB")))


def test_the_entry_lock_leaves_the_event_waiting_then_missed(world):
    fake, book, tmp = world
    pub = Public(note(101, "AAA"))
    kb = krw(book, tmp, pub)
    rep = cycle(book, kb, E0 + MIN, entry_lock="TRADING_ENTRY_KILL_SWITCH is active")["krw"]
    assert rep["entered"] == 0 and event(tmp)["status"] == "waiting" and not orders_sent(fake)
    rep = cycle(book, kb, E0 + 16 * MIN, entry_lock="lock")["krw"]
    assert (event(tmp)["status"], event(tmp)["reason"]) == ("missed", "entry_lock")


# ── orders ──────────────────────────────────────────────────────────────────────

def spy_algo(fake) -> list[tuple[str, set[str]]]:
    """Each order's key with the stops the venue lists when it goes out."""

    seen: list[tuple[str, set[str]]] = []
    real = fake.create_order

    def create(s, typ, side, amount, price=None, params=None):
        seen.append(((params or {}).get("clientOrderId", ""), set(fake.algo)))
        return real(s, typ, side, amount, price, params)

    fake.create_order = create
    return seen


def test_an_event_sells_the_alt_sets_its_stop_buys_btc_and_exits_after_72_h(world):
    fake, book, tmp = world
    pub = Public(note(101, "AAA"))
    kb = krw(book, tmp, pub)
    seen = spy_algo(fake)
    rep = cycle(book, kb, E0 + 2 * MIN)["krw"]
    assert rep["entered"] == 1 and rep["open"] == 1
    keys = [c[5]["clientOrderId"] for c in orders_sent(fake)]
    assert keys == ["krw-101-AAA-o0", "krw-101-AAA-st", "krw-101-AAA-b0"]
    sell, stop, buy = orders_sent(fake)
    assert sell[1:5] == ("AAA/USDT:USDT", "market", "sell", pytest.approx(250.0)) and not sell[5].get("reduceOnly")
    (algo,) = fake.algo.values()
    assert algo["triggerPrice"] == pytest.approx(3.0) and algo["side"] == "buy" and algo["amount"] == pytest.approx(250.0)
    assert buy[1:5] == ("BTC/USDT:USDT", "market", "buy", pytest.approx(0.006)) and not buy[5].get("reduceOnly")
    assert pos(fake, "AAAUSDT") == pytest.approx(-250.0) and pos(fake, "BTCUSDT") == pytest.approx(0.006)
    assert fake.leverage == {"AAA/USDT:USDT": 1, "BTC/USDT:USDT": 1}
    assert kb.held() == {"AAAUSDT": pytest.approx(-250.0), "BTCUSDT": pytest.approx(0.006)}
    ev = event(tmp)
    assert ev["status"] == "open" and ev["stop_price"] == pytest.approx(3.0)
    assert [(f["key"], f["side"], f["mark"]) for f in ev["fills"]] == [("krw-101-AAA-o0", "sell", 2.0),
                                                                       ("krw-101-AAA-b0", "buy", 80_000.0)]
    assert all(len(k) <= KEY_MAX for k in keys)
    # inside the hold nothing is sent; the pair book counts both legs as explained
    n = len(orders_sent(fake))
    assert cycle(book, kb, E0 + 30 * H)["krw"]["closed"] == 0
    assert cycle(book, kb, E0 + 72 * H - kx.EXIT_EARLY_MS - 1)["krw"]["closed"] == 0 and len(orders_sent(fake)) == n
    rep = cycle(book, kb, E0 + 72 * H - kx.EXIT_EARLY_MS)["krw"]
    assert rep["closed"] == 1 and rep["open"] == 0
    back, sold = orders_sent(fake)[n:]
    assert back[1:5] == ("AAA/USDT:USDT", "market", "buy", pytest.approx(250.0)) and back[5]["reduceOnly"]
    assert back[5]["clientOrderId"] == "krw-101-AAA-c0"
    assert sold[1:5] == ("BTC/USDT:USDT", "market", "sell", pytest.approx(0.006)) and sold[5]["reduceOnly"]
    assert sold[5]["clientOrderId"] == "krw-101-AAA-x0"
    # the buy-back went out with the stop still at the venue: the stop is cancelled only after the full fill
    assert ("krw-101-AAA-c0", {"krw-101-AAA-st"}) in seen and ("krw-101-AAA-x0", set()) in seen
    assert fake.algo == {} and pos(fake, "AAAUSDT") == pytest.approx(0) and pos(fake, "BTCUSDT") == pytest.approx(0)
    ev = event(tmp)
    assert (ev["status"], ev["close_reason"]) == ("closed", "hold")
    assert book.orders.state.order_ledger["krw-101-AAA-st"]["status"] == "cancelled"
    assert book.orders.unfinished_orders() == {}
    found = krw_events(tmp)
    assert [e["event"] for e in found] == ["perp_krw_notice", "perp_krw_opened", "perp_krw_closing", "perp_krw_closed"]
    assert all(no_floats({k: v for k, v in e["details"].items() if not k.endswith("qty")}) for e in found)


def test_a_late_run_exits_at_once(world):
    fake, book, tmp = world
    enter(world)
    rep = cycle(book, krw(book, tmp, Public(note(101, "AAA"))), E0 + 100 * H)["krw"]
    assert rep["closed"] == 1 and event(tmp)["status"] == "closed"


def test_a_fired_stop_is_recorded_and_the_btc_leg_sold(world):
    fake, book, tmp = world
    enter(world)
    fake.fire_stop("AAAUSDT")
    n = len(orders_sent(fake))
    rep = cycle(book, krw(book, tmp, Public(note(101, "AAA"))), E0 + 5 * H)["krw"]
    assert rep["closed"] == 1
    (sold,) = orders_sent(fake)[n:]
    assert sold[1] == "BTC/USDT:USDT" and sold[3] == "sell" and sold[5]["reduceOnly"]
    ev = event(tmp)
    assert (ev["status"], ev["close_reason"]) == ("closed", "stop") and ev["stopped_at"]
    stop_row = [f for f in ev["fills"] if f.get("from_stop")]
    assert stop_row == [{"key": "krw-101-AAA-st", "side": "buy", "qty": 250.0, "avg": pytest.approx(3.15), "fees": {},
                         "mark": None, "symbol": "AAAUSDT", "from_stop": True, "avg_from": "stop_trigger"}]
    rec = book.orders.state.order_ledger["krw-101-AAA-st"]
    assert (rec["status"], rec["exchange_status"]) == ("filled", "triggered")
    assert [e["event"] for e in krw_events(tmp)][-3:] == ["perp_krw_stop", "perp_krw_closing", "perp_krw_closed"]


def test_a_short_gone_while_its_stop_is_open_or_a_short_without_its_stop_stops_for_review(world):
    fake, book, tmp = world
    enter(world)
    fake.positions["AAA/USDT:USDT"] = 0.0           # gone, its stop still listed
    with pytest.raises(PerpBookError, match="stop is still open"):
        cycle(book, krw(book, tmp, Public()), E0 + 3 * H)
    fake.positions["AAA/USDT:USDT"] = -250.0
    fake.algo.clear()                               # held, its stop gone
    with pytest.raises(PerpBookError, match="no open stop"):
        cycle(book, krw(book, tmp, Public()), E0 + 4 * H)
    fake.positions["AAA/USDT:USDT"] = -200.0
    with pytest.raises(PerpBookError, match="differs from book"):
        cycle(book, krw(book, tmp, Public()), E0 + 5 * H)


def lose_reply(fake, key_part: str):
    """The venue fills the order but the reply is lost."""

    real = fake.create_order

    def create(s, typ, side, amount, price=None, params=None):
        out = real(s, typ, side, amount, price, params)
        if key_part in (params or {}).get("clientOrderId", ""):
            raise ccxt.NetworkError("reply lost")
        return out

    fake.create_order = create
    return real


def thin(fake, key_part: str, filled: float, price: float | None = None):
    """The venue fills ``filled`` of the order, then expires the rest."""

    real = fake.create_order

    def create(s, typ, side, amount, px=None, params=None):
        cid = (params or {}).get("clientOrderId", "")
        if key_part not in cid:
            return real(s, typ, side, amount, px, params)
        fake.calls.append(("order", s, typ, side, amount, dict(params)))
        signed = filled if side == "buy" else -filled
        fake.positions[s] = fake.positions.get(s, 0.0) + signed
        avg = price or fake.prices[fake.markets[s]["id"]]
        fake.orders[cid] = {"id": "x" + cid, "clientOrderId": cid, "symbol": s, "type": "market", "side": side,
                            "amount": amount, "filled": filled, "average": avg if filled else None,
                            "cost": filled * avg, "status": "expired", "reduceOnly": bool(params.get("reduceOnly"))}
        return dict(fake.orders[cid])

    fake.create_order = create
    return real


def test_a_stop_send_that_fails_buys_the_short_back_before_settling_and_stops(world):
    fake, book, tmp = world
    fake.fail_next = "stop"
    pub = Public(note(101, "AAA"))
    seen = spy_algo(fake)
    with pytest.raises(PerpBookError, match="unknown"):
        cycle(book, krw(book, tmp, pub), E0 + 2 * MIN)
    keys = [c[5]["clientOrderId"] for c in orders_sent(fake)]
    assert keys == ["krw-101-AAA-o0", "krw-101-AAA-st", "krw-101-AAA-c0"]      # no BTC was bought
    back = orders_sent(fake)[-1]
    assert back[3] == "buy" and back[4] == pytest.approx(250.0) and back[5]["reduceOnly"]
    assert pos(fake, "AAAUSDT") == pytest.approx(0) and pos(fake, "BTCUSDT") == pytest.approx(0) and fake.algo == {}
    ev = event(tmp)
    assert (ev["status"], ev["reason"], ev["unwound"]) == ("failed", "entry_failed", True)
    assert book.orders.state.order_ledger["krw-101-AAA-st"]["status"] == "rejected"
    assert "perp_krw_unprotected" in [e["event"] for e in krw_events(tmp)]
    # the venue took the stop although its reply was lost: the short is bought back first, then the stop cancelled
    fake.fail_next = None
    fake.positions.clear()
    pub2 = Public(note(201, "BBB", at=N0 + 10 * DAY))
    real = fake.create_order

    def stop_lost(s, typ, side, amount, price=None, params=None):
        out = real(s, typ, side, amount, price, params)
        if "stopLossPrice" in (params or {}):
            raise ccxt.NetworkError("reply lost")
        return out

    fake.create_order = stop_lost
    seen = spy_algo(fake)
    book2 = fresh(book, tmp)
    with pytest.raises(PerpBookError, match="unknown"):
        cycle(book2, krw(book2, tmp, pub2), E0 + 10 * DAY + 2 * MIN)
    assert ("krw-201-BBB-c0", {"krw-201-BBB-st"}) in seen and fake.algo == {}
    assert book2.orders.state.order_ledger["krw-201-BBB-st"]["status"] == "cancelled"
    assert pos(fake, "BBBUSDT") == pytest.approx(0)


def test_a_lost_reply_is_settled_from_the_ledger(world):
    fake, book, tmp = world
    real = lose_reply(fake, "-o0")                        # the alt sell fills; its reply is lost
    with pytest.raises(PerpBookError, match="unknown"):
        cycle(book, krw(book, tmp, Public(note(101, "AAA"))), E0 + 2 * MIN)
    assert pos(fake, "AAAUSDT") == pytest.approx(0)       # bought back by the venue's position at once
    fake.create_order = real
    book2 = fresh(book, tmp)
    cycle(book2, krw(book2, tmp, Public(note(101, "AAA"))), E0 + 7 * MIN)    # the reconcile reads the sell back
    ev = event(tmp)
    assert ev["status"] == "failed" and [f["key"] for f in ev["fills"]] == ["krw-101-AAA-c0", "krw-101-AAA-o0"]
    assert kx.alt_short(ev) == 0.0 and ev["fills"][1]["from_ledger"]
    # the BTC buy fills and its reply is lost: the short is bought back, the BTC sold once the ledger knows it
    fake.positions.clear()
    lose_reply(fake, "-b0")
    book3 = fresh(book2, tmp)
    with pytest.raises(PerpBookError, match="unknown"):
        cycle(book3, krw(book3, tmp, Public(note(201, "BBB", at=N0 + 10 * DAY))), E0 + 10 * DAY + 2 * MIN)
    assert pos(fake, "BBBUSDT") == pytest.approx(0) and pos(fake, "BTCUSDT") == pytest.approx(0.006)
    assert fake.algo == {}
    fake.create_order = real
    book4 = fresh(book3, tmp)
    kb = krw(book4, tmp, Public())
    rep = cycle(book4, kb, E0 + 10 * DAY + 7 * MIN)["krw"]
    assert pos(fake, "BTCUSDT") == pytest.approx(0) and rep["open"] == 0
    ev = event(tmp, 201, "BBB")
    assert ev["status"] == "failed" and ev["unwound"] is True and kx.btc_long(ev) == 0.0
    sell = orders_sent(fake)[-1]
    assert sell[5]["clientOrderId"] == "krw-201-BBB-x0" and sell[5]["reduceOnly"]


def test_an_exit_buy_back_filled_in_part_keeps_the_stop_and_is_finished_by_the_next_run(world):
    fake, book, tmp = world
    enter(world)
    real = thin(fake, "-c0", 100.0)
    with pytest.raises(PerpBookError, match="expired"):
        cycle(book, krw(book, tmp, Public()), E0 + 72 * H)
    assert "krw-101-AAA-st" in fake.algo and pos(fake, "AAAUSDT") == pytest.approx(-150.0)
    assert pos(fake, "BTCUSDT") == pytest.approx(0.006)       # the BTC waits for the alt
    fake.create_order = real
    book2 = fresh(book, tmp)
    rep = cycle(book2, krw(book2, tmp, Public()), E0 + 72 * H + 5 * MIN)["krw"]
    assert rep["closed"] == 1 and fake.algo == {} and pos(fake, "AAAUSDT") == pytest.approx(0)
    ev = event(tmp)
    assert [(f["key"], f["qty"]) for f in ev["fills"] if f["symbol"] == "AAAUSDT"] == [
        ("krw-101-AAA-o0", 250.0), ("krw-101-AAA-c0", 100.0), ("krw-101-AAA-c1", 150.0)]
    assert ev["status"] == "closed" and book2.orders.unfinished_orders() == {}


def test_an_exit_buy_back_whose_reply_is_lost_is_settled_then_the_stop_and_btc_follow(world):
    fake, book, tmp = world
    enter(world)
    real = lose_reply(fake, "-c0")                            # the buy-back fills; its reply is lost
    with pytest.raises(PerpBookError, match="unknown"):
        cycle(book, krw(book, tmp, Public()), E0 + 72 * H)
    assert pos(fake, "AAAUSDT") == pytest.approx(0) and "krw-101-AAA-st" in fake.algo     # the stop stays
    assert pos(fake, "BTCUSDT") == pytest.approx(0.006) and event(tmp)["closing_key"] == "krw-101-AAA-c0"
    fake.create_order = real
    book2 = fresh(book, tmp)
    rep = cycle(book2, krw(book2, tmp, Public()), E0 + 72 * H + 5 * MIN)["krw"]       # the reconcile reads it back
    assert rep["closed"] == 1 and fake.algo == {} and pos(fake, "BTCUSDT") == pytest.approx(0)
    ev = event(tmp)
    assert ev["status"] == "closed" and ev["closing_key"] is None
    assert [f["key"] for f in ev["fills"]] == ["krw-101-AAA-o0", "krw-101-AAA-b0", "krw-101-AAA-c0", "krw-101-AAA-x0"]
    assert ev["fills"][2]["from_ledger"] and book2.orders.unfinished_orders() == {}


def test_an_exit_buy_back_that_fails_leaves_the_stop(world):
    fake, book, tmp = world
    enter(world)
    real = thin(fake, "-c0", 0.0)                             # expired, nothing filled
    with pytest.raises(PerpBookError, match="expired"):
        cycle(book, krw(book, tmp, Public()), E0 + 72 * H)
    assert "krw-101-AAA-st" in fake.algo and pos(fake, "AAAUSDT") == pytest.approx(-250.0)
    assert event(tmp)["status"] == "closing"
    fake.create_order = real
    book2 = fresh(book, tmp)
    assert cycle(book2, krw(book2, tmp, Public()), E0 + 72 * H + 5 * MIN)["krw"]["closed"] == 1
    assert fake.algo == {} and [c[5]["clientOrderId"] for c in orders_sent(fake)][-2:] == ["krw-101-AAA-c1",
                                                                                          "krw-101-AAA-x0"]
    # a buy-back whose send raises also leaves the stop; the next reconcile asks for the operator
    fake.positions.clear()
    enter(world, note(201, "BBB", at=N0 + 10 * DAY), at=E0 + 10 * DAY + MIN)
    fake.fail_next = "krw-201-BBB-c0"
    book3 = fresh(book2, tmp)
    with pytest.raises(PerpBookError, match="unknown"):
        cycle(book3, krw(book3, tmp, Public()), E0 + 10 * DAY + 72 * H)
    assert "krw-201-BBB-st" in fake.algo and pos(fake, "BBBUSDT") == pytest.approx(-50.0)


def test_a_btc_sell_that_fails_is_sold_again_by_the_next_run(world):
    fake, book, tmp = world
    enter(world)
    real = thin(fake, "-x0", 0.0)
    with pytest.raises(PerpBookError, match="expired"):
        cycle(book, krw(book, tmp, Public()), E0 + 72 * H)
    assert pos(fake, "AAAUSDT") == pytest.approx(0) and fake.algo == {}
    kb = krw(book, tmp, Public())
    assert kb.held() == {"BTCUSDT": pytest.approx(0.006)} and event(tmp)["status"] == "closing"
    fake.create_order = real
    book2 = fresh(book, tmp)
    assert cycle(book2, krw(book2, tmp, Public()), E0 + 72 * H + 5 * MIN)["krw"]["closed"] == 1
    assert pos(fake, "BTCUSDT") == pytest.approx(0) and orders_sent(fake)[-1][5]["clientOrderId"] == "krw-101-AAA-x1"


def test_a_run_killed_mid_entry_is_finished_or_unwound_by_the_next_run(world):
    fake, book, tmp = world
    real = fake.create_order

    def killed_after(part: str):
        def create(s, typ, side, amount, price=None, params=None):
            out = real(s, typ, side, amount, price, params)
            if part in (params or {}).get("clientOrderId", ""):
                raise Killed()
            return out
        return create

    fake.create_order = killed_after("-b0")                # all three orders in place, the record not
    with pytest.raises(Killed):
        cycle(book, krw(book, tmp, Public(note(101, "AAA"))), E0 + 2 * MIN)
    assert event(tmp)["status"] == "opening"
    fake.create_order = real
    book2 = fresh(book, tmp)
    cycle(book2, krw(book2, tmp, Public()), E0 + 7 * MIN)
    ev = event(tmp)
    assert ev["status"] == "open" and kx.btc_long(ev) == pytest.approx(0.006) and kx.alt_short(ev) == pytest.approx(250.0)
    # killed after the stop went out and before the BTC buy: unwound by the next run
    fake.create_order = killed_after("-st")
    book3 = fresh(book2, tmp)
    with pytest.raises(Killed):
        cycle(book3, krw(book3, tmp, Public(note(201, "BBB", at=N0 + 10 * DAY))), E0 + 10 * DAY + 2 * MIN)
    fake.create_order = real
    book4 = fresh(book3, tmp)
    cycle(book4, krw(book4, tmp, Public()), E0 + 10 * DAY + 7 * MIN)
    ev = event(tmp, 201, "BBB")
    assert (ev["status"], ev["reason"], ev["unwound"]) == ("failed", "entry_cut_off", True)
    assert pos(fake, "BBBUSDT") == pytest.approx(0) and "krw-201-BBB-st" not in fake.algo


# ── the account ─────────────────────────────────────────────────────────────────

def test_btc_is_summed_with_flow01_and_flow02_and_each_book_sells_its_own(world):
    """Book FLOW01 (the real book, on a fake feed) and FLOW02 (a fake) hold BTCUSDT beside book KRW's hedge."""

    from tests.test_perp_flowflush import FakeFeed
    from trading_agent.execution.perp import flowflush as fx

    fake, book, tmp = world
    flow02 = FakeBook("flow02", {"BTCUSDT": 0.002})
    hold(fake, "BTCUSDT", 0.002)
    t1 = E0 - 5 * MIN                                          # a FLOW01 flush stamp, fresh at E0 + 2 min
    flow01 = fx.FlowFlushBook(book, PairStore.load(tmp / fx.STORE, KEY), 1000.0,
                              FakeFeed({t1}, lo=E0 - 12 * H, hi=E0 + 24 * H))
    kb = krw(book, tmp, Public(note(101, "AAA")))
    rep = cycle(book, kb, E0 + 2 * MIN, others=[flow02, flow01])
    assert rep["flow01"]["entered"] == 1 and rep["krw"]["entered"] == 1
    assert pos(fake, "BTCUSDT") == pytest.approx(0.020)        # 0.002 + 0.012 + 0.006
    assert book.held_elsewhere() == {"AAAUSDT": pytest.approx(-250.0), "BTCUSDT": pytest.approx(0.020)}
    cycle(book, kb, E0 + 3 * H, others=[flow02, flow01])       # every BTC leg explained
    hold(fake, "BTCUSDT", 0.001)
    with pytest.raises(PerpBookError, match="do not match"):
        cycle(book, kb, E0 + 3 * H + MIN, others=[flow02, flow01])
    hold(fake, "BTCUSDT", -0.001)
    assert cycle(book, kb, E0 + 5 * H, others=[flow02, flow01])["flow01"]["closed"] == 1
    assert pos(fake, "BTCUSDT") == pytest.approx(0.008)
    assert cycle(book, kb, E0 + 72 * H, others=[flow02, flow01])["krw"]["closed"] == 1
    assert pos(fake, "BTCUSDT") == pytest.approx(0.002)        # FLOW02's stays
    sells = [(c[4], c[5]["reduceOnly"], c[5]["clientOrderId"][:4]) for c in orders_sent(fake)
             if c[1] == "BTC/USDT:USDT" and c[3] == "sell"]
    assert sells == [(pytest.approx(0.012), True, "fb1-"), (pytest.approx(0.006), True, "krw-")]


def test_a_symbol_another_book_holds_is_shadow_only_and_book_krw_holds_its_own(world):
    fake, book, tmp = world
    learn = FakeBook("learn", {"AAAUSDT": -10.0})
    hold(fake, "AAAUSDT", -10.0)
    pub = Public(note(101, "AAA"), note(102, "BBB"))
    kb = krw(book, tmp, pub)
    rep = cycle(book, kb, E0 + 2 * MIN, others=[learn])["krw"]
    assert rep["shadow_only"] == 1 and rep["entered"] == 1
    assert (event(tmp)["status"], event(tmp)["reason"]) == ("shadow_only", "held_by_learn")
    assert book.taken("learn") == {"BBBUSDT": "krw"} and book.taken(PAIR_BOOK)["BBBUSDT"] == "krw"
    with pytest.raises(PerpBookError, match="cannot rehearse"):
        cycle(book, kb, E0 + 3 * H, others=[learn], rehearse="BBBUSDT")
    # two books on one symbol stop the run
    with pytest.raises(PerpBookError, match="held by"):
        cycle(book, kb, E0 + 4 * H, others=[FakeBook("learn", {"BBBUSDT": -1.0})])


def test_a_stop_the_venue_keeps_after_the_buy_back_is_cancelled_later_and_keeps_its_symbol(world):
    fake, book, tmp = world
    pub = Public(note(101, "AAA"), note(102, "AAA", at=N0 + 8 * DAY))
    kb = krw(book, tmp, pub)
    cycle(book, kb, E0 + 2 * MIN)
    real_cancel = fake.cancel_order
    fake.cancel_order = lambda oid, s, params=None: {}            # the venue answers, and keeps the stop
    rep = cycle(book, kb, E0 + 72 * H)["krw"]
    assert rep["closed"] == 0 and pos(fake, "AAAUSDT") == pytest.approx(0) and pos(fake, "BTCUSDT") == pytest.approx(0)
    ev = event(tmp)
    assert (ev["status"], ev["stale_stop"]) == ("closing", True) and "krw-101-AAA-st" in fake.algo
    assert kb.held() == {} and krw_events(tmp)[-1]["event"] == "perp_krw_stop_not_cancelled"
    assert krw(book, tmp, pub).counts()["krw_stale_stops"] == 1        # status.json shows it to the operator
    # the 5-minute pre-check does not sign for it: the hourly run's check tries the cancel again
    assert kx.signed_run_reason(E0 + 72 * H + 5 * MIN, PairStore.peek(tmp / kx.STORE), pub.notices, pub.symbols) is None
    # the next AAA event finds the stale stop on its symbol: no KRW short goes under it
    rep = cycle(book, krw(book, tmp, pub), E0 + 8 * DAY + 2 * MIN)["krw"]
    assert (event(tmp, 102, "AAA")["status"], event(tmp, 102, "AAA")["reason"]) == ("shadow_only", "held_by_krw")
    fake.cancel_order = real_cancel
    cycle(book, krw(book, tmp, pub), E0 + 8 * DAY + 3 * H)          # check() cancels it again
    ev = event(tmp)
    assert ev["status"] == "closed" and "stale_stop" not in ev and fake.algo == {}
    assert book.orders.state.order_ledger["krw-101-AAA-st"]["status"] == "cancelled"


def test_no_more_than_four_events_hold_legs(world):
    fake, book, tmp = world
    pub = Public(*(note(100 + i, t) for i, t in enumerate(("AAA", "BBB", "CCC", "DDD", "EEE"), start=1)))
    rep = cycle(book, krw(book, tmp, pub), E0 + 2 * MIN)["krw"]
    assert (rep["entered"], rep["shadow_only"], rep["open"]) == (4, 1, 4)
    assert (event(tmp, 105, "EEE")["status"], event(tmp, 105, "EEE")["reason"]) == ("shadow_only", "max_open")


@pytest.mark.parametrize("equity,first,second", [
    ("1500", ("open", None), ("shadow_only", "equity")),                   # 1,350 gross room, 600 alt short room
    ("1200", ("shadow_only", "alt_short_cap"), ("shadow_only", "alt_short_cap")),   # 1,080 and 480
])
def test_the_account_caps_take_both_legs(world, equity, first, second):
    fake, book, tmp = world
    fake.fetch_balance = lambda: {"info": {"totalMarginBalance": equity}}
    cycle(book, krw(book, tmp, Public(note(101, "AAA"), note(102, "BBB"))), E0 + 2 * MIN)
    got = [(event(tmp, n, t)["status"], event(tmp, n, t).get("reason")) for n, t in ((101, "AAA"), (102, "BBB"))]
    assert got == [first, second]


def test_a_symbol_not_on_demo_or_a_new_size_is_shadow_only(world):
    fake, book, tmp = world
    pub = Public(note(101, "ZZZ"), note(102, "AAA"))
    cycle(book, krw(book, tmp, pub), E0 + 2 * MIN)
    assert event(tmp, 101, "ZZZ")["reason"] == "not_on_demo" and event(tmp, 102, "AAA")["status"] == "open"
    pub2 = Public(note(201, "BBB", at=N0 + 10 * DAY))
    big = krw(book, tmp, pub2, usd=800.0)
    run_cycle(book, now_ms=E0 + 10 * DAY + 2 * MIN, mainnet_info={}, execute=True, others=[big])
    assert (event(tmp, 201, "BBB")["status"], event(tmp, 201, "BBB")["reason"]) == ("shadow_only", "size_changed")


def test_realized_losses_past_fifteen_percent_of_the_sleeve_halt_new_entries(world):
    fake, book, tmp = world
    pub = Public(note(101, "AAA"), note(102, "BBB", at=N0 + 72 * H), note(103, "CCC", at=N0 + 72 * H))
    kb = krw(book, tmp, pub)
    cycle(book, kb, E0 + 2 * MIN)
    fake.prices["AAAUSDT"] = 3.0      # 250 x 1.0 = 250 lost with fees: not past 300 (15% of 4 x 500)
    rep = cycle(book, kb, E0 + 72 * H - MIN)["krw"]
    assert rep["closed"] == 1 and rep["halted"] is False
    assert (rep["entered"], event(tmp, 102, "BBB")["status"]) == (0, "waiting")
    fake.prices["AAAUSDT"] = 2.0
    rep = cycle(book, kb, E0 + 72 * H + MIN)["krw"]
    assert rep["entered"] == 2
    fake.prices["BBBUSDT"] = 13.0     # 50 x 3 = 150 more, and the fees: past 300
    rep = cycle(book, kb, E0 + 144 * H)["krw"]
    assert rep["closed"] == 2 and rep["halted"] is True
    assert [e for e in krw_events(tmp) if e["event"] == "perp_krw_halted"][-1]["details"] == {
        "reason": "realized_net_below_limit"}
    pub.notes.append(note(104, "DDD", at=N0 + 150 * H))
    rep = cycle(book, kb, E0 + 150 * H + 2 * MIN)["krw"]
    ev = event(tmp, 104, "DDD")
    assert (ev["status"], ev["reason"]) == ("shadow_only", "halted") and rep["halted"] is True
    assert no_floats(kb.counts()) and kb.counts()["krw_halted"] is True


def test_a_feed_failure_is_recorded_and_exits_still_run(world):
    fake, book, tmp = world
    pub, _ = enter(world)
    pub.fail.add("notices")
    rep = cycle(book, krw(book, tmp, pub), E0 + 72 * H)["krw"]
    assert rep["closed"] == 1 and stored(tmp).pairs["book"]["feed"]["fails"] == 1
    assert krw_events(tmp)[-1]["event"] == "perp_krw_feed_failed"


def test_a_page_with_nothing_older_than_the_last_one_records_a_gap(world):
    fake, book, tmp = world
    pub = Public(note(101, "AAA", listed=N0))
    kb = krw(book, tmp, pub)
    cycle(book, kb, N0 + MIN)
    pub.notes = [kfeed.Notice(150 + i, f"공지 {i}", N0 + 9 * DAY, N0 + 9 * DAY + i) for i in range(20)]
    cycle(book, kb, N0 + 9 * DAY + H)
    (gap,) = [v for k, v in stored(tmp).pairs.items() if k.startswith("g")]
    assert gap == {"kind": "gap", "from_ms": N0, "to_ms": N0 + 9 * DAY}


def test_the_shadow_is_retried_and_marked_failed_after_seven_days(world):
    fake, book, tmp = world
    pub = Public(note(101, "AAA"))
    pub.fail.add("shadow")
    kb = krw(book, tmp, pub)
    cycle(book, kb, N0 + MIN)
    assert cycle(book, kb, E0 + 73 * H)["krw"]["shadow_rows"] == 0
    sh = event(tmp)["shadow"]
    assert (sh["status"], sh["tries"]) == ("due", 1) and sh["last_error"].startswith("NoData")
    cycle(book, kb, E0 + 73 * H + 7 * DAY - 1)
    assert event(tmp)["shadow"]["status"] == "due"
    cycle(book, kb, E0 + 73 * H + 7 * DAY)
    sh = event(tmp)["shadow"]
    assert (sh["status"], sh["reason"]) == ("shadow_failed", "NoData: no alt bar in the hold") and sh["tries"] == 3
    assert kb.counts()["krw_shadow"] == {"shadow_failed": 1}


def test_the_dry_run_plans_counts_and_writes_nothing(world):
    fake, book, tmp = world
    pub = Public(note(101, "AAA"))
    kb = kx.KrwFadeBook(book, PairStore.peek(tmp / kx.STORE), 500.0, pub.notices, pub.symbols, pub.shadow_row)
    rep = run_cycle(book, now_ms=E0 + 2 * MIN, mainnet_info={}, execute=False, others=[kb])
    assert rep["krw"] == {"events": {}, "open": 0, "halted": False, "exit_due": 0, "in_window": 0, "shadow_due": 0,
                          "new_krw_notices": 1}
    assert not (tmp / kx.STORE).exists() and not orders_sent(fake)
    pub.fail.add("notices")
    assert run_cycle(book, now_ms=E0, mainnet_info={}, execute=False, others=[kb])["krw"]["feed"] == "FeedError"


# ── the keyless pre-check of the 5-minute mode ──────────────────────────────────

def test_the_pre_check_signs_only_for_a_window_an_exit_or_an_unsettled_order(world):
    fake, book, tmp = world
    peek = lambda: PairStore.peek(tmp / kx.STORE)   # noqa: E731  (no key)
    young = {**SYMS, "YNGUSDT": sym("YNGUSDT", N0 - 10 * DAY)}
    pub = Public(note(101, "AAA"), note(102, "YNG"), symbols=young)
    reason = lambda at: kx.signed_run_reason(at, peek(), pub.notices, pub.symbols)   # noqa: E731
    assert reason(N0 + 2 * MIN) is None and pub.calls["symbols"] == 0               # E is ahead: no exchangeInfo read
    assert reason(E0 + 2 * MIN) == "entry_window" and pub.calls["symbols"] == 1
    assert reason(E0 + 16 * MIN) is None                                             # past its window
    pub.notes = [note(102, "YNG")]
    assert reason(E0 + 2 * MIN) is None                                              # not an event
    pub.notes = [note(101, "AAA")]
    kb = krw(book, tmp, pub)
    cycle(book, kb, N0 + 2 * MIN)                                                     # recorded, waiting
    pub.fail.add("notices")
    assert reason(E0 + MIN) == "entry_window"                                         # from the record alone
    pub.fail.clear()
    cycle(book, kb, E0 + MIN)
    calls = pub.calls["notices"]
    assert reason(E0 + 3 * H) is None
    pub.fail.add("notices")
    assert reason(E0 + 72 * H - kx.EXIT_EARLY_MS) == "exit_due" and pub.calls["notices"] == calls + 1
    with pytest.raises(kfeed.FeedError):
        reason(E0 + 3 * H)                                                            # the runner reports it failed
    st = PairStore.load(tmp / kx.STORE, KEY)
    for status, want in (("opening", "settle"), ("closing", "settle"), ("stopped", "exit_due")):
        st.pairs[kx.event_id(101, "AAA")]["status"] = status
        st.save()
        assert reason(E0 + 3 * H) == want
    st.pairs[kx.event_id(101, "AAA")].update(status="failed", unwound=True)     # a failed event that holds legs
    st.save()
    assert reason(E0 + 3 * H) == "settle"
    del st.pairs[kx.event_id(101, "AAA")]["unwound"]
    st.pairs[kx.event_id(101, "AAA")]["status"] = "closed"
    st.pairs["book"]["halted"] = {"reason": "realized_net_below_limit"}
    st.save()
    pub.fail.clear()
    pub.notes = [note(103, "BBB", at=N0 + 10 * DAY)]
    assert reason(E0 + 10 * DAY + MIN) is None                                       # halted: no entry


# ── the shadow row ──────────────────────────────────────────────────────────────

def bars(E: int, opens: dict[int, float] | float, *, highs: dict[int, float] | None = None, missing=(), zero_vol=(),
         n: int = 73, start: int = 0) -> dict:
    out = {}
    for j in range(start, n):
        if j in missing:
            continue
        o = opens.get(j, opens.get("*", 0.0)) if isinstance(opens, dict) else opens
        h = (highs or {}).get(j, o)
        out[E + j * H] = (o, h, o, o + 0.5, 0.0 if j in zero_vol else 10.0, E + j * H + H - 1, 100.0)
    return out


def test_the_shadow_row_holds_72_h_and_counts_funding_in_e_to_x():
    E = E0
    alt = bars(E, {"*": 10.0, 72: 9.0})
    btc = bars(E, {"*": 100.0, 72: 101.0})
    fa = S.funding_rows([(E, 0.5), (E + 8 * H + 3, 0.001), (E + 72 * H, 0.001), (E + 72 * H + 1000, 0.5)])
    fb = S.funding_rows([(E + 8 * H, 0.0001)])
    r = S.row(alt, btc, fa, fb, E)
    assert r["pass_0a"] and r["stopped"] is False and r["exit_ms"] == E + 72 * H
    assert (r["alt_entry"], r["alt_exit"], r["btc_entry"], r["btc_exit"]) == (10.0, 9.0, 100.0, 101.0)
    assert r["g_bps"] == pytest.approx(1100.0)
    assert (r["fund_rows_alt"], r["fund_rows_btc"]) == (2, 1)            # t = E and t > X are out
    assert r["n_bps"] == pytest.approx(1100.0 - 85.0 + 20.0 - 1.0) and r["flags"] == []


def test_the_shadow_stop_fills_at_1_575x_or_just_above_a_gap_open_and_x_is_the_bar_close():
    E = E0
    alt = bars(E, {"*": 10.0, 5: 11.0}, highs={5: 15.5, 72: 99.0})
    btc = bars(E, {"*": 100.0, 5: 101.0})
    r = S.row(alt, btc, S.funding_rows([(E + 5 * H, 0.002), (E + 8 * H, 0.002)]), [], E)
    assert r["stopped"] and r["stop_bar_ms"] == E + 5 * H and r["exit_ms"] == E + 6 * H - 1
    assert r["alt_exit"] == pytest.approx(15.75) and r["btc_exit"] == pytest.approx(101.5)   # BTC at that bar's close
    assert r["g_bps"] == pytest.approx(((10 - 15.75) / 10 + (101.5 - 100) / 100) * 1e4)
    assert r["fund_rows_alt"] == 1 and r["flags"] == ["fund_btc_none"]       # E + 5 h in, E + 8 h after X
    gap = S.row(bars(E, {"*": 10.0, 5: 20.0}, highs={5: 21.0}), btc, [], [], E)
    assert gap["alt_exit"] == pytest.approx(20.1)
    # the exit bar's high never triggers the stop; a missing bar cannot
    assert S.row(bars(E, 10.0, highs={72: 99.0}), btc, [], [], E)["stopped"] is False
    late = S.row(bars(E, 10.0, highs={7: 15.0}, missing=(6,)), bars(E, 100.0, missing=(7,)), [], [], E)
    assert late["stopped"] and late["btc_exit"] == 100.0 and "btc_close_fallback" in late["flags"]


def test_the_shadow_check_0a_drops_gaps_and_zero_volume():
    E = E0
    btc = bars(E, 100.0)
    one = S.row(bars(E, 10.0, missing=(10,)), btc, [], [], E)
    assert one["pass_0a"] and one["alt_max_gap_h"] == 2
    two = S.row(bars(E, 10.0, missing=(10, 11)), btc, [], [], E)
    assert two["pass_0a"] is False and two["reasons_0a"] == ["alt: gap 3 h"] and "g_bps" not in two
    zero = S.row(bars(E, 10.0, zero_vol=(72,)), btc, [], [], E)
    assert zero["reasons_0a"] == ["alt: zero volume at exit"]
    assert S.row({}, btc, [], [], E)["reasons_0a"] == ["alt: no bar at entry", "alt: no bar at exit", "alt: gap 74 h"]


def test_the_shadow_flags_a_notice_hour_without_the_volume_jump():
    E = E0
    alt = bars(E, 10.0, start=-30)
    assert S.row(alt, bars(E, 100.0), [], [], E, N0)["flags"] == ["0b_fail", "fund_alt_none", "fund_btc_none"]
    jump = dict(alt)
    nh = N0 // H * H
    jump[nh] = jump[nh][:6] + (400.0,)
    r = S.row(jump, bars(E, 100.0), [], [], E, N0)
    assert r["pass_0b"] is True and r["qv_ratio"] == 4.0


class Klines:
    """Public mainnet calls for the shadow: 1h klines and funding; anything else fails the test."""

    def __init__(self, empty: bool = False):
        self.paths: list[str] = []
        self.empty = empty

    def __call__(self, url, timeout):
        from urllib.parse import parse_qs, urlsplit

        from tools.newperp import fetch as nf

        nf.check_url(url)
        u = urlsplit(url)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        self.paths.append(u.path)
        if u.path == nf.FUNDING:
            return 200, json.dumps([{"fundingTime": E0 + 8 * H + 2, "fundingRate": "0.0001"}]).encode()
        assert u.path == nf.KLINES and q["interval"] == "1h"
        if self.empty and q["symbol"] != "BTCUSDT":
            return 200, b"[]"
        a, b = int(q["startTime"]), int(q["endTime"])
        rows = [[t, "10", "10", "10", "10", "5", t + H - 1, "50", 1, "0", "0", "0"] for t in range(a - a % H, b + 1, H)]
        return 200, json.dumps(rows).encode()


def test_the_shadow_reads_klines_and_funding_through_the_allowlisted_client():
    from tools.newperp import fetch as nf

    venue = Klines()
    r = S.fetch_row(nf.Client(venue, pace=0.0, sleep=lambda s: None), "AAAUSDT", E0, N0)
    assert r["pass_0a"] and r["g_bps"] == 0.0 and r["fund_rows_alt"] == 1
    assert set(venue.paths) == {nf.KLINES, nf.FUNDING}
    with pytest.raises(S.NoData):
        S.fetch_row(nf.Client(Klines(empty=True), pace=0.0, sleep=lambda s: None), "AAAUSDT", E0, N0)
    with pytest.raises(ValueError):
        S.fetch_row(nf.Client(Klines(), pace=0.0, sleep=lambda s: None), "ETHUSDT", E0, N0)    # masked: refused


# ── the notice feed ─────────────────────────────────────────────────────────────

def test_the_feed_allows_one_get_of_one_url_and_refuses_anything_else():
    kfeed.check_url(kfeed.URL)
    assert kfeed.URL == "https://api-manager.upbit.com/api/v1/announcements?os=web&page=1&per_page=20&category=trade"
    for bad in ("https://api.upbit.com/api/v1/announcements?os=web&page=1&per_page=20&category=trade",
                "http://api-manager.upbit.com/api/v1/announcements?os=web&page=1&per_page=20&category=trade",
                "https://api-manager.upbit.com/api/v1/announcements/6739?os=web&page=1&per_page=20&category=trade",
                "https://api-manager.upbit.com/api/v1/announcements?os=web&page=2&per_page=20&category=trade",
                "https://api-manager.upbit.com/api/v1/announcements?os=web&page=1&per_page=50&category=trade",
                "https://api-manager.upbit.com/api/v1/announcements?os=web&page=1&per_page=20&category=trade&x=1",
                "https://user:pw@api-manager.upbit.com/api/v1/announcements?os=web&page=1&per_page=20&category=trade",
                "https://api-manager.upbit.com/api/v1/announcements?os=web&page=1&per_page=20&category=trade#x"):
        with pytest.raises(kfeed.FeedRefused):
            kfeed.check_url(bad)
        with pytest.raises(kfeed.FeedRefused):
            kfeed.transport(bad, 5.0)                     # refused before a byte leaves
    calls = []
    f = kfeed.Feed(transport=lambda url, timeout: calls.append((url, timeout)) or (200, b'{"success": true, "data": {"notices": []}}'),
                   timeout=60.0)
    for method in ("POST", "PUT", "DELETE", "HEAD"):
        with pytest.raises(kfeed.FeedRefused):
            f.request(method, kfeed.URL)
    assert f.page() == [] and calls == [(kfeed.URL, 10.0)]      # the timeout is at most 10 s


def test_the_feed_sends_a_plain_get_with_a_generic_user_agent_and_no_proxy(monkeypatch):
    got = {}

    class Resp:
        status = 200
        headers = {"Content-Length": "2"}

        def read(self, n):
            return b"{}"

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    class Opener:
        def open(self, req, timeout):
            got.update(method=req.get_method(), url=req.full_url, ua=req.get_header("User-agent"), timeout=timeout)
            return Resp()

    def build(*handlers):
        got["handlers"] = handlers
        return Opener()

    monkeypatch.setattr(urllib.request, "build_opener", build)
    assert kfeed.transport(kfeed.URL, 30.0) == (200, b"{}")
    assert got["method"] == "GET" and got["url"] == kfeed.URL and got["timeout"] == 10.0
    assert got["ua"] == "Mozilla/5.0"
    proxy = [h for h in got["handlers"] if isinstance(h, urllib.request.ProxyHandler)]
    assert proxy and proxy[0].proxies == {}


def test_the_feed_parses_kst_stamps_and_fails_on_anything_odd():
    body = json.dumps({"success": True, "data": {"notices": [
        {"id": 6714, "title": "닐리온(NIL) KRW 마켓 디지털 자산 추가", "listed_at": "2026-10-27T16:30:00+09:00",
         "first_listed_at": "2026-10-27T16:30:00+09:00"},
        {"id": 6687, "title": "액시스로보틱스(AXIS) 신규 거래지원 안내 (KRW, BTC, USDT 마켓) (매도 최저가 기준 가격 안내)",
         "listed_at": "2026-10-21T21:50:00+09:00", "first_listed_at": "2026-10-21T20:00:00+09:00"}]}}).encode()
    nil, axis = kfeed.parse(body)
    assert nil == kfeed.Notice(6714, "닐리온(NIL) KRW 마켓 디지털 자산 추가", 1793086200000, 1793086200000)  # events.json NIL
    assert axis.first_listed_at_ms == axis.listed_at_ms - 110 * MIN       # an edit moves listed_at only
    bad_bodies = [b"<html>", b'{"success": false}', b'{"success": true, "data": {}}',
                  json.dumps({"success": True, "data": {"notices": [{"id": 1, "title": "x", "listed_at": "2026-10-27T16:30:00",
                                                                       "first_listed_at": "2026-10-27T16:30:00"}]}}).encode(),
                  json.dumps({"success": True, "data": {"notices": [{"title": "x"}]}}).encode()]
    for b in bad_bodies:
        with pytest.raises(kfeed.FeedError):
            kfeed.parse(b)
    for status, exc in ((500, None), (429, None), (200, OSError("timed out"))):
        def t(url, timeout, status=status, exc=exc):
            if exc:
                raise exc
            return status, b"{}"

        with pytest.raises(kfeed.FeedError):
            kfeed.Feed(transport=t, sleep=lambda s: None).page()


PAGE = json.dumps({"success": True, "data": {"notices": [
    {"id": 6714, "title": "닐리온(NIL) KRW 마켓 디지털 자산 추가", "listed_at": "2026-10-27T16:30:00+09:00",
     "first_listed_at": "2026-10-27T16:30:00+09:00"}]}}).encode()


def answers(*statuses: int):
    """A transport that answers the given statuses in turn (200 with one notice), and counts its calls."""

    calls: list[int] = []

    def t(url, timeout):
        calls.append(1)
        status = statuses[len(calls) - 1]
        return status, (PAGE if status == 200 else b"{}")

    return t, calls


@pytest.mark.parametrize("first", [429, 418])
def test_a_429_or_418_from_upbit_is_tried_once_more_after_a_wait(first):
    waits: list[float] = []
    t, calls = answers(first, 200)
    (nil,) = kfeed.Feed(transport=t, sleep=waits.append).page()
    assert nil.id == 6714 and len(calls) == 2 and waits == [kfeed.RETRY_WAIT_S]
    t, calls = answers(first, 429)
    with pytest.raises(kfeed.FeedRateLimited):
        kfeed.Feed(transport=t, sleep=waits.append).page()
    assert len(calls) == 2 and waits == [kfeed.RETRY_WAIT_S] * 2


def test_other_statuses_are_plain_feed_errors_with_no_retry():
    waits: list[float] = []
    for status in (500, 503, 403):
        t, calls = answers(status, 200)
        with pytest.raises(kfeed.FeedError) as err:
            kfeed.Feed(transport=t, sleep=waits.append).page()
        assert not isinstance(err.value, kfeed.FeedRateLimited) and len(calls) == 1
    assert waits == []


def rate_limited_notices():
    raise kfeed.FeedRateLimited("status 429")


def test_the_pre_check_finds_an_exit_or_a_waiting_window_before_it_reads_the_notice_list(tmp_path):
    """A 429 on the notice list can never hide a signed run the record or the ledger asks for."""

    sell = {"key": "krw-101-AAA-o0", "symbol": "AAAUSDT", "side": "sell", "qty": 250.0}
    buy = {"key": "krw-101-AAA-b0", "symbol": "BTCUSDT", "side": "buy", "qty": 0.006}
    open_ev = {"kind": "event", "notice_id": 101, "token": "AAA", "perp": "AAAUSDT", "entry_ms": E0, "notice_ms": N0,
               "status": "open", "fills": [sell, buy]}
    store = PairStore(tmp_path / kx.STORE, "", {"e101-AAA": open_ev})
    assert kx.signed_run_reason(E0 + 72 * H, store, rate_limited_notices, dict) == "exit_due"
    waiting = {"kind": "event", "notice_id": 102, "token": "BBB", "perp": "BBBUSDT", "entry_ms": E0, "notice_ms": N0,
               "status": "waiting", "fills": []}
    store = PairStore(tmp_path / kx.STORE, "", {"e102-BBB": waiting})
    assert kx.signed_run_reason(E0 + 2 * MIN, store, rate_limited_notices, dict) == "entry_window"
    led = {"krw-101-AAA-c0": {"status": "reconciling"}}
    store = PairStore(tmp_path / kx.STORE, "", {"e101-AAA": {**open_ev, "status": "failed"}})
    assert kx.signed_run_reason(E0 + 5 * MIN, store, rate_limited_notices, dict, led) == "settle"
    store = PairStore(tmp_path / kx.STORE, "", {})
    with pytest.raises(kfeed.FeedRateLimited):                   # nothing else to do: the list is the only reason
        kx.signed_run_reason(E0 + 2 * MIN, store, rate_limited_notices, dict)
    # The two reasons that alone reveal a naked short with no stop, each on its own (review 429-T1):
    assert kx.signed_run_reason(E0 + 5 * MIN, PairStore(tmp_path / kx.STORE, "", {}), rate_limited_notices, dict,
                                {"krw-101-AAA-o0": {"status": "submitted"}}) == "settle"    # the ledger alone
    lost = {**open_ev, "status": "failed", "fills": []}                                     # a take-off cut off
    assert kx.signed_run_reason(E0 + 5 * MIN, PairStore(tmp_path / kx.STORE, "", {"e101-AAA": lost}),
                                rate_limited_notices, dict, {}) == "settle"


def test_the_signed_run_uses_the_page_the_pre_check_read(krw_runner, monkeypatch):
    """Review 429-S1: one read of Upbit a run, so a signed run sees the notice it signed for."""

    reads: list[int] = []

    class OnePage:
        def page(self):
            reads.append(1)
            return [note(101, "AAA")]

    from tools.krwfade import feed as live_feed

    monkeypatch.setattr(live_feed, "Feed", OnePage)
    box: dict = {"info": None, "notices": None}
    notices, _symbols, _row = krw_runner.krw_sources(box)
    assert notices() == notices() == [note(101, "AAA")] and len(reads) == 1


# ── the runner: flags and book KRW's 5-minute mode ──────────────────────────────

import importlib.util  # noqa: E402
from pathlib import Path  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
FLAGS = ["--lv", "--learn", "--flow02", "--flow01", "--krw", "--pair-usd", "500"]


@pytest.fixture
def krw_runner(monkeypatch):
    """scripts/live_perp_demo.py as a module, with .env never read and no mainnet listing in --only."""

    spec = importlib.util.spec_from_file_location("live_perp_demo_krw_test", REPO / "scripts" / "live_perp_demo.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod, "load_dotenv", lambda *a, **k: None)
    monkeypatch.setattr(mod, "mainnet_listings", lambda: (_ for _ in ()).throw(AssertionError("no listings in --only")))
    from tests.test_perp_flowflush import FakeFeed

    monkeypatch.setattr(mod, "flow01_feed", lambda: FakeFeed())
    return mod


def run_roots(tmp_path) -> list[str]:
    return ["--root", str(tmp_path / "perp_demo"), "--selection-root", str(tmp_path / "selection"),
            "--learn-root", str(tmp_path / "learn_paper"), "--flow02-root", str(tmp_path / "flowbtc")]


def test_the_runner_refuses_a_krw_size_over_its_cap_and_a_lone_only(krw_runner, capsys):
    for bad in ("2001", "0", "-5", "nan"):
        with pytest.raises(SystemExit) as stop:
            krw_runner.main(["--krw-usd", bad])
        assert stop.value.code == 2
    assert krw_runner.main(["--only", "krw"]) == 2 and "needs --krw" in capsys.readouterr().err
    assert krw_runner.main(["--only", "krw", "--krw", "--shadow"]) == 2


def test_only_krw_with_a_quiet_pre_check_signs_nothing(krw_runner, tmp_path, monkeypatch, capsys):
    pub = Public(note(101, "AAA"))
    monkeypatch.setattr(krw_runner, "krw_sources", lambda box: (pub.notices, pub.symbols, pub.shadow_row))
    monkeypatch.setattr(krw_runner, "now_ms", lambda: N0 + 2 * MIN)       # E is ahead

    def never(*a, **k):
        raise AssertionError("a quiet pre-check reached the signed path")

    for name in ("load_dotenv", "require_execution_authorization", "load_demo_credentials", "LiveExecutionLock",
                 "LiveRiskStateStore", "PerpDemoVenue", "append_live_audit_event"):
        monkeypatch.setattr(krw_runner, name, never)
    argv = ["--execute", "--only", "krw", *FLAGS, *run_roots(tmp_path)]
    assert krw_runner.main(argv) == 0
    assert json.loads(capsys.readouterr().out) == {"only": "krw", "signed_run": False}
    assert pub.calls["notices"] == 1 and pub.calls["symbols"] == 0 and not (tmp_path / "perp_demo").exists()
    pub.fail.add("notices")                                             # a pre-check that cannot read fails, unsigned
    assert krw_runner.main(argv) == 1
    assert "krw pre-check failed" in capsys.readouterr().err and not (tmp_path / "perp_demo").exists()


def test_only_krw_skips_a_tick_unsigned_when_upbit_or_binance_asks_to_slow_down(krw_runner, tmp_path, monkeypatch,
                                                                                 capsys):
    """A 429/418 on the notice list (after its one retry) or on exchangeInfo: rc 0, nothing signed, reported."""

    from tools.selection.fetch import FetchStop

    def never(*a, **k):
        raise AssertionError("a rate-limited pre-check reached the signed path")

    for name in ("load_dotenv", "require_execution_authorization", "load_demo_credentials", "LiveExecutionLock",
                 "LiveRiskStateStore", "PerpDemoVenue", "append_live_audit_event"):
        monkeypatch.setattr(krw_runner, name, never)
    pub = Public(note(101, "AAA", at=N0))
    argv = ["--execute", "--only", "krw", *FLAGS, *run_roots(tmp_path)]
    monkeypatch.setattr(krw_runner, "now_ms", lambda: E0 + 2 * MIN)       # AAA is new and inside its window
    monkeypatch.setattr(krw_runner, "krw_sources", lambda box: (rate_limited_notices, pub.symbols, pub.shadow_row))
    assert krw_runner.main(argv) == 0
    out = json.loads(capsys.readouterr().out)
    assert (out["signed_run"], out["skipped"]) == (False, "rate_limited") and "FeedRateLimited" in out["error"]

    def stop():
        raise FetchStop("status 429")

    monkeypatch.setattr(krw_runner, "krw_sources", lambda box: (pub.notices, stop, pub.shadow_row))
    assert krw_runner.main(argv) == 0
    assert json.loads(capsys.readouterr().out)["skipped"] == "rate_limited"
    pub.fail.add("notices")                                            # any other failure still fails the tick
    monkeypatch.setattr(krw_runner, "krw_sources", lambda box: (pub.notices, pub.symbols, pub.shadow_row))
    assert krw_runner.main(argv) == 1 and "krw pre-check failed" in capsys.readouterr().err
    assert not (tmp_path / "perp_demo").exists()


def test_only_krw_signed_run_checks_every_book_then_steps_krw_alone(krw_runner, tmp_path, monkeypatch, capsys):
    for k in ("TRADING_KILL_SWITCH", "TRADING_ENTRY_KILL_SWITCH", "BINANCE_DEMO_FUTURES_PRIVATE_KEY_FILE"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("TRADING_MODE", "testnet")
    monkeypatch.setenv("TRADING_EXECUTION_ENABLED", "true")
    monkeypatch.setenv("BINANCE_DEMO_FUTURES_API_KEY", "demo-key")
    monkeypatch.setenv("BINANCE_DEMO_FUTURES_API_SECRET", "demo-secret")
    monkeypatch.setenv("LIVE_SAFETY_HMAC_KEY", KEY)
    monkeypatch.setattr(os, "fsync", lambda fd: None)
    fake = FakeUsdm({"BTCUSDT": 80_000.0, "AAAUSDT": 2.0})
    monkeypatch.setattr(ca.ccxt, "binanceusdm", lambda cfg: fake)
    pub = Public(note(101, "AAA"))
    monkeypatch.setattr(krw_runner, "krw_sources", lambda box: (pub.notices, pub.symbols, pub.shadow_row))

    class NoSignal:   # book FLOW02's step must not run in this mode
        def update(self, now_ms):
            raise AssertionError("FLOW02 stepped")

        decide = update

    monkeypatch.setattr(krw_runner, "flow02_signal", lambda root: NoSignal())
    root = tmp_path / "perp_demo"
    argv = ["--execute", "--only", "krw", *FLAGS, *run_roots(tmp_path)]

    def run(at: int) -> dict:
        monkeypatch.setattr(krw_runner, "now_ms", lambda: at)
        assert krw_runner.main(argv) == 0
        return json.loads(capsys.readouterr().out.splitlines()[-1])

    rep = run(E0 + 2 * MIN)
    assert rep["only"] == "krw" and rep["only_reason"] == "entry_window" and rep["krw"]["entered"] == 1
    assert not {"lv", "learn", "flow02", "flow01"} & set(rep) and rep["entered"] == []
    assert [c[5]["clientOrderId"] for c in orders_sent(fake)] == ["krw-101-AAA-o0", "krw-101-AAA-st", "krw-101-AAA-b0"]
    assert not any((root / f).exists() for f in ("pairs.json", "lv_blocks.json", "flow01.json", "flow02.json"))
    status = json.loads((root / "status.json").read_text())
    assert status["run"] == "krw" and status["krw_open"] == 1 and status["krw_events"] == {"open": 1}
    assert not [k for k, v in status.items() if isinstance(v, float)] and no_floats(status["krw_shadow"])
    started = [e["details"] for e in events(root) if e["event"] == "perp_run_started"][-1]
    assert started["only"] == "krw" and started["only_reason"] == "entry_window" and started["krw_usd"] == 500.0
    # a quiet moment inside the hold: nothing is signed or written
    calls, audit = len(fake.calls), (root / "audit.jsonl").read_bytes()
    monkeypatch.setattr(krw_runner, "now_ms", lambda: E0 + 30 * H)
    assert krw_runner.main(argv) == 0
    assert json.loads(capsys.readouterr().out) == {"only": "krw", "signed_run": False}
    assert len(fake.calls) == calls and (root / "audit.jsonl").read_bytes() == audit
    # the exit falls due: the alt bought back, then its own BTC sold, reduce-only
    rep = run(E0 + 72 * H + 2 * MIN)
    assert rep["only_reason"] == "exit_due" and rep["krw"]["closed"] == 1
    assert [(c[1], c[3], c[5]["reduceOnly"]) for c in orders_sent(fake)[-2:]] == [
        ("AAA/USDT:USDT", "buy", True), ("BTC/USDT:USDT", "sell", True)]
    assert pos(fake, "AAAUSDT") == pytest.approx(0) and pos(fake, "BTCUSDT") == pytest.approx(0)


# ── review repairs (2026-11-08): each test below failed on the book before its fix ─────────────

def peek_reason(tmp, pub: Public, at: int, ledger: bool = True) -> str | None:
    """The 5-minute pre-check as the runner calls it: krw.json and the order ledger read without a key."""

    return kx.signed_run_reason(at, PairStore.peek(tmp / kx.STORE), pub.notices, pub.symbols,
                                kx.peek_ledger(tmp / "order_state.json") if ledger else None)


def killed_after(fake, part: str):
    """The run is killed right after the order whose key holds ``part`` reached the venue."""

    real = fake.create_order

    def create(s, typ, side, amount, price=None, params=None):
        out = real(s, typ, side, amount, price, params)
        if part in (params or {}).get("clientOrderId", ""):
            raise Killed()
        return out

    fake.create_order = create
    return real


def test_an_exit_whose_btc_sell_reply_is_lost_is_closed_by_the_next_run(world):
    """R1/F1: the exit's last order settled from the ledger used to leave the event 'closing' for ever."""

    fake, book, tmp = world
    pub, _ = enter(world)
    real = lose_reply(fake, "-x0")                     # the BTC sell fills; its reply is lost
    with pytest.raises(PerpBookError, match="unknown"):
        cycle(book, krw(book, tmp, Public()), E0 + 72 * H)
    fake.create_order = real
    assert pos(fake, "BTCUSDT") == pytest.approx(0) and peek_reason(tmp, pub, E0 + 72 * H + 5 * MIN) == "settle"
    b = fresh(book, tmp)
    rep = cycle(b, krw(b, tmp, Public()), E0 + 72 * H + 5 * MIN, only="krw")["krw"]
    ev = event(tmp)
    assert rep["closed"] == 1 and (ev["status"], ev["close_reason"]) == ("closed", "hold")
    assert ev["fills"][-1]["key"] == "krw-101-AAA-x0" and ev["fills"][-1]["from_ledger"]
    assert [c[5]["clientOrderId"] for c in orders_sent(fake)].count("krw-101-AAA-x0") == 1 and len(orders_sent(fake)) == 5
    assert peek_reason(tmp, pub, E0 + 100 * H) is None                 # nothing left to sign for
    assert krw(b, tmp, pub).counts()["krw_events"] == {"closed": 1}


def test_an_exit_cut_off_after_its_btc_sell_is_closed_by_the_next_run(world):
    fake, book, tmp = world
    enter(world)
    real = killed_after(fake, "-x0")
    with pytest.raises(Killed):
        cycle(book, krw(book, tmp, Public()), E0 + 72 * H)
    fake.create_order = real
    b = fresh(book, tmp)
    assert cycle(b, krw(b, tmp, Public()), E0 + 72 * H + 5 * MIN)["krw"]["closed"] == 1
    assert event(tmp)["status"] == "closed" and pos(fake, "BTCUSDT") == pytest.approx(0)


def test_a_stale_stop_exit_whose_btc_sell_reply_is_lost_closes_once_the_stop_is_cancelled(world):
    fake, book, tmp = world
    enter(world)
    real_cancel = fake.cancel_order
    fake.cancel_order = lambda oid, s, params=None: {}            # the venue answers, and keeps the stop
    real = lose_reply(fake, "-x0")
    with pytest.raises(PerpBookError, match="unknown"):
        cycle(book, krw(book, tmp, Public()), E0 + 72 * H)
    assert event(tmp)["stale_stop"] is True and pos(fake, "BTCUSDT") == pytest.approx(0)
    fake.create_order, fake.cancel_order = real, real_cancel
    b = fresh(book, tmp)
    cycle(b, krw(b, tmp, Public()), E0 + 73 * H)                   # the check records the sell, then cancels the stop
    ev = event(tmp)
    assert ev["status"] == "closed" and "stale_stop" not in ev and fake.algo == {}


def test_a_loss_whose_btc_sell_reply_was_lost_still_reaches_the_halt(world):
    fake, book, tmp = world
    pub = Public(note(101, "AAA"), note(102, "BBB", at=N0 + 4 * DAY))
    cycle(book, krw(book, tmp, pub), E0 + 2 * MIN)
    fake.prices["AAAUSDT"] = 3.3                      # bought back at 3.3: 325 lost with fees, past 300 (15% of 4 x 500)
    real = lose_reply(fake, "-x0")
    with pytest.raises(PerpBookError, match="unknown"):
        cycle(book, krw(book, tmp, pub), E0 + 72 * H)
    fake.create_order = real
    fake.prices["AAAUSDT"] = 2.0
    b = fresh(book, tmp)
    assert cycle(b, krw(b, tmp, pub), E0 + 72 * H + 5 * MIN)["krw"]["halted"] is True
    b = fresh(book, tmp)
    cycle(b, krw(b, tmp, pub), E0 + 4 * DAY + 2 * MIN)
    assert (event(tmp, 102, "BBB")["status"], event(tmp, 102, "BBB")["reason"]) == ("shadow_only", "halted")
    assert not [c for c in orders_sent(fake) if c[1] == "BBB/USDT:USDT"]


def test_a_failed_entry_whose_take_off_reply_is_lost_is_marked_unwound_by_the_next_run(world):
    """R1 (failed) and R2: the take-off cut off leaves 'unwound' unset; the pre-check signs until a run finds it flat."""

    fake, book, tmp = world
    real = fake.create_order

    def create(s, typ, side, amount, price=None, params=None):
        out = real(s, typ, side, amount, price, params)
        if (params or {}).get("clientOrderId") in ("krw-101-AAA-o0", "krw-101-AAA-c0"):
            raise ccxt.NetworkError("reply lost")
        return out

    fake.create_order = create
    pub = Public(note(101, "AAA"))
    with pytest.raises(PerpBookError, match="unknown"):
        cycle(book, krw(book, tmp, pub), E0 + 2 * MIN)
    fake.create_order = real
    assert pos(fake, "AAAUSDT") == pytest.approx(0) and "unwound" not in event(tmp)
    assert peek_reason(tmp, pub, E0 + 7 * MIN, ledger=False) == "settle"
    b = fresh(book, tmp)
    cycle(b, krw(b, tmp, pub), E0 + 7 * MIN, only="krw")
    ev = event(tmp)
    assert (ev["status"], ev["unwound"]) == ("failed", True) and kx.alt_short(ev) == 0.0
    assert {f["key"] for f in ev["fills"]} == {"krw-101-AAA-o0", "krw-101-AAA-c0"}
    assert peek_reason(tmp, pub, E0 + 12 * MIN) is None


def test_a_naked_short_a_failed_take_off_left_makes_the_5_minute_pre_check_sign(world):
    """F2/R2: the alt sell's reply is lost and the take-off cannot read the positions: a short with no stop."""

    fake, book, tmp = world
    real_create = lose_reply(fake, "-o0")
    real_pos = fake.fetch_positions

    def flaky(symbols=None):
        if fake.positions.get("AAA/USDT:USDT"):
            raise ccxt.NetworkError("down")
        return real_pos(symbols)

    fake.fetch_positions = flaky
    pub = Public(note(101, "AAA"))
    with pytest.raises(Exception):
        cycle(book, krw(book, tmp, pub), E0 + 2 * MIN)
    fake.create_order, fake.fetch_positions = real_create, real_pos
    assert pos(fake, "AAAUSDT") == pytest.approx(-250.0) and fake.algo == {}
    ev = event(tmp)
    assert ev["status"] == "failed" and not kx.has_legs(ev) and "unwound" not in ev
    assert [peek_reason(tmp, pub, E0 + m * MIN) for m in (7, 12)] == ["settle", "settle"]
    b = fresh(book, tmp)
    cycle(b, krw(b, tmp, pub), E0 + 7 * MIN, only="krw")             # the next 5-minute run buys it back
    assert pos(fake, "AAAUSDT") == pytest.approx(0) and event(tmp)["unwound"] is True
    assert peek_reason(tmp, pub, E0 + 12 * MIN) is None


def test_a_short_the_take_off_could_not_see_is_found_through_the_order_ledger(world):
    """F2: the position read lags the lost sell, so the take-off marks the event unwound over a naked short."""

    fake, book, tmp = world
    real_create = lose_reply(fake, "-o0")
    real_pos = fake.fetch_positions
    fake.fetch_positions = lambda symbols=None: [p for p in real_pos(symbols) if "AAA" not in str(p.get("symbol"))]
    pub = Public(note(101, "AAA"))
    with pytest.raises(PerpBookError):
        cycle(book, krw(book, tmp, pub), E0 + 2 * MIN)
    fake.create_order, fake.fetch_positions = real_create, real_pos
    assert event(tmp)["unwound"] is True and pos(fake, "AAAUSDT") == pytest.approx(-250.0) and fake.algo == {}
    assert peek_reason(tmp, pub, E0 + 7 * MIN, ledger=False) is None          # the record alone does not show it
    assert peek_reason(tmp, pub, E0 + 7 * MIN) == "settle"                     # the ledger's unsettled sell does
    b = fresh(book, tmp)
    cycle(b, krw(b, tmp, pub), E0 + 7 * MIN, only="krw")
    assert pos(fake, "AAAUSDT") == pytest.approx(0) and kx.alt_short(event(tmp)) == 0.0
    assert peek_reason(tmp, pub, E0 + 12 * MIN) is None


def test_unsettled_reads_the_books_open_intents_and_fills_its_record_lacks(tmp_path):
    ev = {"kind": "event", "notice_id": 101, "token": "AAA", "perp": "AAAUSDT", "entry_ms": E0, "notice_ms": N0,
          "status": "failed", "fills": [{"key": "krw-101-AAA-o0", "symbol": "AAAUSDT", "side": "sell", "qty": 250.0}]}
    store = PairStore(tmp_path / kx.STORE, "", {"e101-AAA": ev})
    fill = {"status": "filled", "filled_quantity": 250.0}
    assert kx.unsettled(store, {"krw-101-AAA-c0": {"status": "reconciling"}})
    assert kx.unsettled(store, {"krw-101-AAA-c0": fill})                        # filled, not yet on the record
    assert not kx.unsettled(store, {"krw-101-AAA-o0": fill})                    # on the record
    assert not kx.unsettled(store, {"krw-101-AAA-st": {"status": "open"}})      # a stop at the venue is its own check's
    assert not kx.unsettled(store, {"pp-AAAUSDT-1-s": {"status": "reconciling"}})   # another book's
    assert not kx.unsettled(store, {"krw-101-AAA-c0": {"status": "manual_intervention"}})   # the operator's, not a run's
    ev["status"] = "closed"
    assert not kx.unsettled(store, {"krw-101-AAA-c0": fill})
    assert kx.peek_ledger(tmp_path / "none.json") == {}


def test_a_stop_that_fires_inside_a_failed_entry_is_recorded_as_fired(world):
    """R6: the squeeze fires the stop, then the BTC buy fails; no operator review follows."""

    fake, book, tmp = world
    real = fake.create_order

    def create(s, typ, side, amount, price=None, params=None):
        cid = (params or {}).get("clientOrderId", "")
        if cid == "krw-101-AAA-b0":
            fake.fire_stop("AAAUSDT")                    # the stop fired a moment ago
            fake.calls.append(("order", s, typ, side, amount, dict(params)))
            fake.orders[cid] = {"id": "x" + cid, "clientOrderId": cid, "symbol": s, "type": "market", "side": side,
                                "amount": amount, "filled": 0.0, "average": None, "cost": 0.0, "status": "expired",
                                "reduceOnly": False}
            return dict(fake.orders[cid])
        return real(s, typ, side, amount, price, params)

    fake.create_order = create
    with pytest.raises(PerpBookError, match="expired"):
        cycle(book, krw(book, tmp, Public(note(101, "AAA"))), E0 + 2 * MIN)
    fake.create_order = real
    rec = book.orders.state.order_ledger["krw-101-AAA-st"]
    assert (rec["status"], rec["exchange_status"]) == ("filled", "triggered")
    ev = event(tmp)
    assert (ev["status"], ev["unwound"]) == ("failed", True) and kx.alt_short(ev) == 0.0
    (row,) = [f for f in ev["fills"] if f.get("from_stop")]
    assert (row["qty"], row["avg"], row["avg_from"]) == (250.0, pytest.approx(3.15), "stop_trigger")
    b = fresh(book, tmp)
    cycle(b, krw(b, tmp, Public()), E0 + 7 * MIN)                   # no 'short is gone' review
    assert not kx.has_legs(event(tmp)) and pos(fake, "AAAUSDT") == pytest.approx(0)


def _stop_reply_lost_then_fired(fake):
    """The stop reaches the venue with its reply lost, and fires before the take-off reads the positions."""

    real, real_pos = fake.create_order, fake.fetch_positions
    state = {"fire": False}

    def create(s, typ, side, amount, price=None, params=None):
        out = real(s, typ, side, amount, price, params)
        if (params or {}).get("clientOrderId") == "krw-101-AAA-st":
            state["fire"] = True
            raise ccxt.NetworkError("reply lost")
        return out

    def positions(symbols=None):
        if state["fire"]:
            state["fire"] = False
            fake.fire_stop("AAAUSDT")
        return real_pos(symbols)

    fake.create_order, fake.fetch_positions = create, positions
    return real, real_pos


def test_a_stop_whose_reply_is_lost_and_that_fired_is_recorded_when_the_venue_answers_its_key(world):
    """V1: a stop whose send raised, that the venue took and that fired before the take-off: read back by its key."""

    fake, book, tmp = world
    book.venue.order_by_key = lambda perp, key: ({"status": "filled", "avg_fill_price": 3.0}
                                                  if key == "krw-101-AAA-st" else None)
    real, real_pos = _stop_reply_lost_then_fired(fake)
    pub = Public(note(101, "AAA"))
    with pytest.raises(PerpBookError):
        cycle(book, krw(book, tmp, pub), E0 + 2 * MIN)
    fake.create_order, fake.fetch_positions = real, real_pos
    rec = book.orders.state.order_ledger["krw-101-AAA-st"]
    assert (rec["status"], rec["exchange_status"]) == ("filled", "triggered")
    ev = event(tmp)
    assert (ev["status"], ev["unwound"]) == ("failed", True) and kx.alt_short(ev) == 0.0
    (row,) = [f for f in ev["fills"] if f.get("from_stop")]
    assert (row["avg"], row["avg_from"]) == (pytest.approx(3.0), "venue")
    b = fresh(book, tmp)
    cycle(b, krw(b, tmp, pub), E0 + 7 * MIN, only="krw")             # no operator review
    assert pos(fake, "AAAUSDT") == pytest.approx(0)


def test_a_stop_whose_reply_is_lost_and_that_fired_without_a_venue_answer_stops_for_review(world):
    """V1, the documented limit: with no answer to the key read, the short stays on the record and the next
    check stops every book for operator review (fail closed)."""

    fake, book, tmp = world
    book.venue.order_by_key = lambda perp, key: None
    real, real_pos = _stop_reply_lost_then_fired(fake)
    pub = Public(note(101, "AAA"))
    with pytest.raises(PerpBookError):
        cycle(book, krw(book, tmp, pub), E0 + 2 * MIN)
    fake.create_order, fake.fetch_positions = real, real_pos
    assert book.orders.state.order_ledger["krw-101-AAA-st"]["status"] == "rejected"
    assert kx.alt_short(event(tmp)) > 0 and event(tmp).get("unwound") is False
    b = fresh(book, tmp)
    with pytest.raises(PerpBookError):
        cycle(b, krw(b, tmp, pub), E0 + 7 * MIN, only="krw")
    assert not [c for c in orders_sent(fake) if c[1] == "AAA/USDT:USDT" and c[3] == "sell"][1:]   # no new short


def test_the_take_off_leaves_unwound_unset_when_its_stop_cancel_read_fails(world):
    """V4: 'unwound' is set last. A buy-back filled and saved, then the stop's venue read raises: the flag must not
    be stored, so the 5-minute pre-check signs and the next run cancels the stop left on a flat position."""

    fake, book, tmp = world
    real, real_open = fake.create_order, fake.fetch_open_orders
    state = {"down": False}

    def create(s, typ, side, amount, price=None, params=None):
        cid = (params or {}).get("clientOrderId", "")
        if cid == "krw-101-AAA-b0":             # the BTC buy fails outright: the entry fails after the stop is placed
            fake.calls.append(("order", s, typ, side, amount, dict(params)))
            fake.orders[cid] = {"id": "x" + cid, "clientOrderId": cid, "symbol": s, "type": "market", "side": side,
                                "amount": amount, "filled": 0.0, "average": None, "cost": 0.0, "status": "expired",
                                "reduceOnly": False}
            return dict(fake.orders[cid])
        out = real(s, typ, side, amount, price, params)
        if cid == "krw-101-AAA-c0":             # the take-off's buy-back filled; the venue read after it fails
            state["down"] = True
        return out

    def open_orders(*a, **k):
        if state["down"]:
            raise ccxt.NetworkError("down")
        return real_open(*a, **k)

    fake.create_order, fake.fetch_open_orders = create, open_orders
    pub = Public(note(101, "AAA"))
    with pytest.raises(Exception):
        cycle(book, krw(book, tmp, pub), E0 + 2 * MIN)
    fake.create_order, fake.fetch_open_orders = real, real_open
    ev = event(tmp)
    assert ev["status"] == "failed" and kx.alt_short(ev) == 0.0 and "unwound" not in ev
    assert pos(fake, "AAAUSDT") == pytest.approx(0) and fake.algo                 # the stop is still listed
    assert peek_reason(tmp, pub, E0 + 7 * MIN) == "settle"
    b = fresh(book, tmp)
    cycle(b, krw(b, tmp, pub), E0 + 7 * MIN, only="krw")
    assert fake.algo == {} and event(tmp)["unwound"] is True
    assert peek_reason(tmp, pub, E0 + 12 * MIN) is None


def test_a_429_on_the_exchange_info_read_ends_the_runs_shadow(world):
    """V2: a FetchStop from the exchangeInfo read of a new notice: nothing more is asked of Binance in that run."""

    from tools.selection.fetch import FetchStop

    fake, book, tmp = world
    pub = Public(note(101, "AAA"))
    cycle(book, krw(book, tmp, pub), N0 + MIN)
    pub.notes.append(note(102, "BBB", at=E0 + 73 * H - 30 * MIN))

    def stop():
        raise FetchStop("status 429")

    pub.symbols = stop
    asked: list = []
    real_row = pub.shadow_row

    def row(*a):
        asked.append(a[0])
        return real_row(*a)

    pub.shadow_row = row
    cycle(book, krw(book, tmp, pub), E0 + 73 * H)
    assert asked == [] and event(tmp)["shadow"]["status"] == "due"


def test_an_event_missed_before_the_halt_keeps_its_own_reason(world):
    """V3: no run in BBB's window; a later run's exit halts the book. BBB was not halted in its window."""

    fake, book, tmp = world
    pub = Public(note(101, "AAA"), note(102, "BBB", at=N0 + 72 * H - 30 * MIN))
    cycle(book, krw(book, tmp, pub), E0 + 2 * MIN)                     # AAA open
    cycle(book, krw(book, tmp, pub), N0 + 72 * H - 25 * MIN)           # BBB waits, E = E0 + 72 h
    fake.prices["AAAUSDT"] = 3.3
    rep = cycle(book, krw(book, tmp, pub), E0 + 72 * H + 20 * MIN)["krw"]   # AAA's exit halts the book
    assert rep["halted"] is True
    ev = event(tmp, 102, "BBB")
    assert (ev["status"], ev["reason"]) == ("missed", "no_run_in_window")


def test_a_shadow_row_is_given_up_only_after_three_failed_tries_seven_days_from_the_first(world):
    """R3: one failed try after a long stop of the runs used to give the row up at once."""

    fake, book, tmp = world
    pub = Public(note(101, "AAA"))
    kb = krw(book, tmp, pub)
    cycle(book, kb, N0 + MIN)                                       # recorded; then the PC is off for 8 days
    pub.fail.add("shadow")
    back = E0 + 73 * H + 8 * DAY
    cycle(book, kb, back)
    sh = event(tmp)["shadow"]
    assert (sh["status"], sh["fails"], sh["first_fail_ms"]) == ("due", 1, back)
    cycle(book, kb, back + 7 * DAY)                                 # 7 days on, but 2 failed tries only
    assert event(tmp)["shadow"]["status"] == "due"
    pub.fail.clear()
    cycle(book, kb, back + 7 * DAY + H)                             # the data can be read: the row is kept
    assert event(tmp)["shadow"]["status"] == "done"


def test_a_418_or_429_ends_the_runs_shadow_and_never_gives_a_row_up(world):
    """R3, and F6's M8: a FetchStop on the first due event leaves the later ones untried."""

    from tools.selection.fetch import FetchStop

    fake, book, tmp = world
    pub = Public(note(101, "AAA"), note(102, "BBB"))
    tried: list[str] = []

    def stop(perp, entry_ms, notice_ms):
        tried.append(perp)
        raise FetchStop("status 429")

    pub.shadow_row = stop
    kb = krw(book, tmp, pub)
    cycle(book, kb, N0 + MIN)
    for d in (0, 8, 16):
        cycle(book, kb, E0 + 73 * H + d * DAY)
    assert tried == ["AAAUSDT"] * 3
    sh = event(tmp)["shadow"]
    assert (sh["status"], sh["tries"]) == ("due", 3) and "fails" not in sh and sh["last_error"].startswith("FetchStop")
    assert event(tmp, 102, "BBB")["shadow"] == {"status": "due", "tries": 0}


def test_while_halted_an_event_no_run_could_trade_is_shadow_only_halted(world):
    """R4: the pre-check signs for no window while halted, so the hourly run used to record 'missed'."""

    fake, book, tmp = world
    pub = Public(note(101, "AAA"))
    cycle(book, krw(book, tmp, pub), N0 + MIN)                      # AAA waits for E
    st = PairStore.load(tmp / kx.STORE, KEY)
    st.pairs["book"]["halted"] = {"reason": "realized_net_below_limit", "at": "x"}
    st.save()
    assert peek_reason(tmp, pub, E0 + 2 * MIN) is None
    cycle(book, krw(book, tmp, pub), E0 + 15 * MIN + 5_000)           # the hourly run, after the window
    assert (event(tmp)["status"], event(tmp)["reason"]) == ("shadow_only", "halted")
    pub.notes.append(note(102, "BBB", at=N0 + DAY))                  # first seen after its window
    cycle(book, krw(book, tmp, pub), E0 + DAY + 15 * MIN + 5_000)
    assert (event(tmp, 102, "BBB")["status"], event(tmp, 102, "BBB")["reason"]) == ("shadow_only", "halted")
    assert not orders_sent(fake)


def test_a_stop_another_book_left_on_the_symbol_keeps_book_krw_out(world):
    """R5: a failed LV block's stale stop (LV holds nothing there) would buy a KRW short back."""

    fake, book, tmp = world
    fake.algo["lvw261102-AAAUSDT-st"] = {"id": "a9", "clientOrderId": "lvw261102-AAAUSDT-st", "symbol": "AAA/USDT:USDT",
                                         "type": "stop_market", "side": "buy", "amount": 100.0, "filled": 0,
                                         "status": "open", "triggerPrice": 2.4, "reduceOnly": True}
    rep = cycle(book, krw(book, tmp, Public(note(101, "AAA"), note(102, "BBB"))), E0 + 2 * MIN)["krw"]
    assert (event(tmp)["status"], event(tmp)["reason"]) == ("shadow_only", "stop_listed")
    assert rep["entered"] == 1 and event(tmp, 102, "BBB")["status"] == "open"
    assert not [c for c in orders_sent(fake) if c[1] == "AAA/USDT:USDT"]


def test_a_venue_read_that_fails_before_any_order_leaves_the_event_to_a_later_run_in_its_window(world):
    """F6's M9 (and the stop listing read): nothing is sent, the error is recorded, a later run enters."""

    fake, book, tmp = world
    real = fake.fetch_ticker

    def down(sym):
        if sym == "AAA/USDT:USDT":
            raise ccxt.NetworkError("ticker down")
        return real(sym)

    fake.fetch_ticker = down
    pub = Public(note(101, "AAA"))
    rep = cycle(book, krw(book, tmp, pub), E0 + 2 * MIN)["krw"]
    ev = event(tmp)
    assert rep["entered"] == 0 and ev["status"] == "waiting" and ev["attempts"][-1]["why"].startswith("error_")
    assert not orders_sent(fake)
    fake.fetch_ticker = real
    real_open = fake.fetch_open_orders
    fake.fetch_open_orders = lambda *a, **k: (_ for _ in ()).throw(ccxt.NetworkError("open orders down"))
    assert cycle(book, krw(book, tmp, pub), E0 + 7 * MIN)["krw"]["entered"] == 0
    fake.fetch_open_orders = real_open
    assert cycle(book, krw(book, tmp, pub), E0 + 12 * MIN)["krw"]["entered"] == 1


def test_an_exit_that_fails_holds_back_an_entry_in_its_window(world):
    """F6's M7."""

    fake, book, tmp = world
    pub = Public(note(101, "AAA"), note(102, "BBB", at=N0 + 72 * H))
    cycle(book, krw(book, tmp, pub), E0 + 2 * MIN)
    real = thin(fake, "-c0", 0.0)                                   # AAA's buy-back expires with nothing filled
    with pytest.raises(PerpBookError, match="expired"):
        cycle(book, krw(book, tmp, pub), E0 + 72 * H + MIN)
    ev = event(tmp, 102, "BBB")
    assert ev["status"] == "waiting" and ev["attempts"][-1]["why"] == "exit_failed"
    assert not [c for c in orders_sent(fake) if c[1] == "BBB/USDT:USDT"]
    fake.create_order = real
    b = fresh(book, tmp)
    rep = cycle(b, krw(b, tmp, pub), E0 + 72 * H + 6 * MIN)["krw"]
    assert (rep["closed"], rep["entered"]) == (1, 1)


def test_the_halt_counts_the_fees(world):
    """F6's M13: 299.75 lost on prices alone stays inside 300; the fees take it past."""

    fake, book, tmp = world
    pub = Public(note(101, "AAA"), note(102, "BBB", at=N0 + 4 * DAY))
    cycle(book, krw(book, tmp, pub), E0 + 2 * MIN)
    fake.prices["AAAUSDT"] = 3.199
    assert cycle(book, krw(book, tmp, pub), E0 + 72 * H)["krw"]["halted"] is True


def test_the_btc_leg_is_sized_to_the_nearest_step(world):
    """F6's M41: 500 / 90,000 is 0.00556 BTC: 0.006 to the nearest step, not 0.005."""

    fake, book, tmp = world
    fake.prices["BTCUSDT"] = 90_000.0
    enter(world)
    (buy,) = [c for c in orders_sent(fake) if c[1] == "BTC/USDT:USDT"]
    assert buy[4] == pytest.approx(0.006)


def test_rules_chain_leaves_out_cancelled_listings_and_ambiguous_notices_research_rules_not_krw():
    """F1 (ambiguous chain): classify.py rules first, then merges."""

    T = N0
    amb = {"t": T, "amb": True}
    assert R.chain(T + 3 * DAY, False, [amb]) == []                    # this later own notice rules it not KRW
    assert R.chain(T + 3 * DAY, True, [amb]) == [T]                    # an ambiguous notice rules nothing
    assert R.chain(T + DAY, False, [amb]) == [T]                       # not more than a day later
    own_cancelled = {"t": T + 2 * DAY, "amb": False, "cancelled": True}
    assert R.chain(T + 3 * DAY, True, [amb, own_cancelled]) == []      # a cancelled own notice rules it too
    assert R.chain(T + DAY, False, [T - DAY]) == [T - DAY]             # a bare time is a non-ambiguous notice
    assert R.resolves(T + DAY + 1, T) and not R.resolves(T + DAY, T)


def test_an_ambiguous_notice_never_merges_away_the_tokens_own_krw_notice_and_leaves_the_read(world):
    """F1 (ambiguous chain): research's events here are BBB at notice 101 and AAA at notice 102."""

    fake, book, tmp = world
    shared = kfeed.Notice(101, "KRW, BTC 마켓 디지털 자산 추가 (AAA, BBB)", N0, N0)
    own = kfeed.Notice(102, "에이에이(AAA) KRW 마켓 디지털 자산 추가", N0 + 3 * DAY, N0 + 3 * DAY)
    pub = Public(shared)
    cycle(book, krw(book, tmp, pub), E0 + 2 * MIN)
    assert event(tmp)["ambiguous"] is True and event(tmp)["status"] == "open"
    pub.notes.append(own)
    cycle(book, krw(book, tmp, pub), N0 + 3 * DAY + MIN)
    first, later = event(tmp, 101, "AAA"), event(tmp, 102, "AAA")
    assert first["excluded"]["reason"] == "ambiguous_not_krw" and first["excluded"]["notice"] == 102
    assert first["status"] == "open" and first["shadow"]["status"] == "excluded"      # its legs go to their exit
    assert (later["status"], later["ambiguous"]) == ("waiting", False) and "excluded" not in later
    assert "excluded" not in event(tmp, 101, "BBB")
    notices = stored(tmp).pairs["book"]["krw_notices"]["AAA"]
    assert notices == [{"t": N0, "id": 101, "amb": True, "cancelled": False},
                       {"t": N0 + 3 * DAY, "id": 102, "amb": False, "cancelled": False}]
    assert "102-AAA" not in stored(tmp).pairs["book"]["not_events"]
    assert krw(book, tmp, pub).counts()["krw_excluded"] == 1


LISTED = "테스트(AAA) 신규 거래지원 안내 (KRW, BTC, USDT 마켓)"
CANCELLED = LISTED + " (테스트(AAA) 거래지원 취소 안내)"


def test_a_listing_cancelled_before_e_is_never_traded_and_leaves_the_read(world):
    """F3: the title used to be read once, at first sight."""

    fake, book, tmp = world
    pub = Public(kfeed.Notice(101, LISTED, N0, N0))
    cycle(book, krw(book, tmp, pub), N0 + 5 * MIN)
    assert event(tmp)["status"] == "waiting"
    pub.notes = [kfeed.Notice(101, CANCELLED, N0, N0 + 20 * MIN)]       # Upbit cancels it before E
    rep = cycle(book, krw(book, tmp, pub), E0 + 2 * MIN)["krw"]
    ev = event(tmp)
    assert rep["entered"] == 0 and not orders_sent(fake)
    assert (ev["status"], ev["reason"], ev["excluded"]["reason"]) == ("cancelled", "listing_cancelled",
                                                                       "listing_cancelled")
    assert ev["shadow"]["status"] == "excluded" and ev["excluded"]["title"] == CANCELLED
    assert stored(tmp).pairs["book"]["krw_notices"]["AAA"][0]["cancelled"] is True
    assert cycle(book, krw(book, tmp, pub), E0 + 74 * H)["krw"]["shadow_rows"] == 0 and pub.calls["shadow"] == 0
    assert kx.signed_run_reason(E0 + 2 * MIN, PairStore.peek(tmp / kx.STORE), pub.notices, pub.symbols) is None


def test_a_listing_cancelled_after_the_trade_keeps_its_legs_to_the_exit_and_chains_nothing(world):
    fake, book, tmp = world
    pub = Public(kfeed.Notice(101, LISTED, N0, N0))
    cycle(book, krw(book, tmp, pub), E0 + 2 * MIN)
    pub.notes = [kfeed.Notice(101, CANCELLED, N0, E0 + H)]
    cycle(book, krw(book, tmp, pub), E0 + 2 * H)
    ev = event(tmp)
    assert ev["status"] == "open" and ev["excluded"]["reason"] == "listing_cancelled"
    # relisted 3 days on: research's family has no cancelled listing, so this is a new event, not a merge
    pub.notes.append(kfeed.Notice(102, LISTED, N0 + 3 * DAY, N0 + 3 * DAY))
    cycle(book, krw(book, tmp, pub), N0 + 3 * DAY + MIN)
    assert event(tmp, 102, "AAA")["status"] == "waiting"
    assert cycle(book, krw(book, tmp, pub), E0 + 72 * H - MIN)["krw"]["closed"] == 1
    assert event(tmp)["status"] == "closed" and event(tmp)["shadow"]["status"] == "excluded"
    # a title that drops the token from KRW excludes it the same way
    pub.notes = [kfeed.Notice(102, "테스트(AAA) 신규 거래지원 안내 (BTC, USDT 마켓)", N0 + 3 * DAY, N0 + 3 * DAY + H)]
    cycle(book, krw(book, tmp, pub), N0 + 3 * DAY + 2 * H)
    assert event(tmp, 102, "AAA")["excluded"]["reason"] == "no_longer_krw"


def test_resume_starts_a_new_sleeve_only_when_the_book_is_flat(world):
    """F4: the documented reset used to move krw.json aside, and page 1 then came back as new events."""

    fake, book, tmp = world
    pub = Public(note(101, "AAA"), note(102, "BBB", at=N0 + 2 * H), note(103, "CCC", at=N0 + 10 * DAY))
    cycle(book, krw(book, tmp, pub), E0 + 2 * MIN)
    cycle(book, krw(book, tmp, pub), E0 + 2 * H + 2 * MIN)
    fake.prices["AAAUSDT"] = 3.3                                    # AAA bought back at 3.3: 325 lost, past 300
    assert cycle(book, krw(book, tmp, pub), E0 + 72 * H)["krw"]["halted"] is True
    rep = cycle(book, krw(book, tmp, pub, resume=True), E0 + 72 * H + MIN)["krw"]
    assert rep["resume"] == "not_flat" and rep["halted"] is True     # BBB still holds its legs
    cycle(book, krw(book, tmp, pub), E0 + 74 * H)                    # BBB exits
    rep = cycle(book, krw(book, tmp, pub, resume=True), E0 + 74 * H + MIN)["krw"]
    assert rep["resume"] == "resumed" and rep["halted"] is False
    st = stored(tmp).pairs["book"]
    assert (st["sleeve_from_ms"], st["halted"], len(st["resumes"])) == (E0 + 74 * H + MIN, None, 1)
    assert cycle(book, krw(book, tmp, pub, resume=True), E0 + 74 * H + 2 * MIN)["krw"]["resume"] == "nothing_to_resume"
    rep = cycle(book, krw(book, tmp, pub), E0 + 10 * DAY + 2 * MIN)["krw"]
    assert (rep["entered"], rep["halted"]) == (1, False)            # the earlier sleeve's loss no longer counts
    assert [k for k in stored(tmp).pairs if k.startswith("e")] == ["e101-AAA", "e102-BBB", "e103-CCC"]
    assert [e["event"] for e in krw_events(tmp)].count("perp_krw_resumed") == 1
    assert all(no_floats(e["details"]) for e in krw_events(tmp) if e["event"].startswith("perp_krw_resume"))
    # a new size is a new sleeve the same way
    pub.notes.append(note(104, "DDD", at=N0 + 20 * DAY))
    cycle(book, krw(book, tmp, pub, usd=800.0), E0 + 10 * DAY + 72 * H)
    assert cycle(book, krw(book, tmp, pub, usd=800.0, resume=True), E0 + 13 * DAY)["krw"]["resume"] == "resumed"
    assert stored(tmp).pairs["book"]["usd"] == 800.0
    assert cycle(book, krw(book, tmp, pub, usd=800.0), E0 + 20 * DAY + 2 * MIN)["krw"]["entered"] == 1


def test_the_runner_takes_krw_resume_only_with_krw_and_execute_and_without_only(krw_runner, capsys):
    for argv in (["--krw-resume"], ["--krw-resume", "--krw"], ["--krw-resume", "--execute"],
                 ["--krw-resume", "--krw", "--execute", "--only", "krw"]):
        assert krw_runner.main(argv) == 2
    assert "--krw-resume needs --krw and --execute" in capsys.readouterr().err


def test_the_runner_passes_the_books_in_run_order_with_krw_last(krw_runner, tmp_path, monkeypatch, capsys):
    """F6's M16: pair, LV, LEARN, FLOW02, FLOW01, KRW (book.py)."""

    fake = FakeUsdm({"BTCUSDT": 80_000.0})
    monkeypatch.setattr(ca.ccxt, "binanceusdm", lambda cfg: fake)
    monkeypatch.setattr(krw_runner, "mainnet_listings", lambda: {})
    pub = Public()
    monkeypatch.setattr(krw_runner, "krw_sources", lambda box: (pub.notices, pub.symbols, pub.shadow_row))

    class Waiting:   # the FLOW02 store is not seeded
        def update(self, now_ms):
            raise AssertionError("a dry run fetched FLOW02 data")

        def decide(self, day_ms, *, record):
            return {"ok": False, "missing": ["store"]}

    monkeypatch.setattr(krw_runner, "flow02_signal", lambda root: Waiting())
    order: list[list[str]] = []
    real = krw_runner.run_cycle

    def spy(book, **kw):
        order.append([o.name for o in kw["others"]])
        return real(book, **kw)

    monkeypatch.setattr(krw_runner, "run_cycle", spy)
    assert krw_runner.main([*FLAGS, *run_roots(tmp_path)]) == 0
    assert order == [["lv", "learn", "flow02", "flow01", "krw"]]


def test_the_feed_never_follows_a_redirect_and_refuses_a_body_cut_short(monkeypatch):
    """F6's M22 and M23."""

    import http.client
    import io
    import urllib.error

    got: dict = {}

    class Opener:
        def __init__(self, resp):
            self.resp = resp

        def open(self, req, timeout):
            if self.resp is None:     # what urllib does when the redirect handler declines: the 302 as an error
                raise urllib.error.HTTPError(req.full_url, 302, "Found", {"Location": "https://example.com/"},
                                             io.BytesIO(b""))
            return self.resp

    def build(*handlers):
        got["handlers"] = handlers
        return Opener(got.get("resp"))

    monkeypatch.setattr(urllib.request, "build_opener", build)
    assert kfeed.transport(kfeed.URL, 5.0) == (302, b"")
    assert kfeed._NoRedirect in got["handlers"]
    assert kfeed._NoRedirect().redirect_request(None, None, 302, "Found", {}, "https://example.com/") is None
    with pytest.raises(kfeed.FeedError, match="status 302"):
        kfeed.Feed().page()

    class Short:
        status = 200
        headers = {"Content-Length": "10"}

        def read(self, n):
            return b"{}"

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    got["resp"] = Short()
    with pytest.raises(http.client.IncompleteRead):
        kfeed.transport(kfeed.URL, 5.0)
    with pytest.raises(kfeed.FeedError, match="IncompleteRead"):
        kfeed.Feed().page()


def test_parity_exit_status_needs_both_halves_as_recorded():
    """F2 (parity): the rule half used to decide nothing."""

    from tools.krwfade import parity as P

    diffs = [{"kind": k, "token": t, "notice_day": d, "rule_perp": p} for k, t, d, p in sorted(P.EXPECTED_DIFFS, key=str)]
    rules = {"research_events": 109, "replay_events": 109, "same_token_perp_notice_E": 106, "differences": diffs}
    counts = {"events": 109, "pass_0a_agree": 109, "compared": 104, "g_bps_within_1e-6": 104,
              "n_bps_within_1e-6": 104, "stopped_agree": 104, "fund_rows_agree": 104, "flags_agree": 104}
    good = {"rules": rules, "shadow": {"counts": counts, "mismatches": []}}
    assert P.exit_code(good) == 0 and P.exit_code({"rules": rules}, rules_only=True) == 0
    for broken in ({**rules, "same_token_perp_notice_E": 83}, {**rules, "differences": diffs[:3]},
                   {**rules, "differences": diffs + [{"kind": "entry_differs", "token": "X", "notice_day": "d",
                                                      "rule_perp": "XUSDT"}]}):
        assert P.exit_code({**good, "rules": broken}) == 1 and P.exit_code({"rules": broken}, rules_only=True) == 1
    for bad in ({**counts, "flags_agree": 103}, {**counts, "stopped_agree": 103}, {**counts, "pass_0a_disagree": 1},
                {**counts, "compared": 0, "g_bps_within_1e-6": 0}):
        assert P.exit_code({**good, "shadow": {"counts": bad, "mismatches": []}}) == 1
    assert P.exit_code({**good, "shadow": {"counts": counts, "mismatches": [{"token": "X", "field": "flags"}]}}) == 1


def test_the_runners_pre_check_reads_the_order_ledger_without_a_key(krw_runner, tmp_path):
    """F2: the 5-minute pre-check signs for an order of book KRW the ledger has not settled."""

    root = tmp_path / "perp_demo"
    root.mkdir()
    pub = Public()
    src = (pub.notices, pub.symbols, pub.shadow_row)
    assert krw_runner.krw_reason(root, E0, src) is None                 # no record, no ledger
    (root / "order_state.json").write_text(json.dumps({"order_ledger": {
        "krw-101-AAA-o0": {"status": "reconciling", "symbol": "AAAUSDT"}}, "integrity": "not checked here"}))
    assert krw_runner.krw_reason(root, E0, src) == "settle"
    (root / "order_state.json").write_text(json.dumps({"order_ledger": {
        "krw-101-AAA-o0": {"status": "manual_intervention", "symbol": "AAAUSDT"}}}))
    assert krw_runner.krw_reason(root, E0, src) is None                 # only the operator can settle that one


def test_a_loss_closed_under_a_stale_stop_reaches_the_halt_at_once(world):
    """The halt counts a round trip flat by its fills while its stop is still listed (the event stays closing)."""

    fake, book, tmp = world
    pub = Public(note(101, "AAA"))
    cycle(book, krw(book, tmp, pub), E0 + 2 * MIN)
    fake.prices["AAAUSDT"] = 3.3
    fake.cancel_order = lambda oid, s, params=None: {}               # the venue keeps the stop
    rep = cycle(book, krw(book, tmp, pub), E0 + 72 * H)["krw"]
    assert (event(tmp)["status"], event(tmp)["stale_stop"]) == ("closing", True) and rep["halted"] is True
