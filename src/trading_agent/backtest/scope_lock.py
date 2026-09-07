"""R04 — Scope lock, holdout tracking, and campaign orchestration.

A canonical S3 campaign is auditable when:

1. The scope is locked BEFORE the campaign runs (pair list, strategy list,
   timeframe, grid, cost, fold geometry, holdout window) and cannot be
   silently widened.
2. The frozen holdout is touched at most ONCE per study, only for the
   final one-shot confirmation. The registry tracks every access and
   refuses a second access (no registry reset to "reuse" a touched holdout).
3. The campaign orchestrator runs in three phases:
   - **smoke**: 1 pair + 1 strategy + 1 cost scenario, validate pipeline
   - **scope**: locked pair/strategy list with full cost scenarios
   - **final**: scope + final holdout one-shot, only if scope passed
4. Each phase produces a campaign artifact with provenance_digest, scope
   identity, and result summary. The artifact is content-addressed and
   tamper-evident (R03 integration).
5. The "no winner" outcome (NO_TRADE) is preserved as a valid research
   result; downstream R05–R07 only operate on the artifact, not on raw
   numbers.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any

# Default R04 locked scope (per R00 baseline + R02 canonical authority).
# These are the ONLY allowed values for a campaign run. Any deviation
# requires a new scope lock + new manifest.
R04_LOCKED_PAIRS: tuple[str, ...] = (
    "BTC/USDT",
    "ETH/USDT",
    "SOL/USDT",
)

R04_LOCKED_STRATEGIES: tuple[str, ...] = (
    "rsi",
    "ma_adx",
    "enhanced_ma",
)

R04_LOCKED_TIMEFRAME: str = "1h"

R04_LOCKED_COST_SCENARIOS: tuple[str, ...] = (
    "1x",  # SCENARIO_BASE
    "2x",  # SCENARIO_DOUBLE
    "slip_stress",  # SCENARIO_SLIPPAGE_STRESS
)

R04_LOCKED_FOLD_GEOMETRY: dict[str, int] = {
    "train_months": 12,
    "val_months": 3,
    "test_months": 3,
    "step_months": 3,
}

R04_LOCKED_BUDGET: dict[str, Any] = {
    "max_runtime_seconds": 3600,
    "min_oos_trades": 30,
    "max_cells_per_fold": 12,
}


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class CampaignScope:
    """Locked campaign scope — cannot be modified after creation.

    The scope identity is content-addressed via ``scope_id``. Two campaigns
    with the same scope_id are guaranteed equivalent and can be compared
    by the consumer.
    """

    pairs: tuple[str, ...]
    strategies: tuple[str, ...]
    timeframe: str
    cost_scenarios: tuple[str, ...]
    fold_geometry: dict[str, int]
    budget: dict[str, Any]
    description: str = ""
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    def __post_init__(self) -> None:
        if not self.pairs:
            raise ValueError("CampaignScope requires at least 1 pair")
        if not self.strategies:
            raise ValueError("CampaignScope requires at least 1 strategy")
        if not self.cost_scenarios:
            raise ValueError("CampaignScope requires at least 1 cost scenario")
        for k in ("train_months", "val_months", "test_months", "step_months"):
            if k not in self.fold_geometry:
                raise ValueError(f"fold_geometry missing {k}")
            if self.fold_geometry[k] < 1:
                raise ValueError(f"fold_geometry.{k} must be >= 1")

    @property
    def scope_id(self) -> str:
        """Content-addressed scope identity."""
        payload = {
            "pairs": list(self.pairs),
            "strategies": list(self.strategies),
            "timeframe": self.timeframe,
            "cost_scenarios": list(self.cost_scenarios),
            "fold_geometry": dict(self.fold_geometry),
            "budget": dict(self.budget),
        }
        return f"sha256:{_sha256(payload)}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "scope_id": self.scope_id,
            "pairs": list(self.pairs),
            "strategies": list(self.strategies),
            "timeframe": self.timeframe,
            "cost_scenarios": list(self.cost_scenarios),
            "fold_geometry": dict(self.fold_geometry),
            "budget": dict(self.budget),
            "description": self.description,
            "created_at": self.created_at,
        }

    def allows_pair(self, pair: str) -> bool:
        return pair in self.pairs

    def allows_strategy(self, strategy: str) -> bool:
        return strategy in self.strategies

    def allows_cost_scenario(self, cost: str) -> bool:
        return cost in self.cost_scenarios

    def is_within_budget(self, *, runtime_seconds: float, cell_count: int) -> bool:
        max_runtime = self.budget.get("max_runtime_seconds", float("inf"))
        max_cells = self.budget.get("max_cells_per_fold", float("inf"))
        return runtime_seconds <= max_runtime and cell_count <= max_cells


def r04_default_scope() -> CampaignScope:
    """Build the R04 default scope from locked constants."""
    return CampaignScope(
        pairs=R04_LOCKED_PAIRS,
        strategies=R04_LOCKED_STRATEGIES,
        timeframe=R04_LOCKED_TIMEFRAME,
        cost_scenarios=R04_LOCKED_COST_SCENARIOS,
        fold_geometry=dict(R04_LOCKED_FOLD_GEOMETRY),
        budget=dict(R04_LOCKED_BUDGET),
        description="R04 default scope: 3 pairs x 3 strategies x 3 cost scenarios",
    )


@dataclass
class ScopeViolation:
    """A single violation of a locked scope."""

    kind: str  # "pair" | "strategy" | "cost" | "fold" | "budget" | "timeframe"
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "detail": self.detail}


class ScopeEnforcer:
    """Enforce a locked scope at runtime.

    Used by campaign orchestrator to:
    - Reject any pair/strategy/cost not in the locked scope.
    - Reject runtime over the budget.
    - Reject holdout re-access (the touched holdout cannot be reused).
    """

    def __init__(self, scope: CampaignScope):
        self.scope = scope
        self.violations: list[ScopeViolation] = []

    def check_pair(self, pair: str) -> bool:
        if not self.scope.allows_pair(pair):
            self.violations.append(
                ScopeViolation("pair", f"{pair} not in locked scope {self.scope.pairs}")
            )
            return False
        return True

    def check_strategy(self, strategy: str) -> bool:
        if not self.scope.allows_strategy(strategy):
            self.violations.append(
                ScopeViolation(
                    "strategy",
                    f"{strategy} not in locked scope {self.scope.strategies}",
                )
            )
            return False
        return True

    def check_cost_scenario(self, cost: str) -> bool:
        if not self.scope.allows_cost_scenario(cost):
            self.violations.append(
                ScopeViolation(
                    "cost",
                    f"{cost} not in locked scope {self.scope.cost_scenarios}",
                )
            )
            return False
        return True

    def check_runtime(self, runtime_seconds: float) -> bool:
        max_runtime = self.scope.budget.get("max_runtime_seconds", float("inf"))
        if runtime_seconds > max_runtime:
            self.violations.append(
                ScopeViolation(
                    "budget",
                    f"runtime {runtime_seconds:.1f}s exceeds budget {max_runtime}s",
                )
            )
            return False
        return True

    def is_clean(self) -> bool:
        return len(self.violations) == 0


@dataclass
class HoldoutAccessRecord:
    """Record one access to a frozen holdout window.

    A holdout can be touched at most once per study. The campaign
    orchestrator records every access and refuses a second access.
    """

    pair: str
    strategy: str
    fold_count: int
    accessed_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    outcome: str = ""  # "PASS" | "NO_TRADE" | "FAILED"
    result_artifact: str = ""  # path to holdout result

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class HoldoutAccessGuard:
    """Track holdout accesses and refuse re-use.

    Per R04 contract: a frozen holdout can be touched at most once per
    (pair, strategy) study. A second access returns a guard rejection
    (no reset of the registry, no "touched holdout can be re-used because
    we re-ran with new code").
    """

    def __init__(self) -> None:
        # (pair, strategy) -> HoldoutAccessRecord
        self._accesses: dict[tuple[str, str], HoldoutAccessRecord] = {}

    def request_access(
        self, pair: str, strategy: str, fold_count: int
    ) -> HoldoutAccessRecord:
        key = (pair, strategy)
        if key in self._accesses:
            prior = self._accesses[key]
            raise HoldoutReuseError(
                f"Holdout for {pair}/{strategy} already touched at "
                f"{prior.accessed_at} (outcome={prior.outcome}). "
                f"Cannot re-use a touched holdout. "
                f"Either accept the prior result or run a NEW study with a "
                f"different frozen holdout (extend data/research_manifest.json)."
            )
        record = HoldoutAccessRecord(
            pair=pair,
            strategy=strategy,
            fold_count=fold_count,
        )
        self._accesses[key] = record
        return record

    def record_outcome(
        self,
        pair: str,
        strategy: str,
        outcome: str,
        result_artifact: str = "",
    ) -> None:
        key = (pair, strategy)
        if key not in self._accesses:
            raise KeyError(
                f"No active holdout access for {pair}/{strategy}; call request_access first"
            )
        rec = self._accesses[key]
        rec.outcome = outcome
        rec.result_artifact = result_artifact

    def has_touched(self, pair: str, strategy: str) -> bool:
        return (pair, strategy) in self._accesses

    def to_dict(self) -> dict[str, Any]:
        return {
            "touched": [
                {**r.to_dict(), "pair": p, "strategy": s}
                for (p, s), r in self._accesses.items()
            ],
        }


class HoldoutReuseError(Exception):
    """Raised when a touched holdout is requested again."""


# Campaign phase results
@dataclass
class PhaseResult:
    """Result of a single campaign phase (smoke / scope / final)."""

    phase: str  # "smoke" | "scope" | "final"
    scope_id: str
    started_at: str
    finished_at: str = ""
    runtime_seconds: float = 0.0
    pairs_run: int = 0
    strategies_run: int = 0
    cells_executed: int = 0
    verdicts: dict[str, str] = field(default_factory=dict)
    # R03 integration: bind the phase to the evidence it consumed.
    provenance_digest: str = ""
    scope_violations: tuple[dict[str, Any], ...] = ()
    holdout_accesses: tuple[dict[str, Any], ...] = ()
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "phase": self.phase,
            "scope_id": self.scope_id,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "runtime_seconds": self.runtime_seconds,
            "pairs_run": self.pairs_run,
            "strategies_run": self.strategies_run,
            "cells_executed": self.cells_executed,
            "verdicts": dict(self.verdicts),
            "provenance_digest": self.provenance_digest,
            "scope_violations": list(self.scope_violations),
            "holdout_accesses": list(self.holdout_accesses),
            "notes": self.notes,
        }


def campaign_phase_artifact(
    phase: str,
    scope: CampaignScope,
    *,
    started_at: str,
    finished_at: str = "",
    runtime_seconds: float = 0.0,
    pairs_run: int = 0,
    strategies_run: int = 0,
    cells_executed: int = 0,
    verdicts: dict[str, str] | None = None,
    provenance_digest: str = "",
    enforcer: ScopeEnforcer | None = None,
    holdout_guard: HoldoutAccessGuard | None = None,
    notes: str = "",
) -> PhaseResult:
    """Build a PhaseResult, attaching scope-violation + holdout-access records."""
    return PhaseResult(
        phase=phase,
        scope_id=scope.scope_id,
        started_at=started_at,
        finished_at=finished_at,
        runtime_seconds=runtime_seconds,
        pairs_run=pairs_run,
        strategies_run=strategies_run,
        cells_executed=cells_executed,
        verdicts=verdicts or {},
        provenance_digest=provenance_digest,
        scope_violations=tuple(v.to_dict() for v in (enforcer.violations if enforcer else [])),
        holdout_accesses=tuple(
            a for a in (holdout_guard.to_dict()["touched"] if holdout_guard else [])
        ),
        notes=notes,
    )
