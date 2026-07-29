"""
Market data collector — uses CCXT to fetch OHLCV from crypto exchanges.

Usage:
    from trading_agent.data.collector import Collector
    c = Collector("binance")
    df = c.fetch_ohlcv("BTC/USDT", "1h", since="2025-01-01")
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any

import ccxt
import polars as pl
from rich.console import Console
from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
)

from trading_agent.config.loader import config

console = Console()
_error_console = Console(stderr=True, style="red")


# ── Exchange factory ──────────────────────────────────────────────────────

_EXCHANGE_CACHE: dict[str, ccxt.Exchange] = {}


def get_exchange(name: str) -> ccxt.Exchange:
    """Get or create a CCXT exchange instance with safe defaults."""
    if name not in _EXCHANGE_CACHE:
        exch_config: dict[str, Any] = config.exchanges.get(name, {})
        exchange_class = getattr(ccxt, name, None)
        if exchange_class is None:
            raise ValueError(f"Unknown exchange: {name}")

        ex: ccxt.Exchange = exchange_class({
            "enableRateLimit": True,
            "options": {"defaultType": exch_config.get("type", "spot")},
        })
        _EXCHANGE_CACHE[name] = ex
    return _EXCHANGE_CACHE[name]


# ── Collector ─────────────────────────────────────────────────────────────


class Collector:
    """High-level data collector for a single exchange."""

    def __init__(self, exchange_name: str) -> None:
        self.exchange_name = exchange_name
        self.exchange = get_exchange(exchange_name)

    def available_symbols(self) -> list[str]:
        """List all tradeable symbols on this exchange."""
        markets = self.exchange.load_markets()
        return list(markets.keys())

    def fetch_ohlcv(
        self,
        symbol: str,
        timeframe: str = "1h",
        *,
        since: str | int | None = None,
        limit: int | None = None,
        progress: bool = True,
    ) -> pl.DataFrame:
        """Fetch OHLCV candles and return a Polars DataFrame.

        Parameters
        ----------
        symbol : str
            e.g. ``"BTC/USDT"``
        timeframe : str
            e.g. ``"1h"``, ``"5m"``, ``"1d"``
        since : str | int | None
            Start date as ISO string (``"2025-01-01"``) or unix ms.
            ``None`` = last ``limit`` candles.
        limit : int | None
            Max candles per request. Defaults to ``config.batch_size``.
        progress : bool
            Show a progress bar when fetching multiple pages.
        """
        limit = limit or config.batch_size
        ms_since = self._resolve_since(since)

        if ms_since is not None:
            return self._fetch_paginated(symbol, timeframe, ms_since, limit, progress)
        else:
            return self._fetch_single(symbol, timeframe, limit)

    # ── internal ──────────────────────────────────────────────────────

    def _resolve_since(self, since: str | int | None) -> int | None:
        if since is None:
            return None
        if isinstance(since, int):
            return since
        try:
            dt = datetime.fromisoformat(since)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return int(dt.timestamp() * 1000)
        except ValueError:
            _error_console.print(f"[red]Invalid date format: {since}[/red]")
            return None

    def _fetch_single(self, symbol: str, tf: str, limit: int) -> pl.DataFrame:
        raw = self._fetch_with_retry(symbol, tf, limit=limit)
        return self._raw_to_df(raw, self.exchange_name, symbol, tf)

    def _fetch_paginated(
        self, symbol: str, tf: str, since_ms: int, limit: int, show_progress: bool
    ) -> pl.DataFrame:
        all_candles: list[list] = []
        now_ms = int(time.time() * 1000)
        tf_ms = self._timeframe_ms(tf)

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
            TimeElapsedColumn(),
            console=console,
            disable=not show_progress,
        ) as prog:
            task = prog.add_task(
                f"[cyan]Fetching {symbol} {tf}…", total=now_ms - since_ms
            )

            cursor = since_ms
            while cursor < now_ms:
                raw = self._fetch_with_retry(symbol, tf, since=cursor, limit=limit)
                if not raw:
                    break  # no more data

                all_candles.extend(raw)

                # Advance cursor: last candle's timestamp + 1 tf
                last_ts: int = raw[-1][0]
                cursor = last_ts + tf_ms
                prog.update(task, completed=min(cursor - since_ms, now_ms - since_ms))

                # Small pause to be gentle on the exchange
                time.sleep(0.1)

        return self._raw_to_df(all_candles, self.exchange_name, symbol, tf)

    def _fetch_with_retry(
        self, symbol: str, tf: str, *, since: int | None = None, limit: int | None = None
    ) -> list[list]:
        """Fetch OHLCV with retry logic."""
        last_error: Exception | None = None
        for attempt in range(config.max_retries + 1):
            try:
                return self.exchange.fetch_ohlcv(symbol, tf, since=since, limit=limit)
            except ccxt.RateLimitExceeded:
                wait = self.exchange.rateLimit / 1000 + 1
                _error_console.print(
                    f"[yellow]Rate limited, waiting {wait:.0f}s…[/yellow]"
                )
                time.sleep(wait)
            except ccxt.NetworkError as e:
                last_error = e
                _error_console.print(
                    f"[yellow]Network error (attempt {attempt + 1}): {e}[/yellow]"
                )
                time.sleep(config.retry_delay_sec)
            except Exception as e:
                last_error = e
                if attempt == config.max_retries:
                    raise
                time.sleep(config.retry_delay_sec)

        raise RuntimeError(
            f"Failed to fetch {symbol} {tf} after {config.max_retries} retries"
        ) from last_error

    # ── helpers ───────────────────────────────────────────────────────

    @staticmethod
    def _timeframe_ms(tf: str) -> int:
        """Convert CCXT timeframe string to milliseconds."""
        mapping = {
            "1m": 60000,
            "5m": 300000,
            "15m": 900000,
            "30m": 1800000,
            "1h": 3600000,
            "4h": 14400000,
            "1d": 86400000,
            "1w": 604800000,
        }
        return mapping.get(tf, 3600000)

    @staticmethod
    def _raw_to_df(
        raw: list[list], exchange: str, symbol: str, tf: str
    ) -> pl.DataFrame:
        """Convert CCXT raw OHLCV list to a Polars DataFrame."""
        if not raw:
            return pl.DataFrame(
                schema={
                    "timestamp": pl.Datetime,
                    "open": pl.Float64,
                    "high": pl.Float64,
                    "low": pl.Float64,
                    "close": pl.Float64,
                    "volume": pl.Float64,
                }
            )

        df = pl.DataFrame(
            {
                "timestamp": [int(c[0]) for c in raw],
                "open": [float(c[1]) for c in raw],
                "high": [float(c[2]) for c in raw],
                "low": [float(c[3]) for c in raw],
                "close": [float(c[4]) for c in raw],
                "volume": [float(c[5]) for c in raw],
            }
        ).with_columns(
            pl.from_epoch("timestamp", time_unit="ms").alias("timestamp"),
            pl.lit(exchange).alias("exchange"),
            pl.lit(symbol).alias("symbol"),
            pl.lit(tf).alias("timeframe"),
        )

        return df.sort("timestamp")


# ── High-level helper ─────────────────────────────────────────────────────


def download_all_symbols(exchange_name: str | None = None) -> None:
    """Download all configured symbols for the given (or default) exchange."""
    exchange_name = exchange_name or config.default_exchange
    collector = Collector(exchange_name)

    exchange_symbols = config.symbols.get(exchange_name, [])
    if not exchange_symbols:
        _error_console.print(f"[red]No symbols configured for {exchange_name}[/red]")
        return

    for symbol in exchange_symbols:
        console.print(f"\n[bold]── {symbol} ──[/bold]")
        for tf in config.timeframes:
            try:
                df = collector.fetch_ohlcv(symbol, tf, since=None, limit=500)
                n = len(df)
                if n > 0:
                    from trading_agent.data.storage import save_ohlcv

                    path = save_ohlcv(df, exchange_name, symbol, tf)
                    console.print(
                        f"  [green]✓[/green] {tf:>4}  {n:>6,} candles → {path}"
                    )
                else:
                    console.print(f"  [yellow]–[/yellow] {tf:>4}  no data")
            except Exception as e:
                _error_console.print(f"  [red]✗[/red] {tf:>4}  {e}")
