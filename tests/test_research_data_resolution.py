from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

import polars as pl
import pytest

from trading_agent.data.canonical import resolve_canonical
from trading_agent.data.storage import load_ohlcv, load_research_ohlcv


def write_history(directory, name, count):
    directory.mkdir(parents=True, exist_ok=True)
    frame = pl.DataFrame(
        {
            "timestamp": [
                datetime(2025, 1, 1, tzinfo=UTC) + timedelta(hours=i)
                for i in range(count)
            ],
            "open": [100.0] * count,
            "high": [101.0] * count,
            "low": [99.0] * count,
            "close": [100.0] * count,
            "volume": [1.0] * count,
        }
    )
    frame.write_parquet(directory / f"{name}.parquet")
    return frame


@pytest.mark.parametrize("symbol", ["BTC/USDT", "BTC_USDT"])
@pytest.mark.parametrize("timeframe", ["1h", "60m", "1h_full"])
def test_research_and_selector_resolve_same_extended_history(
    tmp_path, symbol, timeframe
):
    base = tmp_path / "custom_storage"
    directory = base / "binance" / "BTC_USDT"
    write_history(directory, "1h", 2)
    expected = write_history(directory, "1h_extended", 10)
    with patch(
        "trading_agent.data.storage.config",
        SimpleNamespace(project_root=tmp_path, storage_abs_path=base),
    ):
        actual = load_research_ohlcv("binance", symbol, timeframe)
        assert actual.equals(expected)
        resolution = resolve_canonical(tmp_path, symbol, timeframe, storage_base=base)
        assert resolution.rows == actual.height
        # Ingestion/live exact-path semantics must not change.
        assert load_ohlcv("binance", symbol, "1h").height == 2


def test_research_bounds_are_start_inclusive_end_exclusive(tmp_path):
    base = tmp_path / "data" / "raw"
    frame = write_history(base / "binance" / "BTC_USDT", "1h", 10)
    with patch(
        "trading_agent.data.storage.config",
        SimpleNamespace(project_root=tmp_path, storage_abs_path=base),
    ):
        result = load_research_ohlcv(
            "binance",
            "BTC/USDT",
            "1h",
            start=frame["timestamp"][2],
            end=frame["timestamp"][5],
        )
        assert result.equals(frame.slice(2, 3))


def test_missing_canonical_history_does_not_fall_back_to_other_root(tmp_path):
    with patch(
        "trading_agent.data.storage.config",
        SimpleNamespace(project_root=tmp_path, storage_abs_path=tmp_path / "missing"),
    ):
        with pytest.raises(FileNotFoundError):
            load_research_ohlcv("binance", "BTC/USDT", "1h")
