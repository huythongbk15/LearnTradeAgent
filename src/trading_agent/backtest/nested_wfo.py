"""Nested Walk-Forward Optimization (Phase S3).

Implements expanding nested walk-forward with:
- Inner folds: parameter selection on train/validation
- Outer folds: OOS evaluation on test
- Purge/embargo gaps
- Experiment registry logging
- Statistical hardening (block bootstrap, PSR, DSR, PBO/CSCV)
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from collections.abc import Iterator
from dataclasses import asdict, dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

import numpy as np


class CellRunner(Protocol):
    """Protocol for cell execution callback.

    Matches the signature of ``run_cell`` with keyword-only arguments.
    Implementations may accept additional kwargs (e.g. ``signal_delay_bars``,
    ``timeout_seconds``) that ``run_cell`` supports.
    """

    def __call__(
        self,
        spec: EvaluationCellSpec,
        *,
        out_root: Path | None = None,
        start: int = 0,
        end: int | None = None,
        fresh: bool = True,
        measurement_start: int | None = None,
        measurement_end: int | None = None,
        **kwargs: Any,
    ) -> EvaluationArtifact: ...


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from trading_agent.backtest.tournament import (
    EvaluationCellSpec,
    SCENARIO_BASE,
    SCENARIO_DOUBLE,
    SCENARIO_SLIPPAGE_STRESS,
    run_cell,
    EvaluationArtifact,
    CostScenario,
)
from trading_agent.research.trials import (
    ExperimentRegistry,
    ExperimentSpec,
    search_space_hash,
    param_hash,
    TRIAL_PHASE_INNER_VALIDATION,
    TRIAL_PHASE_OUTER_OOS,
)
from trading_agent.alpha_research.stats import (
    block_bootstrap_sharpe_ci,
    deflated_sharpe_ratio,
    probabilistic_sharpe_ratio,
    probability_of_backtest_overfitting,
    series_stats,
    summarize_sharpe,
)
from trading_agent.regime import add_regime_indicators
from trading_agent.backtest.provenance import (
    CompletenessReport,
    ManifestValidator,
    attach_evaluation_identity,
    evaluation_identity,
    provenance_digest,
)


@dataclass(frozen=True)
class WFOSpec:
    """Nested WFO configuration for one strategy family."""

    strategy_id: str
    symbol: str
    timeframe: str = "1h"
    param_grid: dict[str, list[Any]] = field(default_factory=dict)
    cost_scenarios: tuple[CostScenario, ...] = (SCENARIO_BASE,)
    # Fold structure
    train_months: int = 12
    val_months: int = 3
    test_months: int = 3
    step_months: int = 3
    purge_bars: int = 0  # Will default to max(lookback, horizon)
    embargo_bars: int = 0  # Will default to max(lookback, horizon)
    # Legacy name kept for config compatibility. The S3 policy applies this
    # threshold to the aggregate pair-strategy outer-OOS sample, not per fold.
    min_trades_per_fold: int = 30
    min_oos_trades: int | None = None
    # Registry
    registry_path: str = "data/wfo/experiments.sqlite3"
    # Search space
    search_family: str = "default"
    evaluator_version: str = "v1"
    seed: int = 42
    # Synthetic evidence exercises the pipeline but is never promotion-eligible.
    evidence_class: str = "REAL_MARKET"

    def __post_init__(self) -> None:
        if self.evidence_class not in {"REAL_MARKET", "SYNTHETIC_TEST_ONLY"}:
            raise ValueError(f"unsupported evidence_class: {self.evidence_class}")
        if self.min_trades_per_fold < 1:
            raise ValueError("min_trades_per_fold must be positive")
        if self.min_oos_trades is not None and self.min_oos_trades < 1:
            raise ValueError("min_oos_trades must be positive when provided")

    @property
    def effective_min_oos_trades(self) -> int:
        """Versioned S3 aggregate OOS trade threshold."""
        return self.min_oos_trades or self.min_trades_per_fold

    def to_dict(self) -> dict[str, Any]:
        return {
            "strategy_id": self.strategy_id,
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "param_grid": self.param_grid,
            "cost_scenarios": [c.to_dict() for c in self.cost_scenarios],
            "train_months": self.train_months,
            "val_months": self.val_months,
            "test_months": self.test_months,
            "step_months": self.step_months,
            "purge_bars": self.purge_bars,
            "embargo_bars": self.embargo_bars,
            "min_trades_per_fold": self.min_trades_per_fold,
            "min_oos_trades": self.min_oos_trades,
            "registry_path": self.registry_path,
            "search_family": self.search_family,
            "evaluator_version": self.evaluator_version,
            "seed": self.seed,
            "evidence_class": self.evidence_class,
        }


@dataclass(frozen=True)
class WFOInnerResult:
    """Result of inner fold parameter selection."""

    fold_id: str
    train_start: int
    train_end: int
    val_start: int
    val_end: int
    best_params: dict[str, Any]
    best_val_sharpe: float
    val_metrics: dict[str, Any]
    n_trials: int
    candidate_metrics: list[dict[str, Any]]  # All tried params with metrics


@dataclass(frozen=True)
class WFOOuterResult:
    """Result of outer fold OOS evaluation."""

    fold_id: str
    test_start: int
    test_end: int
    params: dict[str, Any]
    test_metrics: dict[str, Any]
    execution_health: dict[str, Any]
    artifact: EvaluationArtifact | None


@dataclass(frozen=True)
class InnerSelectionFreeze:
    """Immutable record of inner fold parameter selection (STR-0304).

    Created after inner selection completes, before outer fold is evaluated.
    Hash binds the selected parameters to this specific fold.
    """

    fold_id: str
    strategy_id: str
    symbol: str
    timeframe: str
    best_params: dict[str, Any]
    best_val_sharpe: float
    inner_train_end: int
    inner_val_start: int
    inner_val_end: int
    search_space_hash: str
    candidate_count: int
    commit_sha: str = "unknown"
    data_manifest_sha: str = "unknown"
    feature_schema_hash: str = "unknown"
    evaluator_version: str = "unknown"
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    @property
    def freeze_id(self) -> str:
        """Content-addressed hash of the frozen selection."""
        payload = json.dumps(
            {
                "fold_id": self.fold_id,
                "strategy_id": self.strategy_id,
                "symbol": self.symbol,
                "timeframe": self.timeframe,
                "best_params": self.best_params,
                "best_val_sharpe": self.best_val_sharpe,
                "inner_train_end": self.inner_train_end,
                "inner_val_start": self.inner_val_start,
                "inner_val_end": self.inner_val_end,
                "search_space_hash": self.search_space_hash,
                "candidate_count": self.candidate_count,
                "commit_sha": self.commit_sha,
                "data_manifest_sha": self.data_manifest_sha,
                "feature_schema_hash": self.feature_schema_hash,
                "evaluator_version": self.evaluator_version,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        return f"sha256:{hashlib.sha256(payload.encode()).hexdigest()}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "fold_id": self.fold_id,
            "strategy_id": self.strategy_id,
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "best_params": self.best_params,
            "best_val_sharpe": self.best_val_sharpe,
            "inner_train_end": self.inner_train_end,
            "inner_val_start": self.inner_val_start,
            "inner_val_end": self.inner_val_end,
            "search_space_hash": self.search_space_hash,
            "candidate_count": self.candidate_count,
            "commit_sha": self.commit_sha,
            "data_manifest_sha": self.data_manifest_sha,
            "feature_schema_hash": self.feature_schema_hash,
            "evaluator_version": self.evaluator_version,
            "freeze_id": self.freeze_id,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "InnerSelectionFreeze":
        return cls(
            fold_id=data["fold_id"],
            strategy_id=data["strategy_id"],
            symbol=data["symbol"],
            timeframe=data["timeframe"],
            best_params=data["best_params"],
            best_val_sharpe=data["best_val_sharpe"],
            inner_train_end=data["inner_train_end"],
            inner_val_start=data["inner_val_start"],
            inner_val_end=data["inner_val_end"],
            search_space_hash=data["search_space_hash"],
            candidate_count=data["candidate_count"],
            commit_sha=data.get("commit_sha", "unknown"),
            data_manifest_sha=data.get("data_manifest_sha", "unknown"),
            feature_schema_hash=data.get("feature_schema_hash", "unknown"),
            evaluator_version=data.get("evaluator_version", "unknown"),
            created_at=data.get("created_at", datetime.now(UTC).isoformat()),
        )


@dataclass(frozen=True)
class WFOStudyManifest:
    """Content-addressed identity for one S3 study before outer windows open."""

    strategy_id: str
    symbol: str
    timeframe: str
    param_grid: dict[str, list[Any]]
    cost_scenarios: tuple[dict[str, Any], ...]
    fold_windows: tuple[dict[str, Any], ...]
    purge_bars: int
    embargo_bars: int
    min_oos_trades: int
    search_family: str
    evaluator_version: str
    training_contract: str
    seed: int
    commit_sha: str
    worktree_dirty: bool
    strategy_code_sha: str
    data_manifest_sha: str
    feature_schema_hash: str
    search_space_hash: str
    evidence_class: str

    @property
    def provenance_eligible(self) -> bool:
        return self.evidence_class == "REAL_MARKET" and not self.worktree_dirty

    def _identity_payload(self) -> dict[str, Any]:
        return {
            "strategy_id": self.strategy_id,
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "param_grid": self.param_grid,
            "cost_scenarios": list(self.cost_scenarios),
            "fold_windows": list(self.fold_windows),
            "purge_bars": self.purge_bars,
            "embargo_bars": self.embargo_bars,
            "min_oos_trades": self.min_oos_trades,
            "search_family": self.search_family,
            "evaluator_version": self.evaluator_version,
            "training_contract": self.training_contract,
            "seed": self.seed,
            "commit_sha": self.commit_sha,
            "worktree_dirty": self.worktree_dirty,
            "strategy_code_sha": self.strategy_code_sha,
            "data_manifest_sha": self.data_manifest_sha,
            "feature_schema_hash": self.feature_schema_hash,
            "search_space_hash": self.search_space_hash,
            "evidence_class": self.evidence_class,
        }

    @property
    def manifest_id(self) -> str:
        encoded = json.dumps(
            self._identity_payload(),
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode()
        return f"sha256:{hashlib.sha256(encoded).hexdigest()}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "manifest_id": self.manifest_id,
            **self._identity_payload(),
            "provenance_eligible": self.provenance_eligible,
        }


@dataclass(frozen=True)
class GateResult:
    """Structured result of a single hard gate evaluation (STR-0306/0309/0310).

    Each gate returns:
    - gate_id: unique identifier for the gate
    - policy_version: version of the gate policy (e.g., "v1")
    - observed_value: the actual metric value observed
    - threshold: the threshold for PASS/FAIL
    - comparison: comparison operator (">", ">=", "<", "<=", "==", "!=")
    - verdict: "PASS" | "FAIL" | "INVALID"
    - reason: human-readable explanation
    - evidence_artifact: path or reference to evidence artifact
    """

    gate_id: str
    policy_version: str
    observed_value: float | None
    threshold: float
    comparison: str
    verdict: str  # "PASS" | "FAIL" | "INVALID"
    reason: str
    evidence_artifact: str | None = None

    def is_pass(self) -> bool:
        return self.verdict == "PASS"

    def is_fail(self) -> bool:
        return self.verdict in ("FAIL", "INVALID")  # INVALID treated as FAIL

    def to_dict(self) -> dict[str, Any]:
        """Serialize to JSON-compliant dict (no NaN/inf)."""
        obs = self.observed_value
        observed_value: str | float
        if obs is None or not (isinstance(obs, float) and abs(obs) != float("inf")):
            # Handle None, nan, inf
            if obs is None:
                observed_value = "null"
            elif obs == float("inf"):
                observed_value = "inf"
            elif obs == float("-inf"):
                observed_value = "-inf"
            else:  # nan
                observed_value = "nan"
        else:
            observed_value = obs

        return {
            "gate_id": self.gate_id,
            "policy_version": self.policy_version,
            "observed_value": observed_value,
            "threshold": self.threshold,
            "comparison": self.comparison,
            "verdict": self.verdict,
            "reason": self.reason,
            "evidence_artifact": self.evidence_artifact,
        }


@dataclass(frozen=True)
class FormalNoTradeArtifact:
    """Formal NO_TRADE result artifact (STR-0310).

    Produced when no candidate passes all hard gates. Contains full provenance
    for audit and downstream consumption. This is NOT an error — it is a
    valid, auditable conclusion that no strategy has sufficient evidence.

    Attributes:
        no_trade_id: Content-addressed hash of this artifact
        candidate_set: List of candidate strategy IDs that were evaluated
        gate_results: All gate results for each candidate
        gate_failures: Aggregated gate failures across all candidates
        best_candidate: Strategy ID of the best candidate (by median Sharpe)
        best_candidate_metrics: Metrics of the best candidate
        registry_identity: Experiment registry identity (experiment_id, hashes)
        policy_version: Version of the gate policy used
        policy_thresholds: Dictionary of gate thresholds used
        commit_sha: Git commit SHA at evaluation time
        data_manifest_sha: Data manifest SHA
        feature_schema_hash: Feature schema hash
        search_space_hash: Search space hash
        evaluation_timestamp: ISO timestamp of evaluation
        evaluation_duration_sec: Time taken for full evaluation
        notes: Additional context (e.g., "all candidates failed DSR gate")
    """

    no_trade_id: str = field(init=False)
    candidate_set: list[str]
    gate_results: dict[str, list[GateResult]]  # candidate_id -> gate results
    gate_failures: dict[str, list[str]]  # candidate_id -> failed gate IDs
    best_candidate: str | None
    best_candidate_metrics: dict[str, Any] | None
    registry_identity: dict[str, str]
    policy_version: str
    policy_thresholds: dict[str, float]
    commit_sha: str
    data_manifest_sha: str
    feature_schema_hash: str
    search_space_hash: str
    evaluation_timestamp: str
    evaluation_duration_sec: float
    notes: str = ""

    def __post_init__(self) -> None:
        # Compute content hash
        payload = json.dumps(
            {
                "candidate_set": self.candidate_set,
                "gate_results": {
                    k: [g.to_dict() if hasattr(g, "to_dict") else g.__dict__ for g in v]
                    for k, v in self.gate_results.items()
                },
                "gate_failures": self.gate_failures,
                "best_candidate": self.best_candidate,
                "best_candidate_metrics": self.best_candidate_metrics,
                "registry_identity": self.registry_identity,
                "policy_version": self.policy_version,
                "policy_thresholds": self.policy_thresholds,
                "commit_sha": self.commit_sha,
                "data_manifest_sha": self.data_manifest_sha,
                "feature_schema_hash": self.feature_schema_hash,
                "search_space_hash": self.search_space_hash,
                "evaluation_timestamp": self.evaluation_timestamp,
                "evaluation_duration_sec": self.evaluation_duration_sec,
                "notes": self.notes,
            },
            sort_keys=True,
            separators=(",", ":"),
            # allow_nan=True: an infinite profit factor (e.g. a candidate that
            # never took a losing OOS trade) is valid evidence; json serialises
            # it deterministically as "Infinity" so the content hash is stable.
            allow_nan=True,
        ).encode("utf-8")
        object.__setattr__(
            self, "no_trade_id", f"sha256:{hashlib.sha256(payload).hexdigest()}"
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "no_trade_id": self.no_trade_id,
            "candidate_set": self.candidate_set,
            "gate_results": {
                k: [
                    {
                        "gate_id": g.gate_id,
                        "policy_version": g.policy_version,
                        "observed_value": g.observed_value,
                        "threshold": g.threshold,
                        "comparison": g.comparison,
                        "verdict": g.verdict,
                        "reason": g.reason,
                        "evidence_artifact": g.evidence_artifact,
                    }
                    for g in v
                ]
                for k, v in self.gate_results.items()
            },
            "gate_failures": self.gate_failures,
            "best_candidate": self.best_candidate,
            "best_candidate_metrics": self.best_candidate_metrics,
            "registry_identity": self.registry_identity,
            "policy_version": self.policy_version,
            "policy_thresholds": self.policy_thresholds,
            "commit_sha": self.commit_sha,
            "data_manifest_sha": self.data_manifest_sha,
            "feature_schema_hash": self.feature_schema_hash,
            "search_space_hash": self.search_space_hash,
            "evaluation_timestamp": self.evaluation_timestamp,
            "evaluation_duration_sec": self.evaluation_duration_sec,
            "notes": self.notes,
        }

    def verify_integrity(self) -> bool:
        """Verify no_trade_id matches content hash (tamper detection)."""
        reconstructed = FormalNoTradeArtifact(
            candidate_set=self.candidate_set,
            gate_results=self.gate_results,
            gate_failures=self.gate_failures,
            best_candidate=self.best_candidate,
            best_candidate_metrics=self.best_candidate_metrics,
            registry_identity=self.registry_identity,
            policy_version=self.policy_version,
            policy_thresholds=self.policy_thresholds,
            commit_sha=self.commit_sha,
            data_manifest_sha=self.data_manifest_sha,
            feature_schema_hash=self.feature_schema_hash,
            search_space_hash=self.search_space_hash,
            evaluation_timestamp=self.evaluation_timestamp,
            evaluation_duration_sec=self.evaluation_duration_sec,
            notes=self.notes,
        )
        return reconstructed.no_trade_id == self.no_trade_id


@dataclass(frozen=True)
class FinalHoldoutManifest:
    """Independent final holdout manifest (STR-0309).

    The final holdout is a period of data that is NEVER used during any part of
    parameter selection, inner validation, or outer test. It is frozen at the
    start of the study and only opened ONCE after all WFO selection is complete.

    This prevents multiple-testing leakage through iterative tuning: the holdout
    is a truly independent confirmation of the selected strategy's edge.

    Attributes:
        holdout_id: Content-addressed hash of the manifest
        strategy_id: Strategy family being evaluated
        symbol: Trading symbol
        timeframe: Bar timeframe
        holdout_start_bar: First bar index of holdout window (int, not date)
        holdout_end_bar: Last bar index of holdout window (int, not date)
        data_manifest_sha: SHA of the data used (must match training data manifest)
        feature_schema_hash: Feature schema hash (must match)
        freeze_timestamp: When the holdout was frozen (before any tuning)
        frozen_by: Actor/system that froze the holdout
        opened: Whether the holdout has been opened (must be False until selection done)
        opened_at: When the holdout was opened (None if not yet)
        opened_by: Who opened it (None if not yet)
        commit_sha_at_freeze: Git commit at freeze time (immutable reference)
        notes: Additional context
    """

    holdout_id: str = field(init=False)
    strategy_id: str
    symbol: str
    timeframe: str
    holdout_start_bar: int
    holdout_end_bar: int
    data_manifest_sha: str
    feature_schema_hash: str
    freeze_timestamp: str
    frozen_by: str = "research_system"
    opened: bool = False
    opened_at: str | None = None
    opened_by: str | None = None
    commit_sha_at_freeze: str = "unknown"
    notes: str = ""

    def __post_init__(self) -> None:
        if self.holdout_start_bar >= self.holdout_end_bar:
            raise ValueError(
                f"holdout_start_bar ({self.holdout_start_bar}) must be < "
                f"holdout_end_bar ({self.holdout_end_bar})"
            )
        payload = json.dumps(
            {
                "strategy_id": self.strategy_id,
                "symbol": self.symbol,
                "timeframe": self.timeframe,
                "holdout_start_bar": self.holdout_start_bar,
                "holdout_end_bar": self.holdout_end_bar,
                "data_manifest_sha": self.data_manifest_sha,
                "feature_schema_hash": self.feature_schema_hash,
                "freeze_timestamp": self.freeze_timestamp,
                "frozen_by": self.frozen_by,
                "commit_sha_at_freeze": self.commit_sha_at_freeze,
                "notes": self.notes,
            },
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        object.__setattr__(
            self, "holdout_id", f"sha256:{hashlib.sha256(payload).hexdigest()}"
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "holdout_id": self.holdout_id,
            "strategy_id": self.strategy_id,
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "holdout_start_bar": self.holdout_start_bar,
            "holdout_end_bar": self.holdout_end_bar,
            "data_manifest_sha": self.data_manifest_sha,
            "feature_schema_hash": self.feature_schema_hash,
            "freeze_timestamp": self.freeze_timestamp,
            "frozen_by": self.frozen_by,
            "opened": self.opened,
            "opened_at": self.opened_at,
            "opened_by": self.opened_by,
            "commit_sha_at_freeze": self.commit_sha_at_freeze,
            "notes": self.notes,
        }

    def verify_integrity(self) -> bool:
        """Verify holdout_id matches content hash (tamper detection)."""
        reconstructed = FinalHoldoutManifest(
            strategy_id=self.strategy_id,
            symbol=self.symbol,
            timeframe=self.timeframe,
            holdout_start_bar=self.holdout_start_bar,
            holdout_end_bar=self.holdout_end_bar,
            data_manifest_sha=self.data_manifest_sha,
            feature_schema_hash=self.feature_schema_hash,
            freeze_timestamp=self.freeze_timestamp,
            frozen_by=self.frozen_by,
            opened=self.opened,
            opened_at=self.opened_at,
            opened_by=self.opened_by,
            commit_sha_at_freeze=self.commit_sha_at_freeze,
            notes=self.notes,
        )
        return reconstructed.holdout_id == self.holdout_id

    def open(self, actor: str, now: str | None = None) -> FinalHoldoutManifest:
        """Return a new manifest with opened=True (one-shot, immutable)."""
        if self.opened:
            raise ValueError(f"Holdout already opened at {self.opened_at}")
        now = now or datetime.now(UTC).isoformat()
        return FinalHoldoutManifest(
            strategy_id=self.strategy_id,
            symbol=self.symbol,
            timeframe=self.timeframe,
            holdout_start_bar=self.holdout_start_bar,
            holdout_end_bar=self.holdout_end_bar,
            data_manifest_sha=self.data_manifest_sha,
            feature_schema_hash=self.feature_schema_hash,
            freeze_timestamp=self.freeze_timestamp,
            frozen_by=self.frozen_by,
            opened=True,
            opened_at=now,
            opened_by=actor,
            commit_sha_at_freeze=self.commit_sha_at_freeze,
            notes=self.notes,
        )

    def save(self, path: Path) -> None:
        """Save manifest to disk (atomic write via temp file + rename)."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_suffix(".json.tmp")
        tmp_path.write_text(
            json.dumps(self.to_dict(), indent=2, allow_nan=False),
            encoding="utf-8",
        )
        os.replace(tmp_path, path)

    @classmethod
    def load(cls, path: Path) -> FinalHoldoutManifest:
        """Load manifest from disk and verify integrity (tamper detection).

        Compares the stored ``holdout_id`` (from the file) against the
        recomputed hash of all immutable fields.  A mismatch means the
        content was tampered after the manifest was created.
        """
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(f"Holdout manifest not found: {path}")
        d = json.loads(path.read_text(encoding="utf-8"))
        stored_holdout_id = d.get("holdout_id")
        manifest = cls(
            strategy_id=d["strategy_id"],
            symbol=d["symbol"],
            timeframe=d["timeframe"],
            holdout_start_bar=d["holdout_start_bar"],
            holdout_end_bar=d["holdout_end_bar"],
            data_manifest_sha=d["data_manifest_sha"],
            feature_schema_hash=d["feature_schema_hash"],
            freeze_timestamp=d["freeze_timestamp"],
            frozen_by=d.get("frozen_by", "research_system"),
            opened=d.get("opened", False),
            opened_at=d.get("opened_at"),
            opened_by=d.get("opened_by"),
            commit_sha_at_freeze=d.get("commit_sha_at_freeze", "unknown"),
            notes=d.get("notes", ""),
        )
        # Compare stored holdout_id with recomputed — detects tampering of
        # any immutable field (strategy_id, data_manifest_sha, window, etc.)
        if stored_holdout_id is not None and stored_holdout_id != manifest.holdout_id:
            raise ValueError(
                f"Holdout manifest integrity check failed: stored holdout_id "
                f"{stored_holdout_id} != computed {manifest.holdout_id}"
            )
        if not manifest.verify_integrity():
            raise ValueError(
                f"Holdout manifest integrity check failed: {manifest.holdout_id}"
            )
        return manifest


@dataclass
class HoldoutAccessGuard:
    """Process-restart-resilient gate for final holdout access (R04a / S3-7).

    Re-loads the manifest from disk on init **and** on every ``open()`` call so
    that a process restart — or an external tamper — is always detected.

    Usage::

        # --- producer side (research) ---
        with HoldoutAccessGuard(path) as guard:
            opened = guard.open(actor="research_system")

        # --- consumer side (live execution) ---
        with HoldoutAccessGuard(path) as guard:
            if not guard.opened:
                raise RuntimeError("Holdout not yet opened — no selected params.")
            manifest = guard.manifest
    """

    manifest_path: Path
    _manifest: FinalHoldoutManifest | None = field(default=None, init=False)

    def __post_init__(self) -> None:
        self.manifest_path = Path(self.manifest_path)
        self.reload()

    # -- lifecycle ---------------------------------------------------------

    def reload(self) -> None:
        """Re-read manifest from disk and verify integrity (tamper detection)."""
        self._manifest = FinalHoldoutManifest.load(self.manifest_path)

    def __enter__(self) -> "HoldoutAccessGuard":
        return self

    def __exit__(self, *exc: object) -> None:
        pass

    # -- properties --------------------------------------------------------

    @property
    def manifest(self) -> FinalHoldoutManifest:
        assert self._manifest is not None
        return self._manifest

    @property
    def opened(self) -> bool:
        assert self._manifest is not None
        return self._manifest.opened

    # -- actions -----------------------------------------------------------

    def open(self, actor: str) -> FinalHoldoutManifest:
        """Atomically mark the holdout as opened (one-shot, fail-closed).

        Re-loads from disk first to ensure ``opened`` reflects persisted state,
        then performs an **atomic** save of the opened manifest.  On success the
        in-memory ``_manifest`` is updated so subsequent calls in the same
        process see the new state without another disk read.
        """
        # Re-load to catch the latest persisted state (process-restart safety)
        self.reload()
        assert self._manifest is not None  # reload() always populates this
        if self._manifest.opened:
            raise ValueError(
                f"Holdout already opened at {self._manifest.opened_at} "
                f"by {self._manifest.opened_by}. Cannot re-open (fail-closed)."
            )
        opened = self._manifest.open(actor=actor)
        opened.save(self.manifest_path)          # atomic write
        self._manifest = opened
        return opened


def _create_final_holdout_manifest(
    spec: WFOSpec,
    n_bars: int,
    data_manifest_sha: str,
    feature_schema_hash: str,
    holdout_bars: int = 0,
) -> FinalHoldoutManifest:
    """Create a final holdout manifest (STR-0309).

    The holdout window is the LAST `holdout_bars` bars of the dataset, completely
    separate from the WFO folds. It is frozen immediately and never opened during
    selection.

    Args:
        spec: WFO spec
        n_bars: Total number of bars in dataset
        data_manifest_sha: SHA of data manifest (must match training)
        feature_schema_hash: Feature schema hash (must match)
        holdout_bars: Number of bars for holdout (default: 10% of data or min 3 months)

    Returns:
        FinalHoldoutManifest (frozen, not yet opened)
    """
    if holdout_bars <= 0:
        # Default: last 10% of data, minimum 3 months (~2160 bars for 1h)
        holdout_bars = max(int(n_bars * 0.10), 2160)

    holdout_start = n_bars - holdout_bars
    if holdout_start <= 0:
        raise ValueError(
            f"Not enough data for holdout: n_bars={n_bars}, holdout_bars={holdout_bars}"
        )

    commit_sha = _compute_commit_sha()

    return FinalHoldoutManifest(
        strategy_id=spec.strategy_id,
        symbol=spec.symbol,
        timeframe=spec.timeframe,
        holdout_start_bar=holdout_start,
        holdout_end_bar=n_bars,
        data_manifest_sha=data_manifest_sha,
        feature_schema_hash=feature_schema_hash,
        freeze_timestamp=datetime.now(UTC).isoformat(),
        frozen_by="research_system",
        commit_sha_at_freeze=commit_sha,
        notes=f"Final holdout: last {holdout_bars} bars ({holdout_start}-{n_bars}) of {n_bars} total",
    )


def _timestamp_to_bar(df, ts: datetime) -> int:
    """Return first bar index whose timestamp >= ts (clamped to [0, n-1])."""
    import numpy as np

    col = df.get_column("timestamp")
    arr = col.to_pandas().values  # numpy datetime64 (tz-naive UTC)
    # Arrays are tz-naive UTC; strip tzinfo from ts to avoid comparison warnings.
    target = np.datetime64(ts.replace(tzinfo=None))
    idx = int(np.searchsorted(arr, target))
    return max(0, min(idx, df.height - 1))


def _last_bar_le_or_before(df, ts: datetime) -> int:
    """Return last bar index whose timestamp <= ts (clamped to [0, n-1])."""
    import numpy as np

    col = df.get_column("timestamp")
    arr = col.to_pandas().values
    target = np.datetime64(ts.replace(tzinfo=None))
    idx = int(np.searchsorted(arr, target, side="right")) - 1
    return max(0, min(idx, df.height - 1))


def _resolve_frozen_holdout_window(df, spec: WFOSpec) -> tuple[int, int] | None:
    """Resolve the frozen holdout window (STR-0309) from research_manifest.json.

    Returns (holdout_start_bar, holdout_end_bar) mapped into the loaded dataset,
    or None if no frozen manifest exists (holdout disabled).

    Fail-closed: if a manifest exists but the window is invalid/empty, returns
    None (and the caller must decide whether to proceed).
    """
    from trading_agent.alpha_research.holdout import (
        HoldoutError,
        holdout_window,
        load_manifest,
    )

    try:
        manifest = load_manifest()
    except (FileNotFoundError, HoldoutError) as exc:
        print(f"[STR-0309] No usable frozen research manifest: {exc}; holdout disabled")
        return None

    start_ts, end_ts = holdout_window(manifest)
    start_bar = _timestamp_to_bar(df, start_ts)
    end_bar = _last_bar_le_or_before(df, end_ts)
    if end_bar <= start_bar:
        print(
            f"[STR-0309] Frozen holdout window {start_ts.date()}..{end_ts.date()} "
            f"maps to empty bar range [{start_bar}..{end_bar}]; holdout disabled"
        )
        return None
    return (start_bar, end_bar)


def _guard_fold_against_holdout(
    fold: NestedFold,
    df,
    descriptor,
    holdout_start_bar: int,
    holdout_end_bar: int,
) -> None:
    """Fail-closed guard: reject any fold whose data window touches the holdout.

    The data window that actually feeds the model spans from the warmup start
    (before inner_train_start, for indicator initialization) through the outer
    test end. If ANY of that overlaps the frozen holdout, raise HoldoutError —
    the holdout must never influence selection or OOS evaluation.
    """
    from datetime import UTC

    from trading_agent.alpha_research.holdout import (
        HoldoutError,
        guard_training_window,
    )

    warmup = getattr(descriptor, "warmup_bars", 0)
    buffer_bars = 100
    sim_start = max(0, fold.inner_train_start - warmup - buffer_bars)
    end_bar = min(fold.outer_test_end, df.height - 1)

    col = df.get_column("timestamp")
    start_ts = col[sim_start]
    end_ts = col[end_bar]
    # Normalize to tz-aware UTC to match holdout_window semantics
    if getattr(start_ts, "tzinfo", None) is None:
        start_ts = start_ts.replace(tzinfo=UTC)
    if getattr(end_ts, "tzinfo", None) is None:
        end_ts = end_ts.replace(tzinfo=UTC)

    try:
        guard_training_window(start=start_ts, end=end_ts)
    except HoldoutError as exc:
        raise HoldoutError(
            f"Fold {fold.fold_id} data window overlaps frozen holdout "
            f"(bars {sim_start}..{end_bar}): {exc}"
        ) from exc


def run_final_holdout(
    spec: WFOSpec,
    selected_params: dict[str, Any],
    manifest: FinalHoldoutManifest,
    *,
    out_root: Path | None = None,
    actor: str = "research_system",
) -> dict[str, Any]:
    """Run the final holdout evaluation (STR-0309).

    This is a ONE-SHOT evaluation on the frozen holdout window. The holdout must
    NOT have been opened before. After this runs, the manifest is marked opened
    and cannot be re-run (fail-closed).

    Args:
        spec: WFO spec
        selected_params: Parameters selected by WFO (frozen)
        manifest: Frozen FinalHoldoutManifest
        out_root: Output root for artifacts
        actor: Who is opening the holdout (for audit)

    Returns:
        dict with holdout metrics, execution health, and manifest reference
    """
    out_root = Path(out_root) if out_root else ROOT / "data" / "backtests" / "wfo"
    out_root.mkdir(parents=True, exist_ok=True)
    manifest_path = out_root / f"holdout_{manifest.holdout_id[:16]}.json"

    # R04a: Use HoldoutAccessGuard for process-restart-resilient, atomic access.
    # The guard re-loads from disk on init to detect prior openings or tampering.
    with HoldoutAccessGuard(manifest_path) as guard:
        opened_manifest = guard.open(actor=actor)

    from trading_agent.backtest.tournament import _research_env
    from trading_agent.strategies.canonical.candidates import build_default_registry

    registry_canonical = build_default_registry()
    _, adapter = registry_canonical.get(spec.strategy_id, environment=_research_env())

    # Get cost scenario (use default or from selected params)
    cost_scenario_name = selected_params.get("cost_scenario", "1x")
    from trading_agent.backtest.tournament import SCENARIO_BASE

    cost_scenario = SCENARIO_BASE  # Use base scenario for holdout

    test_params = {k: v for k, v in selected_params.items() if k != "cost_scenario"}

    # Run evaluation on holdout window
    spec_holdout = EvaluationCellSpec(
        strategy_id=spec.strategy_id,
        symbol=spec.symbol,
        timeframe=spec.timeframe,
        params=test_params,
        cost_scenario=cost_scenario,
    )

    artifact = run_cell(
        spec_holdout,
        out_root=out_root,
        start=manifest.holdout_start_bar,
        end=manifest.holdout_end_bar,
        fresh=True,
        measurement_start=manifest.holdout_start_bar,
        measurement_end=manifest.holdout_end_bar,
    )

    if artifact.status != "COMPLETED":
        return {
            "holdout_id": manifest.holdout_id,
            "status": "FAILED",
            "metrics": {},
            "execution_health": {},
            "manifest_path": str(manifest_path),
            "error": f"Holdout evaluation failed: {artifact.status}",
        }

    return {
        "holdout_id": manifest.holdout_id,
        "status": "COMPLETED",
        "metrics": artifact.metrics,
        "execution_health": artifact.execution_health,
        "manifest_path": str(manifest_path),
        "holdout_window": {
            "start_bar": manifest.holdout_start_bar,
            "end_bar": manifest.holdout_end_bar,
        },
    }


@dataclass(frozen=True)
class WFOResult:
    """Complete nested WFO result for one strategy × symbol."""

    spec: WFOSpec
    inner_results: list[WFOInnerResult]
    outer_results: list[WFOOuterResult]
    inner_selection_freezes: list[InnerSelectionFreeze]  # STR-0304: freeze before outer
    # Aggregate statistics
    aggregate_metrics: dict[str, Any]
    statistical_hardening: dict[str, Any]
    # Hard gate pass/fail - structured results
    gate_results: list[GateResult]
    passes_hard_gates: bool
    gate_failures: list[str]
    # Formal NO_TRADE artifact (STR-0310) — present only if passes_hard_gates=False
    no_trade_artifact: FormalNoTradeArtifact | None = None
    # Final holdout result (STR-0309) — one-shot independent confirmation
    final_holdout: dict[str, Any] | None = None
    # Trial accounting
    trial_counts: dict[str, Any] = field(default_factory=dict)
    study_manifest: WFOStudyManifest | None = None
    # R03: manifest validation result (completeness / tamper detection).
    # ``None`` when the consumer did not request validation (e.g. synthetic
    # evidence runs). When set, is_complete=False MUST be treated as fail-closed.
    completeness_report: CompletenessReport | None = None
    # R03: aggregate provenance digest, computed from the canonical evaluation
    # identity. Binds the decision artifact to the evidence that produced it.
    provenance_digest: str = ""
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    def to_dict(self) -> dict[str, Any]:
        return {
            "spec": self.spec.to_dict(),
            "inner_results": [asdict(r) for r in self.inner_results],
            "outer_results": [
                {
                    "fold_id": r.fold_id,
                    "test_start": r.test_start,
                    "test_end": r.test_end,
                    "test_metrics": r.test_metrics,
                    "params": r.params,
                    "artifact": r.artifact.to_dict() if r.artifact else None,
                }
                for r in self.outer_results
            ],
            "inner_selection_freezes": [
                f.to_dict() for f in self.inner_selection_freezes
            ],
            "aggregate_metrics": self.aggregate_metrics,
            "statistical_hardening": self.statistical_hardening,
            "gate_results": [asdict(g) for g in self.gate_results],
            "passes_hard_gates": self.passes_hard_gates,
            "gate_failures": self.gate_failures,
            "no_trade_artifact": self.no_trade_artifact.to_dict()
            if self.no_trade_artifact
            else None,
            "final_holdout": self.final_holdout,
            "trial_counts": self.trial_counts,
            "study_manifest": self.study_manifest.to_dict()
            if self.study_manifest
            else None,
            # R03: completeness_report must be exported so consumers can verify
            # the evidence passed manifest validation. When this field is
            # absent or is_complete=False, the artifact MUST NOT be promoted.
            "completeness_report": asdict(self.completeness_report)
            if self.completeness_report
            else None,
            # R03: provenance_digest binds the decision to canonical evaluation
            # identity — must be exported for content-addressed verification.
            "provenance_digest": self.provenance_digest,
            "created_at": self.created_at,
        }


@dataclass(frozen=True)
class PortfolioGatePolicy:
    """Content-addressed portfolio gate policy (S3-8 / R07).

    Defines ALL portfolio-level hard gate thresholds in a single, versioned,
    JSON-serializable object.  The ``policy_id`` is a SHA-256 of the immutable
    threshold payload — tampering with any threshold changes the id, making it
    auditable.

    Gates (all fail-closed unless stated):
        - positive_pairs_pct_min  — min % of pairs with positive OOS return
        - median_pair_return_min  — min median pair OOS return (%)
        - contribution_concentration_max — max % any single positive pair contributes
        - aggregate_trades_min    — min total OOS trades across portfolio
        - all_members_pass        — every member pair/strategy must pass its own gates
    """

    policy_version: str = "portfolio-v1"
    positive_pairs_pct_min: float = 60.0
    median_pair_return_min: float = 0.0
    contribution_concentration_max: float = 35.0
    aggregate_trades_min: float = 200.0
    all_members_pass: bool = True

    @property
    def policy_id(self) -> str:
        """Content-addressed hash of immutable policy fields."""
        payload = json.dumps(
            {k: v for k, v in asdict(self).items()},
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        return f"sha256:{hashlib.sha256(payload).hexdigest()}"

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "policy_id": self.policy_id}

    def verify_integrity(self) -> bool:
        """Verify policy_id matches content (tamper detection)."""
        return self.policy_id == PortfolioGatePolicy(*self.__dict__.values()).policy_id


@dataclass(frozen=True)
class PortfolioGateReport:
    """Structured, content-addressed report of portfolio-level gate evaluations (R07).

    Wraps the policy used, all gate results, and the final verdict.
    The ``artifact_id`` binds the policy, gate results, and verdict into a
    single content-addressed fingerprint for audit and downstream loading.
    """

    policy: PortfolioGatePolicy
    gate_results: list[GateResult]
    verdict: str
    aggregate_metrics: dict[str, Any]
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    @property
    def artifact_id(self) -> str:
        payload = json.dumps(
            {
                "policy_id": self.policy.policy_id,
                "policy": self.policy.to_dict(),
                "gate_results": [g.to_dict() for g in self.gate_results],
                "verdict": self.verdict,
                "aggregate_metrics": self.aggregate_metrics,
            },
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=True,
        ).encode("utf-8")
        return f"sha256:{hashlib.sha256(payload).hexdigest()}"

    @property
    def passes_hard_gates(self) -> bool:
        return all(g.is_pass() for g in self.gate_results)

    def to_dict(self) -> dict[str, Any]:
        return {
            "artifact_id": self.artifact_id,
            "policy": self.policy.to_dict(),
            "gate_results": [g.to_dict() for g in self.gate_results],
            "verdict": self.verdict,
            "passes_hard_gates": self.passes_hard_gates,
            "aggregate_metrics": self.aggregate_metrics,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PortfolioGateReport:
        """Reconstruct from serialized dict. Raises ValueError if tampered."""
        policy_data = data["policy"]
        policy = PortfolioGatePolicy(
            policy_version=policy_data.get("policy_version", "portfolio-v1"),
            positive_pairs_pct_min=policy_data.get("positive_pairs_pct_min", 60.0),
            median_pair_return_min=policy_data.get("median_pair_return_min", 0.0),
            contribution_concentration_max=policy_data.get("contribution_concentration_max", 35.0),
            aggregate_trades_min=policy_data.get("aggregate_trades_min", 200.0),
            all_members_pass=policy_data.get("all_members_pass", True),
        )
        gate_results = [
            GateResult(
                gate_id=g["gate_id"],
                policy_version=g["policy_version"],
                observed_value=(
                    None
                    if g["observed_value"] in ("null", "nan", "inf", "-inf")
                    else g["observed_value"]
                ),
                threshold=g["threshold"],
                comparison=g["comparison"],
                verdict=g["verdict"],
                reason=g["reason"],
                evidence_artifact=g.get("evidence_artifact"),
            )
            for g in data["gate_results"]
        ]
        report = cls(
            policy=policy,
            gate_results=gate_results,
            verdict=data["verdict"],
            aggregate_metrics=data["aggregate_metrics"],
            created_at=data.get("created_at", datetime.now(UTC).isoformat()),
        )
        # Verify stored artifact_id matches computed (tamper detection)
        stored_id = data.get("artifact_id")
        if stored_id is not None and stored_id != report.artifact_id:
            raise ValueError(
                f"PortfolioGateReport integrity check failed: stored {stored_id} "
                f"!= computed {report.artifact_id}"
            )
        return report

    def verify_integrity(self) -> bool:
        """Verify artifact_id matches content (tamper detection)."""
        return self.artifact_id == PortfolioGateReport(
            policy=self.policy,
            gate_results=self.gate_results,
            verdict=self.verdict,
            aggregate_metrics=self.aggregate_metrics,
            created_at=self.created_at,
        ).artifact_id


@dataclass(frozen=True)
class WFOPortfolioResult:
    """Portfolio-level selection decision over pair/strategy WFO results."""

    results: list[WFOResult]
    aggregate_metrics: dict[str, Any]
    gate_results: list[GateResult]
    passes_hard_gates: bool
    verdict: str
    gate_report: PortfolioGateReport | None = None
    no_trade_artifact: FormalNoTradeArtifact | None = None
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    def __iter__(self) -> Iterator[WFOResult]:
        return iter(self.results)

    def __len__(self) -> int:
        return len(self.results)

    @property
    def artifact_id(self) -> str:
        payload = {
            "result_artifacts": [
                [
                    outer.artifact.artifact_id
                    for outer in result.outer_results
                    if outer.artifact
                ]
                for result in self.results
            ],
            "aggregate_metrics": self.aggregate_metrics,
            "gate_results": [gate.to_dict() for gate in self.gate_results],
            "passes_hard_gates": self.passes_hard_gates,
            "verdict": self.verdict,
        }
        encoded = json.dumps(
            payload, sort_keys=True, separators=(",", ":"), allow_nan=True
        ).encode()
        return f"sha256:{hashlib.sha256(encoded).hexdigest()}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "artifact_id": self.artifact_id,
            "verdict": self.verdict,
            "passes_hard_gates": self.passes_hard_gates,
            "aggregate_metrics": self.aggregate_metrics,
            "gate_results": [gate.to_dict() for gate in self.gate_results],
            "gate_report": self.gate_report.to_dict()
            if self.gate_report is not None
            else None,
            "members": [
                {
                    "strategy_id": result.spec.strategy_id,
                    "symbol": result.spec.symbol,
                    "passes_hard_gates": result.passes_hard_gates,
                    "gate_failures": result.gate_failures,
                    "aggregate_metrics": result.aggregate_metrics,
                    "final_holdout": result.final_holdout,
                    "study_manifest_id": result.study_manifest.manifest_id
                    if result.study_manifest is not None
                    else None,
                }
                for result in self.results
            ],
            "no_trade_artifact": self.no_trade_artifact.to_dict()
            if self.no_trade_artifact is not None
            else None,
            "created_at": self.created_at,
        }

    def verify_integrity(self) -> bool:
        """Verify all embedded artifacts are internally consistent (R07)."""
        if self.gate_report is not None:
            if not self.gate_report.verify_integrity():
                return False
            # Cross-check: verdict and passes flag must agree
            if self.gate_report.verdict != self.verdict:
                return False
            if self.gate_report.passes_hard_gates != self.passes_hard_gates:
                return False
        # R07: no_trade_artifact must also be tamper-evident
        if self.no_trade_artifact is not None:
            if not self.no_trade_artifact.verify_integrity():
                return False
        return True


def _default_purge_embargo(descriptor) -> tuple[int, int]:
    """Default purge/embargo based on strategy lookback and execution horizon."""
    lookback = descriptor.warmup_bars
    # Execution horizon: time from signal to protective stop fill
    # Conservative: assume 1 bar for entry + SL/TP resolution
    horizon = 2
    gap = max(lookback, horizon)
    return gap, gap


def _generate_param_combinations(
    param_grid: dict[str, list[Any]],
) -> list[dict[str, Any]]:
    """Generate all parameter combinations from grid."""
    import itertools

    keys = list(param_grid.keys())
    values = list(param_grid.values())
    return [dict(zip(keys, combo)) for combo in itertools.product(*values)]


def _coerce_num(v: Any) -> float | None:
    """Coerce a metric value to float for aggregation.

    ``_artifact_from_report`` intentionally stores an infinite profit factor as
    the string ``"inf"`` (STR-0209: distinguish from a missing/zero value).
    Downstream ``np.median``/``np.mean`` would raise on a mixed str/float list,
    so coerce ``"inf"``/``"-inf"`` back to ``float('inf')``; return ``None`` for
    anything else that is not numeric.
    """
    if v is None:
        return None
    if isinstance(v, str):
        try:
            return float(v)
        except ValueError:
            return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _bars_per_month(timeframe: str) -> int:
    """Approximate bars per month for given timeframe."""
    if timeframe == "1h":
        return 24 * 30  # 720
    elif timeframe == "4h":
        return 6 * 30  # 180
    elif timeframe == "1d":
        return 30
    else:
        raise ValueError(f"Unsupported timeframe: {timeframe}")


@dataclass(frozen=True)
class NestedFold:
    """One nested WFO fold with inner train/val and outer test."""

    fold_id: str
    # Inner fold (for parameter selection)
    inner_train_start: int
    inner_train_end: int
    inner_val_start: int
    inner_val_end: int
    # Outer fold (for OOS evaluation)
    outer_test_start: int
    outer_test_end: int
    purge: int
    embargo: int


def _get_fold_indices(
    n_bars: int,
    timeframe: str,
    train_months: int,
    val_months: int,
    test_months: int,
    step_months: int,
    purge: int,
    embargo: int,
) -> list[NestedFold]:
    """Generate nested folds with EXPANDING window (not rolling).

    Expanding window: train_start FIXED at 0, train_end GROWS by step_bars each fold.
    Validation and test windows slide forward.
    Purge/embargo gaps between train/val and val/test.
    """
    bars_per_month = _bars_per_month(timeframe)
    initial_train_bars = train_months * bars_per_month
    val_bars = val_months * bars_per_month
    test_bars = test_months * bars_per_month
    step_bars = step_months * bars_per_month

    # Minimum bars needed for first fold
    min_bars = initial_train_bars + val_bars + test_bars + 2 * (purge + embargo)
    if n_bars < min_bars:
        return []

    folds = []
    fold_idx = 0

    # Expanding window: train_start always 0, train_end grows by step_bars
    # This is the key difference from rolling window
    while True:
        # Expanding: train always starts at 0, grows with each fold
        inner_train_start = 0
        inner_train_end = initial_train_bars + fold_idx * step_bars

        # Validation starts after train + purge + embargo
        inner_val_start = inner_train_end + purge + embargo
        inner_val_end = inner_val_start + val_bars

        # Outer test starts after validation + purge + embargo
        outer_test_start = inner_val_end + purge + embargo
        outer_test_end = outer_test_start + test_bars

        if outer_test_end > n_bars:
            break

        folds.append(
            NestedFold(
                fold_id=f"fold_{fold_idx:03d}",
                inner_train_start=inner_train_start,
                inner_train_end=inner_train_end,
                inner_val_start=inner_val_start,
                inner_val_end=inner_val_end,
                outer_test_start=outer_test_start,
                outer_test_end=outer_test_end,
                purge=purge,
                embargo=embargo,
            )
        )
        fold_idx += 1

    return folds


def _compute_strategy_code_sha(strategy_id: str) -> str:
    """Get strategy code SHA from registry descriptor."""
    from trading_agent.strategies.canonical.candidates import build_default_registry

    registry = build_default_registry()
    entry = registry._entries.get(strategy_id)
    if entry is None:
        return "unknown"
    return entry.descriptor.code_sha


def _compute_data_manifest_sha(symbol: str, timeframe: str) -> str:
    """Compute SHA256 of data manifest (OHLCV data)."""
    import io
    from trading_agent.data.storage import load_ohlcv

    df = load_ohlcv("binance", symbol, timeframe)
    # Hash the data content (timestamps, OHLCV values)
    buffer = io.BytesIO()
    df.write_parquet(buffer)
    return hashlib.sha256(buffer.getvalue()).hexdigest()


def _compute_feature_schema_hash(descriptor) -> str:
    """Compute SHA256 of feature schema required by strategy."""
    # Feature schema is defined by the descriptor's required features
    features = sorted(descriptor.required_features)
    payload = json.dumps(features, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


def _compute_environment_hash(spec: WFOSpec, fold: NestedFold) -> str:
    """Compute environment hash for a specific fold evaluation."""
    payload = json.dumps(
        {
            "timeframe": spec.timeframe,
            "train_months": spec.train_months,
            "val_months": spec.val_months,
            "test_months": spec.test_months,
            "step_months": spec.step_months,
            "purge_bars": fold.purge,
            "embargo_bars": fold.embargo,
            "fold": asdict(fold),
            "cost_scenarios": [
                {
                    "name": c.name,
                    "fee_multiplier": c.fee_multiplier,
                    "slippage_multiplier": c.slippage_multiplier,
                }
                for c in spec.cost_scenarios
            ],
            "evaluator_version": spec.evaluator_version,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def _resolve_training_contract(adapter: Any) -> str:
    """Fail closed for stateful candidates until fold-local fitting is supported.

    The current canonical S3 pool is deterministic/stateless. Merely simulating
    a stateful estimator over the train range would not prove that its scaler,
    calibrator or regime model was fitted without validation leakage, so such an
    adapter is rejected instead of being mislabeled as STR-0303 compliant.
    """
    fit_methods = ("fit", "fit_scaler", "fit_calibrator", "fit_regime_model")
    targets = (adapter, getattr(adapter, "_strategy", None))
    for target in targets:
        if target is None:
            continue
        for method_name in fit_methods:
            if callable(getattr(type(target), method_name, None)):
                raise ValueError(
                    "stateful strategy training is not supported by nested WFO; "
                    f"found {type(target).__name__}.{method_name}"
                )
    return "STATELESS_DETERMINISTIC"


def _compute_parameter_stability(inner_results: list[WFOInnerResult]) -> float | None:
    """Compute parameter stability across folds (STR-0306).

    Measures how consistent the selected parameters are across folds.
    Returns a value between 0.0 and 1.0:
    - 1.0 = identical params selected in all folds (or 0/1 folds)
    - 0.0 = completely different params in each fold

    Uses normalized parameter distance: for each param, compute the
    fraction of folds where the selected value equals the mode (most common).
    Then average across all parameters.
    """
    if not inner_results:
        return None

    # Extract params from each fold (excluding cost_scenario)
    fold_params = []
    for r in inner_results:
        params = {k: v for k, v in r.best_params.items() if k != "cost_scenario"}
        if params:
            fold_params.append(params)

    if len(fold_params) < 2:
        return None

    # Get all parameter names
    all_param_names: set[str] = set()
    for p in fold_params:
        all_param_names.update(p.keys())

    if not all_param_names:
        return None

    # For each parameter, compute stability = fraction of folds with mode value
    stabilities = []
    for param_name in all_param_names:
        values = [p.get(param_name) for p in fold_params if param_name in p]
        if not values:
            continue

        # Find mode (most common value)
        from collections import Counter

        counter = Counter(values)
        mode_value, mode_count = counter.most_common(1)[0]
        stability = mode_count / len(values)
        stabilities.append(stability)

    return float(np.mean(stabilities)) if stabilities else None


def _candidate_key(candidate: dict[str, Any]) -> str:
    return json.dumps(
        {
            "params": candidate.get("params", {}),
            "cost_scenario": candidate.get("cost_scenario"),
        },
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _build_pbo_candidate_returns(
    inner_results: list[WFOInnerResult],
) -> tuple[np.ndarray | None, dict[str, Any]]:
    """Build the chronological observations × candidates matrix CSCV expects.

    Fold-level Sharpe values are never returns. Each candidate must provide a
    finite, equally aligned per-bar validation return series in every included
    fold; otherwise PBO is unavailable and the hard gate becomes INVALID.
    """

    if not inner_results:
        return None, {"status": "INVALID", "reason": "no inner folds"}

    ordered = sorted(inner_results, key=lambda result: result.val_start)
    previous_end: int | None = None
    candidate_keys: list[str] | None = None
    chunks: list[np.ndarray] = []
    for result in ordered:
        if previous_end is not None and result.val_start < previous_end:
            return None, {
                "status": "INVALID",
                "reason": "overlapping validation windows cannot be concatenated for CSCV",
            }
        previous_end = result.val_end
        by_key = {
            _candidate_key(candidate): candidate
            for candidate in result.candidate_metrics
        }
        current_keys = sorted(by_key)
        if candidate_keys is None:
            candidate_keys = current_keys
            if len(candidate_keys) < 2:
                return None, {
                    "status": "INVALID",
                    "reason": "CSCV requires at least two candidates",
                    "n_candidates": len(candidate_keys),
                }
        elif current_keys != candidate_keys:
            return None, {
                "status": "INVALID",
                "reason": "candidate set differs across validation folds",
            }

        columns: list[np.ndarray] = []
        expected_len: int | None = None
        for key in candidate_keys:
            metrics = by_key[key].get("val_metrics", {})
            raw_series = (
                metrics.get("return_series") if isinstance(metrics, dict) else None
            )
            if not isinstance(raw_series, list) or not raw_series:
                return None, {
                    "status": "INVALID",
                    "reason": f"candidate lacks validation return series in {result.fold_id}",
                }
            series: np.ndarray = np.asarray(raw_series, dtype=np.float64)
            if not np.all(np.isfinite(series)):
                return None, {
                    "status": "INVALID",
                    "reason": f"candidate has non-finite returns in {result.fold_id}",
                }
            if expected_len is None:
                expected_len = int(series.size)
            elif series.size != expected_len:
                return None, {
                    "status": "INVALID",
                    "reason": f"candidate return series are not aligned in {result.fold_id}",
                }
            columns.append(series)
        chunks.append(np.column_stack(columns))

    matrix = np.vstack(chunks)
    if matrix.shape[0] < 24:
        return None, {
            "status": "INVALID",
            "reason": "CSCV requires at least 24 chronological observations",
            "n_observations": int(matrix.shape[0]),
            "n_candidates": int(matrix.shape[1]),
        }
    return matrix, {
        "status": "READY",
        "n_observations": int(matrix.shape[0]),
        "n_candidates": int(matrix.shape[1]),
        "candidate_keys": candidate_keys,
        "folds": [result.fold_id for result in ordered],
    }


def _compute_commit_sha() -> str:
    """Get current git commit SHA."""
    import subprocess

    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            cwd=ROOT,
            timeout=5,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass
    return "unknown"


def _is_git_worktree_dirty() -> bool:
    """Return True when HEAD alone cannot reproduce the evaluated source tree."""
    import subprocess

    try:
        result = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=normal"],
            capture_output=True,
            text=True,
            cwd=ROOT,
            timeout=5,
        )
        return result.returncode != 0 or bool(result.stdout.strip())
    except Exception:
        return True


def _persist_study_manifest(out_root: Path, manifest: WFOStudyManifest) -> Path:
    """Persist a deterministic study identity once and reject conflicts."""
    digest = manifest.manifest_id.removeprefix("sha256:")
    path = Path(out_root) / "study_manifests" / f"{digest}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(manifest.to_dict(), indent=2, allow_nan=False)
    if path.exists():
        existing = json.loads(path.read_text(encoding="utf-8"))
        if existing != manifest.to_dict():
            raise ValueError(
                f"study manifest already exists with different content: {path}"
            )
        return path
    tmp_path = path.with_suffix(".json.tmp")
    tmp_path.write_text(encoded, encoding="utf-8")
    tmp_path.replace(path)
    return path


def _find_existing_outer_artifact(
    out_root: Path, freeze_id: str, fold_id: str
) -> EvaluationArtifact | None:
    """Load the immutable outer artifact bound to ``freeze_id``.

    Corrupt evidence is an integrity failure. It must not silently reopen the
    outer window and generate a replacement result.
    """

    path = _outer_artifact_path(out_root, freeze_id, fold_id)
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    artifact = EvaluationArtifact.from_dict(data)
    if artifact.selection_freeze_id != freeze_id:
        raise ValueError(f"outer artifact freeze mismatch for {fold_id}")
    return artifact


def _outer_artifact_path(out_root: Path, freeze_id: str, fold_id: str) -> Path:
    digest = freeze_id.removeprefix("sha256:")
    return Path(out_root) / "outer_one_shot" / fold_id / f"{digest}.json"


def _inner_freeze_path(out_root: Path, freeze_id: str) -> Path:
    digest = freeze_id.removeprefix("sha256:")
    return Path(out_root) / "inner_selection_freezes" / f"{digest}.json"


def _find_existing_inner_freeze(
    out_root: Path, freeze_id: str
) -> InnerSelectionFreeze | None:
    """Load an existing inner selection freeze, or return None if missing."""
    path = _inner_freeze_path(out_root, freeze_id)
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    return InnerSelectionFreeze.from_dict(data)


def _persist_inner_selection_freeze(
    out_root: Path, freeze: InnerSelectionFreeze
) -> InnerSelectionFreeze:
    """Atomically persist an inner selection freeze exactly once."""
    path = _inner_freeze_path(out_root, freeze.freeze_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        existing = _find_existing_inner_freeze(out_root, freeze.freeze_id)
        if existing is not None:
            # Compare identity fields only (created_at may differ on rerun)
            existing_identity = {
                k: v for k, v in existing.to_dict().items() if k != "created_at"
            }
            new_identity = {
                k: v for k, v in freeze.to_dict().items() if k != "created_at"
            }
            if existing_identity != new_identity:
                raise ValueError(
                    f"inner freeze already exists with different content: {path}"
                )
        return existing or freeze
    tmp_path = path.with_suffix(".json.tmp")
    tmp_path.write_text(
        json.dumps(freeze.to_dict(), indent=2, allow_nan=False), encoding="utf-8"
    )
    tmp_path.replace(path)
    return freeze


def _persist_outer_artifact(
    out_root: Path,
    freeze_id: str,
    fold_id: str,
    artifact: EvaluationArtifact,
) -> EvaluationArtifact:
    """Atomically persist an outer result, including failures, exactly once."""

    path = _outer_artifact_path(out_root, freeze_id, fold_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    bound = replace(artifact, selection_freeze_id=freeze_id)
    if path.exists():
        existing = _find_existing_outer_artifact(out_root, freeze_id, fold_id)
        if existing is None or existing.artifact_id != bound.artifact_id:
            raise ValueError(
                f"outer artifact already exists with different content: {path}"
            )
        return existing
    tmp_path = path.with_suffix(".json.tmp")
    tmp_path.write_text(
        json.dumps(bound.to_dict(), indent=2, allow_nan=False), encoding="utf-8"
    )
    tmp_path.replace(path)
    return bound


def _compute_multi_dimensional_evaluation(
    spec: WFOSpec,
    outer_results: list[WFOOuterResult],
    folds: list[NestedFold],
) -> dict[str, Any]:
    """Compute multi-dimensional evaluation metrics (STR-0307).

    Evaluates by:
    - Strategy (already single strategy per run)
    - Pair (already single symbol per run)
    - Outer fold
    - Regime (trending/ranging, low/mid/high vol) — PIT thresholds fit on TRAIN
    - Calendar year
    - Volatility bucket
    - Cost scenario
    - Portfolio aggregate (when run in portfolio mode)
    """
    from datetime import UTC
    import numpy as np

    # Load data for regime/year/vol bucket analysis
    from trading_agent.data.storage import load_ohlcv

    df = load_ohlcv("binance", spec.symbol, spec.timeframe)
    df = df.sort("timestamp")

    # PIT regime indicators: compute ATR percentile and ADX on TRAIN data only
    # to avoid look-ahead bias. Use the FIRST fold's inner_train_end as the
    # training cutoff for regime threshold fitting.
    train_end = folds[0].inner_train_end if folds else len(df)
    train_df = df.slice(0, train_end)

    # Add regime indicators on FULL data (needed for labeling test bars)
    df_with_regime = add_regime_indicators(df)

    # Convert to pandas for easier time-based grouping
    pdf = df_with_regime.to_pandas()
    if "time" in pdf.columns:
        time_col = "time"
    elif "timestamp" in pdf.columns:
        time_col = "timestamp"
    else:
        time_col = pdf.columns[0]
    pdf[time_col] = pdf[time_col].dt.tz_localize(UTC)

    multi_dim: dict[str, Any] = {
        "by_fold": [],
        "by_regime": {},
        "by_year": {},
        "by_vol_bucket": {},
        "by_cost_scenario": {},
    }

    # --- Collect all trades across folds with regime/year labels ---
    all_trades = []
    for r in outer_results:
        if not r.artifact or not r.artifact.report_path:
            continue

        # Fold evaluation
        if r.test_metrics:
            fold_eval = {
                "fold_id": r.fold_id,
                "test_start": r.test_start,
                "test_end": r.test_end,
                "sharpe": r.test_metrics.get("sharpe"),
                "return_pct": r.test_metrics.get("total_return_pct"),
                "trades": r.test_metrics.get("total_trades"),
                "profit_factor": r.test_metrics.get("profit_factor"),
                "max_drawdown_pct": r.test_metrics.get("max_drawdown_pct"),
            }
            multi_dim["by_fold"].append(fold_eval)

        # Load trade-level data from artifact report
        import json
        from pathlib import Path

        report_path = Path(r.artifact.report_path)
        if not report_path.exists():
            continue
        try:
            report_data = json.loads(report_path.read_text())
        except Exception:
            continue

        trades = report_data.get("trades") or []
        cost_scenario = r.params.get("cost_scenario", "1x")

        for t in trades:
            pnl = t.get("pnl")
            if pnl is None:
                continue
            entry_time = t.get("entry_time")
            exit_time = t.get("exit_time")
            if not exit_time:
                continue

            # Find regime at exit time (PIT: use data available up to exit)
            exit_ts = exit_time
            if isinstance(exit_ts, str):
                from datetime import datetime

                exit_ts = datetime.fromisoformat(exit_ts.replace("Z", "+00:00"))

            # Match regime row by timestamp (nearest prior)
            regime_row = (
                pdf[pdf[time_col] <= exit_ts].iloc[-1]
                if len(pdf[pdf[time_col] <= exit_ts]) > 0
                else None
            )
            if regime_row is None:
                continue

            trade_rec = {
                "fold_id": r.fold_id,
                "pnl": float(pnl),
                "return_pct": float(t.get("return_pct", 0.0)),
                "exit_time": exit_ts,
                "trend_regime": regime_row.get("trend_regime", "unknown"),
                "vol_regime": regime_row.get("vol_regime", "unknown"),
                "trend_dir": regime_row.get("trend_dir", "unknown"),
                "year": exit_ts.year,
                "cost_scenario": cost_scenario,
            }
            all_trades.append(trade_rec)

    if not all_trades:
        multi_dim["note"] = (
            "No trade-level data available for multi-dimensional evaluation"
        )
        return multi_dim

    # --- Compute metrics helper ---
    def _metrics_for(trades_list: list[dict]) -> dict:
        if not trades_list:
            return {
                "trades": 0,
                "net_pnl": 0.0,
                "win_rate": 0.0,
                "profit_factor": 0.0,
                "avg_return_pct": 0.0,
            }
        pnls = [t["pnl"] for t in trades_list]
        returns = [t.get("return_pct", 0.0) for t in trades_list]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p < 0]
        gross_profit = sum(wins) if wins else 0.0
        gross_loss = abs(sum(losses)) if losses else 1e-9
        return {
            "trades": len(trades_list),
            "net_pnl": float(sum(pnls)),
            "win_rate": len(wins) / len(pnls) if pnls else 0.0,
            "profit_factor": gross_profit / gross_loss
            if gross_loss > 0
            else float("inf"),
            "avg_return_pct": float(np.mean(returns)),
        }

    # --- By regime ---
    for regime in ["trending", "ranging"]:
        regime_trades = [t for t in all_trades if t["trend_regime"] == regime]
        multi_dim["by_regime"][regime] = _metrics_for(regime_trades)

    # --- By volatility bucket ---
    for vol in ["low_vol", "mid_vol", "high_vol"]:
        vol_trades = [t for t in all_trades if t["vol_regime"] == vol]
        multi_dim["by_vol_bucket"][vol] = _metrics_for(vol_trades)

    # --- By year ---
    years = sorted(set(t["year"] for t in all_trades))
    for yr in years:
        yr_trades = [t for t in all_trades if t["year"] == yr]
        multi_dim["by_year"][str(yr)] = _metrics_for(yr_trades)

    # --- By cost scenario ---
    cost_scenarios = sorted(set(t["cost_scenario"] for t in all_trades))
    for cs in cost_scenarios:
        cs_trades = [t for t in all_trades if t["cost_scenario"] == cs]
        multi_dim["by_cost_scenario"][cs] = _metrics_for(cs_trades)

    # --- Portfolio aggregate (single pair for now) ---
    multi_dim["portfolio"] = _metrics_for(all_trades)

    return multi_dim


def _run_outer_eval(
    spec: WFOSpec,
    params: dict[str, Any],
    cost_scenario: CostScenario,
    fold: NestedFold,
    out_root: Path,
    descriptor,
    signal_delay_bars: int = 0,
) -> EvaluationArtifact | None:
    """Re-run one outer OOS test window under a given cost scenario (STR-0308).

    Uses the same warmup/buffer convention as the inner trial so indicator
    initialization is consistent. Returns the completed EvaluationArtifact.

    Args:
        signal_delay_bars: Number of bars to delay signals (0 = no delay).
            1 = delay by 1 bar (decision at close of bar j-1, execute at open j).
    """
    warmup = getattr(descriptor, "warmup_bars", 0)
    buffer_bars = 100
    sim_start = max(0, fold.outer_test_start - warmup - buffer_bars)
    cell = EvaluationCellSpec(
        strategy_id=spec.strategy_id,
        symbol=spec.symbol,
        timeframe=spec.timeframe,
        params=params,
        cost_scenario=cost_scenario,
    )
    return run_cell(
        cell,
        out_root=out_root,
        start=sim_start,
        end=fold.outer_test_end,
        fresh=True,
        measurement_start=fold.outer_test_start,
        measurement_end=fold.outer_test_end,
        signal_delay_bars=signal_delay_bars,
    )


def _run_outer_eval_with_params(
    spec: WFOSpec,
    params: dict[str, Any],
    cost_scenario: CostScenario,
    fold: NestedFold,
    out_root: Path,
    descriptor,
) -> EvaluationArtifact | None:
    """Re-run one outer OOS test window with modified parameters (for param neighbors).

    Uses the same warmup/buffer convention as the inner trial so indicator
    initialization is consistent. Returns the completed EvaluationArtifact.
    """
    return _run_outer_eval(
        spec, params, cost_scenario, fold, out_root, descriptor, signal_delay_bars=0
    )


def _compute_drop_best_trade(
    artifact: EvaluationArtifact | None,
) -> dict[str, float] | None:
    """Compute net PnL before/after dropping the single best trade (STR-0308).

    Reads trade-level data from the artifact's report JSON. Returns None if no
    trade data is available (so callers can fall back gracefully).
    """
    if artifact is None or artifact.report_path is None:
        return None
    p = Path(artifact.report_path)
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text())
    except Exception:
        return None
    trades = data.get("trades") or []
    if not trades:
        return None
    pnls = [float(t.get("pnl", 0.0)) for t in trades if t.get("pnl") is not None]
    if not pnls:
        return None
    net = float(sum(pnls))
    best = float(max(pnls))
    return {
        "n_trades": len(pnls),
        "net_pnl": net,
        "best_trade_pnl": best,
        "net_pnl_after_drop": net - best,
    }


def _compute_sensitivity_analysis(
    spec: WFOSpec,
    outer_results: list[WFOOuterResult],
    folds: list[NestedFold],
    out_root: Path | None = None,
    descriptor=None,
) -> dict[str, Any]:
    """Compute sensitivity analysis for the selected parameters (STR-0308).

    Evaluates sensitivity to:
    - Baseline cost vs 2x cost (REAL re-run via _run_outer_eval)
    - Slippage stress (REAL re-run via _run_outer_eval)
    - Drop best trade (REAL trade-level recompute from artifact report)
    - Delay signal/execution by 1 bar (REAL re-run via _run_outer_eval with signal_delay_bars=1)
    - Parameter neighbors (REAL re-run via _run_outer_eval_with_params)

    When out_root and descriptor are provided, ALL tests are computed from actual
    re-evaluations. Otherwise they fall back to framework placeholders (so the
    function never crashes without a backtest harness available).
    """
    if not outer_results:
        return {"error": "No outer results to analyze"}

    last_result = outer_results[-1]
    base_params = dict(last_result.params)
    base_params.pop("cost_scenario", "1x")

    sensitivity: dict[str, Any] = {
        "baseline_cost": {},
        "cost_2x": {"folds": {}},
        "slippage_stress": {"folds": {}},
        "drop_best_trade": {"folds": {}},
        "delay_1_bar": {},
        "parameter_neighbors": [],
        "real_computed": [],
    }

    cost_2x_net_pnls: list[float] = []
    cost_2x_pfs: list[float] = []
    cost_2x_returns: list[float] = []
    slip_returns: list[float] = []
    slip_pfs: list[float] = []
    drop_best_net_pnls: list[float] = []
    delay_1_bar_net_pnls: list[float] = []
    delay_1_bar_returns: list[float] = []
    delay_1_bar_pfs: list[float] = []

    rerun_root = (
        Path(out_root) if out_root is not None and descriptor is not None else None
    )

    # For each outer fold, run sensitivity tests on the selected params
    for r in outer_results:
        if not r.test_metrics or not r.artifact:
            continue

        fold_id = r.fold_id
        fold = next((f for f in folds if f.fold_id == fold_id), None)
        if not fold:
            continue

        # Extract params (without cost_scenario)
        test_params = {k: v for k, v in r.params.items() if k != "cost_scenario"}

        # --- 1. Baseline cost (already computed in outer_results) ---
        sensitivity["baseline_cost"][fold_id] = {
            "sharpe": r.test_metrics.get("sharpe"),
            "return_pct": r.test_metrics.get("total_return_pct"),
            "trades": r.test_metrics.get("total_trades"),
            "profit_factor": r.test_metrics.get("profit_factor"),
            "max_drawdown_pct": r.test_metrics.get("max_drawdown_pct"),
        }

        if rerun_root is None:
            sensitivity["cost_2x"]["folds"][fold_id] = {
                "note": "Requires out_root+descriptor for re-evaluation"
            }
            sensitivity["slippage_stress"]["folds"][fold_id] = {
                "note": "Requires out_root+descriptor for re-evaluation"
            }
            sensitivity["drop_best_trade"]["folds"][fold_id] = {
                "note": "Requires out_root+descriptor for re-evaluation"
            }
            continue

        # --- 2. Cost 2x (REAL re-run) ---
        artifact_2x = _run_outer_eval(
            spec, test_params, SCENARIO_DOUBLE, fold, rerun_root, descriptor
        )
        if artifact_2x is not None and artifact_2x.status == "COMPLETED":
            m = artifact_2x.metrics
            dbt = _compute_drop_best_trade(artifact_2x)
            net = dbt["net_pnl"] if dbt else _coerce_num(m.get("net_pnl"))
            pf = _coerce_num(m.get("profit_factor"))
            ret = _coerce_num(m.get("total_return_pct"))
            if net is not None:
                cost_2x_net_pnls.append(net)
            if pf is not None:
                cost_2x_pfs.append(pf)
            if ret is not None:
                cost_2x_returns.append(ret)
            sensitivity["cost_2x"]["folds"][fold_id] = {
                "return_pct": ret,
                "profit_factor": pf,
                "net_pnl": net,
                "trades": m.get("total_trades"),
                "artifact_id": artifact_2x.artifact_id,
            }

        # --- 3. Slippage stress (REAL re-run) ---
        artifact_slip = _run_outer_eval(
            spec,
            test_params,
            SCENARIO_SLIPPAGE_STRESS,
            fold,
            rerun_root,
            descriptor,
        )
        if artifact_slip is not None and artifact_slip.status == "COMPLETED":
            m = artifact_slip.metrics
            ret = _coerce_num(m.get("total_return_pct"))
            pf = _coerce_num(m.get("profit_factor"))
            if ret is not None:
                slip_returns.append(ret)
            if pf is not None:
                slip_pfs.append(pf)
            sensitivity["slippage_stress"]["folds"][fold_id] = {
                "return_pct": ret,
                "profit_factor": pf,
                "trades": m.get("total_trades"),
                "artifact_id": artifact_slip.artifact_id,
            }

        # --- 4. Drop best trade (REAL trade-level recompute) ---
        dbt = _compute_drop_best_trade(r.artifact)
        if dbt is not None:
            drop_best_net_pnls.append(dbt["net_pnl_after_drop"])
            sensitivity["drop_best_trade"]["folds"][fold_id] = dbt

        # --- 5. Delay 1 bar (REAL re-run with signal_delay_bars=1) ---
        artifact_delay = _run_outer_eval(
            spec,
            test_params,
            SCENARIO_BASE,
            fold,
            rerun_root,
            descriptor,
            signal_delay_bars=1,
        )
        if artifact_delay is not None and artifact_delay.status == "COMPLETED":
            m = artifact_delay.metrics
            ret = _coerce_num(m.get("total_return_pct"))
            pf = _coerce_num(m.get("profit_factor"))
            if ret is not None:
                delay_1_bar_returns.append(ret)
            if pf is not None:
                delay_1_bar_pfs.append(pf)
            dbt_d = _compute_drop_best_trade(artifact_delay)
            net_d = dbt_d["net_pnl"] if dbt_d else _coerce_num(m.get("net_pnl"))
            if net_d is not None:
                delay_1_bar_net_pnls.append(net_d)
            sensitivity["delay_1_bar"][fold_id] = {
                "return_pct": ret,
                "profit_factor": pf,
                "net_pnl": net_d,
                "trades": m.get("total_trades"),
                "artifact_id": artifact_delay.artifact_id,
            }
        else:
            sensitivity["delay_1_bar"][fold_id] = {
                "note": "Re-run failed or incomplete",
            }

    # --- Aggregates ---
    if rerun_root is not None:
        sensitivity["cost_2x"]["aggregate"] = {
            "median_net_pnl": float(np.median(cost_2x_net_pnls))
            if cost_2x_net_pnls
            else None,
            "median_profit_factor": float(np.median(cost_2x_pfs))
            if cost_2x_pfs
            else None,
            "median_return_pct": float(np.median(cost_2x_returns))
            if cost_2x_returns
            else None,
        }
        sensitivity["slippage_stress"]["aggregate"] = {
            "median_return_pct": float(np.median(slip_returns))
            if slip_returns
            else None,
            "median_profit_factor": float(np.median(slip_pfs)) if slip_pfs else None,
        }
        sensitivity["drop_best_trade"]["aggregate"] = {
            "total_net_pnl_after_drop": float(sum(drop_best_net_pnls))
            if drop_best_net_pnls
            else None,
            "n_folds": len(drop_best_net_pnls),
        }
        sensitivity["delay_1_bar"]["aggregate"] = {
            "median_net_pnl": float(np.median(delay_1_bar_net_pnls))
            if delay_1_bar_net_pnls
            else None,
            "median_return_pct": float(np.median(delay_1_bar_returns))
            if delay_1_bar_returns
            else None,
            "median_profit_factor": float(np.median(delay_1_bar_pfs))
            if delay_1_bar_pfs
            else None,
        }
        sensitivity["real_computed"] = [
            "cost_2x",
            "slippage_stress",
            "drop_best_trade",
            "delay_1_bar",
        ]
    else:
        sensitivity["note"] = (
            "Real re-evaluation skipped (out_root/descriptor not provided). "
            "Pass out_root and descriptor to enable actual cost-2x / slippage / drop-best / delay-1-bar runs."
        )

    # --- 6. Parameter neighbors (REAL re-run for every eligible outer fold) ---
    if rerun_root is not None:
        for outer in outer_results:
            if not outer.artifact or not outer.test_metrics:
                continue
            outer_fold = next(
                (item for item in folds if item.fold_id == outer.fold_id), None
            )
            if outer_fold is None:
                continue
            selected_params = {
                key: value
                for key, value in outer.params.items()
                if key != "cost_scenario"
            }
            for param_name, param_values in spec.param_grid.items():
                if len(param_values) < 3:
                    continue
                best_val = selected_params.get(param_name)
                if best_val is None:
                    continue
                try:
                    idx = param_values.index(best_val)
                except ValueError:
                    continue

                neighbor_results: list[dict[str, Any]] = []
                neighbor_positions = []
                if idx > 0:
                    neighbor_positions.append(("left", idx - 1))
                if idx < len(param_values) - 1:
                    neighbor_positions.append(("right", idx + 1))
                for direction, neighbor_idx in neighbor_positions:
                    neighbor_params = dict(selected_params)
                    neighbor_params[param_name] = param_values[neighbor_idx]
                    neighbor_artifact = _run_outer_eval_with_params(
                        spec,
                        neighbor_params,
                        SCENARIO_BASE,
                        outer_fold,
                        rerun_root,
                        descriptor,
                    )
                    record: dict[str, Any] = {
                        "param": param_name,
                        "value": param_values[neighbor_idx],
                        "direction": direction,
                    }
                    if (
                        neighbor_artifact is not None
                        and neighbor_artifact.status == "COMPLETED"
                    ):
                        metrics = neighbor_artifact.metrics
                        trade_stats = _compute_drop_best_trade(neighbor_artifact)
                        net_pnl = (
                            trade_stats.get("net_pnl")
                            if trade_stats is not None
                            else _coerce_num(metrics.get("net_pnl"))
                        )
                        record.update(
                            {
                                "return_pct": _coerce_num(
                                    metrics.get("total_return_pct")
                                ),
                                "profit_factor": _coerce_num(
                                    metrics.get("profit_factor")
                                ),
                                "net_pnl": net_pnl,
                                "trades": metrics.get("total_trades"),
                                "artifact_id": neighbor_artifact.artifact_id,
                            }
                        )
                    else:
                        record["note"] = "Re-run failed or incomplete"
                        record["net_pnl"] = None
                    neighbor_results.append(record)

                if neighbor_results:
                    sensitivity["parameter_neighbors"].append(
                        {
                            "fold_id": outer.fold_id,
                            "param": param_name,
                            "best_value": best_val,
                            "neighbors": neighbor_results,
                        }
                    )
        sensitivity["real_computed"].append("parameter_neighbors")
    else:
        # Framework placeholder when can_rerun is False
        for param_name, param_values in spec.param_grid.items():
            if len(param_values) < 3:
                continue

            best_val = base_params.get(param_name)
            if best_val is None:
                continue

            try:
                idx = param_values.index(best_val)
            except ValueError:
                continue

            neighbor_results = []
            if idx > 0:
                neighbor_results.append(
                    {
                        "param": param_name,
                        "value": param_values[idx - 1],
                        "direction": "left",
                        "note": "Requires re-running backtest with this param value",
                    }
                )
            if idx < len(param_values) - 1:
                neighbor_results.append(
                    {
                        "param": param_name,
                        "value": param_values[idx + 1],
                        "direction": "right",
                        "note": "Requires re-running backtest with this param value",
                    }
                )

            if neighbor_results:
                sensitivity["parameter_neighbors"].append(
                    {
                        "param": param_name,
                        "best_value": best_val,
                        "neighbors": neighbor_results,
                    }
                )

    return sensitivity


def _run_parameter_trial(
    strategy_id: str,
    symbol: str,
    timeframe: str,
    params: dict[str, Any],
    cost_scenario: CostScenario,
    inner_train_start: int,
    inner_train_end: int,
    inner_val_start: int,
    inner_val_end: int,
    out_root: Path,
    registry: ExperimentRegistry,
    search_family: str,
    evaluator_version: str,
    seed: int,
    descriptor,
    adapter,
    cell_runner: CellRunner | None = None,
) -> tuple[float, dict[str, Any], EvaluationArtifact | None]:
    """Run one parameter combination: fit on train, evaluate on validation.

    - Train window: [inner_train_start, inner_train_end) — used for any fitting (scaler, calibrator, regime model)
    - Warmup: bars before inner_val_start needed for indicator initialization
    - Validation window: [inner_val_start, inner_val_end) — metrics computed ONLY here
    - No data from validation window used during train-phase fitting
    """
    warmup = descriptor.warmup_bars
    buffer_bars = 100  # Extra buffer for indicator stabilization

    # Simulation starts early enough for warmup before validation window
    # Train window is [inner_train_start, inner_train_end)
    # Validation window is [inner_val_start, inner_val_end)
    # We simulate from max(0, inner_train_start - warmup - buffer) to inner_val_end
    # But the strategy only "fits" on train data; validation metrics are isolated
    sim_start = max(0, inner_train_start - warmup - buffer_bars)

    spec_val = EvaluationCellSpec(
        strategy_id=strategy_id,
        symbol=symbol,
        timeframe=timeframe,
        params=params,
        cost_scenario=cost_scenario,
    )
    runner = cell_runner or run_cell
    artifact_val = runner(
        spec_val,
        out_root=out_root,
        start=sim_start,
        end=inner_val_end,
        fresh=True,
        measurement_start=inner_val_start,
        measurement_end=inner_val_end,
    )

    if artifact_val.status != "COMPLETED":
        return -np.inf, {}, artifact_val

    # ``run_cell`` persisted an authoritative report isolated to
    # [inner_val_start, inner_val_end), including its opening mark and only
    # attributable trades.  The artifact metrics are therefore safe to use for
    # candidate selection and are not contaminated by the warm-up simulation.
    val_sharpe = float(artifact_val.metrics.get("sharpe", -np.inf))
    return val_sharpe, dict(artifact_val.metrics), artifact_val


def run_nested_wfo(
    spec: WFOSpec,
    *,
    out_root: Path | None = None,
    run_holdout: bool = False,
    holdout_actor: str = "research_system",
    real_sensitivity: bool = True,
    cell_runner: CellRunner | None = None,
) -> WFOResult:
    """Run nested walk-forward optimization for one strategy × symbol.

    Implements one-shot outer evaluation (STR-0304):
    - Inner selection freezes before outer test seen (InnerSelectionFreeze)
    - Outer fold identity bound to frozen selection hash
    - Re-run same identity returns existing artifact (idempotent)
    - Different params/search space/commit/data creates new identity

    If run_holdout=True and all hard gates pass, also runs the independent
    final holdout (STR-0309) as a one-shot confirmation.

    Args:
        cell_runner: Optional callable to execute cells. If provided, used instead
            of inline ``run_cell`` for both inner validation and outer OOS trials.
            Enables parallel execution via external scheduler. Signature:
            ``cell_runner(spec, out_root, start, end, fresh, measurement_start, measurement_end) -> EvaluationArtifact``
    """
    out_root = Path(out_root) if out_root else ROOT / "data" / "backtests" / "wfo"
    out_root.mkdir(parents=True, exist_ok=True)

    # Initialize registry
    registry = ExperimentRegistry(spec.registry_path)

    # Get strategy descriptor and adapter
    from trading_agent.strategies.canonical.candidates import build_default_registry
    from trading_agent.backtest.tournament import _research_env

    registry_canonical = build_default_registry()
    descriptor = registry_canonical.describe(spec.strategy_id)
    _, adapter = registry_canonical.get(spec.strategy_id, environment=_research_env())
    training_contract = _resolve_training_contract(adapter)

    # Determine purge/embargo
    purge, embargo = _default_purge_embargo(descriptor)
    if spec.purge_bars > 0:
        purge = spec.purge_bars
    if spec.embargo_bars > 0:
        embargo = spec.embargo_bars

    # Load data to determine number of bars
    from trading_agent.data.storage import load_ohlcv

    df = load_ohlcv("binance", spec.symbol, spec.timeframe)
    n_bars = df.height

    # Generate folds
    folds = _get_fold_indices(
        n_bars=n_bars,
        timeframe=spec.timeframe,
        train_months=spec.train_months,
        val_months=spec.val_months,
        test_months=spec.test_months,
        step_months=spec.step_months,
        purge=purge,
        embargo=embargo,
    )

    if not folds:
        raise ValueError(f"Insufficient data for fold structure: {n_bars} bars")

    # STR-0309: Resolve frozen holdout window and exclude any fold that touches it.
    # The holdout is loaded from data/research_manifest.json (independent, frozen
    # at study start). Folds whose data window overlaps the holdout are dropped so
    # that NO selection or OOS evaluation ever sees holdout data.
    holdout_bars = _resolve_frozen_holdout_window(df, spec)
    # Declare upfront so both branches can assign (mypy no-redef)
    holdout_start_bar: int | None = None
    holdout_end_bar: int | None = None
    if holdout_bars is not None:
        holdout_start_bar, holdout_end_bar = holdout_bars
        kept_folds = [f for f in folds if f.outer_test_end <= holdout_start_bar]
        dropped = len(folds) - len(kept_folds)
        if dropped:
            print(
                f"[STR-0309] Dropped {dropped} fold(s) overlapping frozen holdout "
                f"(bars {holdout_start_bar}..{holdout_end_bar})"
            )
        folds = kept_folds
        if not folds:
            raise ValueError(
                f"All {len(folds) + dropped} folds overlap the frozen holdout "
                f"(bars {holdout_start_bar}..{holdout_end_bar}); reduce horizon or extend data"
            )

    # Generate parameter combinations
    param_combos = _generate_param_combinations(spec.param_grid)
    search_space_hash_val = search_space_hash(spec.param_grid)

    # Compute real provenance hashes (Phase 3: Real Provenance)
    strategy_code_sha = _compute_strategy_code_sha(spec.strategy_id)
    data_manifest_sha = _compute_data_manifest_sha(spec.symbol, spec.timeframe)
    feature_schema_hash = _compute_feature_schema_hash(descriptor)
    commit_sha = _compute_commit_sha()
    worktree_dirty = _is_git_worktree_dirty()
    study_manifest = WFOStudyManifest(
        strategy_id=spec.strategy_id,
        symbol=spec.symbol,
        timeframe=spec.timeframe,
        param_grid=spec.param_grid,
        cost_scenarios=tuple(
            {
                "name": scenario.name,
                "fee_multiplier": scenario.fee_multiplier,
                "slippage_multiplier": scenario.slippage_multiplier,
            }
            for scenario in spec.cost_scenarios
        ),
        fold_windows=tuple(asdict(fold) for fold in folds),
        purge_bars=purge,
        embargo_bars=embargo,
        min_oos_trades=spec.effective_min_oos_trades,
        search_family=spec.search_family,
        evaluator_version=spec.evaluator_version,
        training_contract=training_contract,
        seed=spec.seed,
        commit_sha=commit_sha,
        worktree_dirty=worktree_dirty,
        strategy_code_sha=strategy_code_sha,
        data_manifest_sha=data_manifest_sha,
        feature_schema_hash=feature_schema_hash,
        search_space_hash=search_space_hash_val,
        evidence_class=spec.evidence_class,
    )
    study_manifest_path = _persist_study_manifest(out_root, study_manifest)

    # R03: gate policy version is the SAME for all evaluations under this
    # study. Declared once and used in every evaluation identity record.
    POLICY_VERSION = "v1"

    inner_results = []
    outer_results = []
    inner_selection_freezes = []
    # Distinct candidate experiment ids registered in the append-only registry
    # (one per unique params x cost). Used for full technical identity.
    candidate_experiment_ids: set[str] = set()

    # Register the search space as an experiment with real provenance
    exp_spec = ExperimentSpec.build(
        strategy_name=spec.strategy_id,
        strategy_code_sha=strategy_code_sha,
        data_manifest_sha=data_manifest_sha,
        feature_schema_hash=feature_schema_hash,
        params_hash=search_space_hash_val,
        search_family=spec.search_family,
        search_space_hash=search_space_hash_val,
        target_horizon=f"{spec.timeframe}_wfo",
        evaluator_version=spec.evaluator_version,
        seed=spec.seed,
    )
    # NOTE: exp_spec is the study-level identity used in registry_identity below.
    # It is intentionally NOT registered as a trial row — only per-candidate
    # (params, cost) experiments are registered so the registry's trial accounting
    # reflects the real search-space size (S3-2).

    # For each outer fold
    for fold_idx, fold in enumerate(folds):
        fold_id = fold.fold_id

        # STR-0309: fail-closed guard — this fold's data window must never touch
        # the frozen holdout. (Folds overlapping it were already dropped above,
        # but this is defense-in-depth before any expensive backtest runs.)
        if holdout_start_bar is not None:
            _guard_fold_against_holdout(
                fold,
                df,
                descriptor,
                holdout_start_bar,
                holdout_end_bar,  # type: ignore[arg-type]
            )

        # Compute the environment hash for this fold once (identical for the
        # inner-validation and outer-OOS trial records of this fold).
        env_hash = _compute_environment_hash(spec, fold)

        # Inner loop: parameter selection on train/val
        best_val_sharpe = -np.inf
        best_params = None
        best_val_metrics = {}
        best_candidate_experiment_id = None
        candidate_metrics = []

        # R02: Collect all inner-validation cells across (params × cost) and
        # dispatch them in a single batch via run_batch() if the cell_runner
        # supports batching. Previously each cell was submitted + awaited
        # sequentially inside _run_parameter_trial, providing no parallelism.
        batch_cells: list[tuple[EvaluationCellSpec, int, int, bool, int, int]] = []
        cell_keys: list[tuple[dict, CostScenario, str, int]] = []  # (params, cost_scenario, cost_name, experiment_id)

        warmup = descriptor.warmup_bars  # reuse descriptor warmup
        buffer_bars = 100
        sim_start = max(0, fold.inner_train_start - warmup - buffer_bars)

        for params in param_combos:
            for cost_scenario in spec.cost_scenarios:
                params_with_cost = {**params, "cost_scenario": cost_scenario.name}
                candidate_spec = ExperimentSpec.build(
                    strategy_name=spec.strategy_id,
                    strategy_code_sha=strategy_code_sha,
                    data_manifest_sha=data_manifest_sha,
                    feature_schema_hash=feature_schema_hash,
                    params_hash=param_hash(params_with_cost),
                    search_family=spec.search_family,
                    search_space_hash=search_space_hash_val,
                    target_horizon=f"{spec.timeframe}_wfo",
                    evaluator_version=spec.evaluator_version,
                    seed=spec.seed,
                )
                stored_candidate = registry.register_experiment(candidate_spec)
                candidate_experiment_ids.add(stored_candidate.experiment_id)

                spec_val = EvaluationCellSpec(
                    strategy_id=spec.strategy_id,
                    symbol=spec.symbol,
                    timeframe=spec.timeframe,
                    params=params,
                    cost_scenario=cost_scenario,
                )
                batch_cells.append((
                    spec_val, sim_start, fold.inner_val_end,
                    True, fold.inner_val_start, fold.inner_val_end,
                ))
                cell_keys.append((
                    params, cost_scenario, cost_scenario.name, stored_candidate.experiment_id,
                ))

        # Dispatch all cells in parallel (or serially fallback)
        if hasattr(cell_runner, "run_batch"):
            artifacts = cell_runner.run_batch(batch_cells)  # type: ignore[union-attr]
        else:
            # Fallback: serial execution
            runner = cell_runner or run_cell
            artifacts = [
                runner(spec_val, out_root=out_root, start=sim_start,
                       end=fold.inner_val_end, fresh=True,
                       measurement_start=fold.inner_val_start,
                       measurement_end=fold.inner_val_end)
                for spec_val, _, _, _, _, _ in batch_cells
            ]

        # Map results back
        for idx, (params, cost_scenario_obj, cost_name, experiment_id) in enumerate(cell_keys):
            params_with_cost = {**params, "cost_scenario": cost_name}
            cost_scenario = cost_scenario_obj  # R02: restore loop var for downstream
            artifact_val = artifacts[idx] if artifacts[idx] else None

            if artifact_val is None or artifact_val.status != "COMPLETED":
                val_sharpe = -np.inf
                val_metrics = {}
            else:
                val_sharpe = float(artifact_val.metrics.get("sharpe", -np.inf))
                val_metrics = dict(artifact_val.metrics)

            candidate_metrics.append(
                {
                    "params": params,
                    "cost_scenario": cost_name,
                    "val_sharpe": val_sharpe,
                    "val_metrics": val_metrics,
                }
            )
            # S3-2: every attempted (params, cost, fold) is evidence. Failed,
            # no-trade and non-finite trials remain in the append-only burden
            # instead of disappearing from multiple-testing accounting.
            if not registry.has_trial_phase(
                experiment_id,
                fold_id,
                TRIAL_PHASE_INNER_VALIDATION,
            ):
                metric_available = bool(np.isfinite(val_sharpe))
                artifact_status = artifact_val.status if artifact_val else "FAILED"
                # R03: stamp the full evaluation identity on every trial
                    # record so consumers (ResumeGuard) can verify resume
                # compatibility from any single registry entry.
                inner_identity = evaluation_identity(
                    strategy_id=spec.strategy_id,
                    symbol=spec.symbol,
                    timeframe=spec.timeframe,
                    strategy_code_sha=strategy_code_sha,
                    data_manifest_sha=data_manifest_sha,
                    feature_schema_hash=feature_schema_hash,
                    search_space_hash=search_space_hash_val,
                    evaluator_version=spec.evaluator_version,
                    policy_version=POLICY_VERSION,
                    cost_scenario=cost_scenario.name,
                    window_kind=TRIAL_PHASE_INNER_VALIDATION,
                    seed=spec.seed,
                    commit_sha=commit_sha,
                    extra={
                        "params_hash": param_hash(params_with_cost),
                        "study_manifest_id": study_manifest.manifest_id,
                    },
                )
                inner_metadata = attach_evaluation_identity(
                    {
                        "params": params_with_cost,
                        "cost_scenario": cost_scenario.name,
                        "freeze_id": None,
                        "status": artifact_status,
                        "metric_available": metric_available,
                        "failure_reasons": list(artifact_val.failure_reasons)
                        if artifact_val is not None
                        else ["missing_artifact"],
                    },
                    inner_identity,
                )
                registry.append_evaluation(
                    experiment_id=experiment_id,
                    fold_id=fold_id,
                    metric_name=(
                        "inner_val_sharpe"
                        if metric_available
                        else "inner_val_trial_failed"
                    ),
                    metric_value=float(val_sharpe) if metric_available else 0.0,
                    environment_hash=env_hash,
                    trial_phase=TRIAL_PHASE_INNER_VALIDATION,
                    metadata=inner_metadata,
                )
            if val_sharpe > best_val_sharpe:
                best_val_sharpe = val_sharpe
                best_params = params_with_cost
                best_val_metrics = val_metrics
                best_candidate_experiment_id = experiment_id

        inner_results.append(
            WFOInnerResult(
                fold_id=fold_id,
                train_start=fold.inner_train_start,
                train_end=fold.inner_train_end,
                val_start=fold.inner_val_start,
                val_end=fold.inner_val_end,
                best_params=best_params or {},
                best_val_sharpe=best_val_sharpe,
                val_metrics=best_val_metrics,
                n_trials=len(candidate_metrics),
                candidate_metrics=candidate_metrics,
            )
        )

        # STR-0304: Create InnerSelectionFreeze BEFORE outer evaluation
        # This binds the selected parameters to this fold immutably
        if best_params:
            freeze = InnerSelectionFreeze(
                fold_id=fold_id,
                strategy_id=spec.strategy_id,
                symbol=spec.symbol,
                timeframe=spec.timeframe,
                best_params=best_params,
                best_val_sharpe=best_val_sharpe,
                inner_train_end=fold.inner_train_end,
                inner_val_start=fold.inner_val_start,
                inner_val_end=fold.inner_val_end,
                search_space_hash=search_space_hash_val,
                candidate_count=len(candidate_metrics),
                commit_sha=commit_sha,
                data_manifest_sha=data_manifest_sha,
                feature_schema_hash=feature_schema_hash,
                evaluator_version=spec.evaluator_version,
            )
            # S3-6: atomic persistence of inner selection freeze
            freeze = _persist_inner_selection_freeze(out_root, freeze)
            inner_selection_freezes.append(freeze)

            # Check if outer fold was already evaluated with this freeze (idempotent replay)
            existing_artifact = _find_existing_outer_artifact(
                out_root, freeze.freeze_id, fold_id
            )
            if existing_artifact:
                # Replay: return existing artifact, don't re-run
                test_metrics = (
                    dict(existing_artifact.metrics)
                    if existing_artifact.status == "COMPLETED"
                    else {}
                )
                execution_health = dict(existing_artifact.execution_health)
                artifact = existing_artifact
            else:
                # First evaluation: run outer test
                cost_scenario = next(
                    c
                    for c in spec.cost_scenarios
                    if c.name == best_params.get("cost_scenario", "1x")
                )
                test_params = {
                    k: v for k, v in best_params.items() if k != "cost_scenario"
                }

                spec_test = EvaluationCellSpec(
                    strategy_id=spec.strategy_id,
                    symbol=spec.symbol,
                    timeframe=spec.timeframe,
                    params=test_params,
                    cost_scenario=cost_scenario,
                )
                runner = cell_runner or run_cell
                artifact = runner(
                    spec_test,
                    out_root=out_root,
                    start=fold.outer_test_start,
                    end=fold.outer_test_end,
                    fresh=True,
                    measurement_start=fold.outer_test_start,
                    measurement_end=fold.outer_test_end,
                )
                artifact = _persist_outer_artifact(
                    out_root, freeze.freeze_id, fold_id, artifact
                )

                test_metrics = (
                    dict(artifact.metrics) if artifact.status == "COMPLETED" else {}
                )
                execution_health = (
                    dict(artifact.execution_health)
                    if artifact.status == "COMPLETED"
                    else {}
                )

            outer_results.append(
                WFOOuterResult(
                    fold_id=fold_id,
                    test_start=fold.outer_test_start,
                    test_end=fold.outer_test_end,
                    params=best_params,
                    test_metrics=test_metrics,
                    execution_health=execution_health,
                    artifact=artifact
                    if artifact and artifact.status == "COMPLETED"
                    else None,
                )
            )

            # Log to registry: append-only OUTER_OOS trial record for this fold,
            # bound to the selected candidate's experiment id. Idempotent —
            # replays of the same (candidate, fold, OUTER_OOS) do not duplicate.
            test_sharpe = test_metrics.get("sharpe", -np.inf)
            if (
                best_candidate_experiment_id is not None
                and not registry.has_trial_phase(
                    best_candidate_experiment_id, fold_id, TRIAL_PHASE_OUTER_OOS
                )
            ):
                metric_available = bool(np.isfinite(test_sharpe))
                # R03: stamp the full evaluation identity on OUTER_OOS too.
                outer_cost = best_params.get("cost_scenario", "1x")
                outer_identity = evaluation_identity(
                    strategy_id=spec.strategy_id,
                    symbol=spec.symbol,
                    timeframe=spec.timeframe,
                    strategy_code_sha=strategy_code_sha,
                    data_manifest_sha=data_manifest_sha,
                    feature_schema_hash=feature_schema_hash,
                    search_space_hash=search_space_hash_val,
                    evaluator_version=spec.evaluator_version,
                    policy_version=POLICY_VERSION,
                    cost_scenario=outer_cost,
                    window_kind=TRIAL_PHASE_OUTER_OOS,
                    seed=spec.seed,
                    commit_sha=commit_sha,
                    extra={
                        "params_hash": param_hash(
                            {
                                k: v
                                for k, v in best_params.items()
                                if k != "cost_scenario"
                            }
                        ),
                        "freeze_id": freeze.freeze_id,
                        "study_manifest_id": study_manifest.manifest_id,
                    },
                )
                outer_metadata = attach_evaluation_identity(
                    {
                        "params": {
                            k: v for k, v in best_params.items() if k != "cost_scenario"
                        },
                        "cost_scenario": outer_cost,
                        "freeze_id": freeze.freeze_id,
                        "status": artifact.status if artifact is not None else "FAILED",
                        "metric_available": metric_available,
                        "failure_reasons": list(artifact.failure_reasons)
                        if artifact is not None
                        else ["missing_artifact"],
                    },
                    outer_identity,
                )
                registry.append_evaluation(
                    experiment_id=best_candidate_experiment_id,
                    fold_id=fold_id,
                    metric_name=(
                        "outer_oos_sharpe"
                        if metric_available
                        else "outer_oos_trial_failed"
                    ),
                    metric_value=float(test_sharpe) if metric_available else 0.0,
                    environment_hash=env_hash,
                    trial_phase=TRIAL_PHASE_OUTER_OOS,
                    metadata=outer_metadata,
                )

    # Aggregate statistics
    test_sharpes = [
        r.test_metrics.get("sharpe", 0) for r in outer_results if r.test_metrics
    ]
    test_returns = [
        r.test_metrics.get("total_return_pct", 0)
        for r in outer_results
        if r.test_metrics
    ]
    test_trades = [
        r.test_metrics.get("total_trades", 0) for r in outer_results if r.test_metrics
    ]
    test_net_pnls = [
        value
        for r in outer_results
        if r.test_metrics
        for value in [_coerce_num(r.test_metrics.get("net_pnl"))]
        if value is not None
    ]

    # S3-3: collect the REAL per-bar return series from each outer fold artifact.
    # The measurement-window isolation (S3-1) means ``artifact.metrics`` already
    # reflects only the outer-OOS window, so ``return_series`` is a genuine
    # OOS return series per fold — not a proxy for fold Sharpe values.
    outer_return_series: list[list[float]] = []
    outer_return_series_periods: list[int] = []
    for r in outer_results:
        if r.artifact and r.artifact.metrics:
            series = r.artifact.metrics.get("return_series")
            if isinstance(series, list) and len(series) >= 3:
                outer_return_series.append([float(x) for x in series])
                outer_return_series_periods.append(len(series))

    # S3-3: CSCV consumes real, aligned per-bar validation returns. A fold ×
    # Sharpe proxy has the wrong statistical meaning and is intentionally rejected.
    candidate_returns_matrix, pbo_matrix_evidence = _build_pbo_candidate_returns(
        inner_results
    )

    # Statistical hardening on REAL outer-OOS return series (S3-3).
    statistical_hardening: dict[str, Any] = {}

    # Concatenate all fold return series into one continuous OOS series for
    # CI/PSR/DSR. Each fold's series already excludes purge/embargo bars via
    # the S3-1 measurement window, so the concatenation is a valid OOS sample.
    if outer_return_series:
        flat_returns = np.concatenate(
            [np.asarray(s, dtype=np.float64) for s in outer_return_series]
        )
    else:
        flat_returns = np.asarray([], dtype=np.float64)

    # Trial count comes from the append-only registry (S3-2), not a manual
    # counter. total_trial_runs = INNER_VALIDATION + OUTER_OOS records.
    trial_counts = registry.trial_counts(experiment_ids=candidate_experiment_ids)

    # periods_per_year depends on timeframe
    periods_per_year = {"1h": 24 * 365, "4h": 6 * 365, "1d": 365}.get(
        spec.timeframe, 24 * 365
    )

    # --- Block-bootstrap CI on REAL OOS returns ---
    if flat_returns.size >= 10:
        try:
            lo, hi, _boot = block_bootstrap_sharpe_ci(
                flat_returns,
                periods_per_year=periods_per_year,
                iters=1000,
                seed=spec.seed,
            )
            statistical_hardening["sharpe_ci95_lo"] = float(lo)
            statistical_hardening["sharpe_ci95_hi"] = float(hi)
        except Exception as exc:
            statistical_hardening["sharpe_ci95_lo"] = None
            statistical_hardening["sharpe_ci95_hi"] = None
            statistical_hardening["sharpe_ci95_error"] = str(exc)
    else:
        statistical_hardening["sharpe_ci95_lo"] = None
        statistical_hardening["sharpe_ci95_hi"] = None
        statistical_hardening["sharpe_ci95_note"] = (
            f"need >= 10 return observations, got {flat_returns.size}"
        )

    # --- PSR / DSR from REAL OOS return series ---
    if flat_returns.size >= 3:
        try:
            s = series_stats(flat_returns, periods_per_year=periods_per_year)
            psr = probabilistic_sharpe_ratio(
                s.sharpe,
                sr_benchmark=0.0,
                skew=s.skew,
                excess_kurtosis=s.excess_kurtosis,
                n=s.n,
            )
            statistical_hardening["psr"] = float(psr)

            # DSR: Deflated Sharpe Ratio with multiple testing adjustment.
            # trials = number of distinct candidate configurations actually
            # registered in the registry (real search-space size), not a
            # hand-maintained counter.
            dsr = deflated_sharpe_ratio(
                s.sharpe,
                n=s.n,
                trials=trial_counts.unique_experiments,
                skew=s.skew,
                excess_kurtosis=s.excess_kurtosis,
                sr_benchmark=0.0,
            )
            statistical_hardening["dsr"] = float(dsr)
        except Exception as exc:
            statistical_hardening["psr"] = None
            statistical_hardening["dsr"] = None
            statistical_hardening["psr_dsr_error"] = str(exc)
    else:
        statistical_hardening["psr"] = None
        statistical_hardening["dsr"] = None
        statistical_hardening["psr_dsr_note"] = (
            f"need >= 3 return observations, got {flat_returns.size}"
        )

    # --- One-call summary from summarize_sharpe (reuses bootstrap + PSR + DSR) ---
    if flat_returns.size >= 3 and trial_counts.unique_experiments >= 1:
        try:
            summary = summarize_sharpe(
                flat_returns,
                periods_per_year=periods_per_year,
                trials=trial_counts.unique_experiments,
                experiment_registry=registry,
                experiment_ids=candidate_experiment_ids,
                bootstrap_iters=1000,
                seed=spec.seed,
                sr_benchmark=0.0,
            )
            statistical_hardening["summary"] = summary
        except Exception as exc:
            statistical_hardening["summary_error"] = str(exc)

    # --- PBO: Probability of Backtest Overfitting via CSCV ---
    statistical_hardening["pbo_matrix"] = pbo_matrix_evidence
    if candidate_returns_matrix is not None:
        try:
            pbo = probability_of_backtest_overfitting(
                candidate_returns_matrix,
                n_slices=8,
            )
            statistical_hardening["pbo"] = float(pbo)
        except Exception as exc:
            statistical_hardening["pbo"] = None
            statistical_hardening["pbo_error"] = str(exc)
    else:
        statistical_hardening["pbo"] = None
        statistical_hardening["pbo_note"] = pbo_matrix_evidence.get("reason")

    # --- Parameter Stability (STR-0306): correlation of fold-level param selections ---
    parameter_stability_value = _compute_parameter_stability(inner_results)
    statistical_hardening["param_stability"] = parameter_stability_value

    # --- S3-3: per-fold return-series provenance ---
    statistical_hardening["return_series_folds"] = len(outer_return_series)
    statistical_hardening["return_series_observations"] = int(flat_returns.size)
    statistical_hardening["return_series_periods_per_fold"] = (
        outer_return_series_periods
    )

    # Trial count info — all sourced from the append-only registry (S3-2)
    statistical_hardening["effective_trial_count"] = trial_counts.effective_trial_count
    statistical_hardening["raw_trial_count"] = trial_counts.unique_experiments
    statistical_hardening["inner_validation_trials"] = (
        trial_counts.inner_validation_trials
    )
    statistical_hardening["outer_oos_trials"] = trial_counts.outer_oos_trials
    statistical_hardening["total_trial_runs"] = trial_counts.total_trial_runs
    statistical_hardening["dsr_trials"] = trial_counts.unique_experiments
    statistical_hardening["trial_methodology"] = trial_counts.methodology

    # ============================================================
    # HARD GATES — Full implementation per roadmap (STR-0306/0309/0310)
    # Policy version: "v1" (declared above so R03 identity records can use it)
    # Each gate returns structured GateResult with PASS/FAIL/INVALID
    # INVALID is treated as FAIL
    # ============================================================
    gate_results: list[GateResult] = []
    gate_failures = []
    passes = True

    # Helper to add gate result
    def add_gate(
        gate_id: str,
        observed_value: float | None,
        threshold: float,
        comparison: str,
        reason: str,
        evidence_artifact: str | None = None,
    ) -> GateResult:
        """Evaluate a gate and return GateResult."""
        if observed_value is None or (
            isinstance(observed_value, float)
            and (np.isnan(observed_value) or np.isinf(observed_value))
        ):
            verdict = "INVALID"
            passed = False
        else:
            if comparison == ">=":
                passed = observed_value >= threshold
            elif comparison == ">":
                passed = observed_value > threshold
            elif comparison == "<=":
                passed = observed_value <= threshold
            elif comparison == "<":
                passed = observed_value < threshold
            elif comparison == "==":
                passed = observed_value == threshold
            elif comparison == "!=":
                passed = observed_value != threshold
            else:
                verdict = "INVALID"
                passed = False
            verdict = "PASS" if passed else "FAIL"

        if verdict == "INVALID":
            reason = f"{reason} (INVALID: observed_value={observed_value})"

        gate_result = GateResult(
            gate_id=gate_id,
            policy_version=POLICY_VERSION,
            observed_value=observed_value,
            threshold=threshold,
            comparison=comparison,
            verdict=verdict,
            reason=reason,
            evidence_artifact=evidence_artifact,
        )
        gate_results.append(gate_result)

        if not passed:
            gate_failures.append(gate_id)

        return gate_result

    # --- Single-pair/strategy OOS gates ---

    # Gate 1: Outer-OOS net return > 0 (median across folds)
    median_return = float(np.median(test_returns)) if test_returns else None
    add_gate(
        gate_id="outer_oos_net_return_positive",
        observed_value=median_return,
        threshold=0.0,
        comparison=">",
        reason=f"Median outer OOS return {'{:.2f}%'.format(median_return) if median_return is not None else 'N/A'} must be > 0%",
    )

    # Gate 2: OOS Sharpe ≥ 0.80 (median across folds)
    median_sharpe = float(np.median(test_sharpes)) if test_sharpes else None
    add_gate(
        gate_id="outer_oos_sharpe_ge_080",
        observed_value=median_sharpe,
        threshold=0.80,
        comparison=">=",
        reason=f"Median outer OOS Sharpe {'{:.3f}'.format(median_sharpe) if median_sharpe is not None else 'N/A'} must be ≥ 0.80",
    )

    # Gate 3: Profit factor ≥ 1.20 (median across folds)
    test_profit_factors = [
        _coerce_num(r.test_metrics.get("profit_factor"))
        for r in outer_results
        if r.test_metrics and r.test_metrics.get("profit_factor") is not None
    ]
    median_pf = float(np.median(test_profit_factors)) if test_profit_factors else None
    add_gate(
        gate_id="outer_oos_profit_factor_ge_120",
        observed_value=median_pf,
        threshold=1.20,
        comparison=">=",
        reason=f"Median outer OOS profit factor {'{:.3f}'.format(median_pf) if median_pf is not None else 'N/A'} must be ≥ 1.20",
    )

    # Gate 4: Max drawdown ≤ 10% (median across folds)
    test_max_dd = [
        r.test_metrics.get("max_drawdown_pct", 0)
        for r in outer_results
        if r.test_metrics and r.test_metrics.get("max_drawdown_pct") is not None
    ]
    median_max_dd = float(np.median(test_max_dd)) if test_max_dd else None
    add_gate(
        gate_id="outer_oos_max_drawdown_le_10pct",
        observed_value=median_max_dd,
        threshold=10.0,
        comparison="<=",
        reason=f"Median outer OOS max drawdown {'{:.2f}%'.format(median_max_dd) if median_max_dd is not None else 'N/A'} must be ≤ 10%",
    )

    # Gate 5: Calmar ≥ 0.50 (median across folds)
    test_calmar = [
        r.test_metrics.get("calmar", 0)
        for r in outer_results
        if r.test_metrics and r.test_metrics.get("calmar") is not None
    ]
    median_calmar = float(np.median(test_calmar)) if test_calmar else None
    add_gate(
        gate_id="outer_oos_calmar_ge_050",
        observed_value=median_calmar,
        threshold=0.50,
        comparison=">=",
        reason=f"Median outer OOS Calmar {'{:.3f}'.format(median_calmar) if median_calmar is not None else 'N/A'} must be ≥ 0.50",
    )

    # Gate 6: DSR ≥ 0.95
    dsr_value = statistical_hardening.get("dsr")
    add_gate(
        gate_id="dsr_ge_095",
        observed_value=dsr_value,
        threshold=0.95,
        comparison=">=",
        reason=f"Deflated Sharpe Ratio {'{:.3f}'.format(dsr_value) if dsr_value is not None else 'N/A'} must be ≥ 0.95",
        evidence_artifact="statistical_hardening.dsr",
    )

    # Gate 7: PBO ≤ 0.20
    pbo_value = statistical_hardening.get("pbo")
    add_gate(
        gate_id="pbo_le_020",
        observed_value=pbo_value,
        threshold=0.20,
        comparison="<=",
        reason=f"Probability of Backtest Overfitting {'{:.3f}'.format(pbo_value) if pbo_value is not None else 'N/A'} must be ≤ 0.20",
        evidence_artifact="statistical_hardening.pbo",
    )

    # Gate 8: Parameter stability ≥ 0.70
    parameter_stability_gate_value = _coerce_num(
        statistical_hardening.get("param_stability")
    )
    add_gate(
        gate_id="parameter_stability_ge_070",
        observed_value=parameter_stability_gate_value,
        threshold=0.70,
        comparison=">=",
        reason=f"Parameter stability {'{:.3f}'.format(parameter_stability_gate_value) if parameter_stability_gate_value is not None else 'N/A'} must be ≥ 0.70",
        evidence_artifact="statistical_hardening.param_stability",
    )

    # Gate 9: Positive outer folds ≥ 60%
    positive_folds = sum(1 for s in test_sharpes if s > 0)
    positive_folds_pct = (
        (positive_folds / len(test_sharpes) * 100) if test_sharpes else 0.0
    )
    add_gate(
        gate_id="positive_outer_folds_ge_60pct",
        observed_value=positive_folds_pct,
        threshold=60.0,
        comparison=">=",
        reason=f"Positive outer folds {positive_folds_pct:.1f}% must be ≥ 60%",
    )

    # Gate 10: Minimum trades per pair-strategy OOS ≥ 30
    total_trades = sum(test_trades)
    min_required_trades = spec.effective_min_oos_trades
    add_gate(
        gate_id="min_trades_per_pair_strategy_ge_30",
        observed_value=float(total_trades),
        threshold=float(min_required_trades),
        comparison=">=",
        reason=f"Total pair-strategy outer OOS trades {total_trades} must be ≥ {min_required_trades}",
    )

    # --- Sensitivity analysis (Phase 6: STR-0308) ---
    # Must be computed BEFORE the gates that read it (drop-best-trade, cost-2x).
    sensitivity_eval = _compute_sensitivity_analysis(
        spec=spec,
        outer_results=outer_results,
        folds=folds,
        out_root=out_root if real_sensitivity else None,
        descriptor=descriptor if real_sensitivity else None,
    )

    # Gate 11: Drop best trade — net PnL still > 0
    drop_best_trade_pnl = (
        sensitivity_eval.get("drop_best_trade", {})
        .get("aggregate", {})
        .get("total_net_pnl_after_drop")
    )
    add_gate(
        gate_id="drop_best_trade_net_pnl_positive",
        observed_value=drop_best_trade_pnl,
        threshold=0.0,
        comparison=">",
        reason=f"Net PnL after dropping best trade {'{:.2f}'.format(drop_best_trade_pnl) if drop_best_trade_pnl is not None else 'N/A'} must be > 0",
        evidence_artifact="sensitivity.drop_best_trade",
    )

    # Gate 12: Cost 2× — net PnL > 0 and PF > 1
    cost_2x_pnl = (
        sensitivity_eval.get("cost_2x", {}).get("aggregate", {}).get("median_net_pnl")
    )
    cost_2x_pf = (
        sensitivity_eval.get("cost_2x", {})
        .get("aggregate", {})
        .get("median_profit_factor")
    )
    add_gate(
        gate_id="cost_2x_net_pnl_positive",
        observed_value=cost_2x_pnl,
        threshold=0.0,
        comparison=">",
        reason=f"Net PnL under 2× cost {'{:.2f}'.format(cost_2x_pnl) if cost_2x_pnl is not None else 'N/A'} must be > 0",
        evidence_artifact="sensitivity.cost_2x",
    )
    add_gate(
        gate_id="cost_2x_profit_factor_gt_1",
        observed_value=cost_2x_pf,
        threshold=1.0,
        comparison=">",
        reason=f"Profit factor under 2× cost {'{:.3f}'.format(cost_2x_pf) if cost_2x_pf is not None else 'N/A'} must be > 1",
        evidence_artifact="sensitivity.cost_2x",
    )

    # Gate 13: Delay 1 bar — net PnL > 0 and PF > 1
    delay_1_bar_pnl = (
        sensitivity_eval.get("delay_1_bar", {})
        .get("aggregate", {})
        .get("median_net_pnl")
    )
    delay_1_bar_pf = (
        sensitivity_eval.get("delay_1_bar", {})
        .get("aggregate", {})
        .get("median_profit_factor")
    )
    add_gate(
        gate_id="delay_1_bar_net_pnl_positive",
        observed_value=delay_1_bar_pnl,
        threshold=0.0,
        comparison=">",
        reason=f"Net PnL under 1-bar signal delay {'{:.2f}'.format(delay_1_bar_pnl) if delay_1_bar_pnl is not None else 'N/A'} must be > 0",
        evidence_artifact="sensitivity.delay_1_bar",
    )
    add_gate(
        gate_id="delay_1_bar_profit_factor_gt_1",
        observed_value=delay_1_bar_pf,
        threshold=1.0,
        comparison=">",
        reason=f"Profit factor under 1-bar signal delay {'{:.3f}'.format(delay_1_bar_pf) if delay_1_bar_pf is not None else 'N/A'} must be > 1",
        evidence_artifact="sensitivity.delay_1_bar",
    )

    # Gate 14: Parameter neighbors — all neighbors should have positive net PnL
    # For each parameter with neighbors tested, require median neighbor net PnL > 0
    param_neighbors = sensitivity_eval.get("parameter_neighbors", [])
    neighbor_pnls: list[float] = []
    for pn in param_neighbors:
        for n in pn.get("neighbors", []):
            net_pnl = _coerce_num(n.get("net_pnl"))
            if net_pnl is not None:
                neighbor_pnls.append(net_pnl)
    median_neighbor_pnl = float(np.median(neighbor_pnls)) if neighbor_pnls else None
    add_gate(
        gate_id="parameter_neighbors_net_pnl_positive",
        observed_value=median_neighbor_pnl,
        threshold=0.0,
        comparison=">",
        reason=f"Median parameter neighbor net PnL {'{:.2f}'.format(median_neighbor_pnl) if median_neighbor_pnl is not None else 'N/A'} must be > 0",
        evidence_artifact="sensitivity.parameter_neighbors",
    )

    # Determine overall pass/fail
    passes = all(g.is_pass() for g in gate_results)

    # Multi-dimensional evaluation (Phase 5: STR-0307)
    multi_dim_eval = _compute_multi_dimensional_evaluation(
        spec=spec,
        outer_results=outer_results,
        folds=folds,
    )

    # Aggregate metrics
    aggregate_metrics: dict[str, Any] = {
        "n_outer_folds": len(folds),
        "mean_test_sharpe": float(np.mean(test_sharpes)) if test_sharpes else 0.0,
        "median_test_sharpe": float(np.median(test_sharpes)) if test_sharpes else 0.0,
        "mean_test_return_pct": float(np.mean(test_returns)) if test_returns else 0.0,
        "median_test_return_pct": float(np.median(test_returns))
        if test_returns
        else 0.0,
        "total_test_trades": int(sum(test_trades)),
        "total_oos_net_pnl": float(sum(test_net_pnls)) if test_net_pnls else None,
        "positive_outer_folds_pct": positive_folds_pct,
        "median_profit_factor": median_pf if median_pf is not None else 0.0,
        "median_max_drawdown_pct": median_max_dd if median_max_dd is not None else 0.0,
        "median_calmar": median_calmar if median_calmar is not None else 0.0,
    }
    aggregate_metrics["multi_dimensional"] = multi_dim_eval
    aggregate_metrics["sensitivity"] = sensitivity_eval
    aggregate_metrics["study_manifest_id"] = study_manifest.manifest_id
    aggregate_metrics["study_manifest_path"] = str(study_manifest_path)
    aggregate_metrics["evidence_class"] = study_manifest.evidence_class
    aggregate_metrics["worktree_dirty"] = study_manifest.worktree_dirty
    aggregate_metrics["provenance_eligible"] = study_manifest.provenance_eligible

    trial_counts_dict = asdict(
        registry.trial_counts(experiment_ids=candidate_experiment_ids)
    )

    registry_identity = {
        "study_experiment_id": exp_spec.experiment_id,
        "candidate_experiment_ids": ",".join(sorted(candidate_experiment_ids)),
        "strategy_code_sha": strategy_code_sha,
        "data_manifest_sha": data_manifest_sha,
        "feature_schema_hash": feature_schema_hash,
        "search_space_hash": search_space_hash_val,
        "commit_sha": commit_sha,
        "worktree_dirty": str(worktree_dirty).lower(),
        "study_manifest_id": study_manifest.manifest_id,
    }
    policy_thresholds = {
        "outer_oos_net_return_positive": 0.0,
        "outer_oos_sharpe_ge_080": 0.80,
        "outer_oos_profit_factor_ge_120": 1.20,
        "outer_oos_max_drawdown_le_10pct": 10.0,
        "outer_oos_calmar_ge_050": 0.50,
        "dsr_ge_095": 0.95,
        "pbo_le_020": 0.20,
        "parameter_stability_ge_070": 0.70,
        "positive_outer_folds_ge_60pct": 60.0,
        "min_trades_per_pair_strategy_ge_30": float(spec.effective_min_oos_trades),
        "drop_best_trade_net_pnl_positive": 0.0,
        "cost_2x_net_pnl_positive": 0.0,
        "cost_2x_profit_factor_gt_1": 1.0,
        "delay_1_bar_net_pnl_positive": 0.0,
        "delay_1_bar_profit_factor_gt_1": 1.0,
        "parameter_neighbors_net_pnl_positive": 0.0,
    }

    # Run final holdout whenever explicitly requested (STR-0309).  Selection
    # gates still determine promotion, but executing the independent frozen
    # confirmation even for a rejected candidate preserves an auditable result
    # and lets the holdout gate distinguish ERROR from a normal NO_TRADE.
    final_holdout_result = None
    holdout_gates: list[GateResult] = []
    if run_holdout:
        # STR-0309: the final holdout MUST come from a frozen research manifest.
        # If no frozen manifest exists, this run is fail-closed — a last-10%
        # fallback window would reuse data that already influenced selection, which
        # defeats the purpose of an independent holdout.
        if holdout_start_bar is None:
            final_holdout_result = {
                "status": "ERROR",
                "error": (
                    "run_holdout=True but no frozen research_manifest.json "
                    "holdout window was resolved. STR-0309 forbids a last-10% "
                    "fallback because that window already influenced selection."
                ),
            }
        else:
            try:
                if holdout_end_bar is None:
                    raise ValueError(
                        "frozen holdout manifest is missing holdout_end_bar"
                    )
                # Use only the FROZEN holdout window from research_manifest.json;
                # a dataset-derived last-10% fallback is forbidden.
                # Inside run_holdout=True branch, holdout_start_bar is guaranteed non-None
                # (checked above), so holdout_end_bar is also non-None here.
                holdout_manifest = FinalHoldoutManifest(
                    strategy_id=spec.strategy_id,
                    symbol=spec.symbol,
                    timeframe=spec.timeframe,
                    holdout_start_bar=holdout_start_bar,
                    holdout_end_bar=holdout_end_bar,
                    data_manifest_sha=data_manifest_sha,
                    feature_schema_hash=feature_schema_hash,
                    freeze_timestamp=datetime.now(UTC).isoformat(),
                    frozen_by="research_system",
                    commit_sha_at_freeze=commit_sha,
                    notes=f"Final holdout from frozen research_manifest: bars {holdout_start_bar}..{holdout_end_bar}",
                )

                # Get best params from outer results (last fold = most data/most recent)
                selected_params = {}
                if outer_results:
                    last = outer_results[-1]
                    selected_params = dict(last.params)

                final_holdout_result = run_final_holdout(
                    spec=spec,
                    selected_params=selected_params,
                    manifest=holdout_manifest,
                    out_root=out_root,
                    actor=holdout_actor,
                )
            except Exception as e:  # noqa: BLE001
                final_holdout_result = {
                    "status": "ERROR",
                    "error": str(e),
                }

        # --- Gate 13: final holdout must COMPLETED and be independent ---
        # S3-7: holdout FAILED / ERROR / missing-manifest → the entire WFO run
        # is fail-closed. The holdout is the only truly independent confirmation,
        # so a failed holdout means the candidate does NOT pass hard gates.
        fh: dict[str, Any] = (
            final_holdout_result if isinstance(final_holdout_result, dict) else {}
        )
        holdout_status = fh.get("status")
        holdout_metrics: dict[str, Any] = fh.get("metrics") or {}
        holdout_sharpe = holdout_metrics.get("sharpe")
        holdout_net_return = holdout_metrics.get("total_return_pct")
        holdout_passes = holdout_status == "COMPLETED"
        holdout_reason = (
            f"Final holdout status={holdout_status}"
            if not holdout_passes
            else (
                f"Final holdout COMPLETED: sharpe={holdout_sharpe}, "
                f"return={holdout_net_return}"
            )
        )
        holdout_gates.append(
            GateResult(
                gate_id="final_holdout_completed",
                policy_version=POLICY_VERSION,
                observed_value=1.0 if holdout_passes else 0.0,
                threshold=1.0,
                comparison=">=",
                verdict="PASS" if holdout_passes else "FAIL",
                reason=holdout_reason,
                evidence_artifact="final_holdout.status",
            )
        )
        if not holdout_passes:
            gate_failures.append("final_holdout_completed")

        # Holdout is only meaningful if it actually ran on a frozen window.
        # A last-10% fallback window would reuse selection data, so the gate is
        # INVALID (not just FAIL) when the manifest was not frozen.
        frozen_window_used = bool((final_holdout_result or {}).get("holdout_window"))
        holdout_gates.append(
            GateResult(
                gate_id="final_holdout_frozen_window",
                policy_version=POLICY_VERSION,
                observed_value=1.0 if frozen_window_used else 0.0,
                threshold=1.0,
                comparison=">=",
                verdict="PASS" if frozen_window_used else "INVALID",
                reason=(
                    "Final holdout ran on a frozen research_manifest window"
                    if frozen_window_used
                    else "No frozen holdout window was used (last-10% fallback is forbidden by STR-0309)"
                ),
                evidence_artifact="final_holdout.holdout_window",
            )
        )
        if not frozen_window_used:
            gate_failures.append("final_holdout_frozen_window")

    # --- Recompute passes: holdout gates are part of the hard-gate set ---
    all_gate_results = list(gate_results) + list(holdout_gates)
    passes = all(g.is_pass() for g in all_gate_results)
    aggregate_metrics["promotable"] = bool(
        study_manifest.provenance_eligible
        and passes
        and final_holdout_result
        and final_holdout_result.get("status") == "COMPLETED"
    )

    # Create the formal rejection only after optional holdout gates have been
    # folded into the decision, so a failed holdout cannot yield a bare FAIL.
    no_trade_artifact = None
    if not passes:
        candidate_id = f"{spec.strategy_id}::{spec.symbol}"
        no_trade_artifact = FormalNoTradeArtifact(
            candidate_set=[candidate_id],
            gate_results={candidate_id: all_gate_results},
            gate_failures={candidate_id: list(gate_failures)},
            best_candidate=candidate_id,
            best_candidate_metrics=aggregate_metrics,
            registry_identity=registry_identity,
            policy_version=POLICY_VERSION,
            policy_thresholds=policy_thresholds,
            commit_sha=commit_sha,
            data_manifest_sha=data_manifest_sha,
            feature_schema_hash=feature_schema_hash,
            search_space_hash=search_space_hash_val,
            evaluation_timestamp=datetime.now(UTC).isoformat(),
            evaluation_duration_sec=0.0,
            notes=(
                f"Candidate {candidate_id} failed {len(gate_failures)} hard gates: "
                f"{', '.join(gate_failures)}"
            ),
        )

    # R03: aggregate provenance digest, computed from the canonical evaluation
    # identity. Binds this decision to the evidence that produced it.
    aggregate_provenance_identity = evaluation_identity(
        strategy_id=spec.strategy_id,
        symbol=spec.symbol,
        timeframe=spec.timeframe,
        strategy_code_sha=strategy_code_sha,
        data_manifest_sha=data_manifest_sha,
        feature_schema_hash=feature_schema_hash,
        search_space_hash=search_space_hash_val,
        evaluator_version=spec.evaluator_version,
        policy_version=POLICY_VERSION,
        cost_scenario="aggregate",
        window_kind="AGGREGATE",
        seed=spec.seed,
        commit_sha=commit_sha,
        extra={"study_manifest_id": study_manifest.manifest_id},
    )
    aggregate_provenance_digest = provenance_digest(aggregate_provenance_identity)

    # R03: validate the manifest against evidence on disk so the consumer
    # cannot silently re-aggregate a tampered/incomplete result. Only
    # validated for REAL_MARKET studies (synthetic evidence does not need
    # the same fail-closed gate).
    #
    # FAIL-CLOSED SEMANTICS: completeness_report must gate `passes` and
    # `promotable` — if the evidence is incomplete or tampered, the result
    # MUST NOT be promotable, even if all hard gates passed. This check runs
    # AFTER gate evaluation so completeness is a blocking condition.
    completeness_report: CompletenessReport | None = None
    if spec.evidence_class == "REAL_MARKET":
        try:
            validator = ManifestValidator(study_manifest, out_root)
            completeness_report = validator.validate_manifest()
        except Exception as exc:  # pragma: no cover - defensive
            # If we cannot even validate, fail-closed.
            completeness_report = CompletenessReport(
                is_complete=False,
                expected_count=len(folds),
                found_count=0,
                issues=(f"manifest validator raised: {exc!r}",),
            )

    # --- Fold completeness into passes / promotable / no_trade_artifact ---
    # This must happen AFTER completeness_report is computed (line above),
    # NOT before. Previously `passes` was computed at line ~3436 without
    # considering completeness, so an incomplete evidence set could still
    # yield passes_hard_gates=True. Fix: fail-closed if completeness fails.
    if completeness_report is not None and not completeness_report.is_complete:
        # Fail-closed: incomplete evidence blocks promotion even if gates passed.
        issues_str = ";".join(completeness_report.issues or [])
        gate_failures.append(f"completeness_check:{issues_str}")
        passes = False
        aggregate_metrics["promotable"] = False
        # Build or update formal no-trade artifact for completeness failure
        candidate_id = f"{spec.strategy_id}::{spec.symbol}"
        if no_trade_artifact is None:
            no_trade_artifact = FormalNoTradeArtifact(
                candidate_set=[candidate_id],
                gate_results={candidate_id: all_gate_results},
                gate_failures={candidate_id: list(gate_failures)},
                best_candidate=candidate_id,
                best_candidate_metrics=aggregate_metrics,
                registry_identity=registry_identity,
                policy_version=POLICY_VERSION,
                policy_thresholds=policy_thresholds,
                commit_sha=commit_sha,
                data_manifest_sha=data_manifest_sha,
                feature_schema_hash=feature_schema_hash,
                search_space_hash=search_space_hash_val,
                evaluation_timestamp=datetime.now(UTC).isoformat(),
                evaluation_duration_sec=0.0,
                notes=(
                    f"Manifest completeness check failed for {candidate_id}: "
                    f"{issues_str}"
                ),
            )

    result = WFOResult(
        spec=spec,
        inner_results=inner_results,
        outer_results=outer_results,
        inner_selection_freezes=inner_selection_freezes,
        aggregate_metrics=aggregate_metrics,
        statistical_hardening=statistical_hardening,
        gate_results=all_gate_results,
        passes_hard_gates=passes,
        gate_failures=gate_failures,
        no_trade_artifact=no_trade_artifact,
        final_holdout=final_holdout_result,
        trial_counts=trial_counts_dict,
        study_manifest=study_manifest,
        completeness_report=completeness_report,
        provenance_digest=aggregate_provenance_digest,
    )
    # S3-9: persist composite evidence artifact
    if out_root is not None:
        _persist_wfo_result(out_root, result)
    return result


def _persist_wfo_result(out_root: Path, result: WFOResult) -> Path:
    """Persist the composite WFO decision artifact atomically.

    Uses allow_nan=True to handle infinite values (e.g., profit factor when
    there are no losing trades) — JSON serializes them as "Infinity" which is
    deterministic and safe for content-addressing.
    """
    path = Path(out_root) / "wfo_decision.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(".json.tmp")
    tmp_path.write_text(
        json.dumps(result.to_dict(), indent=2, allow_nan=True, default=str),
        encoding="utf-8",
    )
    tmp_path.replace(path)
    return path


def _combined_identity(values: list[Any]) -> str:
    payload = json.dumps(values, sort_keys=True, separators=(",", ":"), default=str)
    return f"sha256:{hashlib.sha256(payload.encode()).hexdigest()}"


def _build_portfolio_selection_result(
    results: list[WFOResult],
    *,
    run_holdout: bool,
    out_root: Path | None = None,
    policy: PortfolioGatePolicy | None = None,
) -> WFOPortfolioResult:
    if not results:
        raise ValueError("portfolio selection requires at least one WFO result")

    policy = policy or PortfolioGatePolicy()
    policy_version = policy.policy_version
    rows: list[dict[str, Any]] = []
    for result in results:
        pair_id = f"{result.spec.strategy_id}::{result.spec.symbol}"
        rows.append(
            {
                "pair_id": pair_id,
                "return_pct": _coerce_num(
                    result.aggregate_metrics.get("median_test_return_pct")
                ),
                "net_pnl": _coerce_num(
                    result.aggregate_metrics.get("total_oos_net_pnl")
                ),
                "trades": int(result.aggregate_metrics.get("total_test_trades") or 0),
                "individual_pass": result.passes_hard_gates,
                # R03: completeness must flow to portfolio aggregation
                "completeness_report": asdict(result.completeness_report)
                if result.completeness_report
                else None,
                "provenance_digest": result.provenance_digest,
            }
        )

    valid_returns = [row["return_pct"] for row in rows if row["return_pct"] is not None]
    positive_pairs = sum(1 for value in valid_returns if value > 0)
    positive_pairs_pct = (
        positive_pairs / len(rows) * 100.0 if len(valid_returns) == len(rows) else None
    )
    median_pair_return = (
        float(np.median(valid_returns)) if len(valid_returns) == len(rows) else None
    )
    positive_pnls = [
        row["net_pnl"]
        for row in rows
        if row["net_pnl"] is not None and row["net_pnl"] > 0
    ]
    contribution_concentration = (
        max(positive_pnls) / sum(positive_pnls) * 100.0 if positive_pnls else None
    )
    total_trades = sum(row["trades"] for row in rows)
    individual_pass_pct = (
        sum(1 for row in rows if row["individual_pass"]) / len(rows) * 100.0
    )

    gate_results: list[GateResult] = []

    def portfolio_gate(
        gate_id: str,
        observed: float | None,
        threshold: float,
        comparison: str,
        reason: str,
    ) -> None:
        if observed is None or not np.isfinite(observed):
            verdict = "INVALID"
        elif comparison == ">=":
            verdict = "PASS" if observed >= threshold else "FAIL"
        elif comparison == ">":
            verdict = "PASS" if observed > threshold else "FAIL"
        elif comparison == "<=":
            verdict = "PASS" if observed <= threshold else "FAIL"
        else:
            verdict = "INVALID"
        gate_results.append(
            GateResult(
                gate_id=gate_id,
                policy_version=policy_version,
                observed_value=observed,
                threshold=threshold,
                comparison=comparison,
                verdict=verdict,
                reason=reason,
                evidence_artifact="portfolio_selection.aggregate_metrics",
            )
        )

    portfolio_gate(
        "all_pair_strategy_candidates_pass",
        individual_pass_pct,
        100.0 if policy.all_members_pass else 0.0,
        ">=",
        "Every member must pass its pair/strategy hard gates",
    )
    portfolio_gate(
        "positive_pairs_ge_pct",
        positive_pairs_pct,
        policy.positive_pairs_pct_min,
        ">=",
        f"At least {policy.positive_pairs_pct_min}% of portfolio candidate pairs must have positive OOS return",
    )
    portfolio_gate(
        "median_pair_net_return_positive",
        median_pair_return,
        policy.median_pair_return_min,
        ">",
        f"Median pair OOS return must be >= {policy.median_pair_return_min}%",
    )
    portfolio_gate(
        "pair_contribution_concentration_le_pct",
        contribution_concentration,
        policy.contribution_concentration_max,
        "<=",
        f"Largest positive pair contribution must not exceed {policy.contribution_concentration_max}% of positive portfolio PnL",
    )
    portfolio_gate(
        "portfolio_aggregate_trades_ge",
        float(total_trades),
        policy.aggregate_trades_min,
        ">=",
        f"Portfolio aggregate must contain at least {policy.aggregate_trades_min} OOS trades",
    )

    passes = all(gate.is_pass() for gate in gate_results)
    holdout_failed = run_holdout and any(
        result.final_holdout is not None
        and result.final_holdout.get("status") != "COMPLETED"
        for result in results
    )
    if passes and run_holdout:
        verdict = "FINAL_PASS"
    elif passes:
        verdict = "SELECTION_PASS_HOLDOUT_NOT_RUN"
    elif holdout_failed:
        verdict = "HOLDOUT_FAILED"
    else:
        verdict = "NO_TRADE"

    # Aggregate sensitivity from all pair results
    all_sensitivities = []
    for r in results:
        sens = r.aggregate_metrics.get("sensitivity")
        if sens:
            all_sensitivities.append(sens)

    # Combine sensitivity results (use first as template, aggregate real_computed)
    combined_sensitivity = {}
    if all_sensitivities:
        combined_sensitivity = all_sensitivities[0].copy()
        combined_real_computed = []
        for sens in all_sensitivities:
            if isinstance(sens.get("real_computed"), list):
                combined_real_computed.extend(sens["real_computed"])
        # Deduplicate while preserving order
        seen: set[str] = set()
        deduped: list[str] = []
        for x in combined_real_computed:
            if x not in seen:
                seen.add(x)
                deduped.append(x)
        combined_sensitivity["real_computed"] = deduped

    aggregate_metrics = {
        "n_members": len(rows),
        "positive_pairs_pct": positive_pairs_pct,
        "median_pair_return_pct": median_pair_return,
        "pair_contribution_concentration_pct": contribution_concentration,
        "total_oos_net_pnl": sum(positive_pnls)
        + sum(
            row["net_pnl"]
            for row in rows
            if row["net_pnl"] is not None and row["net_pnl"] <= 0
        ),
        "total_oos_trades": total_trades,
        "members": rows,
        "sensitivity": combined_sensitivity,
    }

    data_identity = _combined_identity(
        [
            outer.artifact.data_manifest_sha
            for result in results
            for outer in result.outer_results
            if outer.artifact is not None
        ]
    )
    feature_identity = _combined_identity(
        [
            outer.artifact.descriptor_id
            for result in results
            for outer in result.outer_results
            if outer.artifact is not None
        ]
    )
    search_identity = _combined_identity([result.spec.param_grid for result in results])
    candidate_ids = [row["pair_id"] for row in rows]

    def median_sharpe_rank(item: WFOResult) -> float:
        value = _coerce_num(item.aggregate_metrics.get("median_test_sharpe"))
        return value if value is not None else float("-inf")

    best_result = max(
        results,
        key=median_sharpe_rank,
    )
    best_id = f"{best_result.spec.strategy_id}::{best_result.spec.symbol}"

    no_trade_artifact = None
    if not passes:
        member_gates = {
            f"{result.spec.strategy_id}::{result.spec.symbol}": result.gate_results
            for result in results
        }
        member_gates["__portfolio__"] = gate_results
        member_failures = {
            f"{result.spec.strategy_id}::{result.spec.symbol}": result.gate_failures
            for result in results
        }
        member_failures["__portfolio__"] = [
            gate.gate_id for gate in gate_results if not gate.is_pass()
        ]
        no_trade_artifact = FormalNoTradeArtifact(
            candidate_set=candidate_ids,
            gate_results=member_gates,
            gate_failures=member_failures,
            best_candidate=best_id,
            best_candidate_metrics=best_result.aggregate_metrics,
            registry_identity={
                "portfolio_data_identity": data_identity,
                "portfolio_feature_identity": feature_identity,
                "portfolio_search_identity": search_identity,
                "commit_sha": _compute_commit_sha(),
            },
            policy_version=policy_version,
            policy_thresholds=policy.to_dict(),
            commit_sha=_compute_commit_sha(),
            data_manifest_sha=data_identity,
            feature_schema_hash=feature_identity,
            search_space_hash=search_identity,
            evaluation_timestamp=datetime.now(UTC).isoformat(),
            evaluation_duration_sec=0.0,
            notes=f"Portfolio verdict {verdict}; no candidate portfolio passed every hard gate",
        )

    # R07: Build structured PortfolioGateReport (content-addressed, tamper-evident)
    gate_report = PortfolioGateReport(
        policy=policy,
        gate_results=gate_results,
        verdict=verdict,
        aggregate_metrics=aggregate_metrics,
    )

    def _sanitize_for_json(obj: Any) -> Any:
        """Recursively convert non-JSON-compliant values to strings."""
        import math

        if isinstance(obj, float):
            if math.isnan(obj):
                return "nan"
            if obj == float("inf"):
                return "inf"
            if obj == float("-inf"):
                return "-inf"
            return obj
        if isinstance(obj, dict):
            return {k: _sanitize_for_json(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [_sanitize_for_json(v) for v in obj]
        if isinstance(obj, tuple):
            return tuple(_sanitize_for_json(v) for v in obj)
        return obj

    portfolio_result = WFOPortfolioResult(
        results=results,
        aggregate_metrics=aggregate_metrics,
        gate_results=gate_results,
        passes_hard_gates=passes,
        verdict=verdict,
        gate_report=gate_report,
        no_trade_artifact=no_trade_artifact,
    )
    if out_root is not None:
        path = Path(out_root) / "portfolio_selection.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps(
                _sanitize_for_json(portfolio_result.to_dict()),
                indent=2,
                allow_nan=False,
            ),
            encoding="utf-8",
        )
        tmp.replace(path)
    return portfolio_result


def run_nested_wfo_portfolio(
    specs: list[WFOSpec],
    *,
    out_root: Path | None = None,
    run_holdout: bool = False,
    holdout_actor: str = "research_system",
    real_sensitivity: bool = True,
    cell_runner: CellRunner | None = None,
    gate_policy: PortfolioGatePolicy | None = None,
) -> WFOPortfolioResult:
    """Run pair/strategy WFOs and produce one portfolio-level decision."""
    results: list[WFOResult] = []
    for spec in specs:
        print(f"Running nested WFO for {spec.strategy_id} on {spec.symbol}...")
        result = run_nested_wfo(
            spec,
            out_root=out_root,
            run_holdout=run_holdout,
            holdout_actor=holdout_actor,
            real_sensitivity=real_sensitivity,
            cell_runner=cell_runner,
        )
        results.append(result)
        status = "PASS" if result.passes_hard_gates else "FAIL"
        holdout = ""
        if run_holdout and result.final_holdout:
            holdout = f", holdout={result.final_holdout.get('status')}"
        print(
            f"  {status}: sharpe={result.aggregate_metrics.get('median_test_sharpe', 0):.3f}, "
            f"return={result.aggregate_metrics.get('median_test_return_pct', 0):.2f}%, "
            f"folds={result.aggregate_metrics.get('n_outer_folds', 0)}{holdout}"
        )
    portfolio_result = _build_portfolio_selection_result(
        results, run_holdout=run_holdout, out_root=out_root, policy=gate_policy
    )
    print(f"Portfolio verdict: {portfolio_result.verdict}")
    return portfolio_result
