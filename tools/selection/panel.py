"""Hourly store of the selection run and the panel the library reads.

The store holds one parquet per coin with hour (row on the grid from 2020-01-02 00:00), o, c, q: unique, sorted, append only. A bar enters only if it closed before the cutoff, so a partial bar is never stored. A masked coin cannot enter: every write refuses it, and the loader refuses a file that somehow holds one.

masked=False exists on seed_panel only, for acceptance.py and tests, which prove the port on the study's own universe. run_weekly reads load_panel, which has no such flag. tests/test_selection_mask.py scans the package for that.
"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import polars as pl

from tools.selection import config, universe
from tools.selection.config import BTC, hour_of

_BAR_SCHEMA = {"hour": pl.Int32, "o": pl.Float64, "c": pl.Float64, "q": pl.Float32}
_DAY_SCHEMA = {"day": pl.Int32, "close": pl.Float64, "quote_volume": pl.Float64}


@dataclass
class Panel:
    """Hourly matrices, T by N, row i is hour i on the grid, NaN where a coin has no bar, plus what the library needs to know of the coins.

    lists: Monday to list, no BTC. tier: coin to round trip bps. replay_cost: the study's stamp cost, t and cost, set only on the replay path, where parity needs the study's numbers.
    """

    coins: list[str]
    opens: np.ndarray
    closes: np.ndarray
    quotes: np.ndarray
    lists: dict[str, list[str]]
    tier: dict[str, float] = field(default_factory=dict)
    masked: bool = True
    replay_cost: dict[str, np.ndarray] | None = None

    def __post_init__(self) -> None:
        if self.masked:
            config.assert_all_open(self.coins)
            config.assert_all_open(self.tier)
            config.assert_all_open(c for v in self.lists.values() for c in v)
        if BTC not in self.coins:
            raise ValueError("the panel has no BTC")
        if not self.opens.shape == self.closes.shape == self.quotes.shape or self.opens.shape[1] != len(self.coins):
            raise ValueError(f"matrices {self.opens.shape} {self.closes.shape} {self.quotes.shape} do not fit {len(self.coins)} coins")


def hours_from_open_ms(open_ms) -> np.ndarray:
    """Grid rows of bar open times in ms. A time off the hourly grid is refused."""
    ms = np.asarray(open_ms, np.int64) - config.ts(config.T0) * 1000
    if (ms % config.HOUR_MS).any():
        raise ValueError("bar open time is not on the hourly grid")
    return ms // config.HOUR_MS


def _bars_path(root: Path, coin: str) -> Path:
    return config.bars_dir(root) / f"{coin}.parquet"


def _daily_path(root: Path, coin: str) -> Path:
    return config.daily_dir(root) / f"{coin}.parquet"


def _write(df: pl.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    df.write_parquet(tmp)
    os.replace(tmp, path)


def _priced(x: np.ndarray) -> bool:
    """Every price real and above zero. One open of zero is an infinite return in every candidate, and the store never forgets it."""
    return bool(np.isfinite(x).all() and (x > 0).all())


def _append(path: Path, key: str, keys: np.ndarray, new: pl.DataFrame) -> int:
    """Add the rows of new whose key is past the last stored key. Old rows are written back unchanged."""
    if len(keys) and (np.diff(keys) <= 0).any():
        raise ValueError(f"{path.name}: keys must be strictly increasing")
    if path.exists():
        old = pl.read_parquet(path)
        last = int(old[key][-1])
        fresh = new.filter(pl.col(key) > last)
        if fresh.height:
            _write(pl.concat([old, fresh]), path)
        return fresh.height
    if new.height:
        _write(new, path)
    return new.height


def append_bars(coin: str, hours, o, c, q, cutoff, root: Path) -> int:
    """Append hourly bars of one coin, return the rows added. Only bars closed before the cutoff enter: a bar of row i closes just before hour i + 1."""
    config.assert_open(coin)
    hours = np.asarray(hours, np.int64)
    o, c, q = np.asarray(o, np.float64), np.asarray(c, np.float64), np.asarray(q, np.float32)
    if len(hours) and hours[0] < 0:
        raise ValueError("a bar before the grid")
    if not (_priced(o) and _priced(c) and np.isfinite(q).all()):
        raise ValueError(f"{coin}: a stored bar has a positive open and close and a volume")
    keep = hours < hour_of(cutoff)
    new = pl.DataFrame({"hour": hours[keep], "o": o[keep], "c": c[keep], "q": q[keep]}, schema=_BAR_SCHEMA)
    return _append(_bars_path(root, coin), "hour", hours, new)


def append_daily(coin: str, days: list[date], close, quote_volume, cutoff, root: Path) -> int:
    """Append daily bars of one coin. A day enters only if it closed before the cutoff."""
    config.assert_open(coin)
    idx = np.array([(d - date(1970, 1, 1)).days for d in days], np.int64)
    close, quote_volume = np.asarray(close, np.float64), np.asarray(quote_volume, np.float64)
    if not (_priced(close) and np.isfinite(quote_volume).all()):
        raise ValueError(f"{coin}: a stored day has a positive close and a volume")
    keep = (idx + 1) * 86400 <= config.ts(cutoff)
    new = pl.DataFrame({"day": idx[keep], "close": close[keep], "quote_volume": quote_volume[keep]}, schema=_DAY_SCHEMA)
    return _append(_daily_path(root, coin), "day", idx, new)


def load_daily(root: Path) -> dict[str, dict[date, tuple[float, float]]]:
    """Daily bars in the shape universe.rule_list reads. A masked file is refused."""
    paths = sorted(config.daily_dir(root).glob("*.parquet"))
    config.assert_all_open(p.stem for p in paths)
    out = {}
    for p in paths:
        f = pl.read_parquet(p)
        days = [date(1970, 1, 1) + timedelta(days=int(d)) for d in f["day"].to_list()]
        out[p.stem] = dict(zip(days, zip(f["close"].to_list(), f["quote_volume"].to_list())))
    return out


def verify_seed(seed: Path) -> list[str]:
    """Check every file named in SEED.sha256, return the names. A changed seed stops the run before anything is read."""
    names = []
    for line in (seed / "SEED.sha256").read_text(encoding="utf8").splitlines():
        digest, name = line.split()
        h = hashlib.sha256()
        with open(seed / name, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        if h.hexdigest() != digest:
            raise ValueError(f"seed file {name} does not match SEED.sha256")
        names.append(name)
    return names


def _read_tier(seed: Path, masked: bool = True) -> dict[str, float]:
    tier = json.loads((seed / "tier_cost.json").read_text(encoding="utf8"))
    return {k: v for k, v in tier.items() if not (masked and config.is_sealed(k))}


def seed_store(seed: Path | None = None, root: Path | None = None) -> dict:
    """The seed step: convert the study's matrices to the store, without the masked columns. Safe to repeat: rows already stored are left alone. tier_cost.json goes last, so it marks a seed that finished."""
    seed = Path(seed) if seed else config.seed_dir()
    root = Path(root) if root else config.selection_root()
    verify_seed(seed)
    meta = json.loads((seed / "meta.json").read_text(encoding="utf8"))
    om, cm, qm = (np.load(seed / f"{x}.npy", mmap_mode="r") for x in "OCQ")
    rows = om.shape[0]
    cutoff = config.T0 + np.timedelta64(rows, "h")
    added = 0
    kept = [(j, c) for j, c in enumerate(meta["coins"]) if not config.is_sealed(c)]
    for j, coin in kept:
        close = np.asarray(cm[:, j])
        i = np.flatnonzero(~np.isnan(close))
        added += append_bars(coin, i, np.asarray(om[:, j])[i], close[i], np.asarray(qm[:, j])[i], cutoff, root)
    universe.store_frozen(universe.frozen_lists(meta["weeks"]), root)
    config.write_text(config.tier_path(root), json.dumps(_read_tier(seed), sort_keys=True, ensure_ascii=False))
    return {"coins": len(kept), "dropped": len(meta["coins"]) - len(kept), "rows_added": added, "bars_to_hour": rows}


def seed_panel(end=config.DISCOVERY_END, masked: bool = True, seed: Path | None = None, replay_cost: bool = True) -> Panel:
    """The replay path: a panel straight from the seed matrices, bars before end only. masked=False is for acceptance.py and tests, never for a weekly run."""
    seed = Path(seed) if seed else config.seed_dir()
    meta = json.loads((seed / "meta.json").read_text(encoding="utf8"))
    om, cm, qm = (np.load(seed / f"{x}.npy", mmap_mode="r") for x in "OCQ")
    T = min(hour_of(end), om.shape[0])
    keep = [j for j, c in enumerate(meta["coins"]) if not (masked and config.is_sealed(c))]
    cost = None
    if replay_cost:
        z = np.load(seed / "stamps_seed.npz", allow_pickle=False)
        cost = {"t": z["t"], "cost": z["cost"][:, keep]}
    return Panel(
        coins=[meta["coins"][j] for j in keep],
        opens=np.array(om[:T][:, keep]),
        closes=np.array(cm[:T][:, keep]),
        quotes=np.array(qm[:T][:, keep]).astype(np.float64),
        lists=universe.frozen_lists(meta["weeks"], masked=masked),
        tier=_read_tier(seed, masked),
        masked=masked,
        replay_cost=cost,
    )


def load_panel(end, root: Path | None = None) -> Panel:
    """The weekly path: bars of the store before end, frozen and rule lists, tier costs. Always masked. T is the hour of end even if the bars stop earlier, so the stamps stay on the grid and a missing bar reads as stale."""
    root = Path(root) if root else config.selection_root()
    paths = sorted(config.bars_dir(root).glob("*.parquet"))
    stored = [p.stem for p in paths]
    config.assert_all_open(stored)
    lists = universe.load_lists(root)
    tier = json.loads(config.tier_path(root).read_text(encoding="utf8"))
    coins = sorted(set(stored) | {BTC} | {c for v in lists.values() for c in v})
    T = hour_of(end)
    opens = np.full((T, len(coins)), np.nan)
    closes = opens.copy()
    quotes = opens.copy()
    ci = {c: j for j, c in enumerate(coins)}
    for p in paths:
        f = pl.read_parquet(p).filter(pl.col("hour") < T)
        h, j = f["hour"].to_numpy(), ci[p.stem]
        opens[h, j], closes[h, j], quotes[h, j] = f["o"].to_numpy(), f["c"].to_numpy(), f["q"].to_numpy()
    return Panel(coins=coins, opens=opens, closes=closes, quotes=quotes, lists=lists, tier=tier)
