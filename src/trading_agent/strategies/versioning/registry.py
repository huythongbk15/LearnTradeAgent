"""Strategy registry with metadata and versioning."""

import hashlib
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional
from pathlib import Path

from trading_agent.strategies.plugins import BaseStrategy as Strategy

logger = logging.getLogger(__name__)


class RiskProfile(str, Enum):
    """Strategy risk profile."""

    CONSERVATIVE = "conservative"
    MODERATE = "moderate"
    AGGRESSIVE = "aggressive"
    SPECULATIVE = "speculative"


class AssetClass(str, Enum):
    """Asset class the strategy trades."""

    CRYPTO = "crypto"
    STOCKS = "stocks"
    FOREX = "forex"
    FUTURES = "futures"
    OPTIONS = "options"
    MULTI_ASSET = "multi_asset"


@dataclass
class StrategyMetadata:
    """Strategy metadata for registry."""

    name: str
    version: str
    author: str
    description: str
    asset_class: AssetClass
    risk_profile: RiskProfile
    timeframes: list[str]
    symbols: list[str]
    params_schema: dict
    backtest_hash: str
    backtest_period: str
    backtest_metrics: dict
    is_active: bool = True
    created_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: datetime = field(default_factory=datetime.utcnow)
    tags: list[str] = field(default_factory=list)
    dependencies: list[str] = field(default_factory=list)


@dataclass
class StrategyVersion:
    """Versioned strategy entry."""

    metadata: StrategyMetadata
    source_code: str
    source_hash: str
    abi_hash: str
    is_active: bool = True
    is_deprecated: bool = False
    deployed_at: Optional[datetime] = None
    retired_at: Optional[datetime] = None
    deployment_hash: Optional[str] = None


class StrategyRegistry:
    """Registry for managing strategy versions."""

    def __init__(self, store_path: str = "./strategies_registry"):
        self.store_path = Path(store_path)
        self.store_path.mkdir(parents=True, exist_ok=True)
        self._versions: dict[str, list[StrategyVersion]] = {}  # name -> [versions]
        self._active: dict[str, StrategyVersion] = {}  # name -> active version

    def _compute_hash(self, content: str) -> str:
        """Compute SHA256 hash of content."""
        return hashlib.sha256(content.encode()).hexdigest()[:16]

    def register(
        self,
        strategy_class: type[Strategy],
        metadata: StrategyMetadata,
        source_code: Optional[str] = None,
    ) -> StrategyVersion:
        """Register a new strategy version."""
        # Get source code if not provided
        if source_code is None:
            import inspect

            source_code = inspect.getsource(strategy_class)

        source_hash = self._compute_hash(source_code)

        # Compute ABI hash
        from trading_agent.strategies.versioning.abi import StrategyABI

        abi = StrategyABI.from_strategy(strategy_class)
        abi_hash = abi.hash

        # Check if backtest hash matches
        if metadata.backtest_hash:
            # Verify backtest matches current code
            expected_hash = self._compute_hash(
                source_code + json.dumps(metadata.params_schema, sort_keys=True)
            )
            if expected_hash != metadata.backtest_hash:
                logger.warning(f"Backtest hash mismatch for {metadata.name}")

        version = StrategyVersion(
            metadata=metadata,
            source_code=source_code,
            source_hash=source_hash,
            abi_hash=abi_hash,
        )

        # Store
        name = metadata.name
        if name not in self._versions:
            self._versions[name] = []
        self._versions[name].append(version)

        # Activate if first or explicitly marked
        if metadata.is_active or name not in self._active:
            self._active[name] = version
            version.is_active = True
            version.deployed_at = datetime.utcnow()

        # Persist
        self._persist_version(version)

        logger.info(
            f"Registered strategy {name} v{metadata.version} (hash: {source_hash[:8]})"
        )
        return version

    def _persist_version(self, version: StrategyVersion) -> None:
        """Persist version to disk."""
        name = version.metadata.name
        v = version.metadata.version
        file_path = self.store_path / name / f"v{v}.json"
        file_path.parent.mkdir(parents=True, exist_ok=True)

        data = {
            "metadata": {
                "name": version.metadata.name,
                "version": version.metadata.version,
                "author": version.metadata.author,
                "description": version.metadata.description,
                "asset_class": version.metadata.asset_class.value,
                "risk_profile": version.metadata.risk_profile.value,
                "timeframes": version.metadata.timeframes,
                "symbols": version.metadata.symbols,
                "params_schema": version.metadata.params_schema,
                "backtest_hash": version.metadata.backtest_hash,
                "backtest_period": version.metadata.backtest_period,
                "backtest_metrics": version.metadata.backtest_metrics,
                "created_at": version.metadata.created_at.isoformat(),
                "updated_at": version.metadata.updated_at.isoformat(),
                "tags": version.metadata.tags,
                "dependencies": version.metadata.dependencies,
            },
            "source_hash": version.source_hash,
            "abi_hash": version.abi_hash,
            "is_active": version.is_active,
            "is_deprecated": version.is_deprecated,
            "deployed_at": version.deployed_at.isoformat()
            if version.deployed_at
            else None,
            "retired_at": version.retired_at.isoformat()
            if version.retired_at
            else None,
            "deployment_hash": version.deployment_hash,
        }

        file_path.write_text(json.dumps(data, indent=2))

    def get_active(self, name: str) -> Optional[StrategyVersion]:
        """Get active version of strategy."""
        return self._active.get(name)

    def get_version(self, name: str, version: str) -> Optional[StrategyVersion]:
        """Get specific version."""
        for v in self._versions.get(name, []):
            if v.metadata.version == version:
                return v
        return None

    def list_versions(self, name: str) -> list[StrategyVersion]:
        """List all versions of a strategy."""
        return self._versions.get(name, [])

    def list_strategies(self) -> list[str]:
        """List all registered strategy names."""
        return list(self._versions.keys())

    def activate(self, name: str, version: str) -> bool:
        """Activate a specific version."""
        v = self.get_version(name, version)
        if not v:
            return False

        # Deactivate current
        if name in self._active:
            self._active[name].is_active = False

        # Activate new
        v.is_active = True
        v.deployed_at = datetime.utcnow()
        self._active[name] = v

        self._persist_version(v)
        logger.info(f"Activated {name} v{version}")
        return True

    def deprecate(self, name: str, version: str) -> bool:
        """Deprecate a version."""
        v = self.get_version(name, version)
        if not v:
            return False

        v.is_deprecated = True
        v.retired_at = datetime.utcnow()

        if self._active.get(name) == v:
            del self._active[name]

        self._persist_version(v)
        logger.info(f"Deprecated {name} v{version}")
        return True

    def verify_deployment(self, name: str, version: str, deployment_hash: str) -> bool:
        """Verify deployed code matches registered version."""
        v = self.get_version(name, version)
        if not v:
            return False

        v.deployment_hash = deployment_hash
        self._persist_version(v)
        return True


class StrategyLoader:
    """Load strategy from registry."""

    def __init__(self, registry: StrategyRegistry):
        self.registry = registry

    def load(self, name: str, version: Optional[str] = None) -> type[Strategy]:
        """Load strategy class from registry."""
        if version:
            v = self.registry.get_version(name, version)
        else:
            v = self.registry.get_active(name)

        if not v:
            raise ValueError(f"Strategy not found: {name} {version or '(active)'}")

        # Seed namespace with common trading plugin names so exec'd strategy
        # source can resolve BaseStrategy, StrategyMetadata, Signal, etc.
        import trading_agent.strategies.plugins as plugins_module

        namespace = {"__builtins__": __builtins__}
        for _export in getattr(plugins_module, "__all__", []):
            namespace[_export] = getattr(plugins_module, _export, None)
        namespace["plugins"] = plugins_module

        # Compile and execute source
        exec(v.source_code, namespace)

        # Find strategy class
        for obj in namespace.values():
            if isinstance(obj, type) and issubclass(obj, Strategy) and obj != Strategy:
                return obj

        raise ValueError(f"No Strategy class found in {name} v{version}")

    def load_with_params(
        self, name: str, params: dict, version: Optional[str] = None
    ) -> Strategy:
        """Load and instantiate strategy with parameters."""
        cls = self.load(name, version)
        return cls(params)
