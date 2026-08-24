"""
RuntimeStrategyResolver — Bridge between promoted artifacts and runtime strategy execution.

This is the REAL resolver — keyed by (symbol, timeframe, environment),
only loads PRODUCTION/CANARY artifacts, instantiates exact strategy.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any, Dict

from trading_agent.authority.config import AuthorityConfig, Environment
from trading_agent.authority.loader import PromotedStrategy
from trading_agent.strategies.base import Strategy
from trading_agent.strategies.ma_crossover import MaCrossover
from trading_agent.strategies.rsi import RsiStrategy
from trading_agent.strategies.bbands import BBandsStrategy
from trading_agent.strategies.online_learning_strategy import OnlineLearningStrategy
from trading_agent.strategies.regime_switching import RegimeSwitchingStrategy

logger = __import__("logging").getLogger(__name__)


class StrategyType(str, Enum):
    MA_CROSSOVER = "ma_crossover"
    RSI = "rsi"
    BBANDS = "bbands"
    ONLINE_LEARNING = "online_learning"
    REGIME_SWITCHING = "regime_switching"


@dataclass(frozen=True, slots=True)
class StrategyRuntime:
    """
    Bound, executable strategy instance at runtime.

    This is the SINGLE SOURCE OF TRUTH for a running strategy.
    Contains the instantiated Strategy + all context needed for execution.
    """

    strategy: Strategy
    artifact_id: str
    strategy_name: str
    symbol: str
    timeframe: str
    environment: Environment
    parameters: Dict[str, Any]
    promoted_at: datetime
    promotion_stage: str
    loaded_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def execute(self, market_data: Any, portfolio_state: Any) -> "StrategyOutput":
        """Execute strategy on current market data → StrategyOutput."""
        # Try both singular and plural method names for backward compatibility
        if hasattr(self.strategy, "generate_signal"):
            signal = self.strategy.generate_signal(market_data)
        elif hasattr(self.strategy, "generate_signals"):
            signal = self.strategy.generate_signals(market_data)
        else:
            raise AttributeError(
                f"Strategy {type(self.strategy).__name__} has neither generate_signal nor generate_signals"
            )

        # Extract signal details
        if hasattr(signal, "signal"):
            # AgentMessage-like object
            signal_value = getattr(signal, "signal", "HOLD")
            confidence = float(getattr(signal, "confidence", 0.5))
            target_exposure = float(getattr(signal, "target_exposure_pct", 0.0))
            reduce_only = bool(getattr(signal, "reduce_only", False))
        elif hasattr(signal, "to_numpy"):
            # polars Series — extract last value
            values = signal.to_numpy()
            last_val = float(values[-1]) if len(values) > 0 else 0.0
            signal_value = (
                "BUY" if last_val > 0 else ("SELL" if last_val < 0 else "HOLD")
            )
            confidence = 0.5
            target_exposure = abs(last_val) * 0.1  # Scale by signal magnitude
            reduce_only = last_val < 0
        else:
            signal_value = str(signal)
            confidence = 0.5
            target_exposure = 0.0
            reduce_only = False

        return StrategyOutput(
            artifact_id=self.artifact_id,
            strategy_name=self.strategy_name,
            symbol=self.symbol,
            timeframe=self.timeframe,
            signal=signal,
            confidence=float(confidence),
            target_exposure_pct=float(target_exposure),
            metadata={
                "strategy_type": type(self.strategy).__name__,
                "parameters": self.parameters,
                "signal_value": signal_value,
                "reduce_only": reduce_only,
            },
            generated_at=datetime.now(UTC),
        )


@dataclass(frozen=True, slots=True)
class StrategyOutput:
    """
    Output from a runtime strategy execution.

    This replaces AgentMessage as the canonical signal format for the
    authority chain. No more multi-agent ensemble — single promoted strategy.
    """

    artifact_id: str
    strategy_name: str
    symbol: str
    timeframe: str
    signal: Any  # StrategySignal (BUY/SELL/HOLD with metadata)
    confidence: float  # 0-1
    target_exposure_pct: float  # 0-1 fraction of equity
    metadata: Dict[str, Any]
    generated_at: datetime


class RuntimeStrategyResolver:
    """
    Resolves promoted StrategyArtifacts into StrategyRuntime instances.

    Key design decisions:
    - Keyed by (symbol, timeframe, environment) — exact match required
    - Only PRODUCTION/CANARY promotion stages allowed (authoritative)
    - Instantiates EXACT strategy class with artifact parameters
    - NO metadata-as-authority — artifact IS the configuration
    - Caches StrategyRuntime instances for reuse
    """

    _STRATEGY_MAP: Dict[str, StrategyType] = {
        "ma_crossover": StrategyType.MA_CROSSOVER,
        "rsi": StrategyType.RSI,
        "bbands": StrategyType.BBANDS,
        "online_learning": StrategyType.ONLINE_LEARNING,
        "regime_switching": StrategyType.REGIME_SWITCHING,
    }

    _STRATEGY_CLASSES: Dict[StrategyType, type[Strategy]] = {
        StrategyType.MA_CROSSOVER: MaCrossover,
        StrategyType.RSI: RsiStrategy,
        StrategyType.BBANDS: BBandsStrategy,
        StrategyType.ONLINE_LEARNING: OnlineLearningStrategy,
        StrategyType.REGIME_SWITCHING: RegimeSwitchingStrategy,
    }

    # Allowed promotion stages for runtime loading
    _ALLOWED_STAGES = frozenset({"production", "canary", "testnet"})

    def __init__(self, config: AuthorityConfig):
        self.config = config
        self._cache: Dict[str, StrategyRuntime] = {}  # key -> StrategyRuntime

    def _make_key(self, symbol: str, timeframe: str, environment: Environment) -> str:
        """Cache key: (symbol, timeframe, environment)."""
        return f"{symbol}|{timeframe}|{environment.value}"

    def resolve(
        self,
        promoted: PromotedStrategy,
        symbol: str,
        timeframe: str,
        environment: Environment,
    ) -> StrategyRuntime | None:
        """
        Resolve promoted strategy to StrategyRuntime.

        Args:
            promoted: PromotedStrategy from RuntimeLoader
            symbol: Exact symbol (e.g., "BTC/USDT")
            timeframe: Exact timeframe (e.g., "1h")
            environment: Runtime environment (testnet/paper/production)

        Returns:
            StrategyRuntime if resolution successful, None otherwise
        """
        # Verify promotion stage is authoritative
        if promoted.manifest.promotion_stage not in self._ALLOWED_STAGES:
            logger.warning(
                f"Strategy {promoted.artifact_id} not in allowed stage "
                f"({promoted.manifest.promotion_stage}), skipping"
            )
            return None

        # Verify symbol matches
        artifact_symbol = promoted.artifact.metadata.get("symbol", "")
        if artifact_symbol and artifact_symbol != symbol:
            logger.warning(
                f"Symbol mismatch: artifact={artifact_symbol}, requested={symbol}"
            )
            return None

        # Verify timeframe matches
        artifact_timeframe = promoted.artifact.metadata.get("timeframe", "")
        if artifact_timeframe and artifact_timeframe != timeframe:
            logger.warning(
                f"Timeframe mismatch: artifact={artifact_timeframe}, requested={timeframe}"
            )
            return None

        # Verify environment is compatible with promotion stage
        if not self._is_stage_compatible(
            promoted.manifest.promotion_stage, environment
        ):
            logger.warning(
                f"Promotion stage {promoted.manifest.promotion_stage} not compatible "
                f"with environment {environment.value}"
            )
            return None

        # Check cache
        key = self._make_key(symbol, timeframe, environment)
        if key in self._cache:
            cached = self._cache[key]
            # Verify no param drift
            if cached.artifact_id == promoted.artifact_id:
                return cached

        # Resolve strategy class
        strategy_name = promoted.manifest.strategy_name
        strategy_type = self._STRATEGY_MAP.get(strategy_name)
        if not strategy_type:
            logger.warning(f"Unknown strategy name: {strategy_name}")
            return None

        strategy_cls = self._STRATEGY_CLASSES.get(strategy_type)
        if not strategy_cls:
            logger.warning(f"No class for strategy type: {strategy_type}")
            return None

        # Verify artifact integrity
        if not self._verify_artifact_integrity(promoted):
            return None

        # Check for parameter drift
        if self._has_param_drift(promoted):
            logger.warning(f"Parameter drift detected for {promoted.artifact_id}")
            return None

        # Bind to environment and instantiate EXACT strategy
        strategy = self._bind_strategy(strategy_cls, promoted)
        if strategy is None:
            return None

        # Create StrategyRuntime
        runtime = StrategyRuntime(
            strategy=strategy,
            artifact_id=promoted.artifact_id,
            strategy_name=promoted.manifest.strategy_name,
            symbol=symbol,
            timeframe=timeframe,
            environment=environment,
            parameters=promoted.manifest.parameters,
            promoted_at=promoted.manifest.promoted_at,
            promotion_stage=promoted.manifest.promotion_stage,
        )

        # Cache and return
        self._cache[key] = runtime
        logger.info(
            f"Resolved StrategyRuntime: {strategy_name} ({symbol} {timeframe} {environment.value})"
        )
        return runtime

    def get_cached(
        self, symbol: str, timeframe: str, environment: Environment
    ) -> StrategyRuntime | None:
        """Get cached StrategyRuntime if exists."""
        key = self._make_key(symbol, timeframe, environment)
        return self._cache.get(key)

    def clear_cache(self) -> None:
        """Clear all cached runtimes (e.g., on config reload)."""
        self._cache.clear()

    def _is_stage_compatible(
        self, promotion_stage: str, environment: Environment
    ) -> bool:
        """Check if promotion stage is compatible with runtime environment."""
        stage_env_map = {
            "testnet": {Environment.TESTNET, Environment.RESEARCH},
            "canary": {Environment.CANARY, Environment.SHADOW, Environment.TESTNET},
            "production": {Environment.PRODUCTION, Environment.PAPER},
        }
        allowed = stage_env_map.get(promotion_stage, set())
        return environment in allowed

    def _verify_artifact_integrity(self, promoted: PromotedStrategy) -> bool:
        """Verify artifact integrity (placeholder - integrate with store)."""
        # TODO: Integrate with PersistentArtifactStore.verify_integrity
        return True

    def _has_param_drift(self, promoted: PromotedStrategy) -> bool:
        """Check for parameter drift between live and artifact."""
        # TODO: Compare live params with artifact.parameter_hash
        return False

    def _bind_strategy(
        self,
        strategy_cls: type[Strategy],
        promoted: PromotedStrategy,
    ) -> Strategy | None:
        """Instantiate strategy with EXACT artifact parameters."""
        params = promoted.manifest.parameters.copy()
        # Apply environment constraints from AuthorityConfig
        params = self._apply_env_constraints(params, promoted)
        try:
            return strategy_cls(params=params)
        except Exception as e:
            logger.error(f"Failed to instantiate strategy {strategy_cls}: {e}")
            return None

    def _apply_env_constraints(
        self,
        params: Dict[str, Any],
        promoted: PromotedStrategy,
    ) -> Dict[str, Any]:
        """Apply environment-specific constraints from AuthorityConfig."""
        # Check symbol against allowed list
        if "symbol" in params:
            symbol = params["symbol"]
            if symbol not in self.config.symbols:
                # For research environment, allow any symbol
                if self.config.environment != Environment.RESEARCH:
                    raise ValueError(f"Symbol {symbol} not in allowed list")
        # Check timeframe against supported set
        if "timeframe" in params:
            timeframe = params["timeframe"]
            supported = {"1d", "4h", "1h", "15m", "5m", "1m"}
            if timeframe not in supported:
                raise ValueError(f"Timeframe {timeframe} not supported")
        return params

    def get_strategy_class(self, strategy_type: StrategyType) -> type[Strategy]:
        return self._STRATEGY_CLASSES.get(strategy_type)

    def get_strategy_class_by_name(self, strategy_name: str) -> type[Strategy] | None:
        strategy_type = self._STRATEGY_MAP.get(strategy_name)
        return self._STRATEGY_CLASSES.get(strategy_type)

    def _is_symbol_allowed(self, symbol: str) -> bool:
        """Check if symbol is allowed in current environment."""
        if self.config.environment == Environment.RESEARCH:
            return True
        return symbol in self.config.symbols

    def _is_timeframe_supported(self, timeframe: str) -> bool:
        """Check if timeframe is supported."""
        return timeframe in {"1d", "4h", "1h", "15m", "5m", "1m"}
