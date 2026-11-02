"""Frozen constants of the weekly paper selection run.

The rules are in docs/operations/SELECTION_PAPER_RUN.md. A value here changes only with an operator ruling on that page. Paths come from SELECTION_ROOT and SELECTION_SEED, read at call time so a test or a rehearsal can point them elsewhere.
"""
from __future__ import annotations

import os
import re
import unicodedata
from collections.abc import Iterable
from datetime import date
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[2]

# Option B of the operator's ruling of 2026-11-01: masked at every date, before and after the seal.
SEALED = frozenset({"ADA", "AVAX", "BNB", "DOGE", "DOT", "ETH", "LINK", "NEAR", "SOL", "TRX", "XRP", "ZEC"})
# Wrapped or staked tokens that carry a masked coin's price, masked with it wherever a masked coin is.
# BNSOL is staked SOL. The ruling names twelve coins; this one was added because a stored bar cannot be unread, while a mask can be lifted.
SEAL_PROXIES: frozenset[str] = frozenset({"BNSOL"})
BTC = "BTC"
LIST_N = 18

# The registered primary variant, nothing else runs.
PRIMARY_LOOK = 90
PRIMARY_K = 3
PRIMARY_CADENCE_WEEKS = 4

GRID_START = "2020-01-02"           # hour 0 of the hourly grid
CANDIDATE_START = "2020-04-13"      # first stamp of every candidate series
FIRST_GRID_DAY = "2020-07-13"       # first selection Monday, phase 0
DISCOVERY_END = "2025-07-01"        # first instant of the study's discovery that is not loaded
FROZEN_LAST_WEEK = "2026-08-03"     # last frozen list
RULE_FIRST_WEEK = "2026-08-10"      # first list from the rule

# Round trip bps. "tier" is the study's stamp cost; every basis is either that plus a constant or a flat number.
COST_BASES = {"tier": ("tier", 0.0), "tier_plus_10": ("tier", 10.0), "flat_30": ("flat", 30.0)}
MAIN_COST_BASIS = "tier_plus_10"
FILL_CLOCKS = ("stamp_open", "open_1h_later")
MAIN_FILL_CLOCK = "open_1h_later"

# Order of the page: all that match are written in this order, the first is the main reason.
REASONS = (
    "NO_KEPT_SELECTOR",
    "DATA_STALE_OR_GAP",
    "LIST_INCOMPLETE",
    "LATE_RUN",
    "SEAL_SCOPE",
    "ALL_SLOTS_NON_POSITIVE",
    "POLICY_EXPIRED",
    "BOOK_TRIPWIRE",
)
HOLD_REASONS = frozenset({"DATA_STALE_OR_GAP", "LIST_INCOMPLETE", "SEAL_SCOPE"})  # the due book keeps its list
LIST_FETCH_FAIL_LIMIT = 0.05        # share of pairs whose daily fetch may fail before LIST_INCOMPLETE

COHORT_WEEKS = 60
TRIPWIRE_Z = -2.5
TRIPWIRE_FROM_WEEK = 8
TRIPWIRE_WEEKLY_SD_BPS = 510.0
LATE_LIMIT_HOURS = 6
POLICY_VALIDITY_DAYS = 8

QUOTES = ("USDT", "USDC", "FDUSD", "BUSD", "TUSD", "BTC", "EUR")     # read off a symbol by base_of, so a masked coin is known under any quote
MULTIPLIER = re.compile(r"^(?:10+|1M)(?=[A-Z])")                     # 1000XRP, 1MDOGE: the same coin in a rebased unit
RUNNER_STORES = ("promotion_store", "state", "execution", "research")        # folders under data/ the testnet runner reads or writes

T0 = np.datetime64(f"{GRID_START}T00:00:00", "s")
HOUR_MS = 3_600_000
DAY_MS = 24 * HOUR_MS


class SealedSymbolError(ValueError):
    """A sealed coin reached the run. Every refusal raises this, so the SEAL_SCOPE reason has one source."""


def base_of(symbol: str) -> str:
    """Coin name of a symbol in any spelling, for the mask only: eth, ETHUSDT, ETH/USDT, ETH:USDT, ETHBTC, ETHFDUSD, 1000XRP, 1MDOGE."""
    s = re.sub(r"[^0-9A-Z]", "", unicodedata.normalize("NFKC", symbol).upper())
    for quote in QUOTES:
        if s.endswith(quote) and len(s) > len(quote):
            s = s[:-len(quote)]
            break
    return MULTIPLIER.sub("", s)


def is_sealed(symbol: str) -> bool:
    return base_of(symbol) in SEALED | SEAL_PROXIES


def sealed_hits(symbols: Iterable[str]) -> list[str]:
    return sorted({base_of(s) for s in symbols} & (SEALED | SEAL_PROXIES))


def assert_open(symbol: str) -> None:
    if is_sealed(symbol):
        raise SealedSymbolError(f"{symbol} is masked by the operator's ruling of 2026-11-01")


def assert_all_open(symbols: Iterable[str]) -> None:
    hits = sealed_hits(symbols)
    if hits:
        raise SealedSymbolError(f"masked coins reached the run: {hits}")


def ts(x) -> int:
    """Seconds since 1970 of a date, a datetime or an ISO string, UTC."""
    return int(np.datetime64(x, "s").astype(np.int64))


def hour_of(x) -> int:
    """Row of an instant on the hourly grid. A bar of row i opens at hour i and closes before hour i + 1."""
    return int((np.datetime64(x, "s") - T0) / np.timedelta64(1, "h"))


def weeks_since_first(monday) -> int:
    """Whole weeks from the first grid day. A day that is not on the Monday grid is refused."""
    delta = (date.fromisoformat(str(monday)[:10]) - date.fromisoformat(FIRST_GRID_DAY)).days
    if delta % 7:
        raise ValueError(f"{monday} is not a Monday")
    return delta // 7


def phase_of(monday) -> int:
    """The book that selects on this Monday."""
    return weeks_since_first(monday) % PRIMARY_CADENCE_WEEKS


def refuse_runner_store(path: Path) -> None:
    """A path of this run can never sit in a store the testnet runner reads, in this checkout or another."""
    for a, b in zip(path.parts, path.parts[1:]):
        if a == "data" and b in RUNNER_STORES:
            raise ValueError(f"{path} is inside a runner store")


def selection_root() -> Path:
    """Root of everything the run writes. Inside the repo it is data/selection and nothing else; outside it, a rehearsal may point it anywhere but a runner store."""
    home = REPO / "data" / "selection"
    root = Path(os.environ.get("SELECTION_ROOT") or home).resolve()
    refuse_runner_store(root)
    if root.is_relative_to(REPO) and root != home:
        raise ValueError(f"SELECTION_ROOT {root} is inside the repo and is not data/selection")
    return root


def seed_dir() -> Path:
    return Path(os.environ.get("SELECTION_SEED") or REPO / "data" / "selector_20261101_01" / "seed").resolve()


def write_text(path: Path, text: str) -> None:
    """Replace a file whole or not at all: a kill or a full disk leaves the old bytes, never half a file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf8")
    os.replace(tmp, path)


def bars_dir(root: Path) -> Path:
    return root / "bars"


def daily_dir(root: Path) -> Path:
    return root / "daily"


def lists_dir(root: Path) -> Path:
    return root / "lists"


def runs_dir(root: Path) -> Path:
    return root / "runs"


def registry_dir(root: Path) -> Path:
    return root / "registry"


def tier_path(root: Path) -> Path:
    return root / "tier_cost.json"
