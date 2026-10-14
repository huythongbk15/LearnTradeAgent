"""Append-only experiment governance and registry-derived trial accounting."""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
import threading
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable

import numpy as np


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def param_hash(params: dict[str, Any]) -> str:
    return _sha256(params)[:24]


def search_space_hash(spaces: dict[str, Any]) -> str:
    return _sha256(spaces)[:24]


# Trial phases recorded by the append-only registry. Each (params, cost, fold)
# candidate backtest on the validation window is an INNER_VALIDATION trial; the
# selected-candidate backtest on the outer test window is an OUTER_OOS trial.
# Counting real trial runs by phase is how nested-WFO derives its trial count
# from the registry instead of a hand-maintained counter.
TRIAL_PHASE_INNER_VALIDATION = "INNER_VALIDATION"
TRIAL_PHASE_OUTER_OOS = "OUTER_OOS"
TRIAL_PHASE_UNKNOWN = "UNKNOWN"


def execution_scope_identity(context: dict[str, Any]) -> dict[str, Any]:
    """Strict versioned evaluation scope; does not rename the logical candidate."""
    if not isinstance(context, dict) or type(context.get("trial_identity_version")) is not int or context["trial_identity_version"] != 2:
        raise ValueError("execution context requires trial identity version 2")
    # No default=str: unsupported values/non-finite data cannot masquerade as a
    # reproducible frozen context. Hash the complete context, not just its label.
    frozen = json.loads(json.dumps(context, sort_keys=True, allow_nan=False))
    fingerprint = frozen.get("protocol_fingerprint")
    if not isinstance(fingerprint, str) or len(fingerprint) != 64 or any(c not in "0123456789abcdef" for c in fingerprint):
        raise ValueError("execution context requires a SHA256 protocol fingerprint")
    for context_field in ("source", "environment", "runtime_config"):
        if not isinstance(frozen.get(context_field), dict) or not frozen[context_field]:
            raise ValueError(f"execution context requires frozen {context_field}")
    return {"version": 2, "protocol_fingerprint": fingerprint, "context_sha256": _sha256(frozen)}


def evaluation_scope_from_metadata(metadata: dict[str, Any]) -> str | None:
    """None means retained legacy history, never implicitly upgraded/reused."""
    if not isinstance(metadata, dict):
        raise ValueError("invalid trial metadata")
    marker = metadata.get("evaluation_execution_identity")
    if marker is None:
        identity = metadata.get("evaluation_identity")
        context = identity.get("execution_context") if isinstance(identity, dict) else None
        if isinstance(context, dict) and context.get("trial_identity_version") == 2:
            raise ValueError("versioned evaluation missing execution identity")
        return None
    identity = metadata.get("evaluation_identity")
    if not isinstance(identity, dict):
        raise ValueError("versioned evaluation missing identity")
    expected = execution_scope_identity(identity.get("execution_context"))
    if marker != expected:
        raise ValueError("evaluation execution identity differs from frozen context")
    return expected["context_sha256"]


@dataclass(frozen=True)
class ExperimentSpec:
    experiment_id: str
    strategy_name: str
    strategy_code_sha: str
    data_manifest_sha: str
    feature_schema_hash: str
    params_hash: str
    search_family: str
    search_space_hash: str
    target_horizon: str
    evaluator_version: str
    seed: int
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @classmethod
    def build(
        cls,
        *,
        strategy_name: str,
        strategy_code_sha: str,
        data_manifest_sha: str,
        feature_schema_hash: str,
        params_hash: str,
        search_family: str,
        search_space_hash: str,
        target_horizon: str,
        evaluator_version: str,
        seed: int,
    ) -> "ExperimentSpec":
        # Display names are deliberately excluded: renaming cannot reset history.
        identity = {
            "strategy_code_sha": strategy_code_sha,
            "data_manifest_sha": data_manifest_sha,
            "feature_schema_hash": feature_schema_hash,
            "params_hash": params_hash,
            "search_family": search_family,
            "search_space_hash": search_space_hash,
            "target_horizon": target_horizon,
            "evaluator_version": evaluator_version,
            "seed": int(seed),
        }
        return cls(
            experiment_id=f"exp_{_sha256(identity)[:32]}",
            strategy_name=strategy_name,
            strategy_code_sha=strategy_code_sha,
            data_manifest_sha=data_manifest_sha,
            feature_schema_hash=feature_schema_hash,
            params_hash=params_hash,
            search_family=search_family,
            search_space_hash=search_space_hash,
            target_horizon=target_horizon,
            evaluator_version=evaluator_version,
            seed=int(seed),
        )


@dataclass(frozen=True)
class EvaluationRecord:
    evaluation_id: str
    experiment_id: str
    fold_id: str
    metric_name: str
    metric_value: float
    created_at: datetime
    environment_hash: str
    trial_phase: str = field(default=TRIAL_PHASE_UNKNOWN)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TrialCounts:
    raw_trial_count: int
    effective_trial_count: int
    evaluation_count: int
    unique_experiments: int
    search_family_counts: dict[str, int]
    inner_validation_trials: int = 0
    outer_oos_trials: int = 0
    total_trial_runs: int = 0
    methodology: str = ""


class ExperimentRegistry:
    """SQLite-WAL registry with database-enforced append-only history."""

    def __init__(self, path: str | Path = "research_experiments.sqlite3") -> None:
        self.path = str(path)
        self._lock = threading.RLock()
        self._memory_connection: sqlite3.Connection | None = None
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        if self.path == ":memory:":
            if self._memory_connection is None:
                self._memory_connection = sqlite3.connect(
                    ":memory:", timeout=30.0, check_same_thread=False
                )
                self._configure(self._memory_connection)
            return self._memory_connection
        connection = sqlite3.connect(self.path, timeout=30.0)
        self._configure(connection)
        return connection

    @staticmethod
    def _configure(connection: sqlite3.Connection) -> None:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=30000")
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=FULL")

    def _close(self, connection: sqlite3.Connection) -> None:
        if connection is not self._memory_connection:
            connection.close()

    def _initialize(self) -> None:
        with self._lock:
            connection = self._connect()
            try:
                connection.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS experiment_specs (
                        experiment_id TEXT PRIMARY KEY,
                        strategy_name TEXT NOT NULL,
                        strategy_code_sha TEXT NOT NULL,
                        data_manifest_sha TEXT NOT NULL,
                        feature_schema_hash TEXT NOT NULL,
                        params_hash TEXT NOT NULL,
                        search_family TEXT NOT NULL,
                        search_space_hash TEXT NOT NULL,
                        target_horizon TEXT NOT NULL,
                        evaluator_version TEXT NOT NULL,
                        seed INTEGER NOT NULL,
                        created_at TEXT NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS experiment_aliases (
                        experiment_id TEXT NOT NULL,
                        strategy_name TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        PRIMARY KEY (experiment_id, strategy_name),
                        FOREIGN KEY (experiment_id) REFERENCES experiment_specs(experiment_id)
                    );
                    CREATE TABLE IF NOT EXISTS evaluation_records (
                        evaluation_id TEXT PRIMARY KEY,
                        experiment_id TEXT NOT NULL,
                        fold_id TEXT NOT NULL,
                        metric_name TEXT NOT NULL,
                        metric_value REAL NOT NULL,
                        created_at TEXT NOT NULL,
                        environment_hash TEXT NOT NULL,
                        trial_phase TEXT NOT NULL DEFAULT 'UNKNOWN',
                        metadata_json TEXT NOT NULL,
                        FOREIGN KEY (experiment_id) REFERENCES experiment_specs(experiment_id)
                    );
                    CREATE INDEX IF NOT EXISTS idx_evaluation_experiment
                        ON evaluation_records(experiment_id, created_at);
                    CREATE INDEX IF NOT EXISTS idx_experiment_search_family
                        ON experiment_specs(search_family, search_space_hash);
                    CREATE TRIGGER IF NOT EXISTS experiment_specs_no_update
                    BEFORE UPDATE ON experiment_specs BEGIN
                        SELECT RAISE(ABORT, 'experiment specs are append-only');
                    END;
                    CREATE TRIGGER IF NOT EXISTS experiment_specs_no_delete
                    BEFORE DELETE ON experiment_specs BEGIN
                        SELECT RAISE(ABORT, 'experiment specs are append-only');
                    END;
                    CREATE TRIGGER IF NOT EXISTS evaluation_records_no_update
                    BEFORE UPDATE ON evaluation_records BEGIN
                        SELECT RAISE(ABORT, 'evaluation records are append-only');
                    END;
                    CREATE TRIGGER IF NOT EXISTS evaluation_records_no_delete
                    BEFORE DELETE ON evaluation_records BEGIN
                        SELECT RAISE(ABORT, 'evaluation records are append-only');
                    END;
                    CREATE TRIGGER IF NOT EXISTS experiment_aliases_no_update
                    BEFORE UPDATE ON experiment_aliases BEGIN
                        SELECT RAISE(ABORT, 'experiment aliases are append-only');
                    END;
                    CREATE TRIGGER IF NOT EXISTS experiment_aliases_no_delete
                    BEFORE DELETE ON experiment_aliases BEGIN
                        SELECT RAISE(ABORT, 'experiment aliases are append-only');
                    END;
                    """
                )
                # Backfill the trial_phase column for registries created before
                # the phase column existed. Safe to skip if already present.
                try:
                    connection.execute(
                        "ALTER TABLE evaluation_records "
                        "ADD COLUMN trial_phase TEXT NOT NULL DEFAULT 'UNKNOWN'"
                    )
                except sqlite3.OperationalError:
                    # Column already exists in this (fresh) schema.
                    pass
                connection.commit()
            finally:
                self._close(connection)

    @staticmethod
    def _technical_identity(spec: ExperimentSpec) -> tuple[Any, ...]:
        return (
            spec.strategy_code_sha,
            spec.data_manifest_sha,
            spec.feature_schema_hash,
            spec.params_hash,
            spec.search_family,
            spec.search_space_hash,
            spec.target_horizon,
            spec.evaluator_version,
            spec.seed,
        )

    def register_experiment(self, spec: ExperimentSpec) -> ExperimentSpec:
        expected = ExperimentSpec.build(
            strategy_name=spec.strategy_name,
            strategy_code_sha=spec.strategy_code_sha,
            data_manifest_sha=spec.data_manifest_sha,
            feature_schema_hash=spec.feature_schema_hash,
            params_hash=spec.params_hash,
            search_family=spec.search_family,
            search_space_hash=spec.search_space_hash,
            target_horizon=spec.target_horizon,
            evaluator_version=spec.evaluator_version,
            seed=spec.seed,
        ).experiment_id
        if spec.experiment_id != expected:
            raise ValueError(
                "experiment_id does not match canonical experiment content"
            )

        with self._lock:
            connection = self._connect()
            try:
                connection.execute("BEGIN IMMEDIATE")
                existing_row = connection.execute(
                    "SELECT * FROM experiment_specs WHERE experiment_id = ?",
                    (spec.experiment_id,),
                ).fetchone()
                if existing_row is None:
                    connection.execute(
                        """
                        INSERT INTO experiment_specs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            spec.experiment_id,
                            spec.strategy_name,
                            spec.strategy_code_sha,
                            spec.data_manifest_sha,
                            spec.feature_schema_hash,
                            spec.params_hash,
                            spec.search_family,
                            spec.search_space_hash,
                            spec.target_horizon,
                            spec.evaluator_version,
                            spec.seed,
                            spec.created_at.isoformat(),
                        ),
                    )
                    stored = spec
                else:
                    stored = self._spec_from_row(existing_row)
                    if self._technical_identity(stored) != self._technical_identity(
                        spec
                    ):
                        raise ValueError(
                            "experiment_id collision with different content"
                        )
                connection.execute(
                    "INSERT OR IGNORE INTO experiment_aliases VALUES (?, ?, ?)",
                    (
                        spec.experiment_id,
                        spec.strategy_name,
                        datetime.now(UTC).isoformat(),
                    ),
                )
                connection.commit()
                return stored
            except Exception:
                connection.rollback()
                raise
            finally:
                self._close(connection)

    def append_evaluation(
        self,
        *,
        experiment_id: str,
        fold_id: str,
        metric_name: str,
        metric_value: float,
        environment_hash: str,
        metadata: dict[str, Any] | None = None,
        evaluation_id: str | None = None,
        created_at: datetime | None = None,
        trial_phase: str = TRIAL_PHASE_UNKNOWN,
        idempotent: bool = False,
        execution_context: dict[str, Any] | None = None,
    ) -> EvaluationRecord:
        if not math.isfinite(float(metric_value)):
            raise ValueError("metric_value must be finite")
        metadata = dict(metadata or {})
        scope = None
        if execution_context is not None:
            if not idempotent:
                raise ValueError("versioned evaluation requires atomic idempotent append")
            marker = execution_scope_identity(execution_context)
            identity = metadata.get("evaluation_identity")
            if not isinstance(identity, dict) or identity.get("execution_context") != execution_context:
                raise ValueError("evaluation metadata differs from declared execution context")
            if "evaluation_execution_identity" in metadata and metadata["evaluation_execution_identity"] != marker:
                raise ValueError("evaluation execution identity mismatch")
            metadata["evaluation_execution_identity"] = marker
            metadata = json.loads(json.dumps(metadata, allow_nan=False))
            scope = marker["context_sha256"]
        elif "evaluation_execution_identity" in metadata:
            raise ValueError("versioned evaluation requires explicit execution context")
        record = EvaluationRecord(
            evaluation_id=evaluation_id or f"eval_{uuid.uuid4().hex}",
            experiment_id=experiment_id,
            fold_id=str(fold_id),
            metric_name=str(metric_name),
            metric_value=float(metric_value),
            created_at=created_at or datetime.now(UTC),
            environment_hash=str(environment_hash),
            trial_phase=str(trial_phase),
            metadata=metadata,
        )
        with self._lock:
            connection = self._connect()
            try:
                connection.execute("BEGIN IMMEDIATE")
                if idempotent:
                    rows = connection.execute(
                        "SELECT * FROM evaluation_records WHERE experiment_id = ? "
                        "AND fold_id = ? AND trial_phase = ?",
                        (record.experiment_id, record.fold_id, record.trial_phase),
                    ).fetchall()
                    if scope is not None:
                        # Old scopes/legacy remain immutable and in trial burden.
                        # A new protocol appends a NEW evaluation, not a replay of
                        # an old result. Validate markers before filtering them.
                        history = connection.execute(
                            "SELECT * FROM evaluation_records WHERE experiment_id = ?",
                            (record.experiment_id,),
                        ).fetchall()
                        matching = []
                        for row in history:
                            previous_metadata = json.loads(row["metadata_json"])
                            previous_scope = evaluation_scope_from_metadata(previous_metadata)
                            previous_marker = previous_metadata.get("evaluation_execution_identity")
                            if (previous_marker is not None
                                    and previous_marker["protocol_fingerprint"] == marker["protocol_fingerprint"]
                                    and previous_scope != scope):
                                raise ValueError("same protocol declares different execution context")
                            if (previous_scope == scope and row["fold_id"] == record.fold_id
                                    and row["trial_phase"] == record.trial_phase):
                                matching.append(row)
                        rows = matching
                    if rows:
                        if len(rows) != 1:
                            raise ValueError("duplicate trial phase history; cannot replay")
                        previous = self._evaluation_from_row(rows[0])
                        if (previous.metric_name != record.metric_name
                                or previous.metric_value != record.metric_value
                                or previous.environment_hash != record.environment_hash
                                or _canonical_json(previous.metadata) != _canonical_json(record.metadata)):
                            raise ValueError("trial replay identity/metric differs from immutable history")
                        connection.commit()
                        return previous
                connection.execute(
                    """
                    INSERT INTO evaluation_records
                        (evaluation_id, experiment_id, fold_id, metric_name,
                         metric_value, created_at, environment_hash, trial_phase,
                         metadata_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        record.evaluation_id,
                        record.experiment_id,
                        record.fold_id,
                        record.metric_name,
                        record.metric_value,
                        record.created_at.isoformat(),
                        record.environment_hash,
                        record.trial_phase,
                        _canonical_json(record.metadata),
                    ),
                )
                connection.commit()
                return record
            except Exception:
                connection.rollback()
                raise
            finally:
                self._close(connection)

    def has_trial_phase(
        self, experiment_id: str, fold_id: str, trial_phase: str
    ) -> bool:
        """Return True if a trial record with this (experiment, fold, phase) exists.

        Legacy diagnostic only; tuple existence does not prove replay identity.
        WFO uses atomic append with complete metadata and execution context.
        """
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT 1 FROM evaluation_records "
                "WHERE experiment_id = ? AND fold_id = ? AND trial_phase = ? LIMIT 1",
                (experiment_id, str(fold_id), trial_phase),
            ).fetchone()
            return row is not None
        finally:
            self._close(connection)

    def experiments(self) -> list[ExperimentSpec]:
        connection = self._connect()
        try:
            rows = connection.execute(
                "SELECT * FROM experiment_specs ORDER BY created_at, experiment_id"
            ).fetchall()
            return [self._spec_from_row(row) for row in rows]
        finally:
            self._close(connection)

    def evaluations(self, experiment_id: str | None = None) -> list[EvaluationRecord]:
        connection = self._connect()
        try:
            if experiment_id is None:
                rows = connection.execute(
                    "SELECT * FROM evaluation_records ORDER BY created_at, evaluation_id"
                ).fetchall()
            else:
                rows = connection.execute(
                    """
                    SELECT * FROM evaluation_records
                    WHERE experiment_id = ? ORDER BY created_at, evaluation_id
                    """,
                    (experiment_id,),
                ).fetchall()
            return [self._evaluation_from_row(row) for row in rows]
        finally:
            self._close(connection)

    def aliases(self, experiment_id: str) -> list[str]:
        connection = self._connect()
        try:
            rows = connection.execute(
                """
                SELECT strategy_name FROM experiment_aliases
                WHERE experiment_id = ? ORDER BY created_at, strategy_name
                """,
                (experiment_id,),
            ).fetchall()
            return [str(row["strategy_name"]) for row in rows]
        finally:
            self._close(connection)

    def trial_counts(
        self,
        *,
        empirical_trial_correlation: np.ndarray | None = None,
        experiment_ids: Iterable[str] | None = None,
    ) -> TrialCounts:
        specs = self.experiments()
        evaluations = self.evaluations()
        if experiment_ids is not None:
            selected_ids = set(experiment_ids)
            specs = [spec for spec in specs if spec.experiment_id in selected_ids]
            evaluations = [
                evaluation
                for evaluation in evaluations
                if evaluation.experiment_id in selected_ids
            ]
        unique = len(specs)
        scopes: dict[str, set[str | None]] = {spec.experiment_id: set() for spec in specs}
        for evaluation in evaluations:
            scopes[evaluation.experiment_id].add(evaluation_scope_from_metadata(evaluation.metadata))
        # Each candidate x research context is a selection opportunity. Legacy
        # history retains a bucket; folds/phases/exact replay do not multiply it.
        # Registered but unexecuted candidates conservatively count at least one.
        weights = {key: max(1, len(value)) for key, value in scopes.items()}
        raw = sum(weights.values())
        families: dict[str, int] = {}
        for spec in specs:
            key = f"{spec.search_family}:{spec.search_space_hash}"
            families[key] = families.get(key, 0) + weights[spec.experiment_id]

        methodology = (
            "canonical candidates x immutable execution contexts; retained legacy bucket; "
            "folds/phases/exact same-context replays are not new selection opportunities; "
            "without an empirical trial-correlation matrix, effective equals raw (conservative)"
        )
        if experiment_ids is not None:
            methodology += "; scoped to the supplied experiment ids"
        effective = raw
        if empirical_trial_correlation is not None:
            matrix = np.asarray(empirical_trial_correlation, dtype=float)
            if matrix.shape != (raw, raw):
                raise ValueError(
                    "empirical_trial_correlation must match the candidate-context trial count"
                )
            if not np.all(np.isfinite(matrix)) or not np.allclose(matrix, matrix.T):
                raise ValueError(
                    "empirical_trial_correlation must be finite and symmetric"
                )
            eigenvalues = np.clip(np.linalg.eigvalsh(matrix), 0.0, None)
            denominator = float(np.sum(eigenvalues**2))
            participation_ratio = (
                float(np.sum(eigenvalues) ** 2 / denominator)
                if denominator > 0
                else 0.0
            )
            effective = min(raw, max(1 if raw else 0, math.ceil(participation_ratio)))
            methodology = (
                "candidate-context opportunities with effective trials estimated by the "
                "eigenvalue participation ratio of the supplied empirical trial-correlation matrix"
            )

        # Real trial-run accounting: count actual backtest executions recorded in
        # the append-only registry, partitioned by phase. This is the source of
        # truth for nested-WFO trial counts (inner validation + outer OOS), not a
        # hand-maintained counter.
        inner_validation_trials = sum(
            1 for e in evaluations if e.trial_phase == TRIAL_PHASE_INNER_VALIDATION
        )
        outer_oos_trials = sum(
            1 for e in evaluations if e.trial_phase == TRIAL_PHASE_OUTER_OOS
        )
        total_trial_runs = inner_validation_trials + outer_oos_trials

        return TrialCounts(
            raw_trial_count=raw,
            effective_trial_count=effective,
            evaluation_count=len(evaluations),
            unique_experiments=unique,
            search_family_counts=families,
            inner_validation_trials=inner_validation_trials,
            outer_oos_trials=outer_oos_trials,
            total_trial_runs=total_trial_runs,
            methodology=methodology,
        )

    @staticmethod
    def _spec_from_row(row: sqlite3.Row) -> ExperimentSpec:
        return ExperimentSpec(
            experiment_id=row["experiment_id"],
            strategy_name=row["strategy_name"],
            strategy_code_sha=row["strategy_code_sha"],
            data_manifest_sha=row["data_manifest_sha"],
            feature_schema_hash=row["feature_schema_hash"],
            params_hash=row["params_hash"],
            search_family=row["search_family"],
            search_space_hash=row["search_space_hash"],
            target_horizon=row["target_horizon"],
            evaluator_version=row["evaluator_version"],
            seed=int(row["seed"]),
            created_at=datetime.fromisoformat(row["created_at"]),
        )

    @staticmethod
    def _evaluation_from_row(row: sqlite3.Row) -> EvaluationRecord:
        return EvaluationRecord(
            evaluation_id=row["evaluation_id"],
            experiment_id=row["experiment_id"],
            fold_id=row["fold_id"],
            metric_name=row["metric_name"],
            metric_value=float(row["metric_value"]),
            created_at=datetime.fromisoformat(row["created_at"]),
            environment_hash=row["environment_hash"],
            trial_phase=row["trial_phase"],
            metadata=json.loads(row["metadata_json"]),
        )


@dataclass
class TrialRecord:
    """Compatibility view over one canonical experiment and its latest metric."""

    trial_id: str
    strategy_name: str
    param_hash: str
    search_space_hash: str
    metric_name: str
    metric_value: float
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self) | {"created_at": self.created_at.isoformat()}


class TrialsRegistry:
    """Backward-compatible facade backed by the append-only canonical registry."""

    def __init__(self, path: str | Path = ":memory:") -> None:
        self.registry = ExperimentRegistry(path)
        self._views: dict[str, TrialRecord] = {}

    def record(
        self,
        *,
        strategy_name: str,
        params: dict[str, Any],
        search_space: dict[str, Any],
        metric_value: float,
        metric_name: str = "total_return_pct",
        metadata: dict[str, Any] | None = None,
    ) -> TrialRecord:
        details = dict(metadata or {})
        spec = ExperimentSpec.build(
            strategy_name=strategy_name,
            strategy_code_sha=str(details.pop("strategy_code_sha", "legacy_unknown")),
            data_manifest_sha=str(details.pop("data_manifest_sha", "legacy_unknown")),
            feature_schema_hash=str(
                details.pop("feature_schema_hash", "legacy_unknown")
            ),
            params_hash=param_hash(params),
            search_family=str(details.pop("search_family", "legacy")),
            search_space_hash=search_space_hash(search_space),
            target_horizon=str(details.pop("target_horizon", "legacy_unknown")),
            evaluator_version=str(details.pop("evaluator_version", "legacy_v1")),
            seed=int(details.pop("seed", 0)),
        )
        stored = self.registry.register_experiment(spec)
        self.registry.append_evaluation(
            experiment_id=stored.experiment_id,
            fold_id=str(details.pop("fold_id", "aggregate")),
            metric_name=metric_name,
            metric_value=metric_value,
            environment_hash=str(details.pop("environment_hash", "legacy_unknown")),
            metadata=details,
        )
        view = self._views.get(stored.experiment_id)
        if view is None:
            view = TrialRecord(
                trial_id=stored.experiment_id,
                strategy_name=stored.strategy_name,
                param_hash=stored.params_hash,
                search_space_hash=stored.search_space_hash,
                metric_name=metric_name,
                metric_value=float(metric_value),
                created_at=stored.created_at,
                metadata={**details, "alias_names": []},
            )
            self._views[stored.experiment_id] = view
        else:
            view.metric_name = metric_name
            view.metric_value = float(metric_value)
        aliases = view.metadata.setdefault("alias_names", [])
        if strategy_name != view.strategy_name and strategy_name not in aliases:
            aliases.append(strategy_name)
        return view

    def trials_for_strategy(self, strategy_name: str) -> list[TrialRecord]:
        return [
            view
            for experiment_id, view in self._views.items()
            if strategy_name in self.registry.aliases(experiment_id)
        ]

    def unique_strategies(self) -> set[str]:
        result: set[str] = set()
        for experiment_id in self._views:
            result.update(self.registry.aliases(experiment_id))
        return result

    def total_trials(self) -> int:
        return self.registry.trial_counts().unique_experiments

    def evaluation_count(self) -> int:
        return self.registry.trial_counts().evaluation_count

    def best_trial(
        self,
        strategy_name: str | None = None,
        metric_name: str = "total_return_pct",
    ) -> TrialRecord | None:
        candidates: Iterable[TrialRecord] = self._views.values()
        if strategy_name is not None:
            candidates = self.trials_for_strategy(strategy_name)
        matches = [trial for trial in candidates if trial.metric_name == metric_name]
        return max(matches, key=lambda trial: trial.metric_value) if matches else None
