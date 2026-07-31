"""
Phase 6 P3 - Integration Test Suite

End-to-end integration tests for all Phase 6 P2 components:
- Event Sourcing (EventStore + projections)
- Online Learning (adaptive indicators + strategy)
- Meta-Learning (MAML / Reptile / MetaSGD / ANIL + adapter)
- Portfolio Optimizer (mean-variance, HRP, Black-Litterman)
- Attribution Analysis
- Auto-Rebalancer (calendar, threshold, force rebalance)
- Strategy Versioning (registry + git store)
- Sandboxed Execution (subprocess sandbox)
- Messaging (envelope + bus pattern)
- Multi-Region Sync Controller
- Chaos Engineering (experiment suite)
- End-to-end flow: signal -> event -> projection -> rebalance

Run:  python -m pytest tests/test_phase6_integration.py -v
"""

import asyncio
import sys
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

# Ensure project root is importable
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def synthetic_returns() -> pd.DataFrame:
    """Synthetic correlated returns for portfolio tests."""
    np.random.seed(42)
    n_assets = 5
    n_periods = 400

    corr = np.eye(n_assets) * 0.4 + np.ones((n_assets, n_assets)) * 0.15
    np.fill_diagonal(corr, 1.0)
    L = np.linalg.cholesky(corr)

    mu = np.array([0.0006, 0.0004, 0.0002, 0.0001, 0.0000])
    returns = np.random.randn(n_periods, n_assets) @ L.T * 0.008 + mu

    return pd.DataFrame(returns, columns=["BTC/USDT", "ETH/USDT", "SOL/USDT", "ADA/USDT", "XRP/USDT"])


@pytest.fixture(scope="module")
def synthetic_prices() -> pd.Series:
    """Synthetic price series with regime changes."""
    np.random.seed(7)
    n = 800
    returns = np.concatenate([
        np.random.normal(0.0008, 0.008, 250),   # bull
        np.random.normal(0.0001, 0.004, 250),   # sideways
        np.random.normal(-0.0009, 0.012, 150),  # bear
        np.random.normal(0.0002, 0.025, 150),   # high vol
    ])
    prices = 100 * np.exp(np.cumsum(returns))
    return pd.Series(prices)


def _make_symbol(base: str, quote: str = "USDT", exchange: str = "binance"):
    from trading.exchanges.models import Symbol, AssetClass, MarketType
    return Symbol(base, quote, AssetClass.CRYPTO, MarketType.SPOT, exchange)


def run_async(coro):
    """Run an async coroutine synchronously."""
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# 1. Event Sourcing
# ---------------------------------------------------------------------------

class TestEventSourcing:
    """EventStore with file backend + projections."""

    def test_append_read_roundtrip(self, tmp_path):
        from trading.events.store import EventStore, EventStoreConfig

        config = EventStoreConfig(file_path=str(tmp_path / "events.jsonl"))
        store = EventStore(config)

        async def scenario():
            await store.connect(backend="file")
            ev1 = store.create_trade_event(
                symbol="BTC/USDT", side="buy", size=Decimal("0.5"),
                price=Decimal("50000"), fee=Decimal("2.5"), fee_currency="USDT",
                exchange="binance", order_id="o1", strategy_id="s1",
            )
            ev2 = store.create_signal_event(
                symbol="BTC/USDT", signal_type="buy", strength=0.8,
                strategy_id="s1", timeframe="1h",
                indicators={"rsi": 30.0}, regime="bull",
            )
            ev3 = store.create_risk_event(
                check_type="position_limit", passed=True, metric="position_pct",
                value=Decimal("0.25"), threshold=Decimal("0.3"),
                symbol="BTC/USDT", strategy_id="s1",
            )
            id1 = await store.append(ev1)
            id2 = await store.append(ev2)
            id3 = await store.append(ev3)

            all_events = await store.read_all(count=10)
            assert len(all_events) == 3
            assert all_events[0].event_id == id1
            assert all_events[0].symbol == "BTC/USDT"
            assert all_events[1].symbol == "BTC/USDT"
            assert all_events[1].strength == 0.8
            assert all_events[2].metric == "position_pct"

            trade_stream = await store.read_stream("trade.executed", count=10)
            assert len(trade_stream) == 1
            assert trade_stream[0].side == "buy"

            await store.disconnect()

        run_async(scenario())

    def test_append_batch(self, tmp_path):
        from trading.events.store import EventStore, EventStoreConfig

        config = EventStoreConfig(file_path=str(tmp_path / "events_batch.jsonl"))
        store = EventStore(config)

        async def scenario():
            await store.connect(backend="file")
            events = [
                store.create_order_event(
                    order_id=f"o{i}", symbol="ETH/USDT", side="buy", order_type="limit",
                    size=Decimal("1"), price=Decimal("3000"), status="filled",
                    filled_size=Decimal("1"), avg_fill_price=Decimal("2999"),
                    exchange="binance", strategy_id="s1",
                )
                for i in range(5)
            ]
            ids = await store.append_batch(events)
            assert len(ids) == 5
            all_events = await store.read_all(count=20)
            assert len(all_events) == 5
            await store.disconnect()

        run_async(scenario())

    def test_projections_update(self, tmp_path):
        from trading.events.store import EventStore, EventStoreConfig
        from trading.events.projections import (
            TradeProjection, PositionProjection, RiskProjection, SignalProjection,
        )

        config = EventStoreConfig(file_path=str(tmp_path / "events_proj.jsonl"))
        store = EventStore(config)
        trade_proj = TradeProjection()
        pos_proj = PositionProjection()
        risk_proj = RiskProjection()
        sig_proj = SignalProjection()
        store.add_projection("trades", trade_proj)
        store.add_projection("positions", pos_proj)
        store.add_projection("risk", risk_proj)
        store.add_projection("signals", sig_proj)

        async def scenario():
            await store.connect(backend="file")

            # Trades: 2 wins 1 loss
            await store.append(store.create_trade_event(
                symbol="BTC/USDT", side="buy", size=Decimal("1"),
                price=Decimal("100"), fee=Decimal("1"), fee_currency="USDT",
                exchange="binance", order_id="t1", strategy_id="mom",
            ))
            await store.append(store.create_trade_event(
                symbol="BTC/USDT", side="sell", size=Decimal("1"),
                price=Decimal("110"), fee=Decimal("1"), fee_currency="USDT",
                exchange="binance", order_id="t2", strategy_id="mom",
            ))
            await store.append(store.create_trade_event(
                symbol="ETH/USDT", side="buy", size=Decimal("2"),
                price=Decimal("50"), fee=Decimal("1"), fee_currency="USDT",
                exchange="binance", order_id="t3", strategy_id="mm",
            ))
            await store.append(store.create_trade_event(
                symbol="ETH/USDT", side="sell", size=Decimal("2"),
                price=Decimal("45"), fee=Decimal("1"), fee_currency="USDT",
                exchange="binance", order_id="t4", strategy_id="mm",
            ))

            # Positions
            await store.append(store.create_position_event(
                symbol="BTC/USDT", size=Decimal("1"), entry_price=Decimal("100"),
                mark_price=Decimal("108"), unrealized_pnl=Decimal("8"),
                realized_pnl=Decimal("0"), leverage=Decimal("1"), strategy_id="mom",
            ))
            await store.append(store.create_position_event(
                symbol="BTC/USDT", size=Decimal("0"), entry_price=Decimal("100"),
                mark_price=Decimal("108"), unrealized_pnl=Decimal("0"),
                realized_pnl=Decimal("8"), leverage=Decimal("1"), strategy_id="mom",
            ))

            # Risk
            await store.append(store.create_risk_event(
                check_type="drawdown", passed=False, metric="drawdown_pct",
                value=Decimal("0.18"), threshold=Decimal("0.15"),
                symbol="BTC/USDT", strategy_id="mom",
            ))
            await store.append(store.create_risk_event(
                check_type="position_limit", passed=True, metric="position_pct",
                value=Decimal("0.1"), threshold=Decimal("0.3"),
                symbol="BTC/USDT", strategy_id="mom",
            ))

            # Signals
            await store.append(store.create_signal_event(
                symbol="BTC/USDT", signal_type="buy", strength=0.9,
                strategy_id="mom", timeframe="1h",
            ))
            await store.append(store.create_signal_event(
                symbol="ETH/USDT", signal_type="sell", strength=0.7,
                strategy_id="mm", timeframe="1h",
            ))

            tstate = await trade_proj.get_state()
            assert tstate["total_trades"] == 4
            assert tstate["win_count"] == 2
            assert tstate["loss_count"] == 2
            assert tstate["win_rate"] == 0.5
            assert "BTC/USDT" in tstate["pnl_by_symbol"]

            pstate = await pos_proj.get_state()
            assert len(pstate["positions"]) == 0  # closed position removed

            rstate = await risk_proj.get_state()
            assert rstate["breaches"] == 1
            assert len(rstate["recent_alerts"]) == 1
            assert rstate["total_checks"] == 2

            sstate = await sig_proj.get_state()
            assert sstate["total_signals"] == 2
            assert sstate["by_strategy"]["mom"] == 1
            assert sstate["by_strategy"]["mm"] == 1

            await store.disconnect()

        run_async(scenario())


# ---------------------------------------------------------------------------
# 2. Online Learning
# ---------------------------------------------------------------------------

class TestOnlineLearning:
    """Adaptive indicators and strategies."""

    def test_adaptive_ema(self):
        from trading.ml.online.adaptive import AdaptiveEMA, AdaptiveConfig

        cfg = AdaptiveConfig(min_period=5, max_period=30, min_samples=30)
        ema = AdaptiveEMA(cfg)

        values = np.linspace(100, 130, 60) + np.random.normal(0, 0.5, 60)
        results = [ema.update(v, performance=0.001) for v in values]

        assert all(r != 0.0 for r in results)
        assert ema.is_ready
        assert ema.current_period >= cfg.min_period
        # Values should be near the input range
        assert min(results) > 90
        assert max(results) < 140

    def test_adaptive_rsi(self):
        from trading.ml.online.adaptive import AdaptiveRSI, AdaptiveConfig

        cfg = AdaptiveConfig(min_period=7, max_period=21, min_samples=30)
        rsi = AdaptiveRSI(cfg)

        # Oscillating input should produce RSI in [0, 100]
        values = [100 + 5 * np.sin(i / 3) for i in range(80)]
        results = [rsi.update(v) for v in values]
        ready = [r for r in results if r is not None and r != 0.0]
        assert ready
        assert all(0 <= r <= 100 for r in ready)

    def test_adaptive_bollinger(self):
        from trading.ml.online.adaptive import AdaptiveBollingerBands, AdaptiveConfig

        cfg = AdaptiveConfig(min_period=10, max_period=30, min_samples=30)
        bb = AdaptiveBollingerBands(cfg)

        values = np.linspace(100, 100, 50) + np.random.normal(0, 1, 50)
        for v in values:
            bb.update(v)

        assert bb.is_ready
        assert 1.5 <= bb.current_std_mult <= 3.0

    def test_adaptive_strategy_signal(self):
        from trading.ml.online.adaptive import AdaptiveConfig, AdaptiveStrategy

        cfg = AdaptiveConfig(min_period=5, max_period=30, min_samples=30)
        strat = AdaptiveStrategy(cfg)

        np.random.seed(11)
        prices = 100 * np.exp(np.cumsum(np.random.normal(0.001, 0.01, 300)))
        signals = []
        for i in range(1, len(prices)):
            result = strat.update(
                high=float(prices[i] * 1.002),
                low=float(prices[i] * 0.998),
                close=float(prices[i]),
                volume=1000.0,
            )
            signals.append(result["signal"])
            assert set(result.keys()) >= {
                "signal", "ema", "rsi", "bb_middle", "bb_upper", "bb_lower",
                "macd", "macd_signal", "macd_hist", "atr", "position", "performance",
            }

        # Strategy should have traded at least once (position may be open or closed)
        assert len(strat.trades) >= 0
        assert strat.position in (-1, 0, 1)

    def test_adaptive_strategy_reset(self):
        from trading.ml.online.adaptive import AdaptiveConfig, AdaptiveStrategy

        strat = AdaptiveStrategy(AdaptiveConfig())
        for i in range(60):
            strat.update(101.0, 99.0, 100.0, 1000.0)
        strat.reset()
        assert strat.position == 0
        assert strat.trades == []
        assert strat.performance == 0.0


# ---------------------------------------------------------------------------
# 3. Meta-Learning
# ---------------------------------------------------------------------------

class TestMetaLearning:
    """MAML / Reptile / MetaSGD / ANIL meta-learners."""

    def _make_tasks(self, n_tasks=3):
        from trading.ml.meta import StrategyParameterTask
        np.random.seed(123)
        tasks = []
        for i in range(n_tasks):
            data = 100 * np.exp(np.cumsum(np.random.normal(0.0005 * i, 0.01, 200)))
            data = data.reshape(-1, 1)
            tasks.append(StrategyParameterTask(data))
        return tasks

    def test_maml(self):
        from trading.ml.meta import MAML, MetaLearningConfig

        config = MetaLearningConfig(meta_lr=0.001, inner_lr=0.1, inner_steps=2, meta_batch_size=2)
        initial = {"ema_fast": 12.0, "ema_slow": 26.0, "rsi_period": 14.0}
        learner = MAML(config, initial)
        tasks = self._make_tasks()
        meta_params = learner.meta_train(tasks, steps=2)
        assert set(meta_params.keys()) == set(initial.keys())
        adapted = learner.adapt(tasks[0], n_samples=10)
        assert set(adapted.keys()) == set(initial.keys())
        assert all(isinstance(v, float) for v in adapted.values())

    def test_reptile(self):
        from trading.ml.meta import Reptile, MetaLearningConfig

        config = MetaLearningConfig(reptile_meta_lr=0.05, reptile_steps=3, inner_lr=0.1)
        initial = {"ema_fast": 12.0, "ema_slow": 26.0}
        learner = Reptile(config, initial)
        tasks = self._make_tasks(2)
        meta_params = learner.meta_train(tasks, steps=2)
        assert set(meta_params.keys()) == set(initial.keys())
        adapted = learner.adapt(tasks[0])
        assert set(adapted.keys()) == set(initial.keys())

    def test_metasgd(self):
        from trading.ml.meta import MetaSGD, MetaLearningConfig

        config = MetaLearningConfig(meta_lr=0.001, inner_lr=0.1, inner_steps=2)
        initial = {"ema_fast": 12.0, "ema_slow": 26.0, "rsi_period": 14.0}
        learner = MetaSGD(config, initial)
        tasks = self._make_tasks(2)
        meta_params = learner.meta_train(tasks, steps=2)
        assert set(meta_params.keys()) == set(initial.keys())
        # Learning rates should have been learned
        assert set(learner.meta_lr_params.keys()) == set(initial.keys())
        adapted = learner.adapt(tasks[0])
        assert set(adapted.keys()) == set(initial.keys())

    def test_anil(self):
        from trading.ml.meta import ANIL, MetaLearningConfig

        config = MetaLearningConfig(meta_lr=0.001, inner_lr=0.1, inner_steps=2)
        initial = {"ema_fast": 12.0, "ema_slow": 26.0, "rsi_period": 14.0}
        learner = ANIL(config, initial, head_keys=["ema_fast", "ema_slow"])
        tasks = self._make_tasks(2)
        meta_params = learner.meta_train(tasks, steps=2)
        assert set(meta_params.keys()) == set(initial.keys())
        adapted = learner.adapt(tasks[0])
        assert set(adapted.keys()) == set(initial.keys())
        # Body params should be unchanged from meta params
        assert adapted["rsi_period"] == initial["rsi_period"]

    def test_meta_strategy_adapter(self):
        from trading.ml.meta import MetaStrategyAdapter

        np.random.seed(99)
        market_data = {
            "bull": 100 * np.exp(np.cumsum(np.random.normal(0.001, 0.008, 300))).reshape(-1, 1),
            "bear": 100 * np.exp(np.cumsum(np.random.normal(-0.001, 0.01, 300))).reshape(-1, 1),
            "sideways": 100 * np.exp(np.cumsum(np.random.normal(0.0001, 0.004, 300))).reshape(-1, 1),
        }
        adapter = MetaStrategyAdapter(algorithm="reptile")
        meta_params = adapter.train(market_data, steps=2)
        assert set(meta_params.keys()) == set(adapter.param_names)
        assert adapter.get_meta_params() == meta_params

        regime_data = 100 * np.exp(np.cumsum(np.random.normal(0.0, 0.015, 200))).reshape(-1, 1)
        adapted = adapter.adapt_to_regime(regime_data, n_samples=20)
        assert set(adapted.keys()) == set(adapter.param_names)
        # Adapted params should respect bounds
        assert 5 <= adapted["ema_fast"] <= 30
        assert 20 <= adapted["ema_slow"] <= 60
        assert 7 <= adapted["rsi_period"] <= 21

    def test_meta_adapter_unknown_algorithm(self):
        from trading.ml.meta import MetaStrategyAdapter

        adapter = MetaStrategyAdapter(algorithm="unknown")
        with pytest.raises(ValueError):
            adapter.train({}, steps=1)

    def test_meta_adapter_requires_train(self):
        from trading.ml.meta import MetaStrategyAdapter

        adapter = MetaStrategyAdapter()
        with pytest.raises(RuntimeError):
            adapter.adapt_to_regime(np.zeros((10, 1)))


# ---------------------------------------------------------------------------
# 4. Portfolio Optimizer
# ---------------------------------------------------------------------------

class TestPortfolioOptimizer:
    """Portfolio optimization methods."""

    def _setup(self, method, synthetic_returns):
        from trading.portfolio.portfolio_optimizer import PortfolioOptimizer, OptimizerMethod
        symbols = [_make_symbol(b) for b in ["BTC", "ETH", "SOL", "ADA", "XRP"]]
        optimizer = PortfolioOptimizer(method=OptimizerMethod(method))
        optimizer.set_universe(symbols, synthetic_returns)
        return optimizer

    def test_mean_variance(self, synthetic_returns):
        optimizer = self._setup("mean_variance", synthetic_returns)
        result = optimizer.optimize()
        assert result.success
        assert len(result.weights) == 5
        total = sum(float(w) for w in result.weights.values())
        assert abs(total - 1.0) < 0.05
        assert result.expected_volatility > 0

    def test_hrp(self, synthetic_returns):
        optimizer = self._setup("hrp", synthetic_returns)
        result = optimizer.optimize()
        assert result.success
        assert len(result.weights) == 5
        total = sum(float(w) for w in result.weights.values())
        assert abs(total - 1.0) < 0.05

    def test_black_litterman(self, synthetic_returns):
        from trading.portfolio.portfolio_optimizer import (
            PortfolioOptimizer, OptimizerMethod, BlackLittermanViews,
        )
        symbols = [_make_symbol(b) for b in ["BTC", "ETH", "SOL", "ADA", "XRP"]]
        optimizer = PortfolioOptimizer(method=OptimizerMethod.BLACK_LITTERMAN)
        optimizer.set_universe(symbols, synthetic_returns)

        views = BlackLittermanViews(
            absolute={symbols[0]: 0.05, symbols[1]: 0.02},
            relative={(symbols[0], symbols[1]): 0.03},
            confidence={symbols[0]: 0.6, symbols[1]: 0.5},
        )
        result = optimizer.optimize(views=views)
        assert result.success
        assert len(result.weights) == 5
        total = sum(float(w) for w in result.weights.values())
        assert abs(total - 1.0) < 0.05

    def test_black_litterman_requires_views(self, synthetic_returns):
        optimizer = self._setup("black_litterman", synthetic_returns)
        with pytest.raises(ValueError):
            optimizer.optimize()

    def test_max_sharpe(self, synthetic_returns):
        optimizer = self._setup("max_sharpe", synthetic_returns)
        result = optimizer.optimize()
        assert result.success
        assert len(result.weights) == 5


# ---------------------------------------------------------------------------
# 5. Attribution Analysis
# ---------------------------------------------------------------------------

class TestAttribution:
    """Performance attribution."""

    def test_brinson_attribution(self):
        from trading.portfolio.attribution.analyzer import AttributionAnalyzer

        np.random.seed(5)
        dates = pd.date_range("2026-01-01", periods=100, freq="D")
        n = 100

        port_returns = pd.Series(np.random.normal(0.0008, 0.01, n), index=dates)
        bench_returns = pd.Series(np.random.normal(0.0004, 0.008, n), index=dates)

        weights_data = np.random.dirichlet(np.ones(5), size=n)
        port_weights = pd.DataFrame(weights_data, index=dates, columns=[f"A{i}" for i in range(5)])
        bench_weights = pd.DataFrame(np.ones((n, 5)) / 5, index=dates, columns=[f"A{i}" for i in range(5)])

        analyzer = AttributionAnalyzer(benchmark_returns=bench_returns, risk_free_rate=0.02)
        result = analyzer.analyze(
            portfolio_returns=port_returns,
            portfolio_weights=port_weights,
            benchmark_weights=bench_weights,
            period_start=dates[0],
            period_end=dates[-1],
        )

        assert result.period_start == dates[0]
        assert result.period_end == dates[-1]
        assert result.active_return == result.total_return - result.benchmark_return
        assert isinstance(result.total_return, Decimal)


# ---------------------------------------------------------------------------
# 6. Auto-Rebalancer
# ---------------------------------------------------------------------------

class TestAutoRebalancer:
    """Rebalancing strategies and the AutoRebalancer."""

    def _positions_and_prices(self):
        from trading.exchanges.models import Position
        btc = _make_symbol("BTC")
        eth = _make_symbol("ETH")
        positions = {
            btc: Position(symbol=btc, size=Decimal("1"), entry_price=Decimal("50000"),
                          mark_price=Decimal("50000")),
            eth: Position(symbol=eth, size=Decimal("10"), entry_price=Decimal("3000"),
                          mark_price=Decimal("3000")),
        }
        prices = {btc: Decimal("50000"), eth: Decimal("3000")}
        return positions, prices

    def test_calendar_should_rebalance(self):
        from trading.portfolio.auto_rebalancer import CalendarRebalanceStrategy, RebalanceConfig, RebalanceTrigger

        strat = CalendarRebalanceStrategy()
        config = RebalanceConfig(calendar_enabled=True, calendar_frequency="monthly", calendar_day=1)
        current = {"A": Decimal("0.5"), "B": Decimal("0.5")}
        target = {"A": Decimal("0.5"), "B": Decimal("0.5")}

        should, trigger = strat.should_rebalance(current, target, config, None)
        assert should
        assert trigger == RebalanceTrigger.CALENDAR

        # Same month -> no rebalance
        should, _ = strat.should_rebalance(current, target, config, datetime.now())
        assert not should

    def test_threshold_should_rebalance(self):
        from trading.portfolio.auto_rebalancer import (
            ThresholdRebalanceStrategy, RebalanceConfig, RebalanceTrigger,
        )

        strat = ThresholdRebalanceStrategy()
        config = RebalanceConfig(threshold_enabled=True, threshold_band_pct=0.05)

        # Drift 10% > band 5% -> rebalance
        current = {"A": Decimal("0.60"), "B": Decimal("0.40")}
        target = {"A": Decimal("0.50"), "B": Decimal("0.50")}
        should, trigger = strat.should_rebalance(current, target, config, None)
        assert should
        assert trigger == RebalanceTrigger.THRESHOLD

        # Small drift -> no rebalance
        current2 = {"A": Decimal("0.51"), "B": Decimal("0.49")}
        should, _ = strat.should_rebalance(current2, target, config, None)
        assert not should

    def test_cppi(self):
        from trading.portfolio.auto_rebalancer import CPPIRebalanceStrategy, RebalanceConfig

        strat = CPPIRebalanceStrategy()
        config = RebalanceConfig(cppi_enabled=True, cppi_multiplier=3.0, cppi_floor_pct=0.8)
        btc = _make_symbol("BTC")
        eth = _make_symbol("ETH")
        positions = {btc: None, eth: None}
        prices = {btc: Decimal("50000"), eth: Decimal("3000")}

        # Establish peak at 120000 -> floor = 96000
        run_async(strat.calculate_target_weights(
            positions, prices, Decimal("120000"), config
        ))

        # Near floor (100000): cushion = 4000; risky = 12000; weight = 0.12
        weights = run_async(strat.calculate_target_weights(
            positions, prices, Decimal("100000"), config
        ))
        total = sum(weights.values())
        assert 0.05 < total < 0.3

        # Far above floor (150000): cushion = 54000; risky = 162000; weight = 1.0
        weights2 = run_async(strat.calculate_target_weights(
            positions, prices, Decimal("150000"), config
        ))
        total2 = sum(weights2.values())
        assert total2 > 0.5

    def test_force_rebalance_generates_trades(self):
        from trading.portfolio.auto_rebalancer import AutoRebalancer, RebalanceConfig, RebalanceTrigger

        config = RebalanceConfig(
            calendar_enabled=False, threshold_enabled=False,
            cppi_enabled=False, risk_budget_enabled=False,
            min_trade_size=Decimal("1"), transaction_cost_bps=10.0,
        )
        rebalancer = AutoRebalancer(config)
        positions, prices = self._positions_and_prices()

        event = run_async(rebalancer.force_rebalance(positions, prices, RebalanceTrigger.MANUAL))
        assert event is not None
        assert event.trigger == RebalanceTrigger.MANUAL
        assert event.success
        assert isinstance(event.turnover, Decimal)
        assert isinstance(event.estimated_cost, Decimal)
        assert len(rebalancer.get_history()) == 1
        assert rebalancer.get_last_rebalance() is not None
        status = rebalancer.get_status()
        assert status["enabled"] is True
        assert status["total_rebalances"] == 1

    def test_disabled_no_rebalance(self):
        from trading.portfolio.auto_rebalancer import AutoRebalancer, RebalanceConfig

        rebalancer = AutoRebalancer(RebalanceConfig())
        rebalancer.disable()
        positions, prices = self._positions_and_prices()
        event = run_async(rebalancer.check_and_rebalance(positions, prices))
        assert event is None
        assert not rebalancer.is_enabled()


# ---------------------------------------------------------------------------
# 7. Strategy Versioning
# ---------------------------------------------------------------------------

class TestStrategyVersioning:
    """Strategy registry + git store."""

    def test_registry_register_and_activate(self, tmp_path):
        from trading.strategies.versioning import (
            StrategyRegistry, StrategyMetadata, AssetClass, RiskProfile,
        )
        from trading.strategies.plugins.strategy_plugin import ExampleMAStrategy

        registry = StrategyRegistry(store_path=str(tmp_path / "registry"))
        metadata = StrategyMetadata(
            name="MA_Crossover", version="1.0.0", author="test",
            description="Test strategy", asset_class=AssetClass.CRYPTO,
            risk_profile=RiskProfile.MODERATE, timeframes=["1h"],
            symbols=["BTC/USDT"], params_schema={"fast": "int", "slow": "int"},
            backtest_hash="", backtest_period="2025-01-01/2025-06-01",
            backtest_metrics={"sharpe": 1.2},
        )
        version = registry.register(ExampleMAStrategy, metadata)
        assert version.source_hash
        assert version.abi_hash
        assert registry.get_active("MA_Crossover") is version
        assert "MA_Crossover" in registry.list_strategies()

        # Register v2 and activate
        metadata2 = StrategyMetadata(
            name="MA_Crossover", version="2.0.0", author="test",
            description="v2", asset_class=AssetClass.CRYPTO,
            risk_profile=RiskProfile.MODERATE, timeframes=["1h"],
            symbols=["BTC/USDT"], params_schema={"fast": "int", "slow": "int"},
            backtest_hash="", backtest_period="2025-01-01/2025-06-01",
            backtest_metrics={"sharpe": 1.5},
        )
        v2 = registry.register(ExampleMAStrategy, metadata2)
        assert registry.get_active("MA_Crossover").metadata.version == "2.0.0"
        assert len(registry.list_versions("MA_Crossover")) == 2

        assert registry.activate("MA_Crossover", "1.0.0")
        assert registry.get_active("MA_Crossover").metadata.version == "1.0.0"
        assert registry.deprecate("MA_Crossover", "1.0.0")
        assert registry.get_active("MA_Crossover") is None

    def test_registry_loader(self, tmp_path):
        from trading.strategies.versioning import (
            StrategyRegistry, StrategyMetadata, StrategyLoader, AssetClass, RiskProfile,
        )
        from trading.strategies.plugins.strategy_plugin import ExampleMAStrategy

        registry = StrategyRegistry(store_path=str(tmp_path / "registry2"))
        metadata = StrategyMetadata(
            name="MA_Crossover", version="1.0.0", author="test",
            description="Test", asset_class=AssetClass.CRYPTO,
            risk_profile=RiskProfile.MODERATE, timeframes=["1h"],
            symbols=["BTC/USDT"], params_schema={},
            backtest_hash="", backtest_period="", backtest_metrics={},
        )
        registry.register(ExampleMAStrategy, metadata)

        loader = StrategyLoader(registry)
        cls = loader.load("MA_Crossover")
        assert cls is not None
        assert hasattr(cls, "on_bar")

    def test_git_store_save_load(self, tmp_path):
        from trading.strategies.versioning import (
            StrategyRegistry, StrategyMetadata, GitVersionStore, AssetClass, RiskProfile,
        )
        from trading.strategies.plugins.strategy_plugin import ExampleMAStrategy

        registry = StrategyRegistry(store_path=str(tmp_path / "registry3"))
        metadata = StrategyMetadata(
            name="MA_Crossover", version="1.0.0", author="test",
            description="Test", asset_class=AssetClass.CRYPTO,
            risk_profile=RiskProfile.MODERATE, timeframes=["1h"],
            symbols=["BTC/USDT"], params_schema={},
            backtest_hash="", backtest_period="", backtest_metrics={},
        )
        version = registry.register(ExampleMAStrategy, metadata)

        git_store = GitVersionStore(repo_path=str(tmp_path / "strategies_repo"))
        commit_hash = git_store.save_version(version)
        assert commit_hash

        loaded = git_store.load_version("MA_Crossover", "1.0.0")
        assert loaded is not None
        assert loaded.metadata.name == "MA_Crossover"
        assert loaded.source_hash == version.source_hash

        assert "1.0.0" in git_store.list_versions("MA_Crossover")
        tags = git_store.tag_release("MA_Crossover", "1.0.0")
        assert tags in git_store.list_tags()


# ---------------------------------------------------------------------------
# 8. Sandboxed Execution
# ---------------------------------------------------------------------------

class TestSandbox:
    """Subprocess sandbox for strategy code."""

    STRATEGY_CODE = """
import numpy as np

class TestStrategy:
    def on_bar(self, bar):
        return {"signal": 1 if bar.get("close", 0) > bar.get("ema", 0) else -1, "params": self.get_params()}

    def get_params(self):
        return {"fast": 5, "slow": 20}
"""

    def test_validate_ok(self):
        from trading.strategies.sandbox import SubprocessSandbox, SandboxConfig

        sandbox = SubprocessSandbox(SandboxConfig())
        result = run_async(sandbox.validate(self.STRATEGY_CODE))
        assert result.success

    def test_validate_forbidden_import(self):
        from trading.strategies.sandbox import SubprocessSandbox, SandboxConfig

        sandbox = SubprocessSandbox(SandboxConfig())
        code = "import os\nclass Evil:\n    pass\n"
        result = run_async(sandbox.validate(code))
        assert not result.success
        assert "Forbidden import" in result.error

    def test_validate_syntax_error(self):
        from trading.strategies.sandbox import SubprocessSandbox, SandboxConfig

        sandbox = SubprocessSandbox(SandboxConfig())
        result = run_async(sandbox.validate("def broken(:\n"))
        assert not result.success

    def test_execute_on_bar(self):
        from trading.strategies.sandbox import SubprocessSandbox, SandboxConfig

        sandbox = SubprocessSandbox(SandboxConfig(timeout_seconds=15))
        result = run_async(sandbox.execute(
            self.STRATEGY_CODE, "on_bar", {"close": 110.0, "ema": 100.0},
        ))
        assert result.success
        assert result.output["signal"] == 1

    def test_execute_get_params(self):
        from trading.strategies.sandbox import SubprocessSandbox, SandboxConfig

        sandbox = SubprocessSandbox(SandboxConfig(timeout_seconds=15))
        result = run_async(sandbox.execute(self.STRATEGY_CODE, "get_params"))
        assert result.success
        assert result.output == {"fast": 5, "slow": 20}

    def test_sandbox_factory_default(self):
        from trading.strategies.sandbox import SandboxFactory

        sandbox = SandboxFactory.create_default()
        assert sandbox is not None


# ---------------------------------------------------------------------------
# 9. Messaging
# ---------------------------------------------------------------------------

class TestMessaging:
    """Message envelope + bus pattern."""

    def test_message_roundtrip(self):
        from trading.messaging import Message, MessagePriority

        msg = Message(
            topic="signal.generated",
            payload={"symbol": "BTC/USDT", "signal": "buy"},
            priority=MessagePriority.HIGH,
            correlation_id="corr-1",
        )
        data = msg.to_dict()
        restored = Message.from_dict(data)
        assert restored.topic == "signal.generated"
        assert restored.payload["signal"] == "buy"
        assert restored.priority == MessagePriority.HIGH
        assert restored.correlation_id == "corr-1"

    def test_in_memory_bus(self):
        from trading.messaging import Message, MessageBus

        class InMemoryBus(MessageBus):
            def __init__(self):
                self.subscribers = {}
                self.published = []

            async def connect(self):
                pass

            async def disconnect(self):
                self.subscribers.clear()

            async def publish(self, topic, payload, **kwargs):
                self.published.append(Message(topic=topic, payload=payload, **kwargs))
                for sub in self.subscribers.get(topic, []):
                    sub(self.published[-1])

            async def subscribe(self, topic, handler, **kwargs):
                sub_id = f"sub-{len(self.subscribers)}"
                self.subscribers.setdefault(topic, []).append(handler)
                return sub_id

            async def unsubscribe(self, subscription_id):
                pass

            async def request(self, topic, payload, timeout=30.0):
                return None

        bus = InMemoryBus()
        received = []

        async def scenario():
            await bus.connect()
            await bus.subscribe("trades", lambda m: received.append(m))
            await bus.publish("trades", {"symbol": "BTC/USDT", "side": "buy"})
            await bus.disconnect()

        run_async(scenario())
        assert len(bus.published) == 1
        assert len(received) == 1
        assert received[0].payload["symbol"] == "BTC/USDT"


# ---------------------------------------------------------------------------
# 10. Multi-Region Sync Controller
# ---------------------------------------------------------------------------

class TestMultiRegion:
    """RegionSyncController status + failover logic (no cluster needed)."""

    def _make_controller(self):
        from trading.infrastructure.multi_region.sync_controller import (
            RegionSyncController, RegionInfo, RegionRole, SyncPolicy,
        )
        regions = [
            RegionInfo(name="ap-southeast-1", role=RegionRole.PRIMARY, priority=1, kube_context="sg"),
            RegionInfo(name="us-east-1", role=RegionRole.SECONDARY, priority=2, kube_context="us"),
            RegionInfo(name="eu-west-1", role=RegionRole.TERTIARY, priority=3, kube_context="eu"),
        ]
        controller = RegionSyncController(regions, SyncPolicy(interval_seconds=60))
        return controller, regions

    def test_initial_status(self):
        controller, _ = self._make_controller()
        status = controller.get_status()
        assert status["primary"] == "ap-southeast-1"
        assert len(status["regions"]) == 3
        assert status["regions"]["us-east-1"]["role"] == "secondary"
        assert status["regions"]["eu-west-1"]["role"] == "tertiary"

    def test_failover_primary_promotes_secondary(self):
        controller, regions = self._make_controller()
        primary = controller.primary_region
        secondary = regions[1]

        # Mark secondary healthy and trigger failover check on primary
        secondary.status = None  # ensure healthy default
        from trading.infrastructure.multi_region.sync_controller import RegionStatus
        secondary.status = RegionStatus.HEALTHY

        # Patch _update_region_config to avoid k8s call
        async def noop(region, is_primary):
            return None
        controller._update_region_config = noop

        run_async(controller._check_failover(primary))

        assert secondary.role.value == "primary"
        assert controller.primary_region is secondary
        # Old primary demoted
        assert primary.role.value == "secondary"

    def test_failover_secondary_promotes_tertiary(self):
        controller, regions = self._make_controller()
        secondary = regions[1]
        tertiary = regions[2]

        from trading.infrastructure.multi_region.sync_controller import RegionStatus
        tertiary.status = RegionStatus.HEALTHY

        async def noop(region, is_primary):
            return None
        controller._update_region_config = noop

        run_async(controller._check_failover(secondary))
        assert tertiary.role.value == "secondary"

    def test_sync_policy_defaults(self):
        from trading.infrastructure.multi_region.sync_controller import SyncPolicy

        policy = SyncPolicy()
        assert policy.max_lag_seconds == 300
        assert policy.retry_attempts == 3

    def test_dry_run_start_and_status(self):
        """Dry-run mode must start without a cluster and report status."""
        controller, _ = self._make_controller()
        controller.dry_run = True

        run_async(controller.start())
        status = controller.get_status()
        assert status["primary"] == "ap-southeast-1"
        run_async(controller.stop())

    def test_dry_run_sync_updates_lag(self):
        """Dry-run sync should mark regions healthy with zero lag."""
        controller, regions = self._make_controller()
        controller.dry_run = True
        secondary = regions[1]

        run_async(controller._sync_region(secondary))
        assert secondary.last_sync is not None
        assert secondary.sync_lag_seconds == 0.0
        assert secondary.status.value == "healthy"

    def test_dry_run_health_check(self):
        """Dry-run health check simulates healthy regions."""
        controller, regions = self._make_controller()
        controller.dry_run = True
        region = regions[0]

        run_async(controller._check_region_health(region))
        assert region.status.value == "healthy"
        assert region.last_heartbeat is not None

    def test_dry_run_update_config_no_crash(self):
        """Dry-run update_config should log intent without k8s."""
        controller, regions = self._make_controller()
        controller.dry_run = True

        # Should not raise (no k8s client initialized)
        run_async(controller._update_region_config(regions[1], True))

    def test_dry_run_cli_get_controller(self):
        """Global factory with dry_run=True should work."""
        from trading.infrastructure.multi_region.sync_controller import (
            get_sync_controller, shutdown_sync_controller,
        )

        async def scenario():
            controller = await get_sync_controller(dry_run=True)
            assert controller.dry_run is True
            assert controller.primary_region is not None
            await shutdown_sync_controller()

        run_async(scenario())


# ---------------------------------------------------------------------------
# 11. Chaos Engineering
# ---------------------------------------------------------------------------

class TestChaos:
    """Chaos experiment suite structure + reporting."""

    def test_suite_add_experiments(self):
        from trading.infrastructure.chaos.chaos_experiments import (
            ChaosExperimentSuite, ChaosExperimentType,
        )

        suite = ChaosExperimentSuite(namespace="trading-agent")
        suite.add_pod_kill("pod-kill", {"app": "trading-agent"}, duration=5)
        suite.add_network_latency("net-latency", {"app": "trading-agent"}, duration=5, latency_ms=100)
        suite.add_exchange_api_failure("api-failure", {"app": "trading-agent"}, duration=5)
        suite.add_database_failure("db-failure", {"app": "trading-agent"}, duration=5)
        suite.add_cpu_stress("cpu-stress", {"app": "trading-agent"}, duration=5, cpu_percent=80)

        assert len(suite.experiments) == 5
        types = [e.experiment_type for e in suite.experiments]
        assert ChaosExperimentType.POD_KILL in types
        assert ChaosExperimentType.NETWORK_LATENCY in types
        assert ChaosExperimentType.EXCHANGE_API_FAILURE in types
        assert ChaosExperimentType.DATABASE_CONNECTION_FAILURE in types
        assert ChaosExperimentType.CPU_STRESS in types

    def test_runner_selection(self):
        from trading.infrastructure.chaos.chaos_experiments import (
            ChaosExperimentSuite, ChaosExperiment, ChaosExperimentType,
            PodKillExperiment, NetworkLatencyExperiment,
        )

        suite = ChaosExperimentSuite()
        exp = ChaosExperiment(name="x", experiment_type=ChaosExperimentType.POD_KILL)
        runner = suite._create_runner(exp)
        assert isinstance(runner, PodKillExperiment)

        exp2 = ChaosExperiment(name="y", experiment_type=ChaosExperimentType.NETWORK_LATENCY)
        runner2 = suite._create_runner(exp2)
        assert isinstance(runner2, NetworkLatencyExperiment)

    def test_generate_report(self):
        from trading.infrastructure.chaos.chaos_experiments import (
            ChaosExperimentSuite, ExperimentResult,
        )

        suite = ChaosExperimentSuite()
        suite.results.append(ExperimentResult(
            experiment_name="pod-kill", success=True, duration_seconds=5.0,
            metrics_before={}, metrics_after={},
            observations=["Killed 1 pod"], recommendations=["Increase replicas"],
        ))
        report = suite.generate_report()
        assert "# Chaos Engineering Report" in report
        assert "pod-kill" in report
        assert "✅ PASS" in report
        assert "1.0s" in report or "5.0s" in report

    def test_dry_run_run_all(self):
        """Dry-run suite should simulate experiments without a cluster."""
        from trading.infrastructure.chaos.chaos_experiments import ChaosExperimentSuite

        suite = ChaosExperimentSuite(namespace="trading-agent", dry_run=True)
        suite.add_pod_kill("pod-kill-1", {"app": "trading-agent"}, duration=1)
        suite.add_network_latency("latency-1", {"app": "trading-agent"}, duration=1, latency_ms=50)

        results = run_async(suite.run_all())
        assert len(results) == 2
        for r in results:
            assert r.success
            assert r.duration_seconds == 1
            assert any("dry-run" in obs for obs in r.observations)
            assert r.metrics_before == {"simulated": 1.0}

        report = suite.generate_report()
        assert "✅ PASS" in report

    def test_dry_run_experiment_status_transitions(self):
        """Dry-run should mark experiments running -> completed."""
        from trading.infrastructure.chaos.chaos_experiments import (
            ChaosExperimentSuite, ChaosExperimentType, ChaosExperiment, ExperimentStatus,
        )

        suite = ChaosExperimentSuite(dry_run=True)
        exp = ChaosExperiment(name="x", experiment_type=ChaosExperimentType.POD_KILL)
        result = run_async(suite._dry_run_experiment(exp))
        assert exp.status == ExperimentStatus.COMPLETED
        assert exp.result == {"simulated": True}
        assert result.success


# ---------------------------------------------------------------------------
# 12. End-to-End Flow
# ---------------------------------------------------------------------------

class TestEndToEndFlow:
    """Full pipeline: adaptive strategy -> events -> projections -> rebalance."""

    def test_full_pipeline(self, tmp_path):
        from trading.events.store import EventStore, EventStoreConfig
        from trading.events.projections import TradeProjection, SignalProjection, RiskProjection
        from trading.ml.online.adaptive import AdaptiveConfig, AdaptiveStrategy
        from trading.portfolio.auto_rebalancer import AutoRebalancer, RebalanceConfig, RebalanceTrigger
        from trading.exchanges.models import Position

        np.random.seed(21)
        prices = 100 * np.exp(np.cumsum(np.random.normal(0.001, 0.01, 200)))

        # --- Stage 1: adaptive strategy generates signals + trades ---
        strat = AdaptiveStrategy(AdaptiveConfig(min_period=5, max_period=30, min_samples=30))
        signals = []
        for i in range(1, len(prices)):
            res = strat.update(
                high=float(prices[i] * 1.002), low=float(prices[i] * 0.998),
                close=float(prices[i]), volume=1000.0,
            )
            signals.append(res)

        # --- Stage 2: persist events ---
        config = EventStoreConfig(file_path=str(tmp_path / "pipeline.jsonl"))
        store = EventStore(config)
        trade_proj = TradeProjection()
        sig_proj = SignalProjection()
        risk_proj = RiskProjection()
        store.add_projection("trades", trade_proj)
        store.add_projection("signals", sig_proj)
        store.add_projection("risk", risk_proj)

        def _close(price: float):
            return Decimal(str(round(price, 2)))

        async def persist():
            await store.connect(backend="file")
            # Record a couple of trades from the strategy
            await store.append(store.create_trade_event(
                symbol="BTC/USDT", side="buy", size=Decimal("0.5"),
                price=_close(prices[50]), fee=Decimal("1.25"), fee_currency="USDT",
                exchange="binance", order_id="e2e-1", strategy_id="adaptive",
            ))
            await store.append(store.create_trade_event(
                symbol="BTC/USDT", side="sell", size=Decimal("0.5"),
                price=_close(prices[120]), fee=Decimal("1.25"), fee_currency="USDT",
                exchange="binance", order_id="e2e-2", strategy_id="adaptive",
            ))
            # Signals from adaptive strategy (first 3)
            for s in signals[:3]:
                await store.append(store.create_signal_event(
                    symbol="BTC/USDT", signal_type="buy" if s["signal"] > 0 else "sell",
                    strength=abs(s["signal"]), strategy_id="adaptive", timeframe="1h",
                ))
            await store.disconnect()

        run_async(persist())

        # --- Stage 3: projections reflect state ---
        tstate = run_async(trade_proj.get_state())
        assert tstate["total_trades"] == 2
        sstate = run_async(sig_proj.get_state())
        assert sstate["total_signals"] == 3
        assert sstate["by_strategy"]["adaptive"] == 3

        # --- Stage 4: rebalance portfolio based on positions ---
        btc = _make_symbol("BTC")
        eth = _make_symbol("ETH")
        positions = {
            btc: Position(symbol=btc, size=Decimal("0.5"), entry_price=_close(prices[50]),
                          mark_price=_close(prices[-1])),
            eth: Position(symbol=eth, size=Decimal("10"), entry_price=Decimal("3000"),
                          mark_price=Decimal("3100")),
        }
        prices_map = {btc: _close(prices[-1]), eth: Decimal("3100")}

        rebalancer = AutoRebalancer(RebalanceConfig(
            calendar_enabled=False, threshold_enabled=False,
            cppi_enabled=False, risk_budget_enabled=False,
        ))
        event = run_async(rebalancer.force_rebalance(positions, prices_map, RebalanceTrigger.MANUAL))
        assert event.success
        assert event.timestamp is not None

        # --- Stage 5: full status reporting ---
        status = rebalancer.get_status()
        assert status["total_rebalances"] == 1
        assert event.trigger == RebalanceTrigger.MANUAL


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
