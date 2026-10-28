"""Canonical market-data resolution.

Every symbol directory carries up to three hourly files that are not
interchangeable — `1h` runs 2023-01 to 2026-08 while `1h_extended` and
`1h_full` run 2020-01 to 2026-09 — and the last two are byte-identical for
every symbol that has both. A campaign in this repository measured the
shorter window without recording that the longer one existed.

These tests assert the resolution is deterministic, that duplicates are
detected rather than silently preferred, and that the fingerprint matches
the file it names.

These are pure unit tests: all fixtures are synthetic parquet files written
into a per-test temporary directory so they pass on a clean checkout with
no tracked market data.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta
from pathlib import Path

import polars as pl
import pytest

from trading_agent.data.canonical import (
    PREFERENCE,
    TF_ALIASES,
    assert_canonical,
    describe,
    resolve_canonical,
    sha256_file,
)

SYMBOL_WITH_EXTENDED = "BTC_USDT"
SYMBOL_WITHOUT = "ADA_USDT"


def _make_ohlcv(n: int, start: datetime, base: float = 100.0) -> pl.DataFrame:
    """Deterministic hourly OHLCV frame with timestamp + close."""
    ts = [start + timedelta(hours=i) for i in range(n)]
    return pl.DataFrame(
        {
            "timestamp": ts,
            "open": [base + i * 0.01 for i in range(n)],
            "high": [base + i * 0.01 + 1.0 for i in range(n)],
            "low": [base + i * 0.01 - 1.0 for i in range(n)],
            "close": [base + i * 0.1 for i in range(n)],
            "volume": [1.0] * n,
        }
    )


@pytest.fixture
def synthetic_data_root(tmp_path: Path) -> Path:
    """Create a synthetic ``data/raw/binance`` tree inside *tmp_path*.

    Layout mirrors the real schema (symbol/timeframe.parquet) but uses
    tiny deterministic frames so every test is self-contained and
    reproducible without gitignored market data.
    """
    binance_dir = tmp_path / "data" / "raw" / "binance"

    # BTC_USDT — has short 1h + extended + full (byte-identical to extended)
    btc = binance_dir / SYMBOL_WITH_EXTENDED
    btc.mkdir(parents=True)
    short = _make_ohlcv(100, datetime(2023, 1, 1), base=100.0)
    short.write_parquet(btc / "1h.parquet")
    long = _make_ohlcv(300, datetime(2020, 1, 1), base=100.0)
    long.write_parquet(btc / "1h_extended.parquet")
    long.write_parquet(btc / "1h_full.parquet")  # byte-identical to extended

    # ADA_USDT — 1h only (no extended), plus 1d
    ada = binance_dir / SYMBOL_WITHOUT
    ada.mkdir(parents=True)
    _make_ohlcv(50, datetime(2024, 1, 1), base=10.0).write_parquet(ada / "1h.parquet")
    _make_ohlcv(30, datetime(2024, 1, 1), base=10.0).write_parquet(ada / "1d.parquet")

    return tmp_path


@pytest.fixture
def synthetic_data(synthetic_data_root: Path) -> Path:
    """Convenience alias returning the binance data directory."""
    return synthetic_data_root / "data" / "raw" / "binance"


# ── resolution picks the longest history ─────────────────────────────────


def test_hourly_resolves_to_extended_history(synthetic_data_root: Path):
    r = resolve_canonical(synthetic_data_root, SYMBOL_WITH_EXTENDED, "1h")
    assert r is not None
    assert r.path.name == "1h_extended.parquet"
    # Extended must have more rows than the short file
    short_rows = pl.read_parquet(
        synthetic_data_root / "data" / "raw" / "binance" / SYMBOL_WITH_EXTENDED / "1h.parquet",
        columns=["close"],
    ).height
    assert r.rows > short_rows
    assert r.preferred is True


def test_symbol_without_extended_falls_back_to_plain_hourly(synthetic_data_root: Path):
    r = resolve_canonical(synthetic_data_root, SYMBOL_WITHOUT, "1h")
    assert r is not None
    assert r.path.name == "1h.parquet"
    assert r.rows > 0


def test_aliases_resolve_to_the_same_file(synthetic_data_root: Path):
    a = resolve_canonical(synthetic_data_root, SYMBOL_WITHOUT, "1h")
    b = resolve_canonical(synthetic_data_root, SYMBOL_WITHOUT, "60m")
    assert a is not None and b is not None
    assert a.path == b.path


# ── duplicates ───────────────────────────────────────────────────────────


def test_extended_and_full_are_byte_identical(synthetic_data_root: Path, synthetic_data: Path):
    extended = synthetic_data / SYMBOL_WITH_EXTENDED / "1h_extended.parquet"
    full = synthetic_data / SYMBOL_WITH_EXTENDED / "1h_full.parquet"
    assert sha256_file(extended) == sha256_file(full)


def test_census_flags_duplicate_datasets(synthetic_data_root: Path):
    rows = describe(synthetic_data_root)
    hourly = [r for r in rows if r["timeframe"] == "hourly"]
    dupes = [r for r in hourly if r["duplicate_datasets"]]
    assert len(dupes) >= 1  # BTC_USDT has both extended and full
    for row in dupes:
        assert "1h_extended" in row["available_files"]
        assert "1h_full" in row["available_files"]
        assert row["canonical_path"].endswith("1h_extended.parquet")


def test_census_covers_every_symbol_directory(synthetic_data_root: Path):
    rows = describe(synthetic_data_root)
    symbols = {r["symbol"] for r in rows}
    data_dir = synthetic_data_root / "data" / "raw" / "binance"
    on_disk = {p.name for p in data_dir.iterdir() if p.is_dir()}
    missing = on_disk - symbols
    for symbol in missing:
        has_any = any(
            (data_dir / symbol / f"{n}.parquet").exists()
            for names in PREFERENCE.values()
            for n in names
        )
        assert not has_any, f"{symbol} has data but the census missed it"


# ── fingerprint ──────────────────────────────────────────────────────────


def test_recorded_sha256_matches_the_file(synthetic_data_root: Path):
    r = resolve_canonical(synthetic_data_root, SYMBOL_WITHOUT, "1d")
    assert r is not None
    raw = hashlib.sha256(r.path.read_bytes()).hexdigest()
    assert r.sha256 == raw


def test_row_count_matches_the_file(synthetic_data_root: Path):
    r = resolve_canonical(synthetic_data_root, SYMBOL_WITHOUT, "1d")
    assert r is not None
    assert r.rows == pl.read_parquet(r.path, columns=["close"]).height


def test_manifest_entry_carries_what_the_contract_requires(synthetic_data_root: Path):
    r = resolve_canonical(synthetic_data_root, SYMBOL_WITHOUT, "1d")
    assert r is not None
    entry = r.manifest_entry
    for key in ("input_path", "input_rows", "sha256", "requested", "canonical"):
        assert key in entry
    assert entry["input_rows"] == r.rows


# ── the two hourly datasets really do differ ─────────────────────────────


def test_plain_hourly_and_extended_have_different_digests(synthetic_data_root: Path, synthetic_data: Path):
    short = synthetic_data / SYMBOL_WITH_EXTENDED / "1h.parquet"
    long = synthetic_data / SYMBOL_WITH_EXTENDED / "1h_extended.parquet"
    assert sha256_file(short) != sha256_file(long)
    short_rows = pl.read_parquet(short, columns=["close"]).height
    long_rows = pl.read_parquet(long, columns=["close"]).height
    assert long_rows > short_rows


# ── unknown input ────────────────────────────────────────────────────────


def test_unknown_timeframe_resolves_to_nothing(synthetic_data_root: Path):
    assert resolve_canonical(synthetic_data_root, SYMBOL_WITHOUT, "3m") is None


def test_unknown_symbol_resolves_to_nothing(synthetic_data_root: Path):
    assert resolve_canonical(synthetic_data_root, "NOPE_USDT", "1d") is None


def test_assert_canonical_raises_on_missing():
    with pytest.raises(FileNotFoundError):
        assert_canonical(None)


def test_assert_canonical_passes_through(synthetic_data_root: Path):
    r = resolve_canonical(synthetic_data_root, SYMBOL_WITHOUT, "1d")
    assert assert_canonical(r) is r


def test_timeframe_aliases_are_declared():
    # Every preference bucket must be reachable through at least one alias.
    reachable = {TF_ALIASES[a] for a in TF_ALIASES}
    assert reachable == set(PREFERENCE)
