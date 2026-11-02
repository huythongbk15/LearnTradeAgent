"""Synthetic market of the selection tests: no network, no data folder.

Hourly bars on the real grid from 2020-01-02, closes on a tick grid so ties happen, with the cases the study's rules name planted on purpose: a late listing, a coin that ends, a short gap, a stale gap, a hole above 1000 hours, a flat stretch, zero volume, and masked coins that are as liquid as any other.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import numpy as np

from tools.selection import config
from tools.selection.panel import Panel
from tools.selection.universe import frozen_lists

END = "2020-10-26"      # a Monday, 15 selection Mondays after the first
OPEN = ("AAA", "CCC", "DDD", "EEE", "FFF", "GGG", "HHH", "III", "JJJ", "KKK", "MMM", "PPP", "QQQ", "RRR")
SPECIAL = ("FLAT", "GAPS", "GONE", "HOLE", "LATE", "LUNA2")
OFF_LIST = ("OFFL",)
MASKED = ("ADA", "BNB", "ETH", "SOL")
COINS = tuple(sorted(("BTC",) + OPEN + SPECIAL + OFF_LIST + MASKED))
TIERED = {"BTC": 11.0, "AAA": 20.0, "CCC": 24.0, "DDD": 31.0, "EEE": 37.0, "ETH": 11.0, "SOL": 13.0, "LUNA": 31.0}
GAP_SHORT = (4000, 4005)        # 5 hours, held through
GAP_STALE = (4800, 4840)        # 40 hours, stale
HOLE = (3000, 4200)             # 1200 hours
BTC_GAP = (5000, 5003)
LATE_FROM = 3100
GONE_FROM = 5300
FLAT_SPAN = (3000, 3900)
ZERO_Q = (2000, 2100)


@dataclass(frozen=True)
class Market:
    coins: tuple[str, ...]
    opens: np.ndarray
    closes: np.ndarray
    quotes: np.ndarray      # float32, as the seed
    weeks: dict
    tier: dict


def _matrices(T: int, coins: tuple[str, ...], seed: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    rng = np.random.default_rng(seed)
    N = len(coins)
    vol = rng.uniform(.004, .018, N)
    factor = rng.standard_t(5, (T, 1)) * .004
    r = factor + rng.standard_t(4, (T, N)) * vol / 1.4
    px = np.exp(np.log(rng.uniform(5, 300, N)) + np.cumsum(r, 0))
    tick = 10.0 ** np.floor(np.log10(px[0])) / 200
    C = np.round(px / tick) * tick
    Op = np.vstack([C[:1], C[:-1]])
    Op = np.round(Op * (1 + rng.normal(0, .0005, (T, N))) / tick) * tick
    base = np.array([2e4, 2e5, 1e6])[np.arange(N) % 3]
    Q = base * np.exp(rng.normal(0, .5, (T, N)))
    return Op, C, Q


def _blank(Op: np.ndarray, C: np.ndarray, Q: np.ndarray, j: int, a: int, b: int) -> None:
    Op[a:b, j] = np.nan
    C[a:b, j] = np.nan
    Q[a:b, j] = np.nan


@lru_cache(maxsize=1)
def market(end: str = END, seed: int = 11) -> Market:
    """The whole market, masked coins included, as a seed folder would hold it."""
    T = config.hour_of(end)
    coins = COINS
    Op, C, Q = _matrices(T, coins, seed)
    ix = {c: j for j, c in enumerate(coins)}
    _blank(Op, C, Q, ix["LATE"], 0, LATE_FROM)
    _blank(Op, C, Q, ix["GONE"], GONE_FROM, T)
    _blank(Op, C, Q, ix["GAPS"], *GAP_SHORT)
    _blank(Op, C, Q, ix["GAPS"], *GAP_STALE)
    _blank(Op, C, Q, ix["HOLE"], *HOLE)
    _blank(Op, C, Q, ix["BTC"], *BTC_GAP)
    Op[FLAT_SPAN[0]:FLAT_SPAN[1], ix["FLAT"]] = 40.0
    C[FLAT_SPAN[0]:FLAT_SPAN[1], ix["FLAT"]] = 40.0
    Q[ZERO_Q[0]:ZERO_Q[1], ix["AAA"]] = 0.0
    weeks = {}
    mondays = np.arange(np.datetime64("2020-03-02"), np.datetime64(end), np.timedelta64(7, "D"))
    for i, m in enumerate(mondays):
        names = list(OPEN) + list(SPECIAL) + list(MASKED) + ["BTC"]
        weeks[str(m)] = names[i % 3:]                # the first names come and go, so a coin leaves and rejoins a list
    return Market(coins, Op, C, Q.astype(np.float32), weeks, dict(TIERED))


def panel_of(mk: Market, masked: bool = True, end: str | None = None) -> Panel:
    """A Panel straight from the market. masked=False keeps the masked coins, the shape of the replay path."""
    keep = [j for j, c in enumerate(mk.coins) if not (masked and config.is_sealed(c))]
    T = mk.opens.shape[0] if end is None else config.hour_of(end)
    lists = {k: v for k, v in frozen_lists(mk.weeks, masked=masked).items() if end is None or k <= end}     # a run holds the list of its own Monday
    return Panel(
        coins=[mk.coins[j] for j in keep],
        opens=mk.opens[:T][:, keep].copy(),
        closes=mk.closes[:T][:, keep].copy(),
        quotes=mk.quotes[:T][:, keep].astype(np.float64),
        lists=lists,
        tier={k: v for k, v in mk.tier.items() if not (masked and config.is_sealed(k))},
        masked=masked,
    )


def write_seed(folder: Path, mk: Market, stamps: dict | None = None) -> Path:
    """A seed folder in the study's layout, with its SEED.sha256."""
    folder.mkdir(parents=True, exist_ok=True)
    np.save(folder / "O.npy", mk.opens)
    np.save(folder / "C.npy", mk.closes)
    np.save(folder / "Q.npy", mk.quotes)
    (folder / "meta.json").write_text(json.dumps({"coins": list(mk.coins), "keys": sorted(mk.weeks), "weeks": mk.weeks}), encoding="utf8")
    (folder / "tier_cost.json").write_text(json.dumps(mk.tier), encoding="utf8")
    names = ["O.npy", "C.npy", "Q.npy", "meta.json", "tier_cost.json"]
    if stamps is not None:
        np.savez(folder / "stamps_seed.npz", **stamps)
        names.append("stamps_seed.npz")
    lines = [f"{hashlib.sha256((folder / n).read_bytes()).hexdigest()}  {n}" for n in names]
    (folder / "SEED.sha256").write_text("\n".join(lines) + "\n", encoding="utf8")
    return folder


def test_fixture_holds_the_planted_cases() -> None:
    mk = market()
    ix = {c: j for j, c in enumerate(mk.coins)}
    assert np.isnan(mk.closes[:LATE_FROM, ix["LATE"]]).all() and not np.isnan(mk.closes[LATE_FROM:, ix["LATE"]]).any()
    assert np.isnan(mk.closes[GONE_FROM:, ix["GONE"]]).all()
    assert (np.diff(mk.closes[FLAT_SPAN[0]:FLAT_SPAN[1], ix["FLAT"]]) == 0).all()
    assert (np.isnan(mk.opens) == np.isnan(mk.closes)).all() and (np.isnan(mk.quotes) == np.isnan(mk.closes)).all()
    assert set(MASKED) <= config.SEALED and not (set(OPEN) | set(SPECIAL) | set(OFF_LIST)) & config.SEALED
    first = sorted(mk.weeks)[0]
    assert first == "2020-03-02" and "ETH" in mk.weeks[first] and "BTC" in mk.weeks[first]
