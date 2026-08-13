"""
Unified Data Pipeline — multi-asset OHLCV ingestion into a candle store.

Composed of three pluggable pieces:

1. DataSource — fetches candles from a venue (CCXT/crypto, Alpaca/stocks,
   OANDA/forex, or mock for tests).
2. CandleStore — persists normalized ``Candle`` records. Ships with a
   zero-dependency SQLite store and an optional TimescaleDB store
   (hypertable + ON CONFLICT upsert).
3. DataPipeline — orchestrates fetch -> normalize -> dedupe -> write,
   with per-symbol reports and incremental/backfill modes.

Run a quick demo (mock source + SQLite):
    python -m trading.data.pipeline
"""

from __future__ import annotations

import asyncio
import logging
import os
import sqlite3
import threading
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Optional

from trading_agent.exchanges.models import Symbol, Candle, AssetClass

logger = logging.getLogger(__name__)

DEFAULT_DB_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "data",
    "market_data.db",
)


# ---------------------------------------------------------------------------
# Sources
# ---------------------------------------------------------------------------


class DataSource(ABC):
    """Fetches OHLCV candles for a symbol from a venue."""

    name: str = "abstract"

    @abstractmethod
    async def fetch_candles(
        self,
        symbol: Symbol,
        timeframe: str,
        start: datetime,
        end: datetime,
    ) -> list[Candle]:
        """Fetch all candles in [start, end)."""

    @abstractmethod
    async def fetch_recent(
        self,
        symbol: Symbol,
        timeframe: str,
        limit: int = 200,
    ) -> list[Candle]:
        """Fetch the most recent `limit` candles."""

    def normalize(self, symbol: Symbol, timeframe: str, raw) -> Candle:
        """Convert a source-specific OHLCV record into a unified Candle.

        Accepts ccxt-style tuples (ts_ms, o, h, l, c, v) or dicts with
        timestamp/open/high/low/close/volume keys.
        """
        if isinstance(raw, (list, tuple)):
            ts_ms, o, h, low, c = raw[0], raw[1], raw[2], raw[3], raw[4]
            v = raw[5] if len(raw) > 5 else 0
            ts = datetime.fromtimestamp(ts_ms / 1000.0, tz=timezone.utc)
        elif isinstance(raw, dict):
            ts = raw.get("timestamp") or raw.get("time")
            if isinstance(ts, (int, float)):
                ts = (
                    datetime.fromtimestamp(ts / 1000.0, tz=timezone.utc)
                    if ts > 1e11
                    else datetime.fromtimestamp(ts, tz=timezone.utc)
                )
            o, h, low, c, v = (
                raw["open"],
                raw["high"],
                raw["low"],
                raw["close"],
                raw.get("volume", 0),
            )
        else:
            raise TypeError(f"Unsupported raw candle format: {type(raw)}")

        def dec(x):
            try:
                return Decimal(str(x))
            except (InvalidOperation, TypeError, ValueError):
                return Decimal(0)

        return Candle(
            symbol=symbol,
            timestamp=ts,
            timeframe=timeframe,
            open=dec(o),
            high=dec(h),
            low=dec(low),
            close=dec(c),
            volume=dec(v),
        )


class CCXTSource(DataSource):
    """Crypto OHLCV via CCXT (sync adapter wrapped in a thread)."""

    name = "ccxt"

    def __init__(self, exchange_id: str = "binance", adapter=None):
        self.exchange_id = exchange_id
        self._adapter = adapter  # optional pre-connected CCXTAdapter

    def _get_exchange(self):
        if self._adapter is not None:
            return self._adapter.exchange
        import ccxt

        klass = getattr(ccxt, self.exchange_id)
        return klass({"enableRateLimit": True})

    async def _fetch(
        self, symbol: Symbol, timeframe: str, since_ms: int | None, limit: int
    ) -> list[Candle]:
        return await asyncio.to_thread(
            self._fetch_sync, symbol, timeframe, since_ms, limit
        )

    def _fetch_sync(
        self, symbol: Symbol, timeframe: str, since_ms: int | None, limit: int
    ) -> list[Candle]:
        ex = self._get_exchange()
        ccxt_symbol = symbol.ccxt_symbol
        raw = ex.fetch_ohlcv(
            ccxt_symbol, timeframe=timeframe, since=since_ms, limit=limit
        )
        return [self.normalize(symbol, timeframe, row) for row in raw]

    async def fetch_candles(
        self, symbol: Symbol, timeframe: str, start: datetime, end: datetime
    ) -> list[Candle]:
        since_ms = int(start.timestamp() * 1000)
        end_ms = int(end.timestamp() * 1000)
        out: list[Candle] = []
        cursor = since_ms
        while cursor < end_ms:
            batch = await self._fetch(symbol, timeframe, cursor, limit=1000)
            if not batch:
                break
            out.extend(batch)
            last_ts = batch[-1].timestamp.timestamp() * 1000
            if last_ts <= cursor:
                break
            cursor = int(last_ts) + 1
        return [c for c in out if c.timestamp.timestamp() * 1000 < end_ms]

    async def fetch_recent(
        self, symbol: Symbol, timeframe: str, limit: int = 200
    ) -> list[Candle]:
        candles = await self._fetch(symbol, timeframe, None, limit)
        now = datetime.now(timezone.utc).timestamp()
        duration = self._timeframe_seconds(timeframe)
        return [c for c in candles if c.timestamp.timestamp() + duration <= now][
            -limit:
        ]

    @staticmethod
    def _timeframe_seconds(timeframe: str) -> int:
        units = {"m": 60, "h": 3600, "d": 86400, "w": 604800}
        tf = timeframe.lower().strip()
        if len(tf) < 2 or tf[-1] not in units:
            raise ValueError(f"Unsupported timeframe: {timeframe!r}")
        try:
            amount = int(tf[:-1])
        except ValueError as exc:
            raise ValueError(f"Unsupported timeframe: {timeframe!r}") from exc
        if amount <= 0:
            raise ValueError(f"Unsupported timeframe: {timeframe!r}")
        return amount * units[tf[-1]]


class AlpacaSource(DataSource):
    """US stock OHLCV via Alpaca."""

    name = "alpaca"

    def __init__(self, adapter=None):
        self._adapter = adapter

    def _get_client(self):
        if self._adapter is not None:
            return self._adapter
        from trading_agent.exchanges.alpaca_adapter import create_alpaca_adapter

        return create_alpaca_adapter()

    async def fetch_candles(
        self, symbol: Symbol, timeframe: str, start: datetime, end: datetime
    ) -> list[Candle]:
        client = self._get_client()
        return await asyncio.to_thread(
            self._fetch_sync, client, symbol, timeframe, start, end
        )

    def _fetch_sync(
        self, client, symbol: Symbol, timeframe: str, start: datetime, end: datetime
    ) -> list[Candle]:
        raw = client.get_bars(
            symbol.alpaca_symbol, timeframe=timeframe, start=start, end=end
        )
        out = []
        for bar in raw:
            out.append(
                Candle(
                    symbol=symbol,
                    timestamp=bar.timestamp,
                    timeframe=timeframe,
                    open=Decimal(str(bar.open)),
                    high=Decimal(str(bar.high)),
                    low=Decimal(str(bar.low)),
                    close=Decimal(str(bar.close)),
                    volume=Decimal(str(bar.volume)),
                )
            )
        return out

    async def fetch_recent(
        self, symbol: Symbol, timeframe: str, limit: int = 200
    ) -> list[Candle]:
        end = datetime.now(timezone.utc)
        # rough window: estimate bars per day
        per_day = {"1m": 390, "5m": 78, "15m": 26, "30m": 13, "1h": 6.5, "1d": 1}.get(
            timeframe, 24
        )
        days = max(1, int(limit / per_day) + 1)
        start = end - __import__("datetime").timedelta(days=days)
        candles = await self.fetch_candles(symbol, timeframe, start, end)
        return candles[-limit:]


class OANDASource(DataSource):
    """Forex OHLCV via OANDA."""

    name = "oanda"

    def __init__(self, adapter=None):
        self._adapter = adapter

    async def fetch_candles(
        self, symbol: Symbol, timeframe: str, start: datetime, end: datetime
    ) -> list[Candle]:
        raise NotImplementedError(
            "OANDA candle API requires the oandapyV20 client; wire in via adapter subclass"
        )

    async def fetch_recent(
        self, symbol: Symbol, timeframe: str, limit: int = 200
    ) -> list[Candle]:
        raise NotImplementedError(
            "OANDA candle API requires the oandapyV20 client; wire in via adapter subclass"
        )


class MockSource(DataSource):
    """Synthetic candle source for tests / demos / dry runs."""

    name = "mock"

    def __init__(self, seed: float = 100.0, volatility: float = 0.01):
        self.price = seed
        self.volatility = volatility

    async def fetch_candles(
        self, symbol: Symbol, timeframe: str, start: datetime, end: datetime
    ) -> list[Candle]:
        out = []
        step = {
            "1m": 60,
            "5m": 300,
            "15m": 900,
            "1h": 3600,
            "4h": 14400,
            "1d": 86400,
        }.get(timeframe, 3600)
        cursor = start
        while cursor < end:
            out.append(self._candle(symbol, timeframe, cursor))
            cursor = cursor.timestamp() + step
            cursor = datetime.fromtimestamp(cursor, tz=timezone.utc)
        return out

    async def fetch_recent(
        self, symbol: Symbol, timeframe: str, limit: int = 200
    ) -> list[Candle]:
        end = datetime.now(timezone.utc)
        step = {
            "1m": 60,
            "5m": 300,
            "15m": 900,
            "1h": 3600,
            "4h": 14400,
            "1d": 86400,
        }.get(timeframe, 3600)
        start = datetime.fromtimestamp(end.timestamp() - step * limit, tz=timezone.utc)
        return await self.fetch_candles(symbol, timeframe, start, end)

    def _candle(self, symbol: Symbol, timeframe: str, ts: datetime) -> Candle:
        import random

        o = self.price if isinstance(self.price, Decimal) else Decimal(str(self.price))
        o_f = float(o)
        delta = o_f * self.volatility * (random.random() - 0.5)
        c = Decimal(str(round(o_f + delta, 8)))
        if c <= 0:
            c = Decimal("0.00000001")
        upper_scale = Decimal(str(round(1 + self.volatility * 0.3, 8)))
        lower_scale = Decimal(str(round(max(0.00000001, 1 - self.volatility * 0.3), 8)))
        h = max(o, c) * upper_scale
        low = min(o, c) * lower_scale
        self.price = c
        return Candle(
            symbol=symbol,
            timestamp=ts,
            timeframe=timeframe,
            open=o,
            high=h,
            low=low,
            close=c,
            volume=Decimal(str(round(random.random() * 1000, 4))),
        )


# ---------------------------------------------------------------------------
# Stores
# ---------------------------------------------------------------------------


class CandleStore(ABC):
    """Persistent store for normalized candles."""

    @abstractmethod
    async def write(self, candles: list[Candle]) -> int:
        """Insert candles (idempotent by key). Returns rows written."""

    @abstractmethod
    async def read(
        self, symbol: Symbol, timeframe: str, start: datetime, end: datetime
    ) -> list[Candle]:
        """Read candles in [start, end)."""

    @abstractmethod
    async def count(self, symbol: Symbol, timeframe: str) -> int:
        """Number of stored candles for a symbol/timeframe."""

    @abstractmethod
    async def latest(self, symbol: Symbol, timeframe: str) -> Optional[Candle]:
        """Most recent candle, or None."""


class SQLiteCandleStore(CandleStore):
    """Zero-dependency SQLite store (data/market_data.db by default)."""

    def __init__(self, db_path: str = DEFAULT_DB_PATH):
        self.db_path = db_path
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS candles (
                exchange TEXT NOT NULL,
                symbol   TEXT NOT NULL,
                asset_class TEXT NOT NULL,
                timeframe TEXT NOT NULL,
                ts       INTEGER NOT NULL,
                open     TEXT NOT NULL,
                high     TEXT NOT NULL,
                low      TEXT NOT NULL,
                close    TEXT NOT NULL,
                volume   TEXT NOT NULL,
                PRIMARY KEY (exchange, symbol, timeframe, ts)
            )
            """
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_candles_lookup ON candles(exchange, symbol, timeframe, ts)"
        )
        self._conn.commit()

    def _key(self, symbol: Symbol) -> str:
        return f"{symbol.base}/{symbol.quote}"

    def _write_sync(self, candles: list[Candle]) -> int:
        rows = []
        for c in candles:
            rows.append(
                (
                    c.symbol.exchange,
                    self._key(c.symbol),
                    c.symbol.asset_class.value,
                    c.timeframe,
                    int(c.timestamp.timestamp() * 1000),
                    str(c.open),
                    str(c.high),
                    str(c.low),
                    str(c.close),
                    str(c.volume),
                )
            )
        with self._lock:
            self._conn.executemany(
                """
                INSERT OR REPLACE INTO candles
                    (exchange, symbol, asset_class, timeframe, ts, open, high, low, close, volume)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
            self._conn.commit()
        return len(rows)

    async def write(self, candles: list[Candle]) -> int:
        if not candles:
            return 0
        return await asyncio.to_thread(self._write_sync, candles)

    def _read_sync(
        self, symbol: Symbol, timeframe: str, start: datetime, end: datetime
    ) -> list[Candle]:
        with self._lock:
            cur = self._conn.execute(
                """
                SELECT exchange, symbol, asset_class, timeframe, ts, open, high, low, close, volume
                FROM candles
                WHERE exchange=? AND symbol=? AND timeframe=? AND ts>=? AND ts<=?
                ORDER BY ts ASC
                """,
                (
                    symbol.exchange,
                    self._key(symbol),
                    timeframe,
                    int(start.timestamp() * 1000),
                    int(end.timestamp() * 1000),
                ),
            )
            out = []
            for row in cur.fetchall():
                out.append(
                    Candle(
                        symbol=Symbol(
                            base=row[1].split("/")[0],
                            quote=row[1].split("/")[1],
                            asset_class=AssetClass(row[2]),
                            market_type=symbol.market_type,
                            exchange=row[0],
                        ),
                        timestamp=datetime.fromtimestamp(
                            row[4] / 1000.0, tz=timezone.utc
                        ),
                        timeframe=row[3],
                        open=Decimal(row[5]),
                        high=Decimal(row[6]),
                        low=Decimal(row[7]),
                        close=Decimal(row[8]),
                        volume=Decimal(row[9]),
                    )
                )
            return out

    async def read(
        self, symbol: Symbol, timeframe: str, start: datetime, end: datetime
    ) -> list[Candle]:
        return await asyncio.to_thread(self._read_sync, symbol, timeframe, start, end)

    def _count_sync(self, symbol: Symbol, timeframe: str) -> int:
        with self._lock:
            cur = self._conn.execute(
                "SELECT COUNT(*) FROM candles WHERE exchange=? AND symbol=? AND timeframe=?",
                (symbol.exchange, self._key(symbol), timeframe),
            )
            return int(cur.fetchone()[0])

    async def count(self, symbol: Symbol, timeframe: str) -> int:
        return await asyncio.to_thread(self._count_sync, symbol, timeframe)

    def _latest_sync(self, symbol: Symbol, timeframe: str) -> Optional[Candle]:
        with self._lock:
            cur = self._conn.execute(
                """
                SELECT exchange, symbol, asset_class, timeframe, ts, open, high, low, close, volume
                FROM candles WHERE exchange=? AND symbol=? AND timeframe=?
                ORDER BY ts DESC LIMIT 1
                """,
                (symbol.exchange, self._key(symbol), timeframe),
            )
            row = cur.fetchone()
        if row is None:
            return None
        return Candle(
            symbol=Symbol(
                base=row[1].split("/")[0],
                quote=row[1].split("/")[1],
                asset_class=AssetClass(row[2]),
                market_type=symbol.market_type,
                exchange=row[0],
            ),
            timestamp=datetime.fromtimestamp(row[4] / 1000.0, tz=timezone.utc),
            timeframe=row[3],
            open=Decimal(row[5]),
            high=Decimal(row[6]),
            low=Decimal(row[7]),
            close=Decimal(row[8]),
            volume=Decimal(row[9]),
        )

    async def latest(self, symbol: Symbol, timeframe: str) -> Optional[Candle]:
        return await asyncio.to_thread(self._latest_sync, symbol, timeframe)

    def close(self) -> None:
        self._conn.close()


class TimescaleDBCandleStore(CandleStore):
    """TimescaleDB hypertable store (PostgreSQL extension).

    Requires `psycopg` (v3) and a TimescaleDB instance. DSN example:
        postgresql://user:pass@localhost:5432/market
    """

    def __init__(self, dsn: str, table: str = "candles"):
        self.dsn = dsn
        self.table = table
        try:
            import psycopg
        except ImportError as e:
            raise ImportError(
                "TimescaleDBCandleStore requires `pip install psycopg[binary]`"
            ) from e
        self._psycopg = psycopg
        self._conn = psycopg.connect(dsn, autocommit=True)
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        with self._conn.cursor() as cur:
            cur.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {self.table} (
                    exchange TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    asset_class TEXT NOT NULL,
                    timeframe TEXT NOT NULL,
                    ts TIMESTAMPTZ NOT NULL,
                    open DOUBLE PRECISION NOT NULL,
                    high DOUBLE PRECISION NOT NULL,
                    low DOUBLE PRECISION NOT NULL,
                    close DOUBLE PRECISION NOT NULL,
                    volume DOUBLE PRECISION NOT NULL,
                    PRIMARY KEY (exchange, symbol, timeframe, ts)
                )
                """
            )
            cur.execute(
                f"SELECT create_hypertable('{self.table}', 'ts', if_not_exists => TRUE)"
            )

    async def write(self, candles: list[Candle]) -> int:
        if not candles:
            return 0
        rows = [
            (
                c.symbol.exchange,
                f"{c.symbol.base}/{c.symbol.quote}",
                c.symbol.asset_class.value,
                c.timeframe,
                c.timestamp,
                float(c.open),
                float(c.high),
                float(c.low),
                float(c.close),
                float(c.volume),
            )
            for c in candles
        ]
        with self._conn.cursor() as cur:
            cur.executemany(
                f"""
                INSERT INTO {self.table}
                    (exchange, symbol, asset_class, timeframe, ts, open, high, low, close, volume)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (exchange, symbol, timeframe, ts) DO UPDATE SET
                    open=EXCLUDED.open, high=EXCLUDED.high, low=EXCLUDED.low,
                    close=EXCLUDED.close, volume=EXCLUDED.volume
                """,
                rows,
            )
        return len(rows)

    async def read(
        self, symbol: Symbol, timeframe: str, start: datetime, end: datetime
    ) -> list[Candle]:
        with self._conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT exchange, symbol, asset_class, timeframe, ts, open, high, low, close, volume
                FROM {self.table}
                WHERE exchange=%s AND symbol=%s AND timeframe=%s AND ts>=%s AND ts<=%s
                ORDER BY ts ASC
                """,
                (
                    symbol.exchange,
                    f"{symbol.base}/{symbol.quote}",
                    timeframe,
                    start,
                    end,
                ),
            )
            return [self._row_to_candle(symbol, r) for r in cur.fetchall()]

    async def count(self, symbol: Symbol, timeframe: str) -> int:
        with self._conn.cursor() as cur:
            cur.execute(
                f"SELECT COUNT(*) FROM {self.table} WHERE exchange=%s AND symbol=%s AND timeframe=%s",
                (symbol.exchange, f"{symbol.base}/{symbol.quote}", timeframe),
            )
            return int(cur.fetchone()[0])

    async def latest(self, symbol: Symbol, timeframe: str) -> Optional[Candle]:
        with self._conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT exchange, symbol, asset_class, timeframe, ts, open, high, low, close, volume
                FROM {self.table}
                WHERE exchange=%s AND symbol=%s AND timeframe=%s
                ORDER BY ts DESC LIMIT 1
                """,
                (symbol.exchange, f"{symbol.base}/{symbol.quote}", timeframe),
            )
            row = cur.fetchone()
            return self._row_to_candle(symbol, row) if row else None

    @staticmethod
    def _row_to_candle(symbol: Symbol, r) -> Candle:
        return Candle(
            symbol=Symbol(
                base=r[1].split("/")[0],
                quote=r[1].split("/")[1],
                asset_class=AssetClass(r[2]),
                market_type=symbol.market_type,
                exchange=r[0],
            ),
            timestamp=r[4],
            timeframe=r[3],
            open=Decimal(str(r[5])),
            high=Decimal(str(r[6])),
            low=Decimal(str(r[7])),
            close=Decimal(str(r[8])),
            volume=Decimal(str(r[9])),
        )

    def close(self) -> None:
        self._conn.close()


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class IngestReport:
    """Result of one ingest run."""

    total_written: int = 0
    total_read: int = 0
    symbols: dict[str, int] = field(default_factory=dict)  # key -> candles written
    errors: dict[str, str] = field(default_factory=dict)  # key -> error message
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    finished_at: Optional[datetime] = None
    elapsed_seconds: float = 0.0

    def to_dict(self) -> dict:
        return {
            "total_written": self.total_written,
            "total_read": self.total_read,
            "symbols": self.symbols,
            "errors": self.errors,
            "started_at": self.started_at.isoformat(),
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "elapsed_seconds": round(self.elapsed_seconds, 3),
        }


class DataPipeline:
    """Orchestrates multi-asset candle ingestion."""

    def __init__(
        self, store: CandleStore, sources: Optional[dict[str, DataSource]] = None
    ):
        self.store = store
        self.sources: dict[str, DataSource] = sources or {}

    def register_source(self, name: str, source: DataSource) -> None:
        self.sources[name] = source

    def _source_for(self, symbol: Symbol) -> DataSource:
        """Pick a source by asset class, then exchange name."""
        by_asset = {
            AssetClass.CRYPTO: "ccxt",
            AssetClass.STOCK: "alpaca",
            AssetClass.FOREX: "oanda",
            AssetClass.FUTURES: "ccxt",
            AssetClass.OPTIONS: "ccxt",
        }
        name = by_asset.get(symbol.asset_class, symbol.exchange)
        source = self.sources.get(name) or self.sources.get(symbol.exchange)
        if source is None:
            raise ValueError(
                f"No source registered for {symbol.asset_class.value}/{symbol.exchange}"
            )
        return source

    async def ingest(
        self,
        symbols: list[Symbol],
        timeframe: str,
        start: datetime,
        end: datetime,
    ) -> IngestReport:
        """Full-range ingestion (backfill)."""
        report = IngestReport()
        begin = time.monotonic()
        for symbol in symbols:
            key = f"{symbol.pair}@{symbol.exchange}:{timeframe}"
            try:
                source = self._source_for(symbol)
                candles = await source.fetch_candles(symbol, timeframe, start, end)
                report.total_read += len(candles)
                written = await self.store.write(candles)
                report.symbols[key] = written
                report.total_written += written
                logger.info(f"Ingested {written} candles for {key}")
            except Exception as e:
                report.errors[key] = str(e)
                logger.error(f"Ingest failed for {key}: {e}")
        report.finished_at = datetime.now(timezone.utc)
        report.elapsed_seconds = time.monotonic() - begin
        return report

    async def incremental(
        self, symbols: list[Symbol], timeframe: str, limit: int = 200
    ) -> IngestReport:
        """Ingest the most recent `limit` candles per symbol."""
        report = IngestReport()
        begin = time.monotonic()
        for symbol in symbols:
            key = f"{symbol.pair}@{symbol.exchange}:{timeframe}"
            try:
                source = self._source_for(symbol)
                candles = await source.fetch_recent(symbol, timeframe, limit)
                report.total_read += len(candles)
                written = await self.store.write(candles)
                report.symbols[key] = written
                report.total_written += written
                logger.info(f"Incremental ingested {written} candles for {key}")
            except Exception as e:
                report.errors[key] = str(e)
                logger.error(f"Incremental failed for {key}: {e}")
        report.finished_at = datetime.now(timezone.utc)
        report.elapsed_seconds = time.monotonic() - begin
        return report

    async def count(self, symbol: Symbol, timeframe: str) -> int:
        return await self.store.count(symbol, timeframe)

    async def latest(self, symbol: Symbol, timeframe: str) -> Optional[Candle]:
        return await self.store.latest(symbol, timeframe)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    async def demo():
        store = SQLiteCandleStore(db_path="data/demo_pipeline.db")
        pipeline = DataPipeline(store=store, sources={"mock": MockSource(seed=50000)})

        from trading_agent.exchanges.models import crypto_symbol

        btc = crypto_symbol("BTC", "USDT", exchange="mock")

        start = datetime(2026, 7, 1, tzinfo=timezone.utc)
        end = datetime(2026, 7, 3, tzinfo=timezone.utc)

        report = await pipeline.ingest([btc], "1h", start, end)
        print("report:", report.to_dict())
        print("count:", await pipeline.count(btc, "1h"))
        latest = await pipeline.latest(btc, "1h")
        print("latest:", latest.timestamp, latest.close)
        store.close()

    asyncio.run(demo())
