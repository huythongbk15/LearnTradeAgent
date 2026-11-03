"""Frozen constants of the NL-P paper log.

The rule is the frozen read of 2026-11-03 (data/nonlist_20261103_01/PROTOCOL.md, CONSISTENT, blind rebuild +140.1 bps a week, t 3.26, fragile).
A value here changes only with an operator ruling in docs/operations/NEWPERP_PAPER_RUN.md. The root comes from NEWPERP_ROOT, read at call time.
"""
from __future__ import annotations

import os
from pathlib import Path

from tools.selection import config as sel

REPO = Path(__file__).resolve().parents[2]

NEW_DAYS = 28                       # a perp is new at a stamp when it was listed in the 28 days before it
ENTRY_HOURS = 4                     # entry and exit at the 1h open four hours after the Monday 00:00 stamp
SHORT_SIDE = 0.5                    # equal weight across new perps, .5 in total, against BTC perp long .5
STOP = 1.5                          # resting stop at 1.5x entry
BOOK_USD = 10_000                   # paper book; the depth walk is also reported at 1,000 USD per name
DEPTH_LIMIT = 100
UNDERLYING = "COIN"                 # exchangeInfo underlyingType; equities, commodities, FX, indices and pre-market are out
BTC = "BTCUSDT"
FIRST_STAMP = "2026-11-09"
BOOKS = ("core", "alpha")           # operator's ruling 2026-11-03: perps tagged Alpha run as a separate paper book
READ_SYMBOL_WEEKS = 60              # read once, at the first Monday with this many settled symbol-weeks in the core book


def root() -> Path:
    """Root of everything the log writes. Inside the repo it is data/newperp; outside it a rehearsal may point it anywhere but a runner store."""
    home = REPO / "data" / "newperp"
    r = Path(os.environ.get("NEWPERP_ROOT") or home).resolve()
    sel.refuse_runner_store(r)
    if r.is_relative_to(REPO) and r != home:
        raise ValueError(f"NEWPERP_ROOT {r} is inside the repo and is not data/newperp")
    return r


def ledger_path(r: Path) -> Path:
    return r / "ledger.jsonl"


def status_path(r: Path) -> Path:
    return r / "status.json"
