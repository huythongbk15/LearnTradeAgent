"""Tests for Phase 6 P1 final modules:
- WebSocket Manager (trading/exchanges/websocket_manager.py)
- Exchange Health Monitor (trading/exchanges/health_monitor.py)
- Unified Data Pipeline (trading/data/pipeline.py)
"""
import asyncio
import os


from trading.exchanges.models import crypto_symbol
from trading.exchanges.websocket_manager import (
    WebSocketManager, MockStreamProvider, WSChannel, WSMessage,
)
from trading.exchanges.health_monitor import (
    HealthMonitor, HealthStatus,
)
from trading.data.pipeline import (
    DataPipeline, SQLiteCandleStore, MockSource,
)


def run_async(coro):
    """Run an async coroutine synchronously (matches test_phase6_integration)."""
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# WebSocket Manager
# ---------------------------------------------------------------------------

class TestWebSocketManager:

    def test_subscribe_dispatch(self):
        async def scenario():
            manager = WebSocketManager()
            provider = MockStreamProvider("mock")
            manager.register_provider(provider)

            btc = crypto_symbol("BTC", "USDT", exchange="mock")
            received = []

            async def handler(msg: WSMessage):
                received.append(msg)

            sub_id = await manager.subscribe(btc, WSChannel.TICKER, handler)
            await manager.start()

            await provider.push(WSMessage(
                exchange="mock", channel=WSChannel.TICKER, symbol=btc,
                data={"last": "65000.0"},
            ))
            await asyncio.sleep(0.1)

            assert len(received) == 1
            assert received[0].data["last"] == "65000.0"
            assert received[0].symbol.pair == "BTC/USDT"

            await manager.unsubscribe(sub_id)
            await provider.push(WSMessage(
                exchange="mock", channel=WSChannel.TICKER, symbol=btc, data={"last": "1"},
            ))
            await asyncio.sleep(0.1)
            assert len(received) == 1  # no more deliveries after unsubscribe
            await manager.stop()

        run_async(scenario())

    def test_channel_filtering(self):
        async def scenario():
            manager = WebSocketManager()
            provider = MockStreamProvider("mock")
            manager.register_provider(provider)

            btc = crypto_symbol("BTC", "USDT", exchange="mock")
            ticker_msgs = []
            trade_msgs = []

            await manager.subscribe(btc, WSChannel.TICKER, lambda m: ticker_msgs.append(m))
            await manager.subscribe(btc, WSChannel.TRADES, lambda m: trade_msgs.append(m))
            await manager.start()

            await provider.push(WSMessage(exchange="mock", channel=WSChannel.TICKER, symbol=btc, data={}))
            await provider.push(WSMessage(exchange="mock", channel=WSChannel.TRADES, symbol=btc, data={}))
            await asyncio.sleep(0.1)

            assert len(ticker_msgs) == 1
            assert len(trade_msgs) == 1
            await manager.stop()

        run_async(scenario())

    def test_reconnect_on_failure(self):
        async def scenario():
            manager = WebSocketManager(reconnect_initial_delay=0.1)
            provider = MockStreamProvider("mock")
            provider.fail_on_connect = True
            manager.register_provider(provider)

            await manager.start()
            await asyncio.sleep(0.5)

            # Flip the flag so the reconnect loop can succeed on next attempt.
            provider.fail_on_connect = False
            await asyncio.sleep(1.0)
            assert provider.is_connected
            assert provider.reconnect_count >= 1
            await manager.stop()

        run_async(scenario())

    def test_status_report(self):
        manager = WebSocketManager()
        manager.register_provider(MockStreamProvider("mock"))
        status = manager.get_status()
        assert "mock" in status["providers"]
        assert status["running"] is False


# ---------------------------------------------------------------------------
# Exchange Health Monitor
# ---------------------------------------------------------------------------

class TestHealthMonitor:

    def test_healthy_and_down(self):
        async def scenario():
            monitor = HealthMonitor(interval_seconds=1.0, failures_to_down=2)

            async def good(name: str) -> float:
                await asyncio.sleep(0.01)
                return 0.01

            async def bad(name: str) -> float:
                raise ConnectionError("down")

            monitor.register_exchange("good_ex", good)
            monitor.register_exchange("bad_ex", bad)

            await monitor.check_all()
            # First success moves UNKNOWN -> DEGRADED (needs consecutive successes)
            assert not monitor.is_healthy("good_ex")
            await monitor.check_all()  # second success -> HEALTHY
            assert monitor.is_healthy("good_ex")
            assert "bad_ex" not in monitor.get_healthy_exchanges()

            # Drive bad_ex to DOWN
            await monitor.check_exchange("bad_ex")
            await monitor.check_exchange("bad_ex")
            assert monitor.get_exchange_status("bad_ex")["status"] == HealthStatus.DOWN.value

        run_async(scenario())

    def test_failover_callback(self):
        async def scenario():
            monitor = HealthMonitor(interval_seconds=1.0, failures_to_down=2)
            failover_hits = []

            async def bad(name: str) -> float:
                raise ConnectionError("down")

            monitor.register_exchange("ven_a", bad)
            monitor.on_failover(lambda name: failover_hits.append(name))

            await monitor.check_exchange("ven_a")
            await monitor.check_exchange("ven_a")

            assert failover_hits == ["ven_a"]
            assert "ven_a" in monitor.get_unhealthy()

        run_async(scenario())

    def test_recovers(self):
        async def scenario():
            monitor = HealthMonitor(interval_seconds=1.0, failures_to_down=1, recoveries_to_healthy=2)
            state = {"ok": False}

            async def flaky(name: str) -> float:
                if not state["ok"]:
                    raise ConnectionError("down")
                return 0.02

            monitor.register_exchange("flaky", flaky)

            await monitor.check_exchange("flaky")
            assert monitor.get_exchange_status("flaky")["status"] == HealthStatus.DOWN.value

            state["ok"] = True
            await monitor.check_exchange("flaky")  # 1st success -> still recovering
            assert monitor.get_exchange_status("flaky")["status"] != HealthStatus.HEALTHY.value
            await monitor.check_exchange("flaky")  # 2nd success -> healthy
            assert monitor.is_healthy("flaky")

        run_async(scenario())


# ---------------------------------------------------------------------------
# Unified Data Pipeline
# ---------------------------------------------------------------------------

class TestDataPipeline:

    def test_ingest_and_read(self, tmp_path):
        from datetime import datetime, timezone

        async def scenario():
            db_path = os.path.join(tmp_path, "market_test.db")
            store = SQLiteCandleStore(db_path=db_path)
            pipeline = DataPipeline(store=store, sources={"mock": MockSource(seed=50000)})

            btc = crypto_symbol("BTC", "USDT", exchange="mock")
            start = datetime(2026, 7, 1, tzinfo=timezone.utc)
            end = datetime(2026, 7, 1, 12, tzinfo=timezone.utc)

            report = await pipeline.ingest([btc], "1h", start, end)
            assert report.total_written == 12
            assert report.errors == {}

            assert await pipeline.count(btc, "1h") == 12
            latest = await pipeline.latest(btc, "1h")
            assert latest is not None
            assert latest.symbol.pair == "BTC/USDT"

            # Re-ingest same range -> idempotent (INSERT OR REPLACE)
            report2 = await pipeline.ingest([btc], "1h", start, end)
            assert report2.total_written == 12
            assert await pipeline.count(btc, "1h") == 12

            store.close()

        run_async(scenario())

    def test_incremental(self, tmp_path):
        async def scenario():
            db_path = os.path.join(tmp_path, "market_incr.db")
            store = SQLiteCandleStore(db_path=db_path)
            pipeline = DataPipeline(store=store, sources={"mock": MockSource(seed=100)})

            btc = crypto_symbol("BTC", "USDT", exchange="mock")
            report = await pipeline.incremental([btc], "1h", limit=50)
            assert 0 < report.total_written <= 50
            assert await pipeline.count(btc, "1h") == report.total_written

            store.close()

        run_async(scenario())

    def test_missing_source_reports_error(self, tmp_path):
        from datetime import datetime, timezone

        async def scenario():
            db_path = os.path.join(tmp_path, "market_err.db")
            store = SQLiteCandleStore(db_path=db_path)
            pipeline = DataPipeline(store=store)  # no sources registered

            btc = crypto_symbol("BTC", "USDT", exchange="mock")
            report = await pipeline.ingest(
                [btc], "1h",
                datetime(2026, 7, 1, tzinfo=timezone.utc),
                datetime(2026, 7, 2, tzinfo=timezone.utc),
            )
            assert report.total_written == 0
            assert "BTC/USDT@mock:1h" in report.errors
            store.close()

        run_async(scenario())

    def test_source_normalize_ccxt_tuple(self):
        source = MockSource()
        btc = crypto_symbol("BTC", "USDT", exchange="binance")
        candle = source.normalize(btc, "1h", (1750000000000, "60000", "61000", "59000", "60500", "123.5"))
        assert candle.symbol.pair == "BTC/USDT"
        assert str(candle.close) == "60500"
        assert candle.timeframe == "1h"
