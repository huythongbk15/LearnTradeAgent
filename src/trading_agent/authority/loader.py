"""
PromotedStrategy & RuntimeLoader — Research → Runtime bridge.

This component loads promoted StrategyArtifacts into the runtime authority chain.
No manual parameter copying — the artifact IS the strategy configuration.

Features:
- Content-addressed loading (artifact_id = exact config)
- Hot-reload on artifact promotion (no restart)
- Parameter drift detection (live params vs artifact param_hash)
- Manifest file for operator visibility
"""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from trading_agent.authority.config import AuthorityConfig, get_authority_config
from trading_agent.authority.promotion_store import PromotionStateStore
from trading_agent.research.artifact import PersistentArtifactStore, StrategyArtifact
from trading_agent.research.promotion import ResearchLifecycle, ResearchStage

logger = logging.getLogger(__name__)


# ── Manifest ────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class PromotedStrategyManifest:
    """Operator-visible manifest of a promoted strategy."""

    artifact_id: str
    strategy_name: str
    code_sha: str
    parameter_hash: str
    execution_model_version: str
    framework_version: str
    promoted_at: datetime
    promoted_by: str
    promotion_stage: str
    parameters: dict[str, Any]
    metadata: dict[str, Any]

    @classmethod
    def from_artifact(
        cls, artifact: StrategyArtifact, promotion_event: Any
    ) -> "PromotedStrategyManifest":
        return cls(
            artifact_id=artifact.artifact_id,
            strategy_name=artifact.strategy_name,
            code_sha=artifact.code_sha,
            parameter_hash=artifact.parameter_hash,
            execution_model_version=artifact.execution_model_version,
            framework_version=artifact.framework_version,
            promoted_at=promotion_event.timestamp
            if hasattr(promotion_event, "timestamp")
            else datetime.now(UTC),
            promoted_by=promotion_event.actor
            if hasattr(promotion_event, "actor")
            else "system",
            promotion_stage=promotion_event.to_stage.value
            if hasattr(promotion_event, "to_stage")
            else "production",
            parameters=artifact.metadata.get("parameters", {}),
            metadata=artifact.metadata,
        )

    def to_json(self) -> str:
        return json.dumps(
            {
                "artifact_id": self.artifact_id,
                "strategy_name": self.strategy_name,
                "code_sha": self.code_sha,
                "parameter_hash": self.parameter_hash,
                "execution_model_version": self.execution_model_version,
                "framework_version": self.framework_version,
                "promoted_at": self.promoted_at.isoformat(),
                "promoted_by": self.promoted_by,
                "promotion_stage": self.promotion_stage,
                "parameters": self.parameters,
                "metadata": self.metadata,
            },
            indent=2,
            sort_keys=True,
        )

    @classmethod
    def from_json(cls, json_str: str) -> "PromotedStrategyManifest":
        data = json.loads(json_str)
        data["promoted_at"] = datetime.fromisoformat(data["promoted_at"])
        return cls(**data)


# ── PromotedStrategy ────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class PromotedStrategy:
    """
    Runtime representation of a promoted strategy artifact.

    This is the SINGLE SOURCE OF TRUTH for strategy parameters at runtime.
    No manual config files — the artifact hash IS the configuration.
    """

    artifact: StrategyArtifact
    manifest: PromotedStrategyManifest
    loaded_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @property
    def artifact_id(self) -> str:
        return self.artifact.artifact_id

    @property
    def strategy_name(self) -> str:
        return self.artifact.strategy_name

    @property
    def parameters(self) -> dict[str, Any]:
        """Strategy parameters from artifact metadata."""
        return self.artifact.metadata.get("parameters", {})

    def get_param(self, key: str, default: Any = None) -> Any:
        """Get a parameter with optional default."""
        return self.parameters.get(key, default)

    def verify_integrity(self, store: PersistentArtifactStore) -> bool:
        """Verify artifact integrity against store."""
        return store.verify_integrity(self.artifact_id)

    def verify_param_hash(self) -> bool:
        """Verify live parameters match artifact parameter_hash."""
        from trading_agent.research.artifact import canonical_params, sha256_hex

        live_hash = sha256_hex(canonical_params(self.parameters))
        return live_hash == self.artifact.parameter_hash

    def to_decision_input(
        self,
        symbol: str,
        current_price: float,
        current_exposure: float,
        equity: float,
        available_cash: float,
    ) -> dict[str, Any]:
        """Convert to DecisionAuthority input format."""
        return {
            "strategy_artifact": self.artifact,
            "symbol": symbol,
            "current_price": current_price,
            "current_exposure": current_exposure,
            "equity": equity,
            "available_cash": available_cash,
            "portfolio_value": equity,
        }


# ── RuntimeLoader ───────────────────────────────────────────────────────


class RuntimeLoader:
    """
    Hot-reloadable loader for promoted strategies.

    Watches artifact store for new PRODUCTION promotions and loads them
    without process restart. Emits causation chain for audit.
    """

    def __init__(
        self,
        artifact_store: PersistentArtifactStore,
        manifest_dir: str | Path = "data/promoted_strategies",
        config: AuthorityConfig | None = None,
        poll_interval_seconds: float = 30.0,
        promotion_store: PromotionStateStore | None = None,
    ):
        self.artifact_store = artifact_store
        self.manifest_dir = Path(manifest_dir)
        self.manifest_dir.mkdir(parents=True, exist_ok=True)
        self.config = config or get_authority_config()
        self.poll_interval = poll_interval_seconds
        self.promotion_store = promotion_store

        self._loaded: dict[
            str, PromotedStrategy
        ] = {}  # artifact_id -> PromotedStrategy
        self._lock = threading.RLock()
        self._watcher_thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._callbacks: list[callable] = []

    def start(self) -> None:
        """Start background watcher."""
        if self._watcher_thread and self._watcher_thread.is_alive():
            return

        self._stop_event.clear()
        self._watcher_thread = threading.Thread(target=self._watch_loop, daemon=True)
        self._watcher_thread.start()
        logger.info("RuntimeLoader watcher started")

    def stop(self) -> None:
        """Stop background watcher."""
        self._stop_event.set()
        if self._watcher_thread:
            self._watcher_thread.join(timeout=5.0)
        logger.info("RuntimeLoader watcher stopped")

    def register_callback(self, callback: callable) -> None:
        """Register callback(strategy) fired on new/updated strategy."""
        self._callbacks.append(callback)

    def load_by_artifact_id(self, artifact_id: str) -> PromotedStrategy | None:
        """Load a specific promoted strategy by artifact ID."""
        artifact = self.artifact_store.get(artifact_id)
        if artifact is None:
            return None

        # Check if promoted to PRODUCTION
        if not self._is_production_ready(artifact):
            logger.warning(f"Artifact {artifact_id} not yet promoted to PRODUCTION")
            return None

        return self._load_artifact(artifact)

    def load_latest_for_strategy(self, strategy_name: str) -> PromotedStrategy | None:
        """Load the latest PRODUCTION artifact for a strategy."""
        artifacts = self.artifact_store.all_for(strategy_name)
        production_artifacts = [a for a in artifacts if self._is_production_ready(a)]

        if not production_artifacts:
            return None

        # Sort by created_at, newest first
        latest = max(production_artifacts, key=lambda a: a.created_at)
        return self._load_artifact(latest)

    def get_loaded(self, artifact_id: str) -> PromotedStrategy | None:
        """Get already-loaded strategy by artifact ID."""
        with self._lock:
            return self._loaded.get(artifact_id)

    def get_all_loaded(self) -> dict[str, PromotedStrategy]:
        """Get all currently loaded strategies."""
        with self._lock:
            return dict(self._loaded)

    def check_param_drift(self, artifact_id: str) -> tuple[bool, dict[str, Any]]:
        """
        Check for parameter drift between live and artifact.

        Returns (has_drift, drift_details).
        """
        with self._lock:
            strategy = self._loaded.get(artifact_id)
            if not strategy:
                return False, {"error": "not_loaded"}

        has_drift = not strategy.verify_param_hash()
        details = {
            "artifact_id": artifact_id,
            "artifact_param_hash": strategy.artifact.parameter_hash,
            "live_param_hash": strategy.verify_param_hash(),
            "drift_detected": has_drift,
            "checked_at": datetime.now(UTC).isoformat(),
        }

        if has_drift:
            logger.warning(f"Parameter drift detected for {artifact_id}: {details}")

        return has_drift, details

    def _is_production_ready(self, artifact: StrategyArtifact) -> bool:
        """Check if artifact has been promoted to an eligible stage.

        Uses the authoritative PromotionStateStore when available.
        Falls back to artifact metadata only if no store is configured
        (test/research scenarios only).
        """
        if self.promotion_store is not None:
            stage = self.promotion_store.get_stage(artifact.artifact_id)
            if stage is None:
                return False
            # Map stage to legacy "production_ready" boolean
            # Any stage >= PAPER_ELIGIBLE is considered ready for runtime
            from trading_agent.authority.promotion_store import _STAGE_RANK, _ENV_MIN_STAGE
            min_rank = _STAGE_RANK.get(_ENV_MIN_STAGE.get("paper", ResearchStage.PAPER_ELIGIBLE), 0)
            return _STAGE_RANK.get(stage, -1) >= min_rank

        # Fallback for tests without promotion store
        # In production this path must NOT be used
        promo_stage = artifact.metadata.get("promotion_stage", "")
        return promo_stage in ("production", "canary", "testnet")

    def _load_artifact(self, artifact: StrategyArtifact) -> PromotedStrategy:
        """Load artifact into memory and write manifest."""
        with self._lock:
            # Check if already loaded with same hash
            existing = self._loaded.get(artifact.artifact_id)
            if existing and existing.verify_param_hash():
                return existing

            # Get promotion stage from authoritative store (not metadata)
            promotion_stage = "paper"  # default fallback
            if self.promotion_store is not None:
                stage = self.promotion_store.get_stage(artifact.artifact_id)
                if stage is not None:
                    promotion_stage = stage.value
                else:
                    # Not promoted — fail closed
                    logger.warning(f"Artifact {artifact.artifact_id} has no promotion record")
                    raise ValueError(f"Artifact {artifact.artifact_id} is not promoted")
            else:
                # Fallback for tests without promotion store
                promotion_stage = artifact.metadata.get("promotion_stage", "paper")
                if promotion_stage not in ("production", "canary", "testnet", "paper"):
                    logger.warning(f"Artifact {artifact.artifact_id} has invalid promotion stage in metadata: {promotion_stage}")
                    promotion_stage = "paper"

            # Create manifest with authoritative promotion stage
            manifest = PromotedStrategyManifest(
                artifact_id=artifact.artifact_id,
                strategy_name=artifact.strategy_name,
                code_sha=artifact.code_sha,
                parameter_hash=artifact.parameter_hash,
                execution_model_version=artifact.execution_model_version,
                framework_version=artifact.framework_version,
                promoted_at=artifact.created_at,
                promoted_by=artifact.metadata.get("promoted_by", "system"),
                promotion_stage=promotion_stage,
                parameters=artifact.metadata.get("parameters", {}),
                metadata=artifact.metadata,
            )

            strategy = PromotedStrategy(
                artifact=artifact,
                manifest=manifest,
                loaded_at=datetime.now(UTC),
            )

            self._loaded[artifact.artifact_id] = strategy

            # Write manifest file for operator visibility
            self._write_manifest(manifest)

            # Fire callbacks
            for callback in self._callbacks:
                try:
                    callback(strategy)
                except Exception as e:
                    logger.error(f"RuntimeLoader callback failed: {e}")

            logger.info(
                f"Loaded promoted strategy: {artifact.strategy_name} ({artifact.artifact_id[:8]})"
            )
            return strategy

    def _write_manifest(self, manifest: PromotedStrategyManifest) -> None:
        """Write manifest to disk for operator inspection."""
        path = self.manifest_dir / f"{manifest.artifact_id}.json"
        try:
            path.write_text(manifest.to_json())
        except Exception as e:
            logger.error(f"Failed to write manifest {path}: {e}")

    def _watch_loop(self) -> None:
        """Background loop polling for new promotions."""
        while not self._stop_event.is_set():
            try:
                self._poll_for_new()
            except Exception as e:
                logger.error(f"RuntimeLoader poll failed: {e}")

            self._stop_event.wait(self.poll_interval)

    def _poll_for_new(self) -> None:
        """Poll promotion store for newly eligible artifacts.

        For each artifact in the store that is eligible for the current
        environment but not yet loaded, load and verify it.
        """
        if self.promotion_store is None:
            return  # No store configured — polling not possible

        env = self.config.environment.value.lower()
        try:
            eligible = self.promotion_store.list_eligible(env)
        except Exception as e:
            logger.error(f"RuntimeLoader poll: failed to list eligible artifacts: {e}")
            return

        for record in eligible:
            artifact_id = record.artifact_id
            with self._lock:
                if artifact_id in self._loaded:
                    continue

            # Load from artifact store
            artifact = self.artifact_store.get(artifact_id)
            if artifact is None:
                logger.warning(f"RuntimeLoader poll: promoted artifact {artifact_id} not found in store")
                continue

            try:
                promoted = self._load_artifact(artifact)
                logger.info(
                    f"RuntimeLoader poll: loaded newly eligible artifact {artifact_id} "
                    f"(stage={record.stage.value})"
                )
            except Exception as e:
                logger.error(f"RuntimeLoader poll: failed to load {artifact_id}: {e}")


# ── PromotionHook ───────────────────────────────────────────────────────


def on_promotion_to_production(
    lifecycle: ResearchLifecycle,
    artifact: StrategyArtifact,
    promotion_event: Any,
    loader: RuntimeLoader,
) -> PromotedStrategy:
    """
    Hook called when a strategy is promoted to PRODUCTION.

    This is the bridge from Research → Runtime. It:
    1. Creates the PromotedStrategyManifest
    2. Loads into RuntimeLoader
    3. Makes it available to DecisionAuthority
    """
    manifest = PromotedStrategyManifest.from_artifact(artifact, promotion_event)

    # Write manifest
    manifest_path = loader.manifest_dir / f"{artifact.artifact_id}.json"
    manifest_path.write_text(manifest.to_json())

    # Load into runtime
    strategy = PromotedStrategy(
        artifact=artifact,
        manifest=manifest,
        loaded_at=datetime.now(UTC),
    )

    with loader._lock:
        loader._loaded[artifact.artifact_id] = strategy

    logger.info(
        f"PromotionHook: {artifact.strategy_name} ({artifact.artifact_id[:8]}) "
        f"promoted to PRODUCTION by {promotion_event.actor}"
    )

    return strategy


__all__ = [
    "PromotedStrategy",
    "PromotedStrategyManifest",
    "RuntimeLoader",
    "on_promotion_to_production",
]
