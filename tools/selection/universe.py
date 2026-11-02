"""Weekly lists of the selection run and the membership matrix Mem.

Up to 2026-08-03 a list is the frozen week of the study without the masked coins and BTC, no refill. From 2026-08-10 it comes from the rule of seed/weeks2_fixed.py.txt on all trading USDT spot pairs, masked coins and BTC removed before ranking, top 18. Mem is seed/stamps.py.txt line 141: the list of the last Monday at or before the stamp, and a coin whose last bar at h-1 is at most 6 hours old.
"""
from __future__ import annotations

import json
import re
import statistics
from collections.abc import Iterable, Mapping
from datetime import date, timedelta
from pathlib import Path

import numpy as np

from tools.selection import config
from tools.selection.config import BTC, ts

EXCL = frozenset({
    "AEUR", "AUD", "BFUSD", "BKRW", "BUSD", "DAI", "EUR", "EURI", "FDUSD", "GBP", "PAX", "RLUSD", "SUSD", "TUSD", "USD1",
    "USDC", "USDE", "USDP", "USDS", "USDSB", "USDSOLD", "UST", "XUSD", "BETH", "WBETH", "WBTC",
})
LEV = re.compile(r"^(?!JUP$|SYRUP$).*(UP|DOWN|BULL|BEAR)$")
STOCK = re.compile(r"^[A-Z]{2,6}B$")
# weeks2 applied the name filters only to coins it did not already know. These are the known coins the filters would hit, read from the folder names of data/raw/binance_xsec02 on 2026-11-01. BNB is the tenth and is masked.
KNOWN_EXEMPT = frozenset({"AMB", "ARB", "CKB", "DGB", "MOB", "PHB", "SHIB", "TRB", "VIB"})
ALIASES = {"LUNA": "LUNA2"}         # the exchange's LUNA after 2022-05 is LUNA2 on the panel
MIN_DAILY_BARS = 60
VOLUME_DAYS = 28
BACK_BAR_DAYS = 21
FLAT_RATIO = 0.005


def panel_name(base: str) -> str:
    return ALIASES.get(base, base)


def exchange_base(coin: str) -> str:
    """The base asset to ask the exchange for, the inverse of panel_name."""
    return {v: k for k, v in ALIASES.items()}.get(coin, coin)


def name_ok(base: str) -> bool:
    """False for a base the name filters drop: stablecoins, wrapped coins, leveraged tokens, tokenised stocks."""
    return base in KNOWN_EXEMPT or not (base in EXCL or LEV.search(base) or STOCK.match(base))


def frozen_lists(weeks: Mapping[str, list[str]], masked: bool = True) -> dict[str, list[str]]:
    """The study's weekly lists without BTC, and without the masked coins unless masked is False. A short list stays short."""
    late = [k for k in weeks if k > config.FROZEN_LAST_WEEK]
    if late:
        raise ValueError(f"frozen lists stop at {config.FROZEN_LAST_WEEK}, got {late[0]}")
    return {k: [c for c in weeks[k] if c != BTC and not (masked and config.is_sealed(c))] for k in sorted(weeks)}


def trading_bases(exchange_info: Mapping) -> set[str]:
    """Bases of every USDT spot pair the exchange trades now, BTC included, masked coins left out."""
    out = set()
    for s in exchange_info.get("symbols", []):
        if s.get("status") == "TRADING" and s.get("quoteAsset") == "USDT" and s.get("isSpotTradingAllowed", False) and not config.is_sealed(s["baseAsset"]):
            out.add(s["baseAsset"])
    return out


def tradable_bases(exchange_info: Mapping) -> list[str]:
    """Bases of every trading USDT spot pair that may be ranked: no masked coin, no BTC, no name the filters drop."""
    return sorted(b for b in trading_bases(exchange_info) if b != BTC and name_ok(b))


def rule_list(daily: Mapping[str, Mapping[date, tuple[float, float]]], monday: date, n: int = config.LIST_N) -> list[str]:
    """The list of a Monday from daily bars closed before it: daily[base][day] = (close, quote volume).

    Same arithmetic as weeks2: 28 day quote volume over the bars present, at least 60 bars, a bar on the Sunday and 21 days before it, closes that moved. A masked coin in the input is refused, not skipped: it should never have been fetched.
    """
    config.assert_all_open(daily)
    sunday = monday - timedelta(days=1)
    start = sunday - timedelta(days=BACK_BAR_DAYS)
    scored = []
    for coin, bars in daily.items():
        if coin == BTC or not name_ok(coin):
            continue
        if sunday not in bars or start not in bars:
            continue
        if sum(1 for d in bars if d <= sunday) < MIN_DAILY_BARS:
            continue
        recent = [bars[sunday - timedelta(days=k)] for k in range(VOLUME_DAYS) if sunday - timedelta(days=k) in bars]
        closes = [c for c, _ in recent]
        if len(closes) < 2 or statistics.pstdev(closes) < FLAT_RATIO * statistics.mean(closes):
            continue
        scored.append((-sum(q for _, q in recent), coin))
    return [panel_name(c) for _, c in sorted(scored)[:n]]


def _list_path(root: Path, monday: str) -> Path:
    return config.lists_dir(root) / f"{monday}.json"


def store_frozen(lists: Mapping[str, list[str]], root: Path) -> None:
    config.assert_all_open(c for v in lists.values() for c in v)
    config.write_text(config.lists_dir(root) / "frozen.json", json.dumps(lists, sort_keys=True, ensure_ascii=False))


def store_list(monday, names: Iterable[str], root: Path) -> list[str]:
    """Write the list of a rule Monday once. A later call returns what is stored: a finished Monday never changes."""
    key = str(monday)[:10]
    names = list(names)
    config.assert_all_open(names)
    if BTC in names:
        raise ValueError("BTC is never on a list")
    if key <= config.FROZEN_LAST_WEEK:
        raise ValueError(f"{key} is a frozen week")
    config.weeks_since_first(key)
    path = _list_path(root, key)
    if path.exists():
        return json.loads(path.read_text(encoding="utf8"))
    config.write_text(path, json.dumps(names, ensure_ascii=False))
    return names


def load_lists(root: Path) -> dict[str, list[str]]:
    """Frozen weeks plus the stored rule weeks, sorted by Monday. A masked coin in a stored list is refused."""
    frozen = config.lists_dir(root) / "frozen.json"
    lists = json.loads(frozen.read_text(encoding="utf8")) if frozen.exists() else {}
    for path in sorted(config.lists_dir(root).glob("*.json")):
        if path.name != "frozen.json":
            lists[path.stem] = json.loads(path.read_text(encoding="utf8"))
    config.assert_all_open(c for v in lists.values() for c in v)
    return dict(sorted(lists.items()))


def week_of_stamps(lists: Mapping[str, list[str]], t: np.ndarray) -> tuple[list[str], np.ndarray]:
    """Sorted Mondays and, per stamp, the row of the last Monday at or before it."""
    keys = sorted(lists)
    if not keys:
        raise ValueError("no weekly lists: run the seed step first")
    mon = np.array([ts(k) for k in keys], np.int64)
    return keys, np.maximum(np.searchsorted(mon, t, side="right") - 1, 0)


def mem_matrix(lists: Mapping[str, list[str]], coins: list[str], t: np.ndarray, stale: np.ndarray) -> np.ndarray:
    """Membership, S by N: on the list that covers the stamp and not stale at h-1."""
    keys, kw = week_of_stamps(lists, t)
    ci = {c: j for j, c in enumerate(coins)}
    wm = np.zeros((len(keys), len(coins)), bool)
    for k, key in enumerate(keys):
        for c in lists[key]:
            if c != BTC:
                wm[k, ci[c]] = True
    return wm[kw] & ~stale
