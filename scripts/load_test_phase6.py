"""
Phase 6 P3 - Load Testing

Stress-tests P2 components under sustained load:
- Event store: high-volume appends + concurrent writers
- Event store: read amplification with concurrent readers
- Online learning: sustained multi-bar stream
- Portfolio optimizer: large universe

Run:  python scripts/load_test_phase6.py [--quick]
"""

import argparse
import asyncio
import random
import sys
import tempfile
import time
from decimal import Decimal
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import numpy as np


def run_async(coro):
    return asyncio.run(coro)


def load_test_event_store(n_events=100_000, n_writers=4):
    print(f"\n[Event Store Load] {n_events} events, {n_writers} concurrent writers")
    from trading.events.store import EventStore, EventStoreConfig

    config = EventStoreConfig(file_path="/tmp/loadtest_events.jsonl")
    store = EventStore(config)
    symbols = ["BTC/USDT", "ETH/USDT", "SOL/USDT", "ADA/USDT", "XRP/USDT"]
    sides = ["buy", "sell"]

    async def writer(wid, count):
        for i in range(count):
            ev = store.create_trade_event(
                symbol=random.choice(symbols), side=random.choice(sides),
                size=Decimal("0.5"), price=Decimal(str(random.uniform(100, 50000))),
                fee=Decimal("2.5"), fee_currency="USDT",
                exchange="binance", order_id=f"w{wid}-{i}", strategy_id=f"s{wid}",
            )
            await store.append(ev)

    async def scenario():
        await store.connect(backend="file")
        t0 = time.perf_counter()
        per_writer = n_events // n_writers
        await asyncio.gather(*[writer(i, per_writer) for i in range(n_writers)])
        t1 = time.perf_counter()
        elapsed = t1 - t0
        print(f"  wrote {n_events} events in {elapsed:.2f}s -> {n_events / elapsed:,.0f} ev/s")

        t0 = time.perf_counter()
        all_events = await store.read_all(count=n_events)
        t1 = time.perf_counter()
        elapsed = t1 - t0
        print(f"  read {len(all_events)} events in {elapsed:.2f}s -> {n_events / elapsed:,.0f} ev/s")
        await store.disconnect()

    run_async(scenario())


def load_test_concurrent_readers(n_events=20_000, n_readers=8):
    print(f"\n[Event Store Concurrent Readers] {n_events} events, {n_readers} readers")
    from trading.events.store import EventStore, EventStoreConfig

    config = EventStoreConfig(file_path="/tmp/loadtest_readers.jsonl")
    store = EventStore(config)

    async def seed():
        await store.connect(backend="file")
        events = [
            store.create_signal_event(
                symbol="BTC/USDT", signal_type="buy" if i % 2 == 0 else "sell",
                strength=0.5, strategy_id="s1", timeframe="1h",
            )
            for i in range(n_events)
        ]
        await store.append_batch(events)
        await store.disconnect()

    async def reader(rid, count):
        for _ in range(count):
            await store.read_all(count=500)

    async def scenario():
        await seed()
        await store.connect(backend="file")
        reads_per_reader = 20
        t0 = time.perf_counter()
        await asyncio.gather(*[reader(i, reads_per_reader) for i in range(n_readers)])
        t1 = time.perf_counter()
        elapsed = t1 - t0
        print(f"  {n_readers * reads_per_reader} concurrent reads in {elapsed:.2f}s")
        await store.disconnect()

    run_async(scenario())


def load_test_online_learning(n_bars=200_000):
    print(f"\n[Online Learning Stream] {n_bars} bars")
    from trading.ml.online.adaptive import AdaptiveConfig, AdaptiveStrategy

    np.random.seed(1)
    prices = 100 * np.exp(np.cumsum(np.random.normal(0.001, 0.01, n_bars)))

    strat = AdaptiveStrategy(AdaptiveConfig(min_period=5, max_period=30, min_samples=30))
    t0 = time.perf_counter()
    for i in range(1, len(prices)):
        strat.update(
            high=float(prices[i] * 1.002), low=float(prices[i] * 0.998),
            close=float(prices[i]), volume=1000.0,
        )
    t1 = time.perf_counter()
    elapsed = t1 - t0
    print(f"  processed {n_bars} bars in {elapsed:.2f}s -> {n_bars / elapsed:,.0f} bars/s")


def load_test_portfolio_large_universe(n_assets=100, n_periods=500):
    print(f"\n[Portfolio Optimizer Large Universe] {n_assets} assets")
    import pandas as pd
    from trading.portfolio.portfolio_optimizer import PortfolioOptimizer, OptimizerMethod
    from trading.exchanges.models import Symbol, AssetClass, MarketType

    np.random.seed(2)
    returns = pd.DataFrame(
        np.random.randn(n_periods, n_assets) * 0.01,
        columns=[f"SYM{i}/USDT" for i in range(n_assets)],
    )
    symbols = [
        Symbol(f"SYM{i}", "USDT", AssetClass.CRYPTO, MarketType.SPOT, "binance")
        for i in range(n_assets)
    ]

    for method in ["mean_variance", "hrp"]:
        optimizer = PortfolioOptimizer(method=OptimizerMethod(method))
        optimizer.set_universe(symbols, returns)
        t0 = time.perf_counter()
        result = optimizer.optimize()
        t1 = time.perf_counter()
        elapsed = t1 - t0
        print(f"  {method} with {n_assets} assets: {elapsed * 1000:.1f} ms (success={result.success})")


def main():
    parser = argparse.ArgumentParser(description="Phase 6 P3 load tests")
    parser.add_argument("--quick", action="store_true", help="smaller scale for CI")
    args = parser.parse_args()

    scale = 0.2 if args.quick else 1.0

    print("=" * 70)
    print("Phase 6 P3 - Load Testing" + (" (QUICK)" if args.quick else ""))
    print("=" * 70)

    load_test_event_store(int(100_000 * scale))
    load_test_concurrent_readers(int(20_000 * scale))
    load_test_online_learning(int(200_000 * scale))
    load_test_portfolio_large_universe(int(100 * scale))

    print("\nLoad tests complete.")


if __name__ == "__main__":
    main()
