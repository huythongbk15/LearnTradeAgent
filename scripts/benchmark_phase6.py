"""
Phase 6 P3 - Performance Benchmarks

Benchmarks key P2 components:
- Event store append/read throughput
- Online learning update throughput
- Meta-learning training time
- Portfolio optimizer latency
- Attribution analysis time
- Sandbox execution overhead
- Auto-rebalancer latency

Run:  python scripts/benchmark_phase6.py
"""

import asyncio
import sys
import time
from decimal import Decimal
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import numpy as np
import pandas as pd


def timed(name, fn, *args, **kwargs):
    start = time.perf_counter()
    result = fn(*args, **kwargs)
    elapsed = time.perf_counter() - start
    print(f"  {name:<45} {elapsed * 1000:>10.2f} ms")
    return result


def run_async(coro):
    return asyncio.run(coro)


def benchmark_event_store():
    print("\n[Event Store]")
    from trading.events.store import EventStore, EventStoreConfig

    config = EventStoreConfig(file_path="/tmp/bench_events.jsonl")
    store = EventStore(config)

    async def scenario():
        await store.connect(backend="file")
        events = [
            store.create_trade_event(
                symbol="BTC/USDT", side="buy", size=Decimal("0.5"),
                price=Decimal("50000"), fee=Decimal("2.5"), fee_currency="USDT",
                exchange="binance", order_id=f"o{i}", strategy_id="s1",
            )
            for i in range(10000)
        ]
        t0 = time.perf_counter()
        await store.append_batch(events)
        t1 = time.perf_counter()
        print(f"  {'append 10k events':<45} {(t1 - t0) * 1000:>10.2f} ms")

        t0 = time.perf_counter()
        all_events = await store.read_all(count=10000)
        t1 = time.perf_counter()
        print(f"  {'read 10k events':<45} {(t1 - t0) * 1000:>10.2f} ms")
        assert len(all_events) == 10000

        await store.disconnect()

    run_async(scenario())


def benchmark_online_learning():
    print("\n[Online Learning]")
    from trading.ml.online.adaptive import AdaptiveConfig, AdaptiveStrategy

    np.random.seed(1)
    prices = 100 * np.exp(np.cumsum(np.random.normal(0.001, 0.01, 10000)))

    strat = AdaptiveStrategy(AdaptiveConfig(min_period=5, max_period=30, min_samples=30))
    t0 = time.perf_counter()
    for i in range(1, len(prices)):
        strat.update(
            high=float(prices[i] * 1.002), low=float(prices[i] * 0.998),
            close=float(prices[i]), volume=1000.0,
        )
    t1 = time.perf_counter()
    elapsed = t1 - t0
    print(f"  {'10k adaptive updates':<45} {elapsed * 1000:>10.2f} ms")
    print(f"  {'per-update latency':<45} {elapsed / 9999 * 1e6:>10.2f} us")


def benchmark_meta_learning():
    print("\n[Meta-Learning]")
    from trading.ml.meta import Reptile, MetaLearningConfig, StrategyParameterTask

    np.random.seed(2)
    tasks = []
    for i in range(5):
        data = 100 * np.exp(np.cumsum(np.random.normal(0.0005 * i, 0.01, 500)))
        tasks.append(StrategyParameterTask(data.reshape(-1, 1)))

    learner = Reptile(MetaLearningConfig(reptile_steps=5, inner_lr=0.1), {"ema_fast": 12.0, "ema_slow": 26.0})
    t0 = time.perf_counter()
    learner.meta_train(tasks, steps=3)
    t1 = time.perf_counter()
    print(f"  {'meta-train (5 tasks, 3 steps)':<45} {(t1 - t0) * 1000:>10.2f} ms")


def benchmark_portfolio_optimizer():
    print("\n[Portfolio Optimizer]")
    from trading.portfolio.portfolio_optimizer import PortfolioOptimizer, OptimizerMethod
    from trading.exchanges.models import Symbol, AssetClass, MarketType

    np.random.seed(3)
    n_assets = 10
    n_periods = 500
    returns = pd.DataFrame(
        np.random.randn(n_periods, n_assets) * 0.01,
        columns=[f"SYM{i}/USDT" for i in range(n_assets)],
    )
    symbols = [
        Symbol(f"SYM{i}", "USDT", AssetClass.CRYPTO, MarketType.SPOT, "binance")
        for i in range(n_assets)
    ]

    for method in ["mean_variance", "hrp", "max_sharpe"]:
        optimizer = PortfolioOptimizer(method=OptimizerMethod(method))
        optimizer.set_universe(symbols, returns)
        t0 = time.perf_counter()
        result = optimizer.optimize()
        t1 = time.perf_counter()
        print(f"  {'optimize ' + method:<45} {(t1 - t0) * 1000:>10.2f} ms (success={result.success})")


def benchmark_attribution():
    print("\n[Attribution]")
    from trading.portfolio.attribution.analyzer import AttributionAnalyzer

    np.random.seed(4)
    dates = pd.date_range("2026-01-01", periods=500, freq="D")
    port_returns = pd.Series(np.random.normal(0.0008, 0.01, 500), index=dates)
    bench_returns = pd.Series(np.random.normal(0.0004, 0.008, 500), index=dates)
    weights = pd.DataFrame(np.random.dirichlet(np.ones(5), size=500), index=dates, columns=[f"A{i}" for i in range(5)])
    bench_w = pd.DataFrame(np.ones((500, 5)) / 5, index=dates, columns=[f"A{i}" for i in range(5)])

    analyzer = AttributionAnalyzer(benchmark_returns=bench_returns, risk_free_rate=0.02)
    t0 = time.perf_counter()
    result = analyzer.analyze(port_returns, weights, bench_w, dates[0], dates[-1])
    t1 = time.perf_counter()
    print(f"  {'500-day attribution':<45} {(t1 - t0) * 1000:>10.2f} ms")


def benchmark_sandbox():
    print("\n[Sandbox]")
    from trading.strategies.sandbox import SubprocessSandbox, SandboxConfig

    STRATEGY_CODE = """
class TestStrategy:
    def on_bar(self, bar):
        return {"signal": 1}
"""
    sandbox = SubprocessSandbox(SandboxConfig(timeout_seconds=15))
    t0 = time.perf_counter()
    for _ in range(3):
        result = run_async(sandbox.execute(STRATEGY_CODE, "on_bar", {"close": 110.0}))
    t1 = time.perf_counter()
    assert result.success
    print(f"  {'3 sandbox executions':<45} {(t1 - t0) * 1000:>10.2f} ms")
    print(f"  {'per-execution overhead':<45} {(t1 - t0) / 3 * 1000:>10.2f} ms")


def benchmark_rebalancer():
    print("\n[Auto-Rebalancer]")
    from trading.portfolio.auto_rebalancer import AutoRebalancer, RebalanceConfig, RebalanceTrigger
    from trading.exchanges.models import Position, Symbol, AssetClass, MarketType

    symbols = [Symbol(f"SYM{i}", "USDT", AssetClass.CRYPTO, MarketType.SPOT, "binance") for i in range(5)]
    positions = {
        s: Position(symbol=s, size=Decimal("1"), entry_price=Decimal("100"), mark_price=Decimal("100"))
        for s in symbols
    }
    prices = {s: Decimal("100") for s in symbols}

    rebalancer = AutoRebalancer(RebalanceConfig())
    t0 = time.perf_counter()
    for _ in range(100):
        event = run_async(rebalancer.force_rebalance(positions, prices, RebalanceTrigger.MANUAL))
    t1 = time.perf_counter()
    assert event.success
    print(f"  {'100 force rebalances (5 assets)':<45} {(t1 - t0) * 1000:>10.2f} ms")


def main():
    print("=" * 70)
    print("Phase 6 P3 - Performance Benchmarks")
    print("=" * 70)
    benchmark_event_store()
    benchmark_online_learning()
    benchmark_meta_learning()
    benchmark_portfolio_optimizer()
    benchmark_attribution()
    benchmark_sandbox()
    benchmark_rebalancer()
    print("\nBenchmarks complete.")


if __name__ == "__main__":
    main()
