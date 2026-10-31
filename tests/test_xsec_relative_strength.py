"""H-XSEC-01 D1 on synthetic data only: no test here reads market data."""
from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
import hashlib
import json
import math
import random
import sys
import time
from types import SimpleNamespace

import polars as pl
import pyarrow.parquet as pq
import pytest

from trading_agent.backtest import xsec_relative_strength as lane
from trading_agent.backtest.xsec_relative_strength import COSTS, Series

START = date(2023, 1, 1)
MONDAY = date(2023, 1, 23)
ONE_X = COSTS["1x"]


def flat_series(closes, *, hours=range(24)):
    """One close per day from START; every bar of a day trades at that close."""
    daily = {START + timedelta(days=i): close for i, close in enumerate(closes)}
    stamps = [
        datetime(day.year, day.month, day.day, hour, tzinfo=UTC)
        for day in daily for hour in hours
    ]
    prices = [close for close in closes for _ in hours]
    return Series(daily=daily, stamps=stamps, opens=prices, closes=prices)


def stopped_at(series, end, *, last_bar=None):
    """The same coin with no bar after ``end``; ``last_bar`` is that bar's (open, close)."""
    keep = [i for i, stamp in enumerate(series.stamps) if stamp <= end]
    opens, closes = [series.opens[i] for i in keep], [series.closes[i] for i in keep]
    if last_bar:
        opens[-1], closes[-1] = last_bar
    return Series(series.daily, [series.stamps[i] for i in keep], opens, closes)


def market_of(returns: dict[str, float], days=30):
    """Coins with a chosen 21-day return, flat from the signal Sunday on."""
    market = {}
    for symbol, change in returns.items():
        closes = [100.0] * 21 + [100.0 * (1 + change)] * (days - 21)
        market[symbol] = flat_series(closes)
    return market


FOUR_FLAT = {f"C{i}/USDT": 0.0 for i in range(4)}
MARK = datetime(2023, 1, 29, 11, tzinfo=UTC)


def test_the_constants_are_the_frozen_design():
    proposal = lane.ROOT / lane.PROPOSAL
    assert hashlib.sha256(proposal.read_bytes()).hexdigest() == lane.PROPOSAL_SHA256
    days = lane.rebalance_days()
    assert (days[0], days[-1], len(days)) == (date(2023, 1, 23), date(2026, 2, 2), 159)
    assert all(day.weekday() == 0 for day in days)
    assert lane.UNIVERSE == (
        "ADA/USDT", "AVAX/USDT", "BNB/USDT", "BTC/USDT", "DOGE/USDT", "DOT/USDT", "ETH/USDT",
        "LINK/USDT", "NEAR/USDT", "SOL/USDT", "TRX/USDT", "XRP/USDT", "ZEC/USDT",
    )
    assert lane.RANDOM_SEEDS == tuple(range(1, 21))
    assert lane.MIN_TRADE_USDT == 5.0
    assert lane.CUTOFF == datetime(2026, 2, 6, 12, tzinfo=UTC)  # the holdout starts at 16:00
    assert lane.MARK_BAR == datetime(2026, 2, 6, 11, tzinfo=UTC)
    assert {name: (c.fee, c.slippage) for name, c in COSTS.items()} == {
        "1x": (0.0010, 0.0005), "2x": (0.0020, 0.0010), "slip_stress": (0.0010, 0.0025),
    }


def test_ranking_uses_the_21_day_return_to_sunday_with_ties_by_name():
    market = market_of({
        "ZZZ/USDT": 0.30, "AAA/USDT": 0.30, "MMM/USDT": 0.10,
        "BBB/USDT": 0.05, "CCC/USDT": 0.01, "DDD/USDT": -0.20,
    })
    expected = ("AAA/USDT", "ZZZ/USDT", "MMM/USDT", "BBB/USDT")
    assert lane.top_coins(market, sorted(market), MONDAY) == expected
    assert lane.top_coins(market, sorted(market, reverse=True), MONDAY) == expected


def test_ranking_cannot_see_anything_after_sunday():
    market = market_of({f"C{i}/USDT": 0.01 * i for i in range(6)})
    before = lane.top_coins(market, sorted(market), MONDAY)
    loser = market["C0/USDT"]
    daily = dict(loser.daily)
    for day in daily:
        if day >= MONDAY:
            daily[day] = 1_000_000.0
    market["C0/USDT"] = Series(daily, loser.stamps, loser.opens, loser.closes)
    assert lane.top_coins(market, sorted(market), MONDAY) == before


@pytest.mark.parametrize("days_before_monday", [1, 22])  # both ends of the lookback
@pytest.mark.parametrize("close", [float("nan"), 0.0, -1.0])
def test_an_unusable_ranking_close_makes_the_run_invalid(close, days_before_monday):
    market = market_of(FOUR_FLAT)
    series = market["C0/USDT"]
    market["C0/USDT"] = Series({**series.daily, MONDAY - timedelta(days=days_before_monday): close},
                               series.stamps, series.opens, series.closes)
    with pytest.raises(lane.InvalidRun):
        lane.top_coins(market, sorted(market), MONDAY)


@pytest.mark.parametrize("days_before_monday", [1, 22])  # both ends of the lookback
def test_a_missing_ranking_close_is_never_filled_in(days_before_monday):
    market = market_of(FOUR_FLAT)
    series = market["C0/USDT"]
    daily = {day: close for day, close in series.daily.items()
             if day != MONDAY - timedelta(days=days_before_monday)}
    market["C0/USDT"] = Series(daily, series.stamps, series.opens, series.closes)
    with pytest.raises(lane.InvalidRun, match="missing"):
        lane.top_coins(market, sorted(market), MONDAY)


def one_week(market, picks=None, cost=ONE_X):
    steps = lane.plan_steps(market, [MONDAY], mark_bar=MARK)
    chosen = picks or [steps[0].ranked]
    return lane.simulate(market, steps, chosen, cost, arm="candidate", cost_name="1x", mark_bar=MARK)


def test_the_fill_is_the_open_of_the_0100_bar():
    market = market_of(FOUR_FLAT)
    series = market["C0/USDT"]
    opens = [
        777.0 if stamp == datetime(2023, 1, 23, 1, tzinfo=UTC) else price
        for stamp, price in zip(series.stamps, series.opens)
    ]
    market["C0/USDT"] = Series(series.daily, series.stamps, opens, series.closes)
    fill = next(f for f in one_week(market).fills if f.symbol == "C0/USDT")
    assert fill.reference == 777.0
    assert fill.at == "2023-01-23T01:00:00+00:00"


def test_a_missing_fill_bar_uses_the_next_bar_of_that_coin():
    market = market_of(FOUR_FLAT)
    series = market["C0/USDT"]
    gap = datetime(2023, 1, 23, 1, tzinfo=UTC)
    keep = [i for i, stamp in enumerate(series.stamps) if stamp != gap]
    opens = [series.opens[i] + (5.0 if series.stamps[i] == gap + timedelta(hours=1) else 0.0) for i in keep]
    market["C0/USDT"] = Series(series.daily, [series.stamps[i] for i in keep], opens,
                               [series.closes[i] for i in keep])
    fill = next(f for f in one_week(market).fills if f.symbol == "C0/USDT")
    assert fill.reference == 105.0


def test_one_week_of_costs_by_hand():
    result = one_week(market_of(FOUR_FLAT))
    quantity = 625.0 / 100.05  # 6.25 % of 10 000 at 100 plus 5 bps
    assert len(result.fills) == 4
    assert all(f.side == "BUY" and f.quantity == pytest.approx(quantity) for f in result.fills)
    assert all(f.fee == pytest.approx(0.625) for f in result.fills)
    expected = 10_000.0 - 4 * (625.0 + 0.625) + 4 * quantity * 100.0
    assert result.final_equity == pytest.approx(expected)


@pytest.mark.parametrize("name,bought_at,sold_at,fee", [
    ("1x", 100.05, 99.95, 0.0010),
    ("2x", 100.10, 99.90, 0.0020),
    ("slip_stress", 100.25, 99.75, 0.0010),
])
def test_a_full_rotation_costs_both_sides_at_each_rate(name, bought_at, sold_at, fee):
    closes = {f"C{i}/USDT": [100.0] * 40 for i in range(8)}
    market = {symbol: flat_series(prices) for symbol, prices in closes.items()}
    mondays = [MONDAY, MONDAY + timedelta(weeks=1)]
    mark = datetime(2023, 2, 5, 11, tzinfo=UTC)
    steps = lane.plan_steps(market, mondays, mark_bar=mark)
    first = tuple(f"C{i}/USDT" for i in range(4))
    second = tuple(f"C{i}/USDT" for i in range(4, 8))
    result = lane.simulate(market, steps, [first, second], COSTS[name], arm="a", cost_name=name,
                           mark_bar=mark)
    bought = [f for f in result.fills if f.side == "BUY"]
    sold = [f for f in result.fills if f.side == "SELL"]
    assert {f.symbol for f in sold} == set(first)
    assert all(f.price == pytest.approx(bought_at) for f in bought)
    assert all(f.price == pytest.approx(sold_at) for f in sold)
    assert all(f.fee == pytest.approx(f.quantity * f.price * fee) for f in result.fills)
    paid = sum(f.quantity * f.price * (1 + fee) for f in bought)
    received = sum(f.quantity * f.price * (1 - fee) for f in sold)
    marked = sum(quantity * 100.0 for quantity in result.positions.values())
    assert result.final_equity == pytest.approx(10_000.0 - paid + received + marked)
    lane.reconcile(result)


@pytest.mark.parametrize("later,trades_again", [
    (101.06, False),  # 4.89 USDT over target
    (101.11, True),  # 5.12 USDT over target
    (98.97, False),  # 4.90 USDT under target
    (98.93, True),  # 5.09 USDT under target
])
def test_a_change_under_five_usdt_is_not_traded(later, trades_again):
    prices = {f"C{i}/USDT": [100.0] * 29 + [later] * 11 for i in range(4)}
    market = {symbol: flat_series(p) for symbol, p in prices.items()}
    mondays = [MONDAY, MONDAY + timedelta(weeks=1)]
    mark = datetime(2023, 2, 5, 11, tzinfo=UTC)
    steps = lane.plan_steps(market, mondays, mark_bar=mark)
    picks = [steps[0].ranked, steps[0].ranked]
    result = lane.simulate(market, steps, picks, ONE_X, arm="a", cost_name="1x", mark_bar=mark)
    second = [f for f in result.fills if f.at == "2023-01-30T01:00:00+00:00"]
    assert bool(second) is trades_again
    if trades_again:
        assert {f.side for f in second} == {"SELL" if later > 100 else "BUY"}


@pytest.mark.parametrize("picked", ["ranked", "universe"])
def test_exposure_returns_to_25_percent_at_every_rebalance(picked):
    rng = random.Random(1)
    market = {}
    for i in range(6):
        level, closes = 100.0, []
        for _ in range(120):
            level *= math.exp(rng.gauss(0, 0.03))
            closes.append(level)
        market[f"C{i}/USDT"] = flat_series(closes)
    market["C5/USDT"] = stopped_at(market["C5/USDT"], datetime(2023, 3, 1, 23, tzinfo=UTC))
    mondays = [MONDAY + timedelta(weeks=k) for k in range(12)]
    mark = datetime(2023, 4, 20, 11, tzinfo=UTC)
    steps = lane.plan_steps(market, mondays, mark_bar=mark)
    assert [len(s.universe) for s in steps] == [6] * 6 + [5] * 6
    result = lane.simulate(market, steps, [getattr(s, picked) for s in steps], ONE_X, arm="a",
                           cost_name="1x", mark_bar=mark)
    times = sorted({f.at for f in result.fills})
    assert times == [f"{monday.isoformat()}T01:00:00+00:00" for monday in mondays]
    cash, held = 10_000.0, {}
    for at in times:
        for fill in (f for f in result.fills if f.at == at):
            cash += lane._cash_change(fill)
            held[fill.symbol] = held.get(fill.symbol, 0.0) + (
                fill.quantity if fill.side == "BUY" else -fill.quantity)
        stamp = datetime.fromisoformat(at)
        prices = {s: market[s].first_open(stamp, stamp) for s in held}
        invested = sum(held[s] * prices[s] for s in held if held[s] > 1e-9)
        assert invested / (cash + invested) == pytest.approx(0.25, abs=0.002), at


def test_controls_are_drawn_in_time_order_and_shared_by_every_cost():
    market = {f"C{i:02d}/USDT": flat_series([100.0] * 60) for i in range(13)}
    mondays = [MONDAY + timedelta(weeks=k) for k in range(4)]
    mark = datetime(2023, 2, 20, 11, tzinfo=UTC)
    steps = lane.plan_steps(market, mondays, mark_bar=mark)
    picks = lane.arm_picks(steps)
    expected = random.Random(7)
    names = sorted(market)
    assert picks["random_07"] == [tuple(sorted(expected.sample(names, 4))) for _ in steps]
    assert set(picks) == {"candidate", "basket", *lane.RANDOM_ARMS}
    assert picks["basket"][0] == tuple(names)
    results = lane.run_arms(market, steps, mark_bar=mark)
    assert set(results) == set(COSTS)
    for arm in picks:
        fills = {cost: [(f.at, f.symbol) for f in results[cost][arm].fills] for cost in COSTS}
        assert fills["1x"] == fills["2x"] == fills["slip_stress"]
        for cost, rate in COSTS.items():
            for fill in results[cost][arm].fills:
                slippage = rate.slippage if fill.side == "BUY" else -rate.slippage
                assert fill.price == pytest.approx(fill.reference * (1 + slippage))
                assert fill.fee == pytest.approx(fill.quantity * fill.price * rate.fee)


def test_a_coin_that_stops_trading_leaves_every_arm_at_its_last_price():
    market = {f"C{i}/USDT": flat_series([100.0] * 40) for i in range(5)}
    market["C0/USDT"] = stopped_at(market["C0/USDT"], datetime(2023, 1, 25, 23, tzinfo=UTC),
                                   last_bar=(90.0, 88.0))
    mondays = [MONDAY, MONDAY + timedelta(weeks=1)]
    mark = datetime(2023, 2, 5, 11, tzinfo=UTC)
    steps = lane.plan_steps(market, mondays, mark_bar=mark)
    assert "C0/USDT" in steps[0].universe and "C0/USDT" not in steps[1].universe
    arms = lane.run_arms(market, steps, mark_bar=mark)["1x"]
    first_week = [f for f in arms["basket"].fills if f.at == "2023-01-23T01:00:00+00:00"]
    assert len(first_week) == 5 and all(f.notional == pytest.approx(500.0) for f in first_week)
    held_it = [r for r in arms.values() if any(f.symbol == "C0/USDT" for f in r.fills)]
    assert {"candidate", "basket"} <= {r.arm for r in held_it}
    for result in held_it:
        closed = [f for f in result.fills if f.symbol == "C0/USDT" and f.side == "SELL"]
        assert len(closed) == 1 and closed[0].reference == 88.0 and closed[0].fee > 0
        assert "C0/USDT" not in result.positions


@pytest.mark.parametrize("hours_before_the_mark,exits", [(25, True), (24, False), (23, False)])
def test_only_a_coin_gone_over_24_hours_exits_at_the_mark(hours_before_the_mark, exits):
    market = market_of(FOUR_FLAT)
    market["C0/USDT"] = stopped_at(market["C0/USDT"], MARK - timedelta(hours=hours_before_the_mark),
                                   last_bar=(90.0, 88.0))
    result = one_week(market)
    sold = [f for f in result.fills if f.side == "SELL"]
    if exits:
        assert [(f.at, f.symbol, f.reference) for f in sold] == [(MARK.isoformat(), "C0/USDT", 88.0)]
        assert sold[0].fee == pytest.approx(sold[0].quantity * 88.0 * 0.9995 * 0.001)
        assert "C0/USDT" not in result.positions and "C0/USDT" not in result.marks
    else:
        assert not sold and result.marks["C0/USDT"] == 88.0
    lane.reconcile(result)


def test_the_final_mark_is_the_close_of_the_mark_bar():
    market = market_of(FOUR_FLAT)
    series = market["C0/USDT"]
    index = series.stamps.index(MARK)
    opens, closes = list(series.opens), list(series.closes)
    opens[index], closes[index], closes[index + 1] = 70.0, 80.0, 90.0
    market["C0/USDT"] = Series(series.daily, series.stamps, opens, closes)
    result = one_week(market)
    assert result.marks == {**dict.fromkeys(FOUR_FLAT, 100.0), "C0/USDT": 80.0}
    quantity = result.positions["C0/USDT"]
    assert result.final_equity == pytest.approx(one_week(market_of(FOUR_FLAT)).final_equity - quantity * 20.0)


def test_a_mark_before_any_bar_makes_the_run_invalid():
    with pytest.raises(lane.InvalidRun, match="no 1h bar"):
        flat_series([100.0]).last_close(datetime(2022, 12, 31, 23, tzinfo=UTC))


def test_fewer_than_four_coins_makes_the_run_invalid():
    market = {f"C{i}/USDT": flat_series([100.0] * 25) for i in range(3)}
    with pytest.raises(lane.InvalidRun, match="fewer than 4"):
        lane.plan_steps(market, [MONDAY], mark_bar=MARK)


def test_reconcile_catches_a_ledger_that_does_not_add_up():
    result = one_week(market_of(FOUR_FLAT))
    lane.reconcile(result)
    tampered = lane.ArmResult(result.arm, result.cost, result.final_equity + 1.0,
                              result.positions, result.marks, result.fills)
    with pytest.raises(ValueError, match="equity"):
        lane.reconcile(tampered)
    missing = lane.ArmResult(result.arm, result.cost, result.final_equity,
                             result.positions, result.marks, result.fills[1:])
    with pytest.raises(ValueError):
        lane.reconcile(missing)
    oversold = result.fills + (
        lane._fill(datetime(2023, 1, 24, tzinfo=UTC), "C0/USDT", "SELL", 1e6, 100.0, ONE_X),)
    with pytest.raises(ValueError, match="sold more"):
        lane.reconcile(lane.ArmResult(result.arm, result.cost, result.final_equity,
                                      result.positions, result.marks, oversold))
    positions = {**result.positions, "C0/USDT": result.positions["C0/USDT"] + 1.0}
    with pytest.raises(ValueError, match="quantity"):
        lane.reconcile(lane.ArmResult(result.arm, result.cost, result.final_equity,
                                      positions, result.marks, result.fills))


def test_every_arm_is_reconciled_before_it_counts(monkeypatch):
    market = market_of(FOUR_FLAT)
    steps = lane.plan_steps(market, [MONDAY], mark_bar=MARK)
    honest = lane.simulate

    def inflated(*args, **kwargs):
        result = honest(*args, **kwargs)
        if (result.arm, result.cost) != (lane.RANDOM_ARMS[-1], "slip_stress"):
            return result
        return lane.ArmResult(result.arm, result.cost, result.final_equity + 100.0,
                              result.positions, result.marks, result.fills)

    monkeypatch.setattr(lane, "simulate", inflated)
    with pytest.raises(ValueError, match="equity"):
        lane.run_arms(market, steps, mark_bar=MARK)


def finals(candidate, basket=10_000.0, randoms=None, by_cost=None):
    """Same arms at every cost unless a mapping is given; ``by_cost`` holds per-cost randoms."""
    row = {"basket": basket, **{arm: 9_000.0 for arm in lane.RANDOM_ARMS}}
    if randoms:
        row.update(randoms)
    return {cost: {**row, **(by_cost or {}).get(cost, {}),
                   "candidate": candidate[cost] if isinstance(candidate, dict) else candidate}
            for cost in COSTS}


def beats(count):
    """Random arms arranged so that the candidate at 10 100 beats exactly ``count``."""
    return {arm: (9_000.0 if index < count else 11_000.0)
            for index, arm in enumerate(lane.RANDOM_ARMS)}


@pytest.mark.parametrize("scenario,expected", [
    (finals(10_100.0), "CONTINUE_TO_CONFIRMATION"),
    (finals(10_100.0, randoms=beats(18)), "CONTINUE_TO_CONFIRMATION"),
    (finals(10_100.0, randoms=beats(17)), "INCONCLUSIVE"),  # one control short
    (finals(10_100.0, randoms=beats(5)), "REJECT"),  # below the median at 1x
    (finals({"1x": 10_100.0, "2x": 10_000.0, "slip_stress": 10_100.0}), "REJECT"),  # excess 0
    (finals({"1x": 10_100.0, "2x": 9_900.0, "slip_stress": 10_100.0}), "REJECT"),
    (finals({"1x": 10_100.0, "2x": 10_100.0, "slip_stress": 9_900.0}), "INCONCLUSIVE"),
    (finals({"1x": 9_990.0, "2x": 10_100.0, "slip_stress": 10_100.0}), "INCONCLUSIVE"),
    (finals({"1x": 10_000.0, "2x": 10_100.0, "slip_stress": 10_100.0}), "INCONCLUSIVE"),  # excess 0
    (finals(10_000.0, basket=9_900.0, randoms=beats(10)), "INCONCLUSIVE"),  # on the median, not below
    (finals(10_100.0, randoms=dict.fromkeys(lane.RANDOM_ARMS[:3], 10_100.0)), "INCONCLUSIVE"),  # a tie loses
    (finals(10_100.0, by_cost={"1x": beats(20), "2x": beats(17)}), "INCONCLUSIVE"),  # 18 counts at 2x
    (finals(10_100.0, by_cost={"1x": beats(5), "2x": beats(20)}), "REJECT"),  # the median is at 1x
])
def test_the_frozen_decision_rule(scenario, expected):
    assert lane.decide(scenario)["outcome"] == expected


def planted_market(kind):
    """Thirteen coins over the whole D1 window with a known ranking effect."""
    rng = random.Random(11)
    days = (lane.MARK_BAR.date() - START).days + 1
    levels = {symbol: 100.0 for symbol in lane.UNIVERSE}
    closes = {symbol: [] for symbol in lane.UNIVERSE}
    order = list(lane.UNIVERSE)
    for day in range(days):
        if day % 42 == 0:  # new leaders every six weeks
            rng.shuffle(order)
        for rank, symbol in enumerate(order):
            drift = (0.012 if rank < 4 else -0.004)
            if kind == "reversal" and (day // 21) % 2 == 1:
                drift = -drift * 1.6
            levels[symbol] *= math.exp(drift + rng.gauss(0, 0.002))
            closes[symbol].append(levels[symbol])
    return {symbol: flat_series(values, hours=(1, 11)) for symbol, values in closes.items()}


def measure(market):
    steps = lane.plan_steps(market)
    results = lane.run_arms(market, steps)
    table = {cost: {arm: r.final_equity for arm, r in arms.items()} for cost, arms in results.items()}
    return lane.decide(table)


def test_a_planted_momentum_effect_is_found():
    assert measure(planted_market("momentum"))["outcome"] == "CONTINUE_TO_CONFIRMATION"


def test_a_planted_reversal_effect_is_rejected():
    assert measure(planted_market("reversal"))["outcome"] == "REJECT"


def test_a_shifted_daily_label_is_refused():
    market = market_of(FOUR_FLAT)
    series = market["C0/USDT"]
    moved = {day: close * 1.01 if day == MONDAY - timedelta(days=1) else close
             for day, close in series.daily.items()}
    market["C0/USDT"] = Series(moved, series.stamps, series.opens, series.closes)
    with pytest.raises(lane.InvalidRun, match="23:00"):
        lane.require_daily_alignment(market, [MONDAY])
    lane.require_daily_alignment(market_of(FOUR_FLAT), [MONDAY])


def test_the_start_of_the_lookback_is_aligned_too():
    market = market_of(FOUR_FLAT)
    series = market["C0/USDT"]
    moved = {day: close * 1.01 if day == date(2023, 1, 1) else close
             for day, close in series.daily.items()}
    market["C0/USDT"] = Series(moved, series.stamps, series.opens, series.closes)
    with pytest.raises(lane.InvalidRun, match="2023-01-01"):
        lane.require_daily_alignment(market, [MONDAY])


def test_a_missing_2300_bar_has_nothing_to_compare():
    market = market_of(FOUR_FLAT)
    series = market["C0/USDT"]
    moved = Series({**series.daily, MONDAY - timedelta(days=1): 123.0},
                   series.stamps, series.opens, series.closes)
    hole = series.stamps.index(datetime(2023, 1, 22, 23, tzinfo=UTC))
    market["C0/USDT"] = Series(moved.daily, moved.stamps[:hole] + moved.stamps[hole + 1:],
                               moved.opens[:hole] + moved.opens[hole + 1:],
                               moved.closes[:hole] + moved.closes[hole + 1:])
    lane.require_daily_alignment(market, [MONDAY])
    market["C0/USDT"] = stopped_at(moved, datetime(2023, 1, 22, 22, tzinfo=UTC))
    lane.require_daily_alignment(market, [MONDAY])


POISON = 1e12


def write_coin(root, symbol, *, shift=0.0, half_hours=False):
    """Bars through the holdout, priced absurdly from the cutoff on."""
    folder = root / lane.RAW_DATA / symbol.replace("/", "_")
    folder.mkdir(parents=True)
    first = datetime(2023, 1, 1)
    hours = int((datetime(2026, 2, 10) - first).total_seconds() // 3600)
    stamps = [first + timedelta(hours=k) for k in range(hours)]
    cutoff = datetime(2026, 2, 6, 12)  # written out: the poison must not move with lane.CUTOFF
    level = 100.0 + shift
    rng = random.Random(symbol)
    prices = []
    for stamp in stamps:
        if stamp.hour == 0:
            level *= math.exp(rng.gauss(0, 0.01))
        prices.append(POISON if stamp >= cutoff else level)
    pl.DataFrame({"timestamp": stamps, "open": prices, "close": prices}).write_parquet(folder / "1h.parquet")
    days = sorted({stamp.date() for stamp in stamps})
    closes = {stamp.date(): price for stamp, price in zip(stamps, prices)}
    pl.DataFrame({"date": days, "close": [closes[day] for day in days]}).write_parquet(folder / "1d.parquet")
    if half_hours:
        stamps30 = [stamp + timedelta(minutes=m) for stamp in stamps for m in (0, 30)]
        prices30 = [price for price in prices for _ in (0, 30)]
        pl.DataFrame({"timestamp": stamps30, "open": prices30, "close": prices30}).write_parquet(
            folder / "30m.parquet")


@pytest.fixture
def repo(tmp_path, monkeypatch):
    for index, symbol in enumerate(lane.UNIVERSE):
        write_coin(tmp_path, symbol, shift=float(index), half_hours=symbol == "BTC/USDT")
    design = tmp_path / "design.md"
    design.write_bytes(b"synthetic-fixture-design")
    datasets = {}
    for symbol in lane.UNIVERSE:
        key = symbol.replace("/", "_")
        for timeframe in ("1d", "1h", "30m"):
            if timeframe == "30m" and symbol != "BTC/USDT":
                continue
            raw = (tmp_path / lane.RAW_DATA / key / f"{timeframe}.parquet").read_bytes()
            datasets[f"{key}/{timeframe}"] = {"sha256": hashlib.sha256(raw).hexdigest()}
    (tmp_path / "manifest.json").write_text(json.dumps({"datasets": datasets}))
    monkeypatch.setattr(lane, "ROOT", tmp_path)
    monkeypatch.setattr(lane, "PROPOSAL", "design.md")
    monkeypatch.setattr(lane, "PROPOSAL_SHA256", hashlib.sha256(b"synthetic-fixture-design").hexdigest())
    monkeypatch.setattr(lane, "RESEARCH_MANIFEST", "manifest.json")
    monkeypatch.setattr(lane, "capture_campaign_source", lambda root: {"tree_fingerprint": "synthetic"})
    return tmp_path


def approve(path):
    plan = json.loads(path.read_bytes())
    plan["approval"] = {"approval_state": "APPROVED", "approved_by": "synthetic fixture",
                        "approved_at": "2026-10-30T00:00:00+00:00",
                        "approved_scope": plan["expected_approved_scope"]}
    plan["market_run_authorized"] = True
    reseal(path, plan)


def reseal(path, plan):
    plan["fingerprint"] = lane.digest({k: v for k, v in plan.items() if k != "fingerprint"})
    path.write_text(json.dumps(plan))
    (path.parent / "binding.json").write_text(
        json.dumps({"protocol_sha256": hashlib.sha256(path.read_bytes()).hexdigest()}))


def test_no_row_at_or_after_the_cutoff_leaves_the_loader(repo):
    series = lane.load_series("BTC/USDT")
    assert series.stamps[-1] == lane.MARK_BAR
    assert max(series.closes) < POISON and max(series.daily.values()) < POISON
    assert max(series.daily) == date(2026, 2, 5)


def raw_file(repo, symbol, timeframe):
    return repo / lane.RAW_DATA / symbol.replace("/", "_") / f"{timeframe}.parquet"


def timestamp_only(path, stamp=pl.col("date").cast(pl.Datetime("us"))):
    """A daily file as the files that were never rewritten store it."""
    pl.read_parquet(path).select(stamp.alias("timestamp"), "close").write_parquet(path)


@pytest.mark.parametrize("stamp", [
    pl.col("date").cast(pl.Datetime("us")),
    pl.col("date").cast(pl.Datetime("ms")),
    pl.col("date").cast(pl.Datetime("ns")),
    pl.col("date").cast(pl.Datetime("us")) + timedelta(hours=23, minutes=59, seconds=59),
    pl.col("date").cast(pl.Datetime("us")).dt.replace_time_zone("UTC"),
], ids=["us", "ms", "ns", "end_of_day", "utc"])
def test_a_daily_file_with_only_a_timestamp_loads_the_same(repo, stamp):
    before = lane.load_series("BTC/USDT")
    proof_rows = lane._daily_bars("BTC/USDT")
    timestamp_only(raw_file(repo, "BTC/USDT", "1d"), stamp)
    assert lane.load_series("BTC/USDT").daily == before.daily
    assert lane._daily_bars("BTC/USDT").equals(proof_rows)


def disturb(path, stamp_column, index, kind):
    """Break the stamp order at one row of a file; every other stamp stays put."""
    frame = pl.read_parquet(path)
    stamps = frame[stamp_column].to_list()
    if kind == "swapped":
        stamps[index], stamps[index + 1] = stamps[index + 1], stamps[index]
    else:
        stamps[index + 1] = stamps[index]
    frame.with_columns(pl.Series(stamp_column, stamps)).write_parquet(path)


@pytest.mark.parametrize("kind", ["swapped", "duplicated"])
@pytest.mark.parametrize("timeframe,stamp_column", [("1h", "timestamp"), ("1d", "date")])
def test_an_unsorted_input_is_refused(repo, timeframe, stamp_column, kind):
    disturb(raw_file(repo, "BTC/USDT", timeframe), stamp_column, 5, kind)
    with pytest.raises(lane.InvalidRun, match="strictly increasing"):
        lane.load_series("BTC/USDT")


@pytest.mark.parametrize("kind", ["swapped", "duplicated"])
def test_a_disordered_row_after_the_cutoff_changes_nothing(repo, kind):
    before = lane.load_series("BTC/USDT")
    disturb(raw_file(repo, "BTC/USDT", "1h"), "timestamp", -3, kind)
    disturb(raw_file(repo, "BTC/USDT", "1d"), "date", -3, kind)
    assert lane.load_series("BTC/USDT") == before


@pytest.mark.parametrize("timeframe", ["1h", "1d"])
def test_a_file_with_no_row_before_the_cutoff_is_refused(repo, timeframe):
    path = raw_file(repo, "BTC/USDT", timeframe)
    pl.read_parquet(path).tail(3).write_parquet(path)
    with pytest.raises(lane.InvalidRun, match="strictly increasing"):
        lane.load_series("BTC/USDT")


def test_the_loader_takes_no_cutoff(repo):
    with pytest.raises(TypeError):
        lane.load_series("BTC/USDT", cutoff=datetime(2026, 2, 9, tzinfo=UTC))


def break_rows_after(path, kept):
    """Move every row after the first ``kept`` to a row group that no reader can decode."""
    pl.read_parquet(path).write_parquet(path, row_group_size=kept)
    sealed = pq.ParquetFile(path).metadata.row_group(1)
    raw = bytearray(path.read_bytes())
    for index in range(sealed.num_columns):
        chunk = sealed.column(index)
        start = chunk.dictionary_page_offset or chunk.data_page_offset
        raw[start:start + chunk.total_compressed_size] = b"\xff" * chunk.total_compressed_size
    path.write_bytes(raw)


@pytest.mark.parametrize("daily_column", ["date", "timestamp"])
def test_a_row_group_after_the_cutoff_is_never_decoded(repo, daily_column):
    hourly, daily = raw_file(repo, "BTC/USDT", "1h"), raw_file(repo, "BTC/USDT", "1d")
    if daily_column == "timestamp":
        timestamp_only(daily)
    before = lane.load_series("BTC/USDT")
    break_rows_after(hourly, len(before.stamps))
    break_rows_after(daily, len(before.daily))
    for path in (hourly, daily):
        with pytest.raises(pl.exceptions.ComputeError):
            pl.read_parquet(path)
    assert lane.load_series("BTC/USDT") == before
    assert lane._bars("BTC/USDT", "1h", "close")["close"].to_list() == before.closes
    assert dict(lane._daily_bars("BTC/USDT").iter_rows()) == before.daily


def test_a_pending_packet_cannot_run(repo):
    path = lane.freeze(repo / "packet")
    assert json.loads(path.read_bytes())["market_run_authorized"] is False
    with pytest.raises(PermissionError, match="NOT_AUTHORIZED_TO_RUN"):
        lane.run(path)
    assert not (path.parent / "execution_started.json").exists()


def test_an_approved_packet_runs_once_and_never_reads_the_poison(repo):
    path = lane.freeze(repo / "packet")
    approve(path)
    assert lane.run(path) == 0
    summary = json.loads((path.parent / "summary.json").read_bytes())
    assert summary["status"] == "COMPLETED" and summary["rebalances"] == 159
    assert summary["qualified"] is False and summary["live_authorized"] is False
    assert summary["newest_bar_read"] == lane.MARK_BAR.isoformat()
    assert summary["outcome"] == "REJECT"
    assert all(0 < equity < 100_000 for row in summary["finals"].values() for equity in row.values())
    ledger = json.loads((path.parent / "ledger.json").read_bytes())
    assert set(ledger) == set(COSTS) and len(ledger["1x"]) == 22
    assert max(fill["reference"] for fill in ledger["1x"]["basket"]) < POISON
    with pytest.raises(ValueError, match="no resume"):
        lane.run(path)


def test_an_invalid_run_is_recorded_not_scored(repo, monkeypatch):
    path = lane.freeze(repo / "packet")
    approve(path)
    load = lane.load_series

    def without_a_ranking_close(symbol):
        series = load(symbol)
        daily = {day: close for day, close in series.daily.items() if day != date(2023, 1, 1)}
        return Series(daily, series.stamps, series.opens, series.closes)

    monkeypatch.setattr(lane, "load_series", without_a_ranking_close)
    assert lane.run(path) == 2
    summary = json.loads((path.parent / "summary.json").read_bytes())
    assert summary["status"] == "INVALID" and "outcome" not in summary
    assert not (path.parent / "ledger.json").exists()


def test_a_shifted_daily_close_makes_a_run_invalid(repo, monkeypatch):
    path = lane.freeze(repo / "packet")
    approve(path)
    load = lane.load_series

    def with_a_shifted_close(symbol):
        series = load(symbol)
        sunday = MONDAY - timedelta(days=1)
        daily = {**series.daily, sunday: series.daily[sunday] * 1.01}
        return Series(daily, series.stamps, series.opens, series.closes)

    monkeypatch.setattr(lane, "load_series", with_a_shifted_close)
    assert lane.run(path) == 2
    summary = json.loads((path.parent / "summary.json").read_bytes())
    assert summary["status"] == "INVALID" and "23:00" in summary["reason"]
    assert not (path.parent / "ledger.json").exists()


def test_a_bar_at_the_cutoff_makes_a_run_invalid(repo, monkeypatch):
    path = lane.freeze(repo / "packet")
    approve(path)
    load = lane.load_series

    def with_a_sealed_bar(symbol):
        series = load(symbol)
        return Series(series.daily, [*series.stamps, lane.CUTOFF], [*series.opens, 1.0],
                      [*series.closes, 1.0])

    monkeypatch.setattr(lane, "load_series", with_a_sealed_bar)
    assert lane.run(path) == 2
    summary = json.loads((path.parent / "summary.json").read_bytes())
    assert summary["status"] == "INVALID" and "cutoff" in summary["reason"]
    assert not (path.parent / "ledger.json").exists()


def test_a_run_past_its_deadline_is_incomplete_but_keeps_its_outcome(repo, monkeypatch):
    path = lane.freeze(repo / "packet")
    approve(path)
    clock = iter([0.0, 601.0])
    monkeypatch.setattr(lane, "time", SimpleNamespace(monotonic=lambda: next(clock), time=time.time))
    assert lane.run(path) == 2
    summary = json.loads((path.parent / "summary.json").read_bytes())
    assert summary["status"] == "INCOMPLETE"
    assert summary["budget_reason"] == "TOTAL_DEADLINE_EXCEEDED"
    assert "outcome" in summary  # the status says not to use it; the number stays on record


@pytest.mark.parametrize("change", ["input", "proposal", "source", "spec", "unapproved_scope"])
def test_a_changed_packet_is_refused(repo, monkeypatch, change):
    path = lane.freeze(repo / "packet")
    approve(path)
    if change == "input":
        write = repo / lane.RAW_DATA / "BTC_USDT" / "1d.parquet"
        write.write_bytes(write.read_bytes() + b"x")
    elif change == "proposal":
        (repo / "design.md").write_bytes(b"edited")
    elif change == "source":
        monkeypatch.setattr(lane, "capture_campaign_source", lambda root: {"tree_fingerprint": "other"})
    elif change == "spec":
        monkeypatch.setattr(lane, "TOP_K", 5)
    else:
        plan = json.loads(path.read_bytes())
        plan["approval"]["approved_scope"] = "something-else"
        reseal(path, plan)
    with pytest.raises(ValueError):
        lane.verify(path)


def test_an_edited_protocol_without_a_new_seal_is_refused(repo):
    path = lane.freeze(repo / "packet")
    plan = json.loads(path.read_bytes())
    plan["specification"]["top_k"] = 5
    path.write_text(json.dumps(plan))
    with pytest.raises(ValueError, match="fingerprint"):
        lane.verify(path, approved=False)


def test_a_changed_design_cannot_be_frozen(repo):
    (repo / "design.md").write_bytes(b"edited")
    with pytest.raises(ValueError, match="proposal differs"):
        lane.freeze(repo / "packet")
    assert not (repo / "packet").exists()


def test_a_tampered_binding_is_refused(repo):
    path = lane.freeze(repo / "packet")
    (path.parent / "binding.json").write_text(json.dumps({"protocol_sha256": "0" * 64}))
    with pytest.raises(ValueError, match="binding hash"):
        lane.verify(path, approved=False)


@pytest.mark.parametrize("change,message", [
    ({"extra": "field"}, "approval schema"),
    ({"approved_by": " "}, "approved_by"),
    ({"approved_by": None}, "approved_by"),
    ({"approved_at": "2026-10-30T00:00:00"}, "timezone"),
])
def test_a_malformed_approval_is_refused(repo, change, message):
    path = lane.freeze(repo / "packet")
    approve(path)
    plan = json.loads(path.read_bytes())
    plan["approval"].update(change)
    reseal(path, plan)
    with pytest.raises(ValueError, match=message):
        lane.verify(path)


@pytest.mark.parametrize("change", [{"approved_by": "synthetic fixture"}, {"approval_state": "MAYBE"}])
def test_an_invalid_pending_approval_is_refused(repo, change):
    path = lane.freeze(repo / "packet")
    plan = json.loads(path.read_bytes())
    plan["approval"].update(change)
    reseal(path, plan)
    with pytest.raises(ValueError, match="invalid pending"):
        lane.verify(path, approved=False)


def test_an_input_that_differs_from_the_manifest_blocks_the_run(repo):
    manifest = json.loads((repo / "manifest.json").read_bytes())
    manifest["datasets"]["BTC_USDT/1h"]["sha256"] = "0" * 64
    (repo / "manifest.json").write_text(json.dumps(manifest))
    path = lane.freeze(repo / "packet")
    assert lane.verify(path, approved=False)["inputs"][
        f"{lane.RAW_DATA}/BTC_USDT/1h.parquet"]["matches_research_manifest"] is False
    approve(path)
    with pytest.raises(PermissionError, match="INPUT_NOT_PROVEN"):
        lane.run(path)
    assert not (path.parent / "execution_started.json").exists()


REWRITTEN = ("BTC_USDT/1h", "BTC_USDT/1d", "ETH_USDT/1d", "BNB_USDT/1d")


def rewrite_after_the_manifest(repo):
    """As in the real checkout: four inputs no longer match the manifest."""
    manifest = json.loads((repo / "manifest.json").read_bytes())
    for key in REWRITTEN:
        manifest["datasets"][key]["sha256"] = "0" * 64
    (repo / "manifest.json").write_text(json.dumps(manifest))


def set_price(repo, symbol, timeframe, stamp_column, stamp, column, value):
    path = repo / lane.RAW_DATA / symbol.replace("/", "_") / f"{timeframe}.parquet"
    frame = pl.read_parquet(path).with_columns(
        pl.when(pl.col(stamp_column) == stamp).then(value).otherwise(pl.col(column)).alias(column)
    )
    frame.write_parquet(path)


def test_rewritten_inputs_can_run_once_proven(repo):
    rewrite_after_the_manifest(repo)
    proof = lane.prove_inputs(repo / "proof")
    assert proof["verdict"] == "PASS"
    assert all(c["rows"] > 0 and c["unmatched"] == c["mismatched"] == c["missing"] == 0
               for c in proof["checks"])
    assert {c["input"].split("binance/")[1] for c in proof["checks"]} == {
        f"{key}.parquet" for key in REWRITTEN}
    path = lane.freeze(repo / "packet", proof_path=repo / "proof" / "input_proof.json")
    approve(path)
    assert lane.run(path) == 0


def test_without_a_proof_the_rewritten_inputs_block_the_run(repo):
    rewrite_after_the_manifest(repo)
    path = lane.freeze(repo / "packet")
    approve(path)
    with pytest.raises(PermissionError, match="INPUT_NOT_PROVEN"):
        lane.run(path)


@pytest.mark.parametrize("symbol,timeframe,column,stamp_column,stamp", [
    ("BTC/USDT", "1h", "open", "timestamp", datetime(2023, 2, 6, 1)),
    ("BTC/USDT", "1h", "close", "timestamp", datetime(2023, 2, 6, 1)),
    ("BTC/USDT", "1d", "close", "date", date(2023, 1, 8)),
    ("ETH/USDT", "1d", "close", "date", date(2025, 6, 1)),
    ("BNB/USDT", "1d", "close", "date", date(2026, 2, 1)),
])
def test_one_changed_row_fails_the_proof_and_blocks_the_run(
    repo, symbol, timeframe, column, stamp_column, stamp
):
    rewrite_after_the_manifest(repo)
    set_price(repo, symbol, timeframe, stamp_column, stamp, column, 123.456)
    proof = lane.prove_inputs(repo / "proof")
    assert proof["verdict"] == "FAIL"
    failed = [c for c in proof["checks"] if c["verdict"] == "FAIL"]
    assert len(failed) == 1 and failed[0]["mismatched"] == 1 and failed[0]["column"] == column
    path = lane.freeze(repo / "packet", proof_path=repo / "proof" / "input_proof.json")
    approve(path)
    with pytest.raises(PermissionError, match="INPUT_NOT_PROVEN"):
        lane.run(path)


def test_a_row_missing_from_the_reference_fails_the_proof(repo):
    rewrite_after_the_manifest(repo)
    path = repo / lane.RAW_DATA / "BTC_USDT" / "30m.parquet"
    frame = pl.read_parquet(path).filter(pl.col("timestamp") != datetime(2024, 5, 5, 23, 30))
    frame.write_parquet(path)
    manifest = json.loads((repo / "manifest.json").read_bytes())
    manifest["datasets"]["BTC_USDT/30m"]["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    (repo / "manifest.json").write_text(json.dumps(manifest))
    proof = lane.prove_inputs(repo / "proof")
    assert proof["verdict"] == "FAIL"
    assert sum(c["unmatched"] for c in proof["checks"]) == 2  # the 1h close and the 1d close


@pytest.mark.parametrize("symbol,timeframe,stamp_column,stamp,failed_checks", [
    ("BTC/USDT", "1h", "timestamp", datetime(2023, 2, 6, 1), 2),  # the open and the close
    ("ETH/USDT", "1d", "date", date(2025, 6, 4), 1),
])
def test_a_row_missing_from_the_input_fails_the_proof(
    repo, symbol, timeframe, stamp_column, stamp, failed_checks
):
    rewrite_after_the_manifest(repo)
    path = raw_file(repo, symbol, timeframe)
    pl.read_parquet(path).filter(pl.col(stamp_column) != stamp).write_parquet(path)
    proof = lane.prove_inputs(repo / "proof")
    assert proof["verdict"] == "FAIL"
    failed = [c for c in proof["checks"] if c["verdict"] == "FAIL"]
    assert len(failed) == failed_checks
    assert all(c["missing"] == 1 and c["unmatched"] == c["mismatched"] == 0 for c in failed)
    packet = lane.freeze(repo / "packet", proof_path=repo / "proof" / "input_proof.json")
    approve(packet)
    with pytest.raises(PermissionError, match="INPUT_NOT_PROVEN"):
        lane.run(packet)


def older_rows(path, stamp_column, step):
    """Put two rows from before FIRST_DATA_DAY in front of a file, in order."""
    frame = pl.read_parquet(path)
    pl.concat([frame.head(2).with_columns(pl.col(stamp_column).dt.offset_by(step)), frame]).write_parquet(path)


@pytest.mark.parametrize("daily_column", ["date", "timestamp"])
def test_the_daily_proof_rows_stay_between_both_bounds(repo, daily_column):
    path = raw_file(repo, "ETH/USDT", "1d")
    clean = lane._daily_bars("ETH/USDT")
    older_rows(path, "date", "-2d")
    if daily_column == "timestamp":
        timestamp_only(path)
    bars = lane._daily_bars("ETH/USDT")
    assert (bars["date"].min(), bars["date"].max()) == (date(2023, 1, 1), date(2026, 2, 5))
    assert bars.equals(clean)


def test_the_hourly_proof_rows_start_at_the_first_data_day(repo):
    clean = lane._bars("BTC/USDT", "1h", "close")
    older_rows(raw_file(repo, "BTC/USDT", "1h"), "timestamp", "-2h")
    assert lane._bars("BTC/USDT", "1h", "close").equals(clean)


def test_a_proof_with_nothing_to_compare_fails(repo):
    rewrite_after_the_manifest(repo)
    daily, hourly = raw_file(repo, "ETH/USDT", "1d"), raw_file(repo, "ETH/USDT", "1h")
    older_rows(daily, "date", "-2d")
    older_rows(hourly, "timestamp", "-2h")
    for path in (daily, hourly):
        pl.read_parquet(path).head(2).write_parquet(path)
    manifest = json.loads((repo / "manifest.json").read_bytes())
    manifest["datasets"]["ETH_USDT/1h"]["sha256"] = hashlib.sha256(hourly.read_bytes()).hexdigest()
    (repo / "manifest.json").write_text(json.dumps(manifest))
    proof = lane.prove_inputs(repo / "proof")
    check = next(c for c in proof["checks"] if c["input"].endswith("ETH_USDT/1d.parquet"))
    assert (check["rows"], check["missing"], check["verdict"]) == (0, 0, "FAIL")
    assert proof["verdict"] == "FAIL"


@pytest.mark.parametrize("kind", ["swapped", "duplicated"])
def test_a_disordered_row_after_the_cutoff_leaves_the_proof_alone(repo, kind):
    rewrite_after_the_manifest(repo)
    disturb(raw_file(repo, "BTC/USDT", "1h"), "timestamp", -3, kind)
    disturb(raw_file(repo, "ETH/USDT", "1d"), "date", -3, kind)
    assert lane.prove_inputs(repo / "proof")["verdict"] == "PASS"


@pytest.mark.parametrize("kind", ["swapped", "duplicated"])
@pytest.mark.parametrize("symbol,timeframe,stamp_column", [
    ("BTC/USDT", "1h", "timestamp"),
    ("ETH/USDT", "1d", "date"),
])
def test_the_proof_refuses_a_disordered_row_before_the_cutoff(repo, symbol, timeframe, stamp_column, kind):
    rewrite_after_the_manifest(repo)
    disturb(raw_file(repo, symbol, timeframe), stamp_column, 5, kind)
    with pytest.raises(lane.InvalidRun, match="strictly increasing"):
        lane.prove_inputs(repo / "proof")


@pytest.mark.parametrize("kind", ["swapped", "duplicated"])
@pytest.mark.parametrize("timeframe,stamp_column,step", [
    ("1h", "timestamp", "-2h"),
    ("1d", "date", "-2d"),
])
def test_the_proof_refuses_a_disordered_row_before_the_first_data_day(repo, timeframe, stamp_column, step, kind):
    rewrite_after_the_manifest(repo)
    path = raw_file(repo, "BTC/USDT", timeframe)
    older_rows(path, stamp_column, step)
    disturb(path, stamp_column, 0, kind)
    with pytest.raises(lane.InvalidRun, match="strictly increasing"):
        lane.prove_inputs(repo / "proof")


def test_a_reference_that_left_the_manifest_is_refused(repo):
    manifest = json.loads((repo / "manifest.json").read_bytes())
    manifest["datasets"]["BTC_USDT/30m"]["sha256"] = "0" * 64
    (repo / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="reference"):
        lane.prove_inputs(repo / "proof")


def test_a_proof_for_other_bytes_does_not_count(repo):
    rewrite_after_the_manifest(repo)
    lane.prove_inputs(repo / "proof")
    proof = json.loads((repo / "proof" / "input_proof.json").read_bytes())
    inputs = lane.input_bindings()
    assert len(lane.proven_inputs(proof, inputs)) == 4
    btc = f"{lane.RAW_DATA}/BTC_USDT/1h.parquet"
    assert btc not in lane.proven_inputs(proof, {**inputs, btc: {"sha256": "1" * 64}})
    one_check_short = {**proof, "checks": [c for c in proof["checks"] if c["column"] != "open"]}
    assert btc not in lane.proven_inputs(one_check_short, inputs)


def test_the_proof_stops_at_the_cutoff(repo):
    rewrite_after_the_manifest(repo)
    set_price(repo, "BTC/USDT", "1h", "timestamp", datetime(2026, 2, 8, 5), "close", 123.456)
    assert lane.prove_inputs(repo / "proof")["verdict"] == "PASS"


def test_a_fresh_packet_is_required(repo):
    lane.freeze(repo / "packet")
    with pytest.raises(ValueError, match="fresh"):
        lane.freeze(repo / "packet")


def cli(monkeypatch, *arguments):
    monkeypatch.setattr(sys, "argv", ["xsec", *map(str, arguments)])
    return lane.main()


def test_the_command_line_proves_prepares_and_runs(repo, monkeypatch, capsys):
    rewrite_after_the_manifest(repo)
    assert cli(monkeypatch, "--prove-inputs", repo / "proof") == 0
    assert json.loads(capsys.readouterr().out) == {"verdict": "PASS"}
    proof = repo / "proof" / "input_proof.json"
    assert cli(monkeypatch, "--prepare", repo / "packet", "--input-proof", proof) == 0
    printed = json.loads(capsys.readouterr().out)
    path = repo / "packet" / "protocol.json"
    assert printed["status"] == "PENDING_OPERATOR_APPROVAL" and printed["protocol"] == str(path)
    approve(path)
    command = json.loads(path.read_bytes())["command"]
    assert command[1:3] == ["-m", lane.__name__]
    assert cli(monkeypatch, *command[3:]) == 0
    assert json.loads((repo / "packet" / "summary.json").read_bytes())["status"] == "COMPLETED"


def test_the_command_line_reports_a_failed_proof(repo, monkeypatch, capsys):
    rewrite_after_the_manifest(repo)
    set_price(repo, "BTC/USDT", "1h", "timestamp", datetime(2023, 2, 6, 1), "close", 123.456)
    assert cli(monkeypatch, "--prove-inputs", repo / "proof") == 2
    assert json.loads(capsys.readouterr().out) == {"verdict": "FAIL"}
