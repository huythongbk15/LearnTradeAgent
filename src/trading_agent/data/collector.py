"""
Market data collector — uses CCXT to fetch OHLCV from crypto exchanges.

Usage:
    from trading_agent.data.collector import Collector
    c = Collector("binance")
    df = c.fetch_ohlcv("BTC/USDT", "1h", since="2025-01-01")
    df = c.update_ohlcv("BTC/USDT", "1h")          # incremental
    report = c.validate_data("BTC/USDT", "1h")      # data quality
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
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
    TimeRemainingColumn,
)

from trading_agent.config.loader import config
from trading_agent.data.storage import get_date_range, load_ohlcv, save_ohlcv

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

    # ── Public API ─────────────────────────────────────────────────────

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

    def update_ohlcv(
        self,
        symbol: str,
        timeframe: str = "1h",
        *,
        since: str | int | None = None,
        progress: bool = True,
    ) -> pl.DataFrame:
        """Incremental update — fetch only candles not yet in storage.

        1. Check what's the latest timestamp in local storage
        2. Fetch from there to now
        3. Append + dedup

        Parameters
        ----------
        symbol : str
        timeframe : str
        since : str | int | None
            Override — fetch from this date even if we have older data.
        progress : bool
        """
        # Determine fetch start point
        if since is not None:
            start_ms = self._resolve_since(since)
        else:
            try:
                rng = get_date_range(self.exchange_name, symbol, timeframe)
                # Start 1 candle before the stored end (for overlap safety)
                end_dt = rng["end"]
                start_dt = end_dt - timedelta(
                    milliseconds=self._timeframe_ms(timeframe)
                )
                start_ms = int(start_dt.timestamp() * 1000)
            except FileNotFoundError:
                # No existing data — fetch all time
                start_ms = None

        if start_ms is None:
            # Full fetch from beginning
            return self.fetch_ohlcv(symbol, timeframe, progress=progress)

        # Fetch only new data
        new_df = self.fetch_ohlcv(
            symbol, timeframe, since=start_ms, progress=progress
        )
        if new_df.is_empty():
            return new_df

        # Save (append + dedup handled by save_ohlcv)
        save_ohlcv(new_df, self.exchange_name, symbol, timeframe, append=True)

        rng_after = get_date_range(self.exchange_name, symbol, timeframe)
        console.print(
            f"  [dim]Updated {symbol} {timeframe}: "
            f"now has data {rng_after['start']} → {rng_after['end']}"
            f"  ({rng_after['count']:,} candles)[/dim]"
        )
        return new_df

    def validate_data(
        self,
        symbol: str,
        timeframe: str = "1h",
    ) -> dict:
        """Validate stored data quality and return a report dict.

        Checks performed:
        - File existence & readability
        - Gap detection (missing time intervals)
        - Price outlier detection
        - Volume outlier detection
        - Basic statistics
        """
        report = {
            "exchange": self.exchange_name,
            "symbol": symbol,
            "timeframe": timeframe,
            "status": "OK",
            "checks": {},
        }

        # 1. Load data
        try:
            df = load_ohlcv(self.exchange_name, symbol, timeframe)
        except FileNotFoundError:
            report["status"] = "NO_DATA"
            return report
        except Exception as e:
            report["status"] = "CORRUPTED"
            report["error"] = str(e)
            return report

        n = len(df)
        if n == 0:
            report["status"] = "EMPTY"
            return report

        # 2. Basic stats
        report["checks"]["row_count"] = n
        report["checks"]["date_range"] = {
            "start": str(df["timestamp"].min()),
            "end": str(df["timestamp"].max()),
        }

        # 3. Column completeness
        cols = ["open", "high", "low", "close", "volume"]
        nulls = {c: int(df[c].is_null().sum()) for c in cols}
        report["checks"]["null_counts"] = nulls

        # 4. Gap detection
        tf_ms = self._timeframe_ms(timeframe)
        ts = df["timestamp"].to_numpy().astype("int64") // 10**6  # ms
        diffs = ts[1:] - ts[:-1]
        gaps = diffs[diffs > tf_ms * 1.5]

        if len(gaps) > 0:
            # Find where gaps occur
            gap_indices = (diffs > tf_ms * 1.5).nonzero()[0]
            gap_samples = []
            for idx in gap_indices[:10]:  # max 10 examples
                gap_samples.append({
                    "from": str(df["timestamp"][idx]),
                    "to": str(df["timestamp"][idx + 1]),
                    "gap_ms": int(diffs[idx]),
                    "gap_candles": int(diffs[idx] / tf_ms),
                })
            report["checks"]["gaps"] = {
                "count": len(gaps),
                "total_missing_candles": int(sum(diffs[diffs > tf_ms * 1.5]) / tf_ms),
                "samples": gap_samples,
            }
        else:
            report["checks"]["gaps"] = {"count": 0}

        # 5. Price outlier detection (z-score on returns)
        returns = df.with_columns(
            ((pl.col("close") / pl.col("close").shift(1) - 1) * 100).alias("ret_pct")
        ).drop_nulls()

        if len(returns) > 0:
            mean_ret = returns["ret_pct"].mean()
            std_ret = returns["ret_pct"].std()
            if std_ret > 0:
                outliers = returns.filter(
                    (pl.col("ret_pct").abs() > mean_ret + 5 * std_ret)
                )
                report["checks"]["price_outliers"] = {
                    "count": len(outliers),
                    "threshold_pct": f"{5 * std_ret:.2f}%",
                    "max_return_pct": f"{returns['ret_pct'].max():.2f}%",
                    "min_return_pct": f"{returns['ret_pct'].min():.2f}%",
                }

        # 6. Volume anomaly
        vol = df["volume"]
        vol_median = vol.median()
        if vol_median > 0:
            vol_spikes = df.filter(pl.col("volume") > vol_median * 20)
            report["checks"]["volume_spikes"] = {
                "count": len(vol_spikes),
                "threshold": f"{vol_median * 20:.2f}",
            }

        # Overall status
        issues = 0
        if report["checks"].get("gaps", {}).get("count", 0) > 0:
            issues += 1
        if report["checks"].get("price_outliers", {}).get("count", 0) > 3:
            issues += 1
        if report["checks"].get("null_counts", {}):
            if sum(report["checks"]["null_counts"].values()) > 0:
                issues += 1

        report["status"] = "ISSUES_FOUND" if issues > 0 else "OK"
        return report

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
        total_range = now_ms - since_ms

        # Estimate total pages for a better progress bar
        est_total_pages = max(1, total_range // (tf_ms * limit))
        page = 0

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
            TimeRemainingColumn(),
            TimeElapsedColumn(),
            console=console,
            disable=not show_progress,
        ) as prog:
            task = prog.add_task(
                f"[cyan]Fetching {symbol} {tf}…",
                total=est_total_pages,
            )

            cursor = since_ms
            empty_responses = 0
            while cursor < now_ms:
                raw = self._fetch_with_retry(symbol, tf, since=cursor, limit=limit)
                if not raw:
                    empty_responses += 1
                    if empty_responses >= 3:
                        break  # consecutive empty = no more data
                    cursor += tf_ms * limit
                    page += 1
                    prog.update(task, completed=min(page, est_total_pages))
                    continue

                empty_responses = 0
                all_candles.extend(raw)

                # Advance cursor
                last_ts: int = raw[-1][0]
                cursor = last_ts + tf_ms
                page += 1
                prog.update(task, completed=min(page, est_total_pages))

                # Be gentle on the exchange
                time.sleep(0.1)

        return self._raw_to_df(all_candles, self.exchange_name, symbol, tf)

    def _fetch_with_retry(
        self,
        symbol: str,
        tf: str,
        *,
        since: int | None = None,
        limit: int | None = None,
    ) -> list[list]:
        """Fetch OHLCV with retry logic and exponential backoff."""
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
                backoff = config.retry_delay_sec * (2**attempt)
                _error_console.print(
                    f"[yellow]Network error (attempt {attempt + 1}/{config.max_retries + 1}): "
                    f"{e}. Retrying in {backoff}s…[/yellow]"
                )
                time.sleep(backoff)
            except Exception as e:
                last_error = e
                if attempt == config.max_retries:
                    raise RuntimeError(
                        f"Failed after {config.max_retries} retries: {e}"
                    ) from e
                backoff = config.retry_delay_sec * (2**attempt)
                _error_console.print(
                    f"[yellow]Error (attempt {attempt + 1}): {e}. "
                    f"Retrying in {backoff}s…[/yellow]"
                )
                time.sleep(backoff)

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


def download_all_symbols(
    exchange_name: str | None = None,
    *,
    incremental: bool = True,
) -> None:
    """Download all configured symbols for the given (or default) exchange.

    Parameters
    ----------
    exchange_name : str, optional
    incremental : bool
        If True, use ``update_ohlcv()`` — only fetch new candles.
        If False, full re-download from ``since`` date.
    """
    exchange_name = exchange_name or config.default_exchange
    collector = Collector(exchange_name)

    exchange_symbols = config.symbols.get(exchange_name, [])
    if not exchange_symbols:
        _error_console.print(f"[red]No symbols configured for {exchange_name}[/red]")
        return

    total_candles = 0
    for symbol in exchange_symbols:
        console.print(f"\n[bold]── {symbol} ──[/bold]")
        for tf in config.timeframes:
            try:
                if incremental:
                    df = collector.update_ohlcv(symbol, tf)
                    label = "incremental"
                else:
                    df = collector.fetch_ohlcv(symbol, tf, since=None, limit=500)
                    save_ohlcv(df, exchange_name, symbol, tf)
                    label = "full"

                n = len(df)
                total_candles += n
                if n > 0:
                    console.print(f"  [green]✓[/green] {tf:>4}  {n:>6,} candles ({label})")
                else:
                    console.print(f"  [yellow]–[/yellow] {tf:>4}  up to date")
            except Exception as e:
                _error_console.print(f"  [red]✗[/red] {tf:>4}  {e}")

    if total_candles > 0:
        console.print(f"\n[bold green]✅ Total: {total_candles:,} new candles[/bold green]")
    else:
        console.print(f"\n[bold]✅ All up to date[/bold]")


def validate_all_symbols(exchange_name: str | None = None) -> list[dict]:
    """Run data quality checks on all stored datasets."""
    exchange_name = exchange_name or config.default_exchange
    collector = Collector(exchange_name)

    from trading_agent.data.storage import list_datasets

    datasets = [
        d
        for d in list_datasets()
        if d["exchange"] == exchange_name
    ]

    if not datasets:
        _error_console.print("[red]No datasets to validate[/red]")
        return []

    reports = []
    for ds in datasets:
        report = collector.validate_data(ds["symbol"], ds["timeframe"])
        reports.append(report)
        status_icon = "✅" if report["status"] == "OK" else "⚠️" if report["status"] == "ISSUES_FOUND" else "❌"
        console.print(f"  {status_icon} {ds['symbol']:12s} {ds['timeframe']:4s}  {report['status']}")

    return reports
