"""Second frozen paper book, beside rs_book. It writes records only and nothing reads them before the read date.

The rules are in docs/operations/SELECTION_PAPER_RUN.md. The book is rs_book with one change: names rank by calm, minus the std of the changes of log 4 hour closes over the 30 days before the Monday. Fills, cost, stop and timing are rs_book's own functions, and rs_book stays as it is. Fractions, not bps.
"""
from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import numpy as np

from tools.selection import config, rs_book, universe
from tools.selection.config import hour_of


def book_dir(root: Path) -> Path:
    return root / "lv_book"


def calm(hours: np.ndarray, closes: np.ndarray, monday: int) -> float:
    """Minus the std of the last 180 changes of log 4 hour closes before the Monday hour. NaN with too few changes, no last close, or a last bar too old to count: carried closes read as calm."""
    i = np.searchsorted(hours, monday) - 1
    if i < 0 or monday - 1 - hours[i] > config.LV_FRESH_HOURS:
        return float("nan")
    c = rs_book._on_grid(hours, closes, monday - 1 - rs_book.STAMP * np.arange(rs_book.WINDOW + 1)[::-1])
    changes = np.diff(np.log(c))
    return -float(np.nanstd(changes, ddof=1)) if np.count_nonzero(np.isfinite(changes)) >= rs_book.MIN_CLOSES and np.isfinite(c[-1]) else float("nan")


def blocks_done(root: Path) -> int:
    return len(rs_book._load(book_dir(root) / "ledger.jsonl"))


def update(root: Path, as_of: str, first: str = config.LV_FIRST_MONDAY) -> int:
    """Pick every Monday that has a list and no pick, then close every block whose last open is stored. Blocks closed."""
    if not config.tier_path(root).exists():      # no finished seed, nothing to read
        return blocks_done(root)
    folder = book_dir(root)
    lists = universe.load_lists(root)
    picks = {r["monday"]: r for r in rs_book._load(folder / "picks.jsonl")}
    for monday in rs_book._mondays(first, as_of):
        if monday in picks or monday not in lists:
            continue
        names = lists[monday]
        config.assert_all_open(names)
        x = {}
        for c in names:
            b = rs_book._bars(root, c)
            x[c] = calm(b[0], b[2], hour_of(monday)) if b else float("nan")
        got = rs_book.legs(x)
        picks[monday] = {"monday": monday, "long": got[0] if got else [], "short": got[1] if got else [], "ranked": int(np.isfinite(list(x.values())).sum())}
    rs_book._save(folder / "picks.jsonl", [picks[k] for k in sorted(picks)])

    rows = rs_book._load(folder / "ledger.jsonl")
    done = {r["week"] for r in rows}
    tier = json.loads(config.tier_path(root).read_text(encoding="utf8"))
    for monday in rs_book._mondays(first, as_of):
        due = (date.fromisoformat(monday) + timedelta(days=14)).isoformat()
        if monday in done or due > as_of:
            continue
        p = picks.get(monday)
        if p is None or not p["long"] or not p["short"]:
            rows.append({"week": monday, "missed": "no list" if p is None else "too few names"})
            continue
        bars = {c: b[:2] for c in {*p["long"], *p["short"]} if (b := rs_book._bars(root, c))}
        prior = next((r["end_weights"] for r in reversed(rows) if "end_weights" in r), {})
        rows.append(rs_book.settle(p, prior, tier, bars))
    rs_book._save(folder / "ledger.jsonl", rows)
    return len(rows)
