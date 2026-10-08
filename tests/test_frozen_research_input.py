import os
import subprocess
import sys
from datetime import datetime, timedelta

import polars as pl
import pytest

from trading_agent.data.storage import frozen_research_data, load_research_ohlcv


def test_frozen_loader_uses_exact_file_and_rejects_changes(tmp_path):
    path = tmp_path / "bars.parquet"
    frame = pl.DataFrame(
        {
            "timestamp": [datetime(2024, 1, 1) + timedelta(hours=i) for i in range(3)],
            "close": [1.0, 2.0, 3.0],
        }
    )
    frame.write_parquet(path)
    with frozen_research_data(
        path, exchange="binance", symbol="BTC/USDT", timeframe="1h"
    ):
        assert load_research_ohlcv("binance", "BTC/USDT", "1h").equals(frame)
        child = subprocess.run(
            [
                sys.executable,
                "-c",
                "from trading_agent.data.storage import load_research_ohlcv; print(load_research_ohlcv('binance', 'BTC/USDT', '1h').height)",
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        assert child.stdout.strip().endswith("3")
        assert (
            load_research_ohlcv(
                "binance", "BTC/USDT", "1h", end=datetime(2024, 1, 1, 2)
            ).height
            == 2
        )
        with pytest.raises(ValueError, match="subject"):
            load_research_ohlcv("binance", "ETH/USDT", "1h")
        with pytest.raises(ValueError, match="already active"):
            with frozen_research_data(
                path, exchange="binance", symbol="BTC/USDT", timeframe="1h"
            ):
                pass
        frame.with_columns(pl.lit(99.0).alias("close")).write_parquet(path)
        with pytest.raises(ValueError, match="changed"):
            load_research_ohlcv("binance", "BTC/USDT", "1h")
    assert "TRADING_FROZEN_RESEARCH_DATA" not in os.environ
