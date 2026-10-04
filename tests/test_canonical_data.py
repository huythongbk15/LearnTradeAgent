"""Canonical market-data resolution.

Every symbol directory carries up to three hourly files that are not
interchangeable — `1h` runs 2023-01 to 2026-08 while `1h_extended` and
`1h_full` run 2020-01 to 2026-09 — and the last two are byte-identical for
every symbol that has both. A campaign in this repository measured the
shorter window without recording that the longer one existed.

These tests assert the resolution is deterministic, that duplicates are
detected rather than silently preferred, and that the fingerprint matches
the file it names.
"""

from __future__ import annotations

import hashlib
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

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "raw" / "binance"
SYMBOL_WITH_EXTENDED = "BTC_USDT"
SYMBOL_WITHOUT = "ADA_USDT"


def _has_extended() -> bool:
    return (DATA / SYMBOL_WITH_EXTENDED / "1h_extended.parquet").exists()


requires_extended = pytest.mark.skipif(
    not _has_extended(), reason="extended hourly file not present"
)


# ── resolution picks the longest history ─────────────────────────────────

@requires_extended
def test_hourly_resolves_to_extended_history():
    r = resolve_canonical(ROOT, SYMBOL_WITH_EXTENDED, "1h")
    assert r is not None
    assert r.path.name == "1h_extended.parquet"
    assert r.rows > 31_783  # the 1h file stops at 31,783 bars
    assert r.preferred is True


def test_symbol_without_extended_falls_back_to_plain_hourly():
    r = resolve_canonical(ROOT, SYMBOL_WITHOUT, "1h")
    assert r is not None
    assert r.path.name == "1h.parquet"
    # Fallback is not an error: fourteen of seventeen symbols have only 1h.
    assert r.rows > 0


def test_aliases_resolve_to_the_same_file():
    a = resolve_canonical(ROOT, SYMBOL_WITHOUT, "1h")
    b = resolve_canonical(ROOT, SYMBOL_WITHOUT, "60m")
    assert a is not None and b is not None
    assert a.path == b.path


# ── duplicates ───────────────────────────────────────────────────────────

@requires_extended
def test_extended_and_full_are_byte_identical():
    extended = DATA / SYMBOL_WITH_EXTENDED / "1h_extended.parquet"
    full = DATA / SYMBOL_WITH_EXTENDED / "1h_full.parquet"
    if not full.exists():
        pytest.skip("1h_full absent")
    assert sha256_file(extended) == sha256_file(full)


@requires_extended
def test_census_flags_duplicate_datasets():
    rows = describe(ROOT)
    hourly = [r for r in rows if r["timeframe"] == "hourly"]
    dupes = [r for r in hourly if r["duplicate_datasets"]]
    for row in dupes:
        assert "1h_extended" in row["available_files"]
        assert "1h_full" in row["available_files"]
        assert row["canonical_path"].endswith("1h_extended.parquet")


def test_census_covers_every_symbol_directory():
    rows = describe(ROOT)
    symbols = {r["symbol"] for r in rows}
    on_disk = {p.name for p in DATA.iterdir() if p.is_dir()}
    # Every directory has at least one bucket, or none of them.
    missing = on_disk - symbols
    for symbol in missing:
        has_any = any((DATA / symbol / f"{n}.parquet").exists()
                      for names in PREFERENCE.values() for n in names)
        assert not has_any, f"{symbol} has data but the census missed it"


# ── fingerprint ──────────────────────────────────────────────────────────

def test_recorded_sha256_matches_the_file():
    r = resolve_canonical(ROOT, SYMBOL_WITHOUT, "1d")
    assert r is not None
    raw = hashlib.sha256(r.path.read_bytes()).hexdigest()
    assert r.sha256 == raw


def test_row_count_matches_the_file():
    r = resolve_canonical(ROOT, SYMBOL_WITHOUT, "1d")
    assert r is not None
    assert r.rows == pl.read_parquet(r.path, columns=["close"]).height


def test_manifest_entry_carries_what_the_contract_requires():
    r = resolve_canonical(ROOT, SYMBOL_WITHOUT, "1d")
    assert r is not None
    entry = r.manifest_entry
    for key in ("input_path", "input_rows", "sha256", "requested", "canonical"):
        assert key in entry
    assert entry["input_rows"] == r.rows


# ── the two hourly datasets really do differ ─────────────────────────────

@requires_extended
def test_plain_hourly_and_extended_have_different_digests():
    short = DATA / SYMBOL_WITH_EXTENDED / "1h.parquet"
    long = DATA / SYMBOL_WITH_EXTENDED / "1h_extended.parquet"
    if not short.exists():
        pytest.skip("plain hourly absent")
    assert sha256_file(short) != sha256_file(long)
    short_rows = pl.read_parquet(short, columns=["close"]).height
    long_rows = pl.read_parquet(long, columns=["close"]).height
    assert long_rows > short_rows


# ── unknown input ────────────────────────────────────────────────────────

def test_unknown_timeframe_resolves_to_nothing():
    assert resolve_canonical(ROOT, SYMBOL_WITHOUT, "3m") is None


def test_unknown_symbol_resolves_to_nothing():
    assert resolve_canonical(ROOT, "NOPE_USDT", "1d") is None


def test_assert_canonical_raises_on_missing():
    with pytest.raises(FileNotFoundError):
        assert_canonical(None)


def test_assert_canonical_passes_through():
    r = resolve_canonical(ROOT, SYMBOL_WITHOUT, "1d")
    assert assert_canonical(r) is r


def test_timeframe_aliases_are_declared():
    # Every preference bucket must be reachable through at least one alias.
    reachable = {TF_ALIASES[a] for a in TF_ALIASES}
    assert reachable == set(PREFERENCE)
