"""
Adapter strategies for plugin system.

These wrap the trading_agent strategies to implement the plugin interface.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from trading.exchanges.models import Order
from trading.strategies.plugins.strategy_plugin import (
    BaseStrategy,
    StrategyContext,
    Signal,
    StrategyMetadata,
    StrategyType,
    RiskProfile,
)
from trading_agent.strategies import get_strategy


class TradingAgentStrategyAdapter(BaseStrategy):
    """
    Adapter that wraps a trading_agent Strategy to implement the plugin interface.
    """

    def __init__(
        self,
        name: str,
        version: str = "1.0.0",
        author: str = "Trading Agent",
        description: str = "Wrapped trading_agent strategy",
        strategy_type: StrategyType = StrategyType.TREND_FOLLOWING,
        risk_profile: RiskProfile = RiskProfile.MODERATE,
        asset_classes: list[str] | None = None,
        timeframes: list[str] | None = None,
        parameters: dict[str, Any] | None = None,
        strategy_params: dict | None = None,
    ):
        # Create the wrapped strategy
        self._wrapped = get_strategy(name)
        if self._wrapped is None:
            raise ValueError(f"Strategy '{name}' not found in trading_agent registry")

        if strategy_params:
            self._wrapped = self._wrapped.__class__(strategy_params)

        # Metadata
        self._metadata = StrategyMetadata(
            name=name,
            version=version,
            author=author,
            description=description,
            strategy_type=strategy_type,
            risk_profile=risk_profile,
            asset_classes=asset_classes or ["crypto"],
            timeframes=timeframes or ["1h", "4h", "1d"],
            parameters=parameters or {},
        )

        # State
        self._state = {}
        self._config = strategy_params or {}

    @classmethod
    def get_metadata(cls) -> StrategyMetadata:  # noqa: F811 — class-level fallback; instance method below shadows it
        # This is a classmethod, but we need instance metadata
        # Subclasses should override this
        return StrategyMetadata(
            name="adapter",
            version="1.0.0",
            author="Trading Agent",
            description="Base adapter class",
            strategy_type=StrategyType.CUSTOM,
            risk_profile=RiskProfile.UNKNOWN,
            asset_classes=["crypto"],
            timeframes=["1h"],
        )

    def get_metadata(self) -> StrategyMetadata:  # noqa: F811 — shadows class-level fallback above
        return self._metadata

    def on_start(self, context: StrategyContext) -> None:
        """Initialize strategy state"""
        self._state = {
            'symbol': str(context.symbol),
            'timeframe': context.bar.timeframe,
            'initialized': True,
        }

    def on_bar(self, context: StrategyContext) -> list[Signal]:
        """Process a bar and generate signals using the wrapped strategy"""

        # Build a small DataFrame from context
        # We need historical data - for now, we'll just return empty list
        # In a real implementation, we'd need to maintain a rolling window
        return []

    def on_fill(self, order: Order, fill_price: Decimal, fill_size: Decimal) -> None:
        pass

    def on_order_update(self, order: Order) -> None:
        pass

    def on_stop(self) -> None:
        """Cleanup on strategy stop"""
        self._state = {}

    def get_state(self) -> dict:
        return self._state

    def set_state(self, state: dict) -> None:
        self._state = state

    def get_parameters(self) -> dict:
        return self._config

    def set_parameters(self, params: dict) -> None:
        self._config = params
        # Recreate wrapped strategy with new params
        self._wrapped = get_strategy(self._metadata.name)
        if self._wrapped and params:
            self._wrapped = self._wrapped.__class__(params)


# Pre-configured adapters for known strategies


class MaCrossoverPluginStrategy(TradingAgentStrategyAdapter):
    """MA Crossover strategy as a plugin"""

    @classmethod
    def get_metadata(cls) -> StrategyMetadata:
        return StrategyMetadata(
            name="ma_crossover",
            version="1.0.0",
            author="Trading Agent",
            description="MA Crossover - buy when fast MA crosses above slow MA",
            strategy_type=StrategyType.TREND_FOLLOWING,
            risk_profile=RiskProfile.MODERATE,
            asset_classes=["crypto", "stock", "forex"],
            timeframes=["1h", "4h", "1d"],
            parameters={
                "fast_period": {"type": "int", "default": 20, "min": 5, "max": 100, "description": "Fast MA period"},
                "slow_period": {"type": "int", "default": 50, "min": 10, "max": 200, "description": "Slow MA period"},
            },
        )

    def __init__(self, params: dict | None = None):
        super().__init__(
            name="ma_crossover",
            version="1.0.0",
            author="Trading Agent",
            description="MA Crossover - buy when fast MA crosses above slow MA",
            strategy_type=StrategyType.TREND_FOLLOWING,
            risk_profile=RiskProfile.MODERATE,
            asset_classes=["crypto", "stock", "forex"],
            timeframes=["1h", "4h", "1d"],
            parameters={
                "fast_period": {"type": "int", "default": 20, "min": 5, "max": 100},
                "slow_period": {"type": "int", "default": 50, "min": 10, "max": 200},
            },
            strategy_params=params,
        )

    def on_bar(self, context: StrategyContext) -> list[Signal]:

        # This is a simplified implementation - in production you'd maintain
        # a rolling window of bars to compute indicators
        return []


class RsiPluginStrategy(TradingAgentStrategyAdapter):
    """RSI Mean Reversion strategy as a plugin"""

    @classmethod
    def get_metadata(cls) -> StrategyMetadata:
        return StrategyMetadata(
            name="rsi",
            version="1.0.0",
            author="Trading Agent",
            description="RSI Mean Reversion - buy oversold, sell overbought",
            strategy_type=StrategyType.MEAN_REVERSION,
            risk_profile=RiskProfile.MODERATE,
            asset_classes=["crypto", "stock", "forex"],
            timeframes=["1h", "4h", "1d"],
            parameters={
                "period": {"type": "int", "default": 14, "min": 5, "max": 50},
                "oversold": {"type": "float", "default": 30, "min": 10, "max": 45},
                "overbought": {"type": "float", "default": 70, "min": 55, "max": 90},
            },
        )

    def __init__(self, params: dict | None = None):
        super().__init__(
            name="rsi",
            version="1.0.0",
            author="Trading Agent",
            description="RSI Mean Reversion - buy oversold, sell overbought",
            strategy_type=StrategyType.MEAN_REVERSION,
            risk_profile=RiskProfile.MODERATE,
            asset_classes=["crypto", "stock", "forex"],
            timeframes=["1h", "4h", "1d"],
            parameters={
                "period": {"type": "int", "default": 14, "min": 5, "max": 50},
                "oversold": {"type": "float", "default": 30, "min": 10, "max": 45},
                "overbought": {"type": "float", "default": 70, "min": 55, "max": 90},
            },
            strategy_params=params,
        )

    def on_bar(self, context: StrategyContext) -> list[Signal]:
        return []


class BBandsPluginStrategy(TradingAgentStrategyAdapter):
    """Bollinger Bands Mean Reversion strategy as a plugin"""

    @classmethod
    def get_metadata(cls) -> StrategyMetadata:
        return StrategyMetadata(
            name="bbands",
            version="1.0.0",
            author="Trading Agent",
            description="Bollinger Bands Mean Reversion - buy at lower band, sell at upper band",
            strategy_type=StrategyType.MEAN_REVERSION,
            risk_profile=RiskProfile.MODERATE,
            asset_classes=["crypto", "stock", "forex"],
            timeframes=["1h", "4h", "1d"],
            parameters={
                "period": {"type": "int", "default": 20, "min": 10, "max": 50},
                "std_dev": {"type": "float", "default": 2.0, "min": 1.0, "max": 3.0},
            },
        )

    def __init__(self, params: dict | None = None):
        super().__init__(
            name="bbands",
            version="1.0.0",
            author="Trading Agent",
            description="Bollinger Bands Mean Reversion - buy at lower band, sell at upper band",
            strategy_type=StrategyType.MEAN_REVERSION,
            risk_profile=RiskProfile.MODERATE,
            asset_classes=["crypto", "stock", "forex"],
            timeframes=["1h", "4h", "1d"],
            parameters={
                "period": {"type": "int", "default": 20, "min": 10, "max": 50},
                "std_dev": {"type": "float", "default": 2.0, "min": 1.0, "max": 3.0},
            },
            strategy_params=params,
        )

    def on_bar(self, context: StrategyContext) -> list[Signal]:
        return []


# Auto-register on import
def _auto_register():
    from trading.strategies.plugins import get_registry
    registry = get_registry()
    for strategy_class in [MaCrossoverPluginStrategy, RsiPluginStrategy, BBandsPluginStrategy]:
        try:
            registry.register(strategy_class)
        except Exception:
            pass  # Already registered


_auto_register()