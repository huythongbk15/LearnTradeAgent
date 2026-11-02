"""Weekly lists and Mem. The rule is checked against a plain copy of the arithmetic of seed/weeks2_fixed.py.txt, kept here so the port cannot drift from it."""
from __future__ import annotations

import statistics
from datetime import date, timedelta

import numpy as np
import pytest

from tests.test_selection_fixture import market, panel_of
from tools.selection import config, universe
from tools.selection import library as lib
from tools.selection.config import ts

MONDAY = date(2026, 8, 10)
SUNDAY = MONDAY - timedelta(days=1)


def bars_of(rng, days: int = 90, level: float = 10.0, vol: float = .03, qv: float = 1e6, end: date = SUNDAY) -> dict:
    px = level * np.exp(np.cumsum(rng.normal(0, vol, days)))
    q = qv * np.exp(rng.normal(0, .3, days))
    return {end - timedelta(days=days - 1 - i): (float(px[i]), float(q[i])) for i in range(days)}


def plain_rule(daily: dict, monday: date, n: int) -> list[str]:
    """The arithmetic of weeks2, line for line, on names already filtered."""
    sunday = monday - timedelta(days=1)
    start = sunday - timedelta(days=21)
    scored = []
    for coin, bars in daily.items():
        if sunday not in bars or start not in bars:
            continue
        if sum(1 for d in bars if d <= sunday) < 60:
            continue
        recent = [bars[sunday - timedelta(days=k)] for k in range(28) if sunday - timedelta(days=k) in bars]
        closes = [c for c, _ in recent]
        if len(closes) < 2 or statistics.pstdev(closes) < 0.005 * statistics.mean(closes):
            continue
        scored.append((-sum(q for _, q in recent), coin))
    return [c for _, c in sorted(scored)[:n]]


def test_frozen_list_loses_masked_coins_and_btc_with_no_refill() -> None:
    weeks = {"2026-08-03": ["BTC", "ETH", "AAA", "SOL", "CCC"]}
    assert universe.frozen_lists(weeks) == {"2026-08-03": ["AAA", "CCC"]}
    assert universe.frozen_lists(weeks, masked=False) == {"2026-08-03": ["ETH", "AAA", "SOL", "CCC"]}


def test_a_frozen_list_past_the_last_frozen_week_is_refused() -> None:
    with pytest.raises(ValueError):
        universe.frozen_lists({"2026-08-10": ["AAA"]})


def test_rule_against_the_plain_arithmetic() -> None:
    rng = np.random.default_rng(4)
    daily = {f"C{i:02d}": bars_of(rng, qv=float(rng.uniform(1e5, 1e7))) for i in range(40)}
    assert universe.rule_list(daily, MONDAY, 18) == plain_rule(daily, MONDAY, 18)
    assert len(universe.rule_list(daily, MONDAY)) == config.LIST_N == 18


def test_rule_ranks_by_28_day_quote_volume_over_the_bars_present() -> None:
    rng = np.random.default_rng(5)
    a, b = bars_of(rng, qv=1e6), bars_of(rng, qv=1e6)
    for k in range(28):
        day = SUNDAY - timedelta(days=k)
        a[day] = (a[day][0], 100.0)
        b[day] = (b[day][0], 300.0)
    del b[SUNDAY - timedelta(days=3)]
    assert universe.rule_list({"AAA": a, "BBC": b}, MONDAY) == ["BBC", "AAA"]
    for k in range(1, 28):
        day = SUNDAY - timedelta(days=k)
        if day in b and k not in (21,):
            del b[day]
    assert universe.rule_list({"AAA": a, "BBC": b}, MONDAY) == ["AAA", "BBC"]    # two bars of b left in the window


def test_rule_needs_the_sunday_the_bar_21_days_back_and_60_bars() -> None:
    rng = np.random.default_rng(6)
    full = bars_of(rng)
    no_sunday = {k: v for k, v in bars_of(rng).items() if k != SUNDAY}
    no_back = {k: v for k, v in bars_of(rng).items() if k != SUNDAY - timedelta(days=21)}
    short = bars_of(rng, days=59)
    enough = bars_of(rng, days=60)
    assert universe.rule_list({"AAA": full, "CCC": no_sunday, "DDD": no_back, "EEE": short, "FFF": enough}, MONDAY) == sorted(
        ["AAA", "FFF"], key=lambda c: -sum({"AAA": full, "FFF": enough}[c][SUNDAY - timedelta(days=k)][1] for k in range(28))
    )


def test_rule_counts_only_bars_up_to_the_sunday() -> None:
    rng = np.random.default_rng(7)
    bars = bars_of(rng, days=59)
    later = dict(bars)
    later[MONDAY] = (1.0, 1e12)
    later[MONDAY + timedelta(days=1)] = (1.0, 1e12)
    assert universe.rule_list({"AAA": later}, MONDAY) == []


def test_rule_drops_a_coin_whose_closes_did_not_move() -> None:
    rng = np.random.default_rng(8)
    stable = {SUNDAY - timedelta(days=k): (1.0 + (k % 2) * 1e-4, 1e9) for k in range(90)}
    assert universe.rule_list({"AAA": bars_of(rng), "PEG": stable}, MONDAY) == ["AAA"]


@pytest.mark.parametrize("swing,kept", [(.4, False), (.6, True)])
def test_rule_keeps_closes_that_moved_half_a_percent(swing: float, kept: bool) -> None:
    bars = {SUNDAY - timedelta(days=k): (100.0 + swing * (-1) ** k, 1e6) for k in range(90)}     # 14 up, 14 down: sd over mean is swing percent
    assert universe.rule_list({"AAA": bars}, MONDAY) == (["AAA"] if kept else [])


def test_rule_never_ranks_btc() -> None:
    rng = np.random.default_rng(9)
    assert universe.rule_list({"BTC": bars_of(rng, qv=1e12), "AAA": bars_of(rng)}, MONDAY) == ["AAA"]


def test_rule_keeps_the_top_n_after_removal_so_names_move_up() -> None:
    rng = np.random.default_rng(10)
    daily = {f"C{i:02d}": bars_of(rng, qv=1e6 * (i + 1)) for i in range(25)}
    daily["BTC"] = bars_of(rng, qv=1e12)
    got = universe.rule_list(daily, MONDAY)
    assert len(got) == 18 and "BTC" not in got and got == plain_rule({k: v for k, v in daily.items() if k != "BTC"}, MONDAY, 18)


@pytest.mark.parametrize("base", ["USDC", "FDUSD", "WBTC", "BETH", "BTCUP", "ETHDOWN", "XBULL", "YBEAR", "AAPLB", "TSLAB"])
def test_name_filters_drop(base: str) -> None:
    assert not universe.name_ok(base)
    rng = np.random.default_rng(11)
    assert universe.rule_list({base: bars_of(rng), "AAA": bars_of(rng)}, MONDAY) == ["AAA"]


@pytest.mark.parametrize("base", ["JUP", "SYRUP", "ARB", "SHIB", "TRB", "CKB", "PHB", "VIB", "DGB", "MOB", "AMB", "AAA"])
def test_name_filters_keep_the_exceptions_and_the_known_coins(base: str) -> None:
    assert universe.name_ok(base)


def test_the_only_known_coin_dropped_from_the_exceptions_is_masked() -> None:
    hits = {"AMB", "ARB", "BNB", "CKB", "DGB", "MOB", "PHB", "SHIB", "TRB", "VIB"}
    assert hits - universe.KNOWN_EXEMPT == {"BNB"} and "BNB" in config.SEALED


def test_luna_on_the_exchange_is_luna2_on_the_panel() -> None:
    rng = np.random.default_rng(12)
    assert universe.rule_list({"LUNA": bars_of(rng)}, MONDAY) == ["LUNA2"]
    assert universe.exchange_base("LUNA2") == "LUNA" and universe.exchange_base("AAA") == "AAA"


def test_tradable_bases_read_trading_usdt_spot_pairs_only() -> None:
    def row(base, status="TRADING", quote="USDT", spot=True):
        return {"baseAsset": base, "status": status, "quoteAsset": quote, "isSpotTradingAllowed": spot}

    info = {"symbols": [row("AAA"), row("CCC", status="BREAK"), row("DDD", quote="BTC"), row("EEE", spot=False), row("BTC"), row("USDC"), row("ARB"), row("BTCUP")]}
    assert universe.tradable_bases(info) == ["AAA", "ARB"]


def test_trading_bases_keep_btc_and_filtered_names_and_never_a_masked_coin() -> None:
    def row(base, status="TRADING"):
        return {"baseAsset": base, "status": status, "quoteAsset": "USDT", "isSpotTradingAllowed": True}

    info = {"symbols": [row("AAA"), row("CCC", status="BREAK"), row("BTC"), row("USDC"), row("ETH")]}
    assert universe.trading_bases(info) == {"AAA", "BTC", "USDC"}


@pytest.mark.parametrize("write", [lambda root: universe.store_list("2026-08-10", ["AAA"], root), lambda root: universe.store_frozen({"2026-08-03": ["AAA"]}, root)])
def test_a_list_file_is_written_whole_or_not_at_all(tmp_path, monkeypatch, write) -> None:
    def dies(a, b):
        raise OSError("disk full")

    monkeypatch.setattr(config.os, "replace", dies)
    with pytest.raises(OSError):
        write(tmp_path)
    assert not list(config.lists_dir(tmp_path).glob("*.json"))


def test_a_rule_list_is_stored_once(tmp_path) -> None:
    assert universe.store_list("2026-08-10", ["AAA", "CCC"], tmp_path) == ["AAA", "CCC"]
    assert universe.store_list("2026-08-10", ["DDD"], tmp_path) == ["AAA", "CCC"]
    assert universe.load_lists(tmp_path) == {"2026-08-10": ["AAA", "CCC"]}


@pytest.mark.parametrize("monday", ["2026-08-03", "2020-07-13"])
def test_a_frozen_week_cannot_be_rewritten(tmp_path, monday: str) -> None:
    with pytest.raises(ValueError):
        universe.store_list(monday, ["AAA"], tmp_path)


def test_a_list_day_must_be_a_monday_and_hold_no_btc(tmp_path) -> None:
    with pytest.raises(ValueError):
        universe.store_list("2026-08-11", ["AAA"], tmp_path)
    with pytest.raises(ValueError):
        universe.store_list("2026-08-10", ["BTC"], tmp_path)


def test_lists_merge_frozen_and_rule_weeks_in_order(tmp_path) -> None:
    universe.store_frozen({"2026-08-03": ["AAA"]}, tmp_path)
    universe.store_list("2026-08-17", ["DDD"], tmp_path)
    universe.store_list("2026-08-10", ["CCC"], tmp_path)
    assert list(universe.load_lists(tmp_path).items()) == [("2026-08-03", ["AAA"]), ("2026-08-10", ["CCC"]), ("2026-08-17", ["DDD"])]


def test_mem_against_a_plain_loop() -> None:
    p = panel_of(market())
    d = lib.Data(p)
    keys = sorted(p.lists)
    for s in range(0, d.S, 5):
        week = max(k for k in keys if ts(k) <= d.t[s])       # the last Monday at or before the stamp
        hit = set(p.lists[week])
        for j, c in enumerate(d.coins):
            traded = np.flatnonzero(~np.isnan(p.closes[:int(d.h[s]), j]))
            fresh = len(traded) > 0 and int(d.h[s]) - 1 - int(traded[-1]) <= 6
            assert d.Mem[s, j] == (c in hit and fresh and c != "BTC"), (s, c)


def test_mem_holds_the_last_list_when_a_monday_has_none() -> None:
    lists = {"2020-03-02": ["AAA"], "2020-03-16": ["CCC"]}
    coins = ["AAA", "CCC"]
    t = np.array([ts("2020-03-02"), ts("2020-03-15T20:00:00"), ts("2020-03-16"), ts("2020-04-20")])
    mem = universe.mem_matrix(lists, coins, t, np.zeros((4, 2), bool))
    assert mem.tolist() == [[True, False], [True, False], [False, True], [False, True]]


def test_mem_needs_a_list() -> None:
    with pytest.raises(ValueError):
        universe.mem_matrix({}, ["AAA"], np.array([0]), np.zeros((1, 1), bool))
