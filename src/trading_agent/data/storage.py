"""
Data storage layer — saves & loads market data.

Current backend: Parquet files (fast, columnar, queryable with DuckDB later).
"""

from __future__ import annotations

import os
import logging
import tempfile
import threading
from contextlib import contextmanager
from pathlib import Path

import polars as pl

from trading_agent.config.loader import config
from trading_agent.execution.indicators import compute_atr

logger = logging.getLogger(__name__)
_STORAGE_LOCK = threading.RLock()

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows development fallback
    fcntl = None


@contextmanager
def _dataset_lock(path: Path):
    """Serialize read-merge-write across threads and Unix processes."""
    lock_path = path.with_suffix(path.suffix + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with _STORAGE_LOCK, lock_path.open("a+") as lock_file:
        if fcntl is not None:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            if fcntl is not None:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def _write_parquet_atomic(df: pl.DataFrame, path: Path) -> None:
    """Write a complete parquet file and atomically replace the old version."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{path.stem}.", suffix=".tmp.parquet", dir=path.parent
    )
    os.close(fd)
    tmp_path = Path(tmp_name)
    try:
        df.write_parquet(tmp_path)
        os.replace(tmp_path, path)
    finally:
        tmp_path.unlink(missing_ok=True)


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

    with _dataset_lock(path):
        if append and path.exists():
            existing = pl.read_parquet(path)
            had_atr = "atr" in existing.columns
            df = (
                pl.concat([existing, df], how="diagonal_relaxed")
                .unique(subset=["timestamp"], keep="last")
                .sort("timestamp")
            )
            # ATR is derived data. Recompute it after an append instead of
            # leaving the newly appended rows as null/stale values.
            if had_atr:
                df = df.with_columns(compute_atr(df, period=14))

        _write_parquet_atomic(df, path)
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
                datasets.append(
                    {
                        "exchange": exchange_dir.name,
                        "symbol": symbol_dir.name.replace("_", "/"),
                        "timeframe": parquet_file.stem,
                    }
                )
    return datasets


def enrich_with_atr(
    exchange: str,
    symbol: str,
    timeframe: str,
    period: int = 14,
) -> Path:
    """Load OHLCV data, compute ATR, and overwrite with enriched data.

    This pre-computes ATR and stores it in the parquet file, so downstream
    consumers (paper exchange, risk controller) don't need to compute it
    on-demand.

    ⚠️  WARNING FOR BACKTESTING:
    This computes ATR using high/low/close of the SAME bar (t).
    If used for backtesting, position sizing at bar t would use ATR[t]
    which is ONLY known after bar t closes — LOOK-AHEAD BIAS.

    For backtesting, DO NOT use pre-enriched ATR. Instead, compute ATR
    on-the-fly in the strategy/engine with shift(1) so ATR[t-1] is used
    for bar t's position sizing.
    """
    path = _table_path(exchange, symbol, timeframe)
    if not path.exists():
        raise FileNotFoundError(
            f"No data for {exchange} {symbol} {timeframe} at {path}"
        )

    with _dataset_lock(path):
        df = pl.read_parquet(path).sort("timestamp")
        if df.is_empty():
            logger.warning(f"Empty dataset: {exchange} {symbol} {timeframe}")
            return path

        atr_series = compute_atr(df, period=period)
        df = df.with_columns(atr_series)
        _write_parquet_atomic(df, path)
    logger.info(f"Enriched {exchange} {symbol} {timeframe} with ATR (period={period})")
    return path


def enrich_all_datasets(period: int = 14) -> list[Path]:
    """Enrich all stored datasets with ATR."""
    enriched = []
    for ds in list_datasets():
        try:
            path = enrich_with_atr(
                ds["exchange"],
                ds["symbol"],
                ds["timeframe"],
                period=period,
            )
            enriched.append(path)
        except Exception as e:
            logger.warning(f"Failed to enrich {ds}: {e}")
    return enriched
