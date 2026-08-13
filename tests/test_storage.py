"""Tests for data/storage.py"""

from __future__ import annotations

import tempfile
from pathlib import Path

import polars as pl
import pytest

# Redirect storage for the module under test. IMPORTANT: the redirect is
# applied via an autouse fixture with monkeypatch (auto-restored after each
# test) — never assign at module level, because pytest imports all test
# modules during collection and a module-level assignment would permanently
# rewrite the shared config object, breaking later tests that read real data
# (e.g. orchestrator analyze() -> load_ohlcv).
_TEMP = Path(tempfile.mkdtemp())

import trading_agent.data.storage as storage_mod  # noqa: E402

from trading_agent.data.storage import (  # noqa: E402
    get_date_range,
    list_datasets,
    load_ohlcv,
    save_ohlcv,
)


@pytest.fixture(autouse=True)
def _redirect_storage(monkeypatch):
    """Point storage at the temp dir for this test, then restore."""
    monkeypatch.setattr(storage_mod.config, "storage_path", str(_TEMP))


def _fake_candle(ts: str) -> dict:
    return {
        "timestamp": ts,
        "open": 100.0,
        "high": 101.0,
        "low": 99.0,
        "close": 100.5,
        "volume": 50.0,
    }


class TestSaveLoad:
    exchange = "test_exchange"
    symbol = "BTC/USDT"
    tf = "1h"

    def _table_path(self) -> Path:
        return _TEMP / self.exchange / "BTC_USDT" / f"{self.tf}.parquet"

    def setup_method(self):
        path = self._table_path().parent
        if path.exists():
            import shutil

            shutil.rmtree(path)

    def test_save_and_load(self):
        df = pl.DataFrame([_fake_candle("2026-01-01 00:00:00")])
        save_ohlcv(df, self.exchange, self.symbol, self.tf)

        loaded = load_ohlcv(self.exchange, self.symbol, self.tf)
        assert len(loaded) == 1
        assert loaded["close"][0] == 100.5

    def test_append_dedup(self):
        df1 = pl.DataFrame([_fake_candle("2026-01-01 00:00:00")])
        save_ohlcv(df1, self.exchange, self.symbol, self.tf, append=True)

        df2 = pl.DataFrame([_fake_candle("2026-01-01 00:00:00")])
        save_ohlcv(df2, self.exchange, self.symbol, self.tf, append=True)

        loaded = load_ohlcv(self.exchange, self.symbol, self.tf)
        assert len(loaded) == 1  # dedup

    def test_append_new_rows(self):
        df1 = pl.DataFrame([_fake_candle("2026-01-01 00:00:00")])
        save_ohlcv(df1, self.exchange, self.symbol, self.tf, append=True)

        df2 = pl.DataFrame([_fake_candle("2026-01-01 01:00:00")])
        save_ohlcv(df2, self.exchange, self.symbol, self.tf, append=True)

        loaded = load_ohlcv(self.exchange, self.symbol, self.tf)
        assert len(loaded) == 2

    def test_load_nonexistent(self):
        with pytest.raises(FileNotFoundError):
            load_ohlcv("nonexist", "XXX/USDT", "1h")

    def test_missing_columns(self):
        bad_df = pl.DataFrame({"foo": [1, 2]})
        with pytest.raises(ValueError, match="missing columns"):
            save_ohlcv(bad_df, self.exchange, self.symbol, self.tf)

    def test_list_datasets(self):
        # Save first, then list
        df = pl.DataFrame([_fake_candle("2026-01-01 00:00:00")])
        save_ohlcv(df, self.exchange, self.symbol, self.tf)
        datasets = list_datasets()
        assert len(datasets) >= 1

    def test_get_date_range(self):
        df = pl.DataFrame([_fake_candle("2026-01-01 00:00:00")])
        save_ohlcv(df, self.exchange, self.symbol, self.tf)
        rng = get_date_range(self.exchange, self.symbol, self.tf)
        assert rng["count"] >= 1
        assert rng["start"] is not None
        assert rng["end"] is not None

    def test_get_date_range_nonexistent(self):
        with pytest.raises(FileNotFoundError):
            get_date_range("nonexist", "XXX/USDT", "1h")
