"""
Data storage layer — saves & loads market data.

Current backend: Parquet files (fast, columnar, queryable with DuckDB later).
"""

from __future__ import annotations

from pathlib import Path

import polars as pl

from trading_agent.config.loader import config


def _table_path(
    exchange: str,
    symbol: str,
    timeframe: str,
    base: Path | None = None,
) -> Path:
    """Return the parquet path for a given (exchange, symbol, timeframe)."""
    base = base or config.storage_abs_path
    # Normalise symbol: replace / → _
    safe_symbol = symbol.replace("/", "_").replace(":", "_")
    return base / exchange / safe_symbol / f"{timeframe}.parquet"


def save_ohlcv(
    df: pl.DataFrame,
    exchange: str,
    symbol: str,
    timeframe: str,
    *,
    append: bool = True,
) -> Path:
    """Save OHLCV DataFrame to parquet. Appends by default.

    If *append* is True and the file already exists, new data is merged
    (overwriting duplicates on ``timestamp``) and sorted.
    """
    path = _table_path(exchange, symbol, timeframe)
    path.parent.mkdir(parents=True, exist_ok=True)

    # Ensure required columns
    required = {"timestamp", "open", "high", "low", "close", "volume"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"DataFrame missing columns: {missing}")

    if append and path.exists():
        existing = pl.read_parquet(path)
        df = pl.concat([existing, df]).unique(
            subset=["timestamp"], keep="last"
        ).sort("timestamp")

    df.write_parquet(path)
    return path


def load_ohlcv(
    exchange: str,
    symbol: str,
    timeframe: str,
    *,
    start: str | None = None,
    end: str | None = None,
) -> pl.DataFrame:
    """Load OHLCV data from parquet, optionally filtered by date range.

    Parameters
    ----------
    start, end : str, optional
        ISO-format dates e.g. ``"2025-01-01"``. Inclusive on start,
        exclusive on end.
    """
    path = _table_path(exchange, symbol, timeframe)
    if not path.exists():
        raise FileNotFoundError(
            f"No data for {exchange} {symbol} {timeframe} at {path}"
        )

    df = pl.read_parquet(path).sort("timestamp")

    if start:
        df = df.filter(pl.col("timestamp") >= start)
    if end:
        df = df.filter(pl.col("timestamp") < end)
    return df


def get_date_range(
    exchange: str,
    symbol: str,
    timeframe: str,
) -> dict:
    """Return date range info for a stored dataset.

    Returns
    -------
    dict with keys: ``start`` (datetime), ``end`` (datetime), ``count`` (int).

    Raises FileNotFoundError if no data exists.
    """
    path = _table_path(exchange, symbol, timeframe)
    if not path.exists():
        raise FileNotFoundError(
            f"No data for {exchange} {symbol} {timeframe} at {path}"
        )

    df = pl.read_parquet(path)
    n = len(df)
    if n == 0:
        return {"start": None, "end": None, "count": 0}

    return {
        "start": df["timestamp"].min(),
        "end": df["timestamp"].max(),
        "count": n,
    }


def list_datasets() -> list[dict[str, str]]:
    """List all available datasets in storage."""
    base = config.storage_abs_path
    if not base.exists():
        return []

    datasets = []
    for exchange_dir in sorted(base.iterdir()):
        if not exchange_dir.is_dir():
            continue
        for symbol_dir in sorted(exchange_dir.iterdir()):
            if not symbol_dir.is_dir():
                continue
            for parquet_file in sorted(symbol_dir.glob("*.parquet")):
                datasets.append({
                    "exchange": exchange_dir.name,
                    "symbol": symbol_dir.name.replace("_", "/"),
                    "timeframe": parquet_file.stem,
                })
    return datasets
