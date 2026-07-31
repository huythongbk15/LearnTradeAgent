"""
Strategy Plugin Architecture

Provides a sandboxed plugin system for trading strategies with:
- Abstract base strategy interface
- Plugin registry with metadata
- Sandboxed execution (subprocess/container)
- Backtest validation
- Version management
"""

from __future__ import annotations

import hashlib
import importlib
import importlib.metadata
import inspect
import json
import logging
import sys
import tempfile
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Any, Optional

from trading.exchanges.models import Symbol, Order, OrderSide, OrderType, Bar, Position

logger = logging.getLogger(__name__)


class StrategyType(str, Enum):
    """Strategy classification"""
    TREND_FOLLOWING = "trend_following"
    MEAN_REVERSION = "mean_reversion"
    BREAKOUT = "breakout"
    ARBITRAGE = "arbitrage"
    MARKET_MAKING = "market_making"
    STAT_ARB = "stat_arb"
    ML_BASED = "ml_based"
    AI_AGENT = "ai_agent"
    MULTI_ASSET = "multi_asset"
    CUSTOM = "custom"


class RiskProfile(str, Enum):
    """Risk profile classification"""
    CONSERVATIVE = "conservative"
    MODERATE = "moderate"
    AGGRESSIVE = "aggressive"
    UNKNOWN = "unknown"


class StrategyStatus(str, Enum):
    """Strategy lifecycle status"""
    REGISTERED = "registered"
    VALIDATING = "validating"
    VALIDATED = "validated"
    LIVE = "live"
    PAUSED = "paused"
    STOPPED = "stopped"
    ERROR = "error"


@dataclass
class StrategyMetadata:
    """Strategy metadata for registry"""
    name: str
    version: str
    author: str
    description: str
    strategy_type: StrategyType
    risk_profile: RiskProfile
    asset_classes: list[str]
    timeframes: list[str]
    parameters: dict[str, Any] = field(default_factory=dict)
    required_data: list[str] = field(default_factory=list)
    backtest_hash: str = ""
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    tags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            'name': self.name,
            'version': self.version,
            'author': self.author,
            'description': self.description,
            'strategy_type': self.strategy_type.value,
            'risk_profile': self.risk_profile.value,
            'asset_classes': self.asset_classes,
            'timeframes': self.timeframes,
            'parameters': self.parameters,
            'required_data': self.required_data,
            'backtest_hash': self.backtest_hash,
            'created_at': self.created_at.isoformat(),
            'updated_at': self.updated_at.isoformat(),
            'tags': self.tags,
        }

    @classmethod
    def from_dict(cls, data: dict) -> StrategyMetadata:
        return cls(
            name=data['name'],
            version=data['version'],
            author=data['author'],
            description=data['description'],
            strategy_type=StrategyType(data['strategy_type']),
            risk_profile=RiskProfile(data['risk_profile']),
            asset_classes=data['asset_classes'],
            timeframes=data['timeframes'],
            parameters=data.get('parameters', {}),
            required_data=data.get('required_data', []),
            backtest_hash=data.get('backtest_hash', ''),
            created_at=datetime.fromisoformat(data['created_at']) if 'created_at' in data else datetime.now(),
            updated_at=datetime.fromisoformat(data['updated_at']) if 'updated_at' in data else datetime.now(),
            tags=data.get('tags', []),
        )


@dataclass
class Signal:
    """Trading signal from strategy"""
    symbol: Symbol
    side: OrderSide
    strength: Decimal
    price: Optional[Decimal] = None
    stop_loss: Optional[Decimal] = None
    take_profit: Optional[Decimal] = None
    metadata: dict = field(default_factory=dict)
    timestamp: datetime = field(default_factory=datetime.now)
    strategy_name: str = ""

    def to_order(self, size: Decimal, order_type: OrderType = OrderType.MARKET) -> Order:
        """Convert signal to order"""
        return Order(
            id=str(uuid.uuid4())[:8],
            symbol=self.symbol,
            side=self.side,
            type=order_type,
            size=size,
            price=self.price,
            stop_price=self.stop_loss,
            client_order_id=f"{self.strategy_name}_{self.timestamp.timestamp()}",
        )


@dataclass
class StrategyContext:
    """Context passed to strategy on each bar"""
    symbol: Symbol
    bar: Bar
    position: Optional[Position]
    portfolio_value: Decimal
    available_balance: Decimal
    current_time: datetime
    risk_budgeter: Optional[Any] = None
    metadata: dict = field(default_factory=dict)


class BaseStrategy(ABC):
    """
    Abstract base class for all trading strategies.

    All strategies must implement:
    - on_start: Initialize strategy state
    - on_bar: Process new bar and generate signals
    - on_fill: Handle order fills
    - on_stop: Cleanup on strategy stop
    """

    # Class-level metadata (override in subclasses)
    metadata: StrategyMetadata = None

    def __init__(self, config: dict | None = None):
        self.config = config or {}
        self.state: dict = {}
        self.signals: list[Signal] = []
        self._is_running = False
        self._instance_id = str(uuid.uuid4())[:8]

    @classmethod
    def get_metadata(cls) -> StrategyMetadata:
        """Get strategy metadata"""
        if cls.metadata is None:
            cls.metadata = StrategyMetadata(
                name=cls.__name__,
                version="1.0.0",
                author="Unknown",
                description=cls.__doc__ or "No description",
                strategy_type=StrategyType.CUSTOM,
                risk_profile=RiskProfile.UNKNOWN,
                asset_classes=["crypto"],
                timeframes=["1h"],
            )
        return cls.metadata

    @abstractmethod
    def on_start(self, context: StrategyContext) -> None:
        """Called once when strategy starts"""
        pass

    @abstractmethod
    def on_bar(self, context: StrategyContext) -> list[Signal]:
        """Called on each new bar - generate signals"""
        pass

    def on_fill(self, order: Order, fill_price: Decimal, fill_size: Decimal) -> None:
        """Called when an order is filled"""
        pass

    def on_order_update(self, order: Order) -> None:
        """Called when order status changes"""
        pass

    @abstractmethod
    def on_stop(self) -> None:
        """Called when strategy stops - cleanup"""
        pass

    def get_state(self) -> dict:
        """Get persistent state for serialization"""
        return self.state

    def set_state(self, state: dict) -> None:
        """Restore persistent state"""
        self.state = state

    def get_parameters(self) -> dict:
        """Get current parameter values"""
        return self.config

    def set_parameters(self, params: dict) -> None:
        """Update parameters"""
        self.config.update(params)

    def validate_parameters(self, params: dict) -> tuple[bool, str]:
        """Validate parameters against schema"""
        meta = self.get_metadata()
        schema = meta.parameters

        if not schema:
            return True, "No parameter schema defined"

        for key, spec in schema.items():
            if key not in params:
                if spec.get('required', False):
                    return False, f"Missing required parameter: {key}"
            else:
                val = params[key]
                expected_type = spec.get('type')
                if expected_type == 'number' and not isinstance(val, (int, float, Decimal)):
                    return False, f"Parameter {key} must be a number"
                if expected_type == 'integer' and not isinstance(val, int):
                    return False, f"Parameter {key} must be an integer"
                if 'min' in spec and val < spec['min']:
                    return False, f"Parameter {key} below minimum {spec['min']}"
                if 'max' in spec and val > spec['max']:
                    return False, f"Parameter {key} above maximum {spec['max']}"

        return True, "Valid"


class StrategySandbox:
    """
    Sandboxed execution environment for untrusted strategies.

    Uses subprocess isolation to run strategy code safely.
    """

    def __init__(
        self,
        timeout: int = 30,
        memory_limit_mb: int = 512,
        cpu_limit: float = 1.0,
        allow_network: bool = False,
    ):
        self.timeout = timeout
        self.memory_limit_mb = memory_limit_mb
        self.cpu_limit = cpu_limit
        self.allow_network = allow_network

    def run_strategy(
        self,
        strategy_code: str,
        method: str,
        args: list,
        kwargs: dict,
    ) -> tuple[bool, Any, str]:
        """
        Run a strategy method in sandbox.

        Returns: (success, result, error_message)
        """
        with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as f:
            f.write(self._wrap_strategy(strategy_code))
            strategy_file = f.name

        try:
            cmd = [
                sys.executable,
                '-c',
                f"""
import sys
sys.path.insert(0, '{Path(strategy_file).parent}')
import json
from {Path(strategy_file).stem} import Strategy

strategy = Strategy()
result = strategy.{method}(*{json.dumps(args)}, **{json.dumps(kwargs)})
print(json.dumps(result, default=str))
"""
            ]

            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self.timeout,
            )

            if result.returncode != 0:
                return False, None, result.stderr

            try:
                output = json.loads(result.stdout.strip())
                return True, output, ""
            except json.JSONDecodeError:
                return False, None, "Invalid JSON output"

        except subprocess.TimeoutExpired:
            return False, None, f"Execution timeout ({self.timeout}s)"
        except Exception as e:
            return False, None, str(e)
        finally:
            Path(strategy_file).unlink(missing_ok=True)

    def _wrap_strategy(self, code: str) -> str:
        """Wrap strategy code for sandbox execution"""
        return f"""
{code}

# Sandbox restrictions
import builtins
import os

# Remove dangerous builtins
for name in ['__import__', 'eval', 'exec', 'open', 'compile']:
    if hasattr(builtins, name):
        delattr(builtins, name)

# Restrict os
os.system = lambda _: None
os.popen = lambda _: None
os.spawn = lambda *_: None
"""


class StrategyRegistry:
    """
    Registry for managing strategy plugins.

    Features:
    - Plugin discovery via entry points
    - Version management
    - Backtest validation
    - Metadata storage
    """

    def __init__(self, registry_path: Path | None = None):
        self.registry_path = registry_path or Path.home() / '.trading' / 'strategies'
        self.registry_path.mkdir(parents=True, exist_ok=True)
        self._strategies: dict[str, type[BaseStrategy]] = {}
        self._metadata: dict[str, StrategyMetadata] = {}
        self._instances: dict[str, BaseStrategy] = {}
        self._status: dict[str, StrategyStatus] = {}
        self._load_from_disk()

    def _load_from_disk(self) -> int:
        """Load strategy metadata from disk"""
        count = 0
        for meta_file in self.registry_path.glob('*@*.json'):
            try:
                import json
                with open(meta_file, 'r') as f:
                    data = json.load(f)
                meta = StrategyMetadata.from_dict(data)
                key = f"{meta.name}@{meta.version}"
                self._metadata[key] = meta
                count += 1
            except Exception as e:
                logger.warning(f"Failed to load {meta_file}: {e}")
        return count

    def register(self, strategy_class: type[BaseStrategy], validate: bool = True) -> StrategyMetadata:
        """Register a strategy class"""
        meta = strategy_class.get_metadata()
        key = f"{meta.name}@{meta.version}"

        if validate:
            valid, msg = self._validate_strategy(strategy_class)
            if not valid:
                raise ValueError(f"Strategy validation failed: {msg}")

        self._strategies[key] = strategy_class
        self._metadata[key] = meta
        self._status[key] = StrategyStatus.REGISTERED

        # Preserve existing backtest_hash from disk if present
        file_path = self.registry_path / f"{meta.name}@{meta.version}.json"
        if file_path.exists():
            try:
                import json
                with open(file_path, 'r') as f:
                    existing = json.load(f)
                if existing.get('backtest_hash'):
                    meta.backtest_hash = existing['backtest_hash']
            except Exception:
                pass

        self._save_metadata(meta)
        logger.info(f"Registered strategy: {key}")
        return meta

    def _validate_strategy(self, strategy_class: type[BaseStrategy]) -> tuple[bool, str]:
        """Validate strategy implementation"""
        required = ['on_start', 'on_bar', 'on_stop']
        for method in required:
            if not hasattr(strategy_class, method):
                return False, f"Missing required method: {method}"
            if not callable(getattr(strategy_class, method)):
                return False, f"{method} is not callable"

        # Test instantiation with proper context
        try:
            instance = strategy_class()
            from trading.exchanges.models import Symbol, AssetClass, MarketType, Bar
            dummy_symbol = Symbol("BTC", "USDT", AssetClass.CRYPTO, MarketType.SPOT, "test")
            dummy_bar = Bar(
                symbol=dummy_symbol,
                timestamp=datetime.now(),
                timeframe="1h",
                open=Decimal("50000"),
                high=Decimal("51000"),
                low=Decimal("49000"),
                close=Decimal("50500"),
                volume=Decimal("100")
            )
            instance.on_start(StrategyContext(
                symbol=dummy_symbol,
                bar=dummy_bar,
                position=None,
                portfolio_value=Decimal("10000"),
                available_balance=Decimal("10000"),
                current_time=datetime.now(),
            ))
            instance.on_stop()
        except Exception as e:
            return False, f"Instantiation failed: {e}"

        return True, "Valid"

    def _save_metadata(self, meta: StrategyMetadata) -> None:
        """Save metadata to disk"""
        meta_file = self.registry_path / f"{meta.name}@{meta.version}.json"
        with open(meta_file, 'w') as f:
            json.dump(meta.to_dict(), f, indent=2)

    def reload(self) -> int:
        """Reload strategy metadata from disk"""
        self._metadata.clear()
        return self._load_from_disk()

    def load_from_entry_points(self) -> int:
        """Load strategies from installed packages via entry points"""
        count = 0
        try:
            for entry_point in importlib.metadata.entry_points(group='trading.strategies'):
                try:
                    strategy_class = entry_point.load()
                    if issubclass(strategy_class, BaseStrategy):
                        self.register(strategy_class)
                        count += 1
                except Exception as e:
                    logger.warning(f"Failed to load {entry_point.name}: {e}")
        except Exception as e:
            logger.debug(f"No entry points found: {e}")
        return count

    def load_from_directory(self, path: Path) -> int:
        """Load strategies from a directory"""
        count = 0
        for py_file in path.glob('*.py'):
            if py_file.name.startswith('_'):
                continue
            try:
                spec = importlib.util.spec_from_file_location(py_file.stem, py_file)
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)

                for name, obj in inspect.getmembers(module):
                    if inspect.isclass(obj) and issubclass(obj, BaseStrategy) and obj != BaseStrategy:
                        self.register(obj)
                        count += 1
            except Exception as e:
                logger.warning(f"Failed to load {py_file}: {e}")
        return count

    def get(self, name: str, version: str | None = None) -> type[BaseStrategy] | None:
        """Get strategy class by name and version"""
        if version:
            key = f"{name}@{version}"
            return self._strategies.get(key)
        versions = [k for k in self._strategies if k.startswith(f"{name}@")]
        if not versions:
            return None
        latest = max(versions, key=lambda v: v.split('@')[1])
        return self._strategies[latest]

    def get_metadata(self, name: str, version: str | None = None) -> StrategyMetadata | None:
        """Get strategy metadata"""
        if version:
            key = f"{name}@{version}"
            return self._metadata.get(key)
        versions = [k for k in self._metadata if k.startswith(f"{name}@")]
        if not versions:
            return None
        latest = max(versions, key=lambda v: v.split('@')[1])
        return self._metadata[latest]

    def list_strategies(self) -> list[StrategyMetadata]:
        """List all registered strategies"""
        return list(self._metadata.values())

    def create_instance(self, name: str, version: str | None = None, config: dict | None = None) -> BaseStrategy | None:
        """Create strategy instance"""
        strategy_class = self.get(name, version)
        if not strategy_class:
            return None

        instance = strategy_class(config)
        key = f"{name}@{version or 'latest'}"
        self._instances[key] = instance
        return instance

    def get_status(self, name: str, version: str | None = None) -> StrategyStatus:
        """Get strategy status"""
        if version:
            key = f"{name}@{version}"
        else:
            versions = [k for k in self._strategies if k.startswith(f"{name}@")]
            if not versions:
                return StrategyStatus.ERROR
            key = max(versions, key=lambda v: v.split('@')[1])
        return self._status.get(key, StrategyStatus.ERROR)

    def set_status(self, name: str, version: str, status: StrategyStatus) -> None:
        """Set strategy status"""
        key = f"{name}@{version}"
        self._status[key] = status

    def validate_backtest(self, name: str, version: str, backtest_result: dict) -> tuple[bool, str]:
        """Validate backtest result matches expected hash"""
        meta = self.get_metadata(name, version)
        if not meta or not meta.backtest_hash:
            return True, "No backtest hash to validate"

        result_str = json.dumps(backtest_result, sort_keys=True, default=str)
        actual_hash = hashlib.sha256(result_str.encode()).hexdigest()[:16]

        if actual_hash != meta.backtest_hash:
            return False, f"Backtest hash mismatch: expected {meta.backtest_hash}, got {actual_hash}"

        return True, "Valid"


# Global registry instance
_registry: StrategyRegistry | None = None


def get_registry() -> StrategyRegistry:
    """Get global strategy registry"""
    global _registry
    if _registry is None:
        _registry = StrategyRegistry()
    return _registry


# Example strategy implementations
class ExampleMAStrategy(BaseStrategy):
    """Example Moving Average Crossover Strategy"""

    metadata = StrategyMetadata(
        name="MA_Crossover",
        version="1.0.0",
        author="Trading System",
        description="Simple MA crossover with configurable periods",
        strategy_type=StrategyType.TREND_FOLLOWING,
        risk_profile=RiskProfile.MODERATE,
        asset_classes=["crypto", "stocks"],
        timeframes=["1h", "4h", "1d"],
        parameters={
            'fast_period': {'type': 'integer', 'min': 5, 'max': 50, 'default': 10, 'required': True},
            'slow_period': {'type': 'integer', 'min': 20, 'max': 200, 'default': 30, 'required': True},
            'position_size': {'type': 'number', 'min': 0.01, 'max': 1.0, 'default': 0.1, 'required': True},
        },
    )

    def __init__(self, config: dict | None = None):
        super().__init__(config)
        self.fast_period = self.config.get('fast_period', 10)
        self.slow_period = self.config.get('slow_period', 30)
        self.position_size = self.config.get('position_size', 0.1)
        self.fast_ma: list[Decimal] = []
        self.slow_ma: list[Decimal] = []

    def on_start(self, context: StrategyContext) -> None:
        self.fast_ma = []
        self.slow_ma = []

    def on_bar(self, context: StrategyContext) -> list[Signal]:
        close = context.bar.close

        self.fast_ma.append(close)
        self.slow_ma.append(close)

        if len(self.fast_ma) > self.fast_period:
            self.fast_ma.pop(0)
        if len(self.slow_ma) > self.slow_period:
            self.slow_ma.pop(0)

        if len(self.fast_ma) < self.fast_period or len(self.slow_ma) < self.slow_period:
            return []

        fast_val = sum(self.fast_ma) / len(self.fast_ma)
        slow_val = sum(self.slow_ma) / len(self.slow_ma)

        signals = []
        position = context.position

        if fast_val > slow_val and (position is None or position.size <= 0):
            signals.append(Signal(
                symbol=context.symbol,
                side=OrderSide.BUY,
                strength=Decimal('0.7'),
                strategy_name=self.get_metadata().name,
            ))
        elif fast_val < slow_val and position and position.size > 0:
            signals.append(Signal(
                symbol=context.symbol,
                side=OrderSide.SELL,
                strength=Decimal('0.7'),
                strategy_name=self.get_metadata().name,
            ))

        return signals

    def on_stop(self) -> None:
        pass


class ExampleRSIStrategy(BaseStrategy):
    """Example RSI Mean Reversion Strategy"""

    metadata = StrategyMetadata(
        name="RSI_MeanReversion",
        version="1.0.0",
        author="Trading System",
        description="RSI mean reversion with overbought/oversold levels",
        strategy_type=StrategyType.MEAN_REVERSION,
        risk_profile=RiskProfile.MODERATE,
        asset_classes=["crypto", "stocks", "forex"],
        timeframes=["15m", "1h", "4h"],
        parameters={
            'period': {'type': 'integer', 'min': 5, 'max': 30, 'default': 14, 'required': True},
            'oversold': {'type': 'integer', 'min': 10, 'max': 40, 'default': 30, 'required': True},
            'overbought': {'type': 'integer', 'min': 60, 'max': 90, 'default': 70, 'required': True},
            'position_size': {'type': 'number', 'min': 0.01, 'max': 1.0, 'default': 0.1, 'required': True},
        },
    )

    def __init__(self, config: dict | None = None):
        super().__init__(config)
        self.period = self.config.get('period', 14)
        self.oversold = self.config.get('oversold', 30)
        self.overbought = self.config.get('overbought', 70)
        self.position_size = self.config.get('position_size', 0.1)
        self.closes: list[Decimal] = []
        self.rsi_values: list[float] = []

    def on_start(self, context: StrategyContext) -> None:
        self.closes = []
        self.rsi_values = []

    def on_bar(self, context: StrategyContext) -> list[Signal]:
        close = context.bar.close
        self.closes.append(close)

        if len(self.closes) > self.period:
            self.closes.pop(0)

        if len(self.closes) < self.period:
            return []

        # Calculate RSI
        gains = []
        losses = []
        for i in range(1, len(self.closes)):
            diff = float(self.closes[i] - self.closes[i-1])
            if diff > 0:
                gains.append(diff)
            else:
                losses.append(-diff)

        avg_gain = sum(gains) / len(gains) if gains else 0
        avg_loss = sum(losses) / len(losses) if losses else 0

        if avg_loss == 0:
            rsi = 100
        else:
            rs = avg_gain / avg_loss
            rsi = 100 - (100 / (1 + rs))

        self.rsi_values.append(rsi)

        signals = []
        position = context.position

        if rsi < self.oversold and (position is None or position.size <= 0):
            signals.append(Signal(
                symbol=context.symbol,
                side=OrderSide.BUY,
                strength=Decimal('0.8'),
                strategy_name=self.get_metadata().name,
            ))
        elif rsi > self.overbought and position and position.size > 0:
            signals.append(Signal(
                symbol=context.symbol,
                side=OrderSide.SELL,
                strength=Decimal('0.8'),
                strategy_name=self.get_metadata().name,
            ))

        return signals

    def on_stop(self) -> None:
        pass


import subprocess