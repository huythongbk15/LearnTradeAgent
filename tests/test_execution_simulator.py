"""
Tests for Execution Simulator.
"""

import pytest
import numpy as np
from datetime import UTC, datetime, timedelta

from trading_agent.execution.backtest_sim import (
    ExecutionSimulator,
    SimulatorConfig,
    SimulatedOrder,
    SimulatedFill,
    OrderBookSnapshot,
    OrderType,
    OrderSide,
    FillModel,
    ImpactModel,
    create_execution_simulator,
    SimulatorBacktestEngine,
    run_simulator_backtest,
)
from trading_agent.strategies.ma_crossover import MaCrossover
from trading_agent.data.storage import load_ohlcv


class TestExecutionSimulator:
    """Tests for ExecutionSimulator core functionality."""
    
    def test_market_order_immediate_fill(self):
        """Market orders should fill immediately at next step."""
        sim = create_execution_simulator(seed=42)
        
        book = OrderBookSnapshot(
            symbol="BTC/USDT",
            timestamp=datetime.now(UTC),
            bid_price=49900.0,
            bid_size=10.0,
            ask_price=50100.0,
            ask_size=10.0,
            mid_price=50000.0,
            spread=200.0,
            spread_bps=4.0,
            volume_24h=1000000.0,
            volatility=0.02,
        )
        sim.update_order_book(book)
        
        order = SimulatedOrder(
            order_id="test_1",
            symbol="BTC/USDT",
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            quantity=1.0,
            price=50000.0,
        )
        sim.submit_order(order)
        
        fills = sim.step(datetime.now(UTC))
        
        assert len(fills) == 1
        fill = fills[0]
        assert fill.order_id == "test_1"
        assert fill.side == OrderSide.BUY
        assert fill.quantity == 1.0
        assert fill.price > 50000.0  # Should have slippage
        assert fill.fee > 0
        assert not fill.is_maker
        assert fill.latency_ms > 0
    
    def test_limit_order_queue_fill(self):
        """Limit orders should fill based on queue position."""
        config = SimulatorConfig(
            fill_model=FillModel.QUEUE,
            queue_fill_probability=1.0,  # Guaranteed fill for test
            partial_fill_prob=0.0,
            base_slippage_bps=0.0,  # No slippage for clean test
            impact_model=ImpactModel.NONE,  # No impact for clean test
        )
        sim = ExecutionSimulator(config, seed=42)
        
        book = OrderBookSnapshot(
            symbol="BTC/USDT",
            timestamp=datetime.now(UTC),
            bid_price=49900.0,
            bid_size=10.0,
            ask_price=50100.0,
            ask_size=10.0,
            mid_price=50000.0,
            spread=200.0,
            spread_bps=4.0,
            volume_24h=1000000.0,
            volatility=0.02,
        )
        sim.update_order_book(book)
        
        # Buy limit above ask - should cross and fill as taker
        order = SimulatedOrder(
            order_id="test_limit_1",
            symbol="BTC/USDT",
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            quantity=1.0,
            price=50200.0,  # Above ask
        )
        sim.submit_order(order)
        
        fills = sim.step(datetime.now(UTC))
        
        assert len(fills) == 1
        fill = fills[0]
        assert fill.price == 50100.0  # Filled at ask price
        assert not fill.is_maker  # Crossed spread = taker
    
    def test_limit_order_maker_fill(self):
        """Limit orders providing liquidity should fill as maker."""
        config = SimulatorConfig(
            fill_model=FillModel.QUEUE,
            queue_fill_probability=1.0,
            partial_fill_prob=0.0,
        )
        sim = ExecutionSimulator(config, seed=42)
        
        book = OrderBookSnapshot(
            symbol="BTC/USDT",
            timestamp=datetime.now(UTC),
            bid_price=49900.0,
            bid_size=10.0,
            ask_price=50100.0,
            ask_size=10.0,
            mid_price=50000.0,
            spread=200.0,
            spread_bps=4.0,
            volume_24h=1000000.0,
            volatility=0.02,
        )
        sim.update_order_book(book)
        
        # Buy limit below ask - provides liquidity
        order = SimulatedOrder(
            order_id="test_limit_maker",
            symbol="BTC/USDT",
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            quantity=1.0,
            price=49800.0,  # Below bid
        )
        sim.submit_order(order)
        
        fills = sim.step(datetime.now(UTC))
        
        assert len(fills) == 1
        fill = fills[0]
        assert fill.is_maker
        assert fill.fee < 1.0 * 50000 * 0.0005  # Maker fee < taker fee
    
    def test_partial_fill(self):
        """Orders can be partially filled."""
        config = SimulatorConfig(
            partial_fill_prob=1.0,  # Always partial
        )
        sim = ExecutionSimulator(config, seed=42)
        
        book = OrderBookSnapshot(
            symbol="BTC/USDT",
            timestamp=datetime.now(UTC),
            bid_price=49900.0,
            bid_size=10.0,
            ask_price=50100.0,
            ask_size=10.0,
            mid_price=50000.0,
            spread=200.0,
            spread_bps=4.0,
            volume_24h=1000000.0,
            volatility=0.02,
        )
        sim.update_order_book(book)
        
        order = SimulatedOrder(
            order_id="test_partial",
            symbol="BTC/USDT",
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            quantity=10.0,
        )
        sim.submit_order(order)
        
        fills = sim.step(datetime.now(UTC))
        
        assert len(fills) == 1
        assert fills[0].quantity < 10.0  # Partial fill
        assert fills[0].quantity > 0.0
    
    def test_cancel_order(self):
        """Orders can be cancelled."""
        sim = create_execution_simulator(seed=42)
        
        order = SimulatedOrder(
            order_id="test_cancel",
            symbol="BTC/USDT",
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            quantity=1.0,
            price=49000.0,
        )
        sim.submit_order(order)
        
        assert "test_cancel" in sim.state.open_orders
        
        cancelled = sim.cancel_order("test_cancel")
        
        assert cancelled is True
        assert "test_cancel" not in sim.state.open_orders
    
    def test_latency_sampling(self):
        """Latency should be sampled from configured distribution."""
        config = SimulatorConfig(
            base_latency_ms=100.0,
            latency_jitter_ms=10.0,
            latency_distribution="lognormal",
        )
        sim = ExecutionSimulator(config, seed=42)
        
        book = OrderBookSnapshot(
            symbol="BTC/USDT",
            timestamp=datetime.now(UTC),
            bid_price=49900.0,
            bid_size=10.0,
            ask_price=50100.0,
            ask_size=10.0,
            mid_price=50000.0,
            spread=200.0,
            spread_bps=4.0,
            volume_24h=1000000.0,
            volatility=0.02,
        )
        sim.update_order_book(book)
        
        latencies = []
        for _ in range(100):
            order = SimulatedOrder(
                order_id=f"lat_{_}",
                symbol="BTC/USDT",
                side=OrderSide.BUY,
                order_type=OrderType.MARKET,
                quantity=0.01,
            )
            sim.submit_order(order)
            fills = sim.step(datetime.now(UTC))
            latencies.append(fills[0].latency_ms)
        
        # All latencies should be positive
        assert all(l > 0 for l in latencies)
        # Mean should be around base_latency
        assert 50 < np.mean(latencies) < 200
    
    def test_market_impact(self):
        """Market impact should increase with order size."""
        config = SimulatorConfig(
            impact_model=ImpactModel.SQUARE_ROOT,
            impact_coefficient=0.1,
            partial_fill_prob=0.0,
        )
        sim = ExecutionSimulator(config, seed=42)
        
        book = OrderBookSnapshot(
            symbol="BTC/USDT",
            timestamp=datetime.now(UTC),
            bid_price=49900.0,
            bid_size=100.0,
            ask_price=50100.0,
            ask_size=100.0,
            mid_price=50000.0,
            spread=200.0,
            spread_bps=4.0,
            volume_24h=1000000.0,
            volatility=0.02,
        )
        sim.update_order_book(book)
        
        # Small order
        order1 = SimulatedOrder(
            order_id="small", symbol="BTC/USDT", side=OrderSide.BUY,
            order_type=OrderType.MARKET, quantity=0.1,
        )
        sim.submit_order(order1)
        fill1 = sim.step(datetime.now(UTC))[0]
        
        # Reset
        sim.state = sim.state.__class__()
        sim.update_order_book(book)
        
        # Large order
        order2 = SimulatedOrder(
            order_id="large", symbol="BTC/USDT", side=OrderSide.BUY,
            order_type=OrderType.MARKET, quantity=10.0,
        )
        sim.submit_order(order2)
        fill2 = sim.step(datetime.now(UTC))[0]
        
        # Large order should have more slippage/impact
        slip1 = (fill1.price - book.mid_price) / book.mid_price
        slip2 = (fill2.price - book.mid_price) / book.mid_price
        assert slip2 > slip1


class TestSimulatorBacktestEngine:
    """Tests for SimulatorBacktestEngine integration."""
    
    def test_backtest_runs(self):
        """Backtest should complete without errors."""
        df = load_ohlcv("binance", "BTC_USDT", "1d").head(200)
        strategy = MaCrossover(params={"fast_period": 10, "slow_period": 30})
        
        result = run_simulator_backtest(
            strategy=strategy,
            df=df,
            symbol="BTC/USDT",
            timeframe="1d",
            initial_capital=100000,
            simulator_config={
                "fill_model": FillModel.IMMEDIATE,
                "impact_model": ImpactModel.NONE,
                "maker_fee_bps": 1.0,
                "taker_fee_bps": 5.0,
                "base_slippage_bps": 0.0,
                "partial_fill_prob": 0.0,
            },
        )
        
        assert result.total_trades >= 0
        assert result.total_fees >= 0
        assert result.avg_latency_ms >= 0
        assert len(result.equity_curve) == len(df)
    
    def test_simulator_vs_standard(self):
        """Simulator should produce different (more conservative) results."""
        df = load_ohlcv("binance", "BTC_USDT", "1d").head(500)
        strategy = MaCrossover(params={"fast_period": 50, "slow_period": 200})
        
        # Standard backtest
        from trading_agent.backtest.engine import BacktestEngine
        engine = BacktestEngine(
            strategy=strategy,
            initial_capital=100000,
            commission=0.0005,
            slippage=0.0002,
            long_only=True,
        )
        std_result = engine.run(df, symbol="BTC/USDT", timeframe="1d")
        
        # Simulator backtest with same cost parameters
        sim_result = run_simulator_backtest(
            strategy=strategy,
            df=df,
            symbol="BTC/USDT",
            timeframe="1d",
            initial_capital=100000,
            simulator_config={
                "fill_model": FillModel.IMMEDIATE,
                "impact_model": ImpactModel.NONE,
                "maker_fee_bps": 5.0,  # Match commission
                "taker_fee_bps": 5.0,
                "base_slippage_bps": 2.0,  # Match slippage
                "partial_fill_prob": 0.0,
            },
        )
        
        # Simulator should have similar or worse returns due to more realistic modeling
        assert sim_result.total_return_pct <= std_result.total_return_pct + 5  # Allow small variance
        assert sim_result.total_trades >= 0


class TestFillModels:
    """Test different fill models."""
    
    def test_immediate_fill_model(self):
        """Immediate fill model should fill market orders instantly."""
        config = SimulatorConfig(fill_model=FillModel.IMMEDIATE)
        sim = ExecutionSimulator(config, seed=42)
        
        book = OrderBookSnapshot(
            symbol="BTC/USDT",
            timestamp=datetime.now(UTC),
            bid_price=49900.0, bid_size=10.0,
            ask_price=50100.0, ask_size=10.0,
            mid_price=50000.0, spread=200.0, spread_bps=4.0,
            volume_24h=1000000.0, volatility=0.02,
        )
        sim.update_order_book(book)
        
        order = SimulatedOrder(
            order_id="imm_1", symbol="BTC/USDT",
            side=OrderSide.BUY, order_type=OrderType.MARKET,
            quantity=1.0,
        )
        sim.submit_order(order)
        fills = sim.step(datetime.now(UTC))
        
        assert len(fills) == 1
        assert fills[0].quantity == 1.0
    
    def test_no_impact_model(self):
        """NONE impact model should have zero market impact."""
        config = SimulatorConfig(
            impact_model=ImpactModel.NONE,
            partial_fill_prob=0.0,
            base_slippage_bps=0.0,  # No slippage for clean test
        )
        sim = ExecutionSimulator(config, seed=42)
        
        book = OrderBookSnapshot(
            symbol="BTC/USDT",
            timestamp=datetime.now(UTC),
            bid_price=49900.0, bid_size=10.0,
            ask_price=50100.0, ask_size=10.0,
            mid_price=50000.0, spread=200.0, spread_bps=4.0,
            volume_24h=1000000.0, volatility=0.02,
        )
        sim.update_order_book(book)
        
        order = SimulatedOrder(
            order_id="no_impact", symbol="BTC/USDT",
            side=OrderSide.BUY, order_type=OrderType.MARKET,
            quantity=100.0,  # Very large
        )
        sim.submit_order(order)
        fills = sim.step(datetime.now(UTC))
        
        fill = fills[0]
        # Price should only have spread + slippage, no impact
        # With zero slippage config, should be at ask
        assert fill.price <= book.ask_price * 1.001  # Allow tiny rounding


if __name__ == "__main__":
    pytest.main([__file__, "-v"])