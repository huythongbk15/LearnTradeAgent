#!/usr/bin/env python3
"""
Phase 6 Integration Test

Tests the core Phase 6 components:
- Unified Data Model
- CCXT Multi-Exchange Adapter
- Risk Budgeting
- Correlation Monitor
- Drawdown Controller
- Strategy Plugin Architecture
- Regime Detection
"""

import asyncio
import sys
from decimal import Decimal
from datetime import datetime


def smoke_unified_data_model():
    """Test unified data model"""
    print("Testing Unified Data Model...")
    from trading_agent.exchanges.models import (
        AssetClass,
        MarketType,
        Bar,
        OrderBook,
        OrderBookLevel,
        crypto_symbol,
        stock_symbol,
        forex_symbol,
    )

    # Test crypto symbol
    btc = crypto_symbol("BTC", "USDT", "binance", MarketType.SPOT)
    assert btc.base == "BTC"
    assert btc.quote == "USDT"
    assert btc.asset_class == AssetClass.CRYPTO
    assert btc.exchange == "binance"
    print(f"  Crypto: {btc} -> ccxt: {btc.ccxt_symbol}")

    # Test futures
    btc_fut = crypto_symbol("BTC", "USDT", "binance", MarketType.FUTURES)
    print(f"  Futures: {btc_fut} -> ccxt: {btc_fut.ccxt_symbol}")

    # Test stock
    aapl = stock_symbol("AAPL", "alpaca")
    assert aapl.asset_class == AssetClass.STOCK
    assert aapl.alpaca_symbol == "AAPL"
    print(f"  Stock: {aapl} -> alpaca: {aapl.alpaca_symbol}")

    # Test forex
    eurusd = forex_symbol("EUR", "USD", "oanda")
    assert eurusd.asset_class == AssetClass.FOREX
    assert eurusd.oanda_instrument == "EUR_USD"
    print(f"  Forex: {eurusd} -> oanda: {eurusd.oanda_instrument}")

    # Test Bar
    bar = Bar(
        symbol=btc,
        timestamp=datetime.now(),
        open=Decimal("50000"),
        high=Decimal("51000"),
        low=Decimal("49000"),
        close=Decimal("50500"),
        volume=Decimal("100"),
        timeframe="1h",
    )
    print(
        f"  Bar: {bar.symbol} O={bar.open} H={bar.high} L={bar.low} C={bar.close} V={bar.volume}"
    )
    print(f"  Typical Price: {bar.typical_price}, Range%: {bar.range_pct}")

    # Test OrderBook
    ob = OrderBook(
        symbol=btc,
        timestamp=datetime.now(),
        bids=[
            OrderBookLevel(Decimal("50000"), Decimal("1.5")),
            OrderBookLevel(Decimal("49999"), Decimal("2.0")),
        ],
        asks=[
            OrderBookLevel(Decimal("50001"), Decimal("1.0")),
            OrderBookLevel(Decimal("50002"), Decimal("1.5")),
        ],
    )
    print(
        f"  OrderBook: bid={ob.best_bid} ask={ob.best_ask} spread={ob.spread} mid={ob.mid_price}"
    )

    print("  ✓ Unified Data Model tests passed\n")
    return True


def smoke_risk_budgeting():
    """Test risk budgeting and correlation monitoring"""
    print("Testing Risk Budgeting & Correlation Monitor...")
    import numpy as np
    import pandas as pd
    from trading_agent.portfolio.risk_budgeting import (
        RiskBudgeter,
        RiskBudgetMethod,
        CorrelationMethod,
        CorrelationMonitor,
        DrawdownController,
    )

    # Create synthetic returns data
    np.random.seed(42)
    n_assets = 5
    n_periods = 500

    # Generate correlated returns
    corr_matrix = np.eye(n_assets) * 0.3 + np.ones((n_assets, n_assets)) * 0.1
    np.fill_diagonal(corr_matrix, 1.0)

    # Cholesky decomposition
    L = np.linalg.cholesky(corr_matrix)
    returns = np.random.randn(n_periods, n_assets) @ L.T * 0.01

    # Create DataFrame
    symbols = [f"ASSET{i}" for i in range(n_assets)]
    returns_df = pd.DataFrame(returns, columns=symbols)

    # Test ERC
    budgeter = RiskBudgeter(method=RiskBudgetMethod.EQUAL_RISK_CONTRIBUTION)
    result = budgeter.optimize(returns_df)

    print(f"  ERC Weights: {[(k, float(v)) for k, v in result.weights.items()]}")
    print(f"  Portfolio Vol: {float(result.portfolio_vol):.4f}")
    print(f"  Diversification Ratio: {float(result.diversification_ratio):.4f}")
    print(
        f"  Risk Contributions: {[(k, float(v)) for k, v in result.risk_contributions.items()]}"
    )
    assert result.success

    # Test Max Diversification
    budgeter_div = RiskBudgeter(method=RiskBudgetMethod.MAX_DIVERSIFICATION)
    result_div = budgeter_div.optimize(returns_df)
    print(f"  MaxDiv Weights: {[(k, float(v)) for k, v in result_div.weights.items()]}")
    assert result_div.success

    # Test Min Variance
    budgeter_mv = RiskBudgeter(method=RiskBudgetMethod.MIN_VARIANCE)
    result_mv = budgeter_mv.optimize(returns_df)
    print(f"  MinVar Weights: {[(k, float(v)) for k, v in result_mv.weights.items()]}")
    assert result_mv.success

    # Test Inverse Vol
    budgeter_iv = RiskBudgeter(method=RiskBudgetMethod.INVERSE_VOL)
    result_iv = budgeter_iv.optimize(returns_df)
    print(f"  InvVol Weights: {[(k, float(v)) for k, v in result_iv.weights.items()]}")
    assert result_iv.success

    # Test Correlation Monitor
    monitor = CorrelationMonitor(window=30, method=CorrelationMethod.PEARSON)
    corr_matrix_obj = monitor.update(returns_df)
    print(f"  Correlation Matrix shape: {corr_matrix_obj.matrix.shape}")
    print(f"  Regime: {corr_matrix_obj.regime}")

    # Test clustering
    clusters = corr_matrix_obj.get_cluster(n_clusters=2)
    print(f"  Clusters: {clusters}")

    # Test Drawdown Controller
    dd_ctrl = DrawdownController(
        max_drawdown=0.15,
        warning_threshold=0.05,
        reduce_threshold=0.10,
        stop_threshold=0.15,
    )

    # Simulate equity curve
    equity = Decimal("100000")
    for i in range(10):
        equity = equity * Decimal("1.01")
        dd_ctrl.update_equity(equity)

    print(
        f"  After gains: DD={float(dd_ctrl.get_drawdown_pct()):.2%}, mult={float(dd_ctrl.get_position_multiplier()):.2f}"
    )

    # Simulate drawdown
    for i in range(5):
        equity = equity * Decimal("0.95")
        dd_ctrl.update_equity(equity)

    print(
        f"  After losses: DD={float(dd_ctrl.get_drawdown_pct()):.2%}, mult={float(dd_ctrl.get_position_multiplier()):.2f}"
    )
    print(f"  Trading allowed: {dd_ctrl.is_trading_allowed()}")
    print(f"  Status: {dd_ctrl.get_status()}")

    print("  ✓ Risk Budgeting tests passed\n")
    return True


def smoke_strategy_plugins():
    """Test strategy plugin architecture"""
    print("Testing Strategy Plugin Architecture...")
    from trading_agent.strategies.plugins.strategy_plugin import (
        StrategyContext,
        ExampleMAStrategy,
        ExampleRSIStrategy,
        get_registry,
    )
    from trading_agent.exchanges.models import Symbol, AssetClass, MarketType, Bar

    registry = get_registry()

    # Register example strategies
    registry.register(ExampleMAStrategy)
    registry.register(ExampleRSIStrategy)

    strategies = registry.list_strategies()
    print(f"  Registered strategies: {len(strategies)}")
    for s in strategies:
        print(f"    - {s.name}@{s.version}: {s.description}")

    # Create MA strategy instance
    ma_strategy = registry.create_instance(
        "MA_Crossover", config={"fast_period": 5, "slow_period": 20}
    )
    assert ma_strategy is not None

    # Create context
    symbol = Symbol("BTC", "USDT", AssetClass.CRYPTO, MarketType.SPOT, "binance")
    bar = Bar(
        symbol=symbol,
        timestamp=datetime.now(),
        open=Decimal("50000"),
        high=Decimal("51000"),
        low=Decimal("49000"),
        close=Decimal("50500"),
        volume=Decimal("100"),
        timeframe="1h",
    )

    context = StrategyContext(
        symbol=symbol,
        bar=bar,
        position=None,
        portfolio_value=Decimal("100000"),
        available_balance=Decimal("100000"),
        current_time=datetime.now(),
    )

    # Start strategy
    ma_strategy.on_start(context)
    print(f"  MA Strategy started, state: {ma_strategy.state}")

    # Simulate bars
    for i in range(30):
        price = Decimal("50000") + Decimal(str(i * 10))
        bar = Bar(
            symbol=symbol,
            timestamp=datetime.now(),
            open=price - 100,
            high=price + 100,
            low=price - 200,
            close=price,
            volume=Decimal("100"),
            timeframe="1h",
        )
        context.bar = bar
        signals = ma_strategy.on_bar(context)
        if signals:
            for sig in signals:
                print(f"  Signal: {sig.side} {sig.symbol} strength={sig.strength}")

    # Test RSI strategy
    rsi_strategy = registry.create_instance("RSI_MeanReversion", config={"period": 14})
    rsi_strategy.on_start(context)

    for i in range(20):
        price = Decimal("50000") + Decimal(str(50 * (-1) ** i))  # Oscillating
        bar = Bar(
            symbol=symbol,
            timestamp=datetime.now(),
            open=price - 50,
            high=price + 50,
            low=price - 100,
            close=price,
            volume=Decimal("100"),
            timeframe="1h",
        )
        context.bar = bar
        signals = rsi_strategy.on_bar(context)
        if signals:
            for sig in signals:
                print(f"  RSI Signal: {sig.side} {sig.symbol} strength={sig.strength}")

    rsi_strategy.on_stop()
    ma_strategy.on_stop()

    # Test parameter validation
    valid, msg = ma_strategy.validate_parameters({"fast_period": 5, "slow_period": 30})
    print(f"  Param validation (valid): {valid} - {msg}")

    valid, msg = ma_strategy.validate_parameters({"fast_period": 5})  # missing slow
    print(f"  Param validation (invalid): {valid} - {msg}")

    print("  ✓ Strategy Plugin tests passed\n")
    return True


def smoke_regime_detection():
    """Test regime detection"""
    print("Testing Regime Detection...")
    import numpy as np
    import pandas as pd
    from trading_agent.ml.regime_detection import (
        MarketRegime,
        RegimeMethod,
        HMMStrategy,
        RuleBasedStrategy,
        HybridRegimeDetector,
        AdaptivePositionSizer,
    )

    # Create synthetic price data with regime changes
    np.random.seed(42)
    n = 500

    # Regime 1: Bull trend (0-150)
    bull_returns = np.random.normal(0.001, 0.01, 150)
    # Regime 2: Sideways (150-300)
    sideways_returns = np.random.normal(0.0001, 0.005, 150)
    # Regime 3: Bear trend (300-400)
    bear_returns = np.random.normal(-0.001, 0.015, 100)
    # Regime 4: High vol (400-500)
    highvol_returns = np.random.normal(0.0001, 0.03, 100)

    all_returns = np.concatenate(
        [bull_returns, sideways_returns, bear_returns, highvol_returns]
    )
    prices = 100 * np.exp(np.cumsum(all_returns))
    prices_series = pd.Series(prices)

    # Test HMM
    hmm = HMMStrategy(n_regimes=4, lookback=252)
    hmm.fit(prices_series)
    state = hmm.predict(prices_series)
    print(f"  HMM: regime={state.regime.value}, confidence={state.confidence:.3f}")
    print(f"    Probabilities: {state.probability}")
    print(f"    Expected duration: {state.expected_duration}")

    # Test Rule-based
    rule = RuleBasedStrategy(fast_ma=50, slow_ma=200)
    state_rule = rule.detect(prices_series)
    print(
        f"  Rule-based: regime={state_rule.regime.value}, confidence={state_rule.confidence:.3f}"
    )

    # Test Hybrid
    hybrid = HybridRegimeDetector(
        methods=[RegimeMethod.HMM, RegimeMethod.RULE_BASED],
        weights={RegimeMethod.HMM: 0.7, RegimeMethod.RULE_BASED: 0.3},
    )
    hybrid.initialize(prices_series)
    state_hybrid = hybrid.detect(prices_series)
    print(
        f"  Hybrid: regime={state_hybrid.regime.value}, confidence={state_hybrid.confidence:.3f}"
    )

    # Test Adaptive Position Sizer
    sizer = AdaptivePositionSizer(target_vol=0.15, max_leverage=3.0)
    position = sizer.size_position(
        signal_strength=0.8,
        current_vol=0.20,
        regime=MarketRegime.BULL_TREND,
        win_rate=0.55,
        avg_win=0.02,
        avg_loss=0.015,
    )
    print(f"  Position size (bull): {float(position):.3f}")

    position = sizer.size_position(
        signal_strength=0.8,
        current_vol=0.20,
        regime=MarketRegime.HIGH_VOLATILITY,
        win_rate=0.55,
        avg_win=0.02,
        avg_loss=0.015,
    )
    print(f"  Position size (high vol): {float(position):.3f}")

    # Test portfolio sizing
    signals = {"BTC": 0.8, "ETH": 0.6, "SOL": 0.4}
    vols = {"BTC": 0.60, "ETH": 0.70, "SOL": 0.90}
    corr = np.array([[1, 0.8, 0.6], [0.8, 1, 0.7], [0.6, 0.7, 1]])
    portfolio_sizes = sizer.size_portfolio(signals, vols, MarketRegime.BULL_TREND, corr)
    print(f"  Portfolio sizes: {[(k, float(v)) for k, v in portfolio_sizes.items()]}")
    total = sum(float(v) for v in portfolio_sizes.values())
    print(f"  Total leverage: {total:.2f}x")

    print("  ✓ Regime Detection tests passed\n")
    return True


def smoke_ccxt_adapter_structure():
    """Test CCXT adapter structure (without actual connections)"""
    print("Testing CCXT Adapter Structure...")
    from trading_agent.exchanges.ccxt_adapter import (
        ExchangeConfig,
        RateLimitManager,
        get_default_exchange_configs,
    )
    from trading_agent.exchanges.models import MarketType

    # Test configs
    configs = get_default_exchange_configs()
    print(f"  Default exchange configs: {len(configs)}")
    for c in configs[:3]:
        print(f"    - {c.name} ({c.id}): markets={[m.value for m in c.markets]}")

    # Test RateLimitManager
    rl = RateLimitManager()
    state = rl.get_state("binance")
    print(f"  RateLimitManager: state={state.exchange_id}, remaining={state.remaining}")

    # Test ExchangeConfig
    config = ExchangeConfig(
        id="binance",
        name="Binance",
        api_key="test",
        secret="test",
        sandbox=True,
        markets=[MarketType.SPOT, MarketType.FUTURES, MarketType.PERPETUAL],
    )
    ccxt_config = config.to_ccxt_config()
    print(f"  CCXT config keys: {list(ccxt_config.keys())}")

    print("  ✓ CCXT Adapter Structure tests passed\n")
    return True


async def main():
    """Run all Phase 6 tests"""
    print("=" * 60)
    print("PHASE 6 INTEGRATION TESTS")
    print("=" * 60)
    print()

    tests = [
        smoke_unified_data_model,
        smoke_risk_budgeting,
        smoke_strategy_plugins,
        smoke_regime_detection,
        smoke_ccxt_adapter_structure,
    ]

    passed = 0
    failed = 0

    for test in tests:
        try:
            result = test()
            if result:
                passed += 1
            else:
                failed += 1
                print(f"  ✗ {test.__name__} FAILED\n")
        except Exception as e:
            failed += 1
            print(f"  ✗ {test.__name__} ERROR: {e}\n")
            import traceback

            traceback.print_exc()

    print("=" * 60)
    print(f"RESULTS: {passed} passed, {failed} failed")
    print("=" * 60)

    return failed == 0


if __name__ == "__main__":
    success = asyncio.run(main())
    sys.exit(0 if success else 1)
