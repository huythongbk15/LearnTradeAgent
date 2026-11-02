"""Paper record of one frozen market-neutral book, beside the selection run. It writes records only and nothing reads them before the read date.

The rules are in docs/operations/SELECTION_PAPER_RUN.md. Each Monday: dhi30 of the names on the list, from bars before 00:00, 4 hour closes, top against bottom fifth. A block enters four hours after the Monday and leaves four hours after the next, buy and hold, half the capital a side. A short leaves at the first 4 hour open at or above 1.5 times its entry. Cost is twice the tier on the turnover from drifted weights. No funding: spot stands for perp.

A pick reads only bars before its Monday and the list of that Monday, so a late run gets the same pick and a run after a missed Monday fills it. A block closes on the first run two Mondays on, when its last open is in the store. A row is final. Fractions, not bps.
"""
from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import polars as pl
from scipy.stats import rankdata

from tools.selection import config, universe
from tools.selection.config import hour_of

STAMP = 4                       # hours
WINDOW = 180                    # 30 days of 4 hour closes
MIN_CLOSES = 160
MIN_NAMES = 10
FIFTHS = 5
STALE_HOURS = 24
HOLD_HOURS = 168
DEFAULT_COST = 37.0             # the study's round trip bps of a coin with no tier


def book_dir(root: Path) -> Path:
    return root / "rs_book"


def _load(path: Path) -> list[dict]:
    return [json.loads(x) for x in path.read_text(encoding="utf8").splitlines()] if path.exists() else []


def _save(path: Path, rows: list[dict]) -> None:
    config.write_text(path, "".join(json.dumps(r, sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n" for r in rows))


def _on_grid(hours: np.ndarray, values: np.ndarray, grid: np.ndarray) -> np.ndarray:
    """Values at the grid hours, the last bar at or before each, NaN before the first bar."""
    i = np.searchsorted(hours, grid, side="right") - 1
    out = np.full(len(grid), np.nan)
    out[i >= 0] = values[i[i >= 0]]
    return out


def dhi30(hours: np.ndarray, closes: np.ndarray, monday: int) -> float:
    """Log distance of the last 4 hour close before the Monday hour to the highest of the last 30 days. NaN with too few closes."""
    c = _on_grid(hours, closes, monday - 1 - STAMP * np.arange(WINDOW)[::-1])
    return float(np.log(c[-1] / np.nanmax(c))) if np.count_nonzero(np.isfinite(c)) >= MIN_CLOSES and np.isfinite(c[-1]) else float("nan")


def legs(x: dict[str, float]) -> tuple[list[str], list[str]] | None:
    """Top and bottom fifth of the names with a value, None with too few."""
    names = sorted(k for k, v in x.items() if np.isfinite(v))
    if len(names) < MIN_NAMES:
        return None
    r = rankdata([x[k] for k in names], method="average")
    b = np.floor(FIFTHS * (r - .5) / len(names)).astype(int)
    return [k for k, q in zip(names, b) if q == FIFTHS - 1], [k for k, q in zip(names, b) if q == 0]


def hold(hours: np.ndarray, opens: np.ndarray, entry: int, stop: float | None = None) -> tuple[float, bool] | None:
    """Price ratio of a hold on 4 hour opens, and whether the last open is real rather than carried. None with no fresh entry open."""
    grid = entry + STAMP * np.arange(HOLD_HOURS // STAMP + 1)
    p = _on_grid(hours, opens, grid)
    i = np.searchsorted(hours, entry, side="right") - 1
    if not np.isfinite(p[0]) or entry - hours[i] > STALE_HOURS:
        return None
    ratio = p / p[0]
    if stop is not None:
        hit = np.flatnonzero(ratio >= 1 + stop)
        if len(hit):
            return float(ratio[hit[0]]), True
    return float(ratio[-1]), bool(grid[-1] - hours[-1] <= STALE_HOURS)


def settle(picks: dict, prior: dict[str, float], tier: dict[str, float], bars: dict[str, tuple[np.ndarray, np.ndarray]]) -> dict:
    """The row of one block. prior holds the weights the last block drifted to."""
    entry = hour_of(picks["monday"]) + config.RS_ENTRY_HOURS
    held = {c: hold(*bars[c], entry) for c in picks["long"] if c in bars}
    short = {c: hold(*bars[c], entry, config.RS_SHORT_STOP) for c in picks["short"] if c in bars}
    held, short = {k: v for k, v in held.items() if v}, {k: v for k, v in short.items() if v}
    if not held or not short:
        return {"week": picks["monday"], "missed": "no entry"}
    side = config.RS_SIDE
    long_r = float(np.mean([v[0] for v in held.values()])) - 1
    short_r = float(np.mean([v[0] for v in short.values()])) - 1
    gross = side * long_r - side * short_r
    target = {c: side / len(held) for c in held}
    for c in short:
        target[c] = target.get(c, 0.0) - side / len(short)
    cost = sum(abs(target.get(c, 0.0) - prior.get(c, 0.0)) * tier.get(c, DEFAULT_COST) * config.RS_COST_TIMES / 2e4 for c in set(target) | set(prior))
    end: dict[str, float] = {}
    for c, v in held.items():
        end[c] = end.get(c, 0.0) + side / len(held) * v[0] / (1 + gross)
    for c, v in short.items():
        end[c] = end.get(c, 0.0) - side / len(short) * v[0] / (1 + gross)
    carried = sum(not v[1] for v in (*held.values(), *short.values()))
    return {"week": picks["monday"], "gross": gross, "cost": cost, "net": gross - cost, "long": long_r, "short": short_r, "carried": carried, "end_weights": end}


def _bars(root: Path, coin: str) -> tuple[np.ndarray, np.ndarray, np.ndarray] | None:
    config.assert_open(coin)
    path = config.bars_dir(root) / f"{coin}.parquet"
    if not path.exists():
        return None
    f = pl.read_parquet(path, columns=["hour", "o", "c"])
    return f["hour"].to_numpy(), f["o"].to_numpy(), f["c"].to_numpy()


def _mondays(first: str, last: str) -> list[str]:
    day, out = date.fromisoformat(first), []
    while day.isoformat() <= last:
        out.append(day.isoformat())
        day += timedelta(days=7)
    return out


def blocks_done(root: Path) -> int:
    return len(_load(book_dir(root) / "ledger.jsonl"))


def update(root: Path, as_of: str, first: str = config.RS_FIRST_MONDAY) -> int:
    """Pick every Monday that has a list and no pick, then close every block whose last open is stored. Blocks closed."""
    if not config.tier_path(root).exists():      # no finished seed, nothing to read
        return blocks_done(root)
    folder = book_dir(root)
    lists = universe.load_lists(root)
    picks = {r["monday"]: r for r in _load(folder / "picks.jsonl")}
    for monday in _mondays(first, as_of):
        if monday in picks or monday not in lists:
            continue
        names = lists[monday]
        config.assert_all_open(names)
        x = {}
        for c in names:
            b = _bars(root, c)
            x[c] = dhi30(b[0], b[2], hour_of(monday)) if b else float("nan")
        got = legs(x)
        picks[monday] = {"monday": monday, "long": got[0] if got else [], "short": got[1] if got else [], "ranked": int(np.isfinite(list(x.values())).sum())}
    _save(folder / "picks.jsonl", [picks[k] for k in sorted(picks)])

    rows = _load(folder / "ledger.jsonl")
    done = {r["week"] for r in rows}
    tier = json.loads(config.tier_path(root).read_text(encoding="utf8"))
    for monday in _mondays(first, as_of):
        due = (date.fromisoformat(monday) + timedelta(days=14)).isoformat()
        if monday in done or due > as_of:
            continue
        p = picks.get(monday)
        if p is None or not p["long"] or not p["short"]:
            rows.append({"week": monday, "missed": "no list" if p is None else "too few names"})
            continue
        bars = {c: b[:2] for c in {*p["long"], *p["short"]} if (b := _bars(root, c))}
        prior = next((r["end_weights"] for r in reversed(rows) if "end_weights" in r), {})
        rows.append(settle(p, prior, tier, bars))
    _save(folder / "ledger.jsonl", rows)
    return len(rows)
