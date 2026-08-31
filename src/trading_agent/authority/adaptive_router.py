"""Signed-policy adaptive strategy routing with fail-closed handover semantics.

The router consumes the complete regime posterior, not only its argmax label.
It never submits orders itself; it emits an immutable :class:`RoutingDecision`
that the canonical execution authority can enforce.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Mapping

from trading_agent.ml.regime_detection import RegimePosterior
from trading_agent.authority.config import Environment
from trading_agent.research.forecast import Forecast, MarketObservation
from trading_agent.research.selection_policy import (
    SelectionPolicyArtifact,
    SelectionPolicyRegistry,
)
from trading_agent.strategies.canonical.adapter import LegacyDataFrameAdapter
from trading_agent.strategies.canonical.descriptor import StrategyDescriptor


class HandoverState(str, Enum):
    STABLE = "STABLE"
    SWITCH_PENDING = "SWITCH_PENDING"
    WAIT_FLAT = "WAIT_FLAT"
    ACTIVATE = "ACTIVATE"


@dataclass(frozen=True)
class AdaptiveRouterConfig:
    entropy_threshold: float = 0.75
    max_ood_score: float = 0.50
    max_posterior_age_seconds: int = 7200
    persistence_bars: int = 3
    score_margin: float = 0.10
    min_score_delta: float = 0.05
    min_dwell_bars: int = 6
    cooldown_bars: int = 3
    min_policy_coverage: float = 0.80
    max_policy_age_days: int = 30

    def __post_init__(self) -> None:
        for name in (
            "entropy_threshold",
            "max_ood_score",
            "score_margin",
            "min_policy_coverage",
        ):
            value = float(getattr(self, name))
            if not math.isfinite(value) or not 0.0 <= value <= 1.0:
                raise ValueError(f"{name} must be finite and in [0, 1]")
        if not math.isfinite(self.min_score_delta) or self.min_score_delta < 0.0:
            raise ValueError("min_score_delta must be finite and non-negative")
        for name in (
            "max_posterior_age_seconds",
            "persistence_bars",
            "min_dwell_bars",
            "max_policy_age_days",
        ):
            if int(getattr(self, name)) <= 0:
                raise ValueError(f"{name} must be positive")
        if self.cooldown_bars < 0:
            raise ValueError("cooldown_bars cannot be negative")


@dataclass(frozen=True)
class RoutingDecision:
    symbol: str
    timeframe: str
    observed_at: datetime
    posterior_fingerprint: str
    policy_ids: tuple[str, ...]
    incumbent_strategy_id: str | None
    challenger_strategy_id: str | None
    chosen_strategy_id: str | None
    chosen_policy_id: str | None
    chosen_params: Mapping[str, Any]
    handover_state: HandoverState
    reason: str
    allow_new_exposure: bool
    exposure_multiplier: float
    candidate_score: float | None
    incumbent_score: float | None
    position_owner_strategy_id: str | None
    decision_id: str = field(init=False)

    def __post_init__(self) -> None:
        if self.observed_at.tzinfo is None or self.observed_at.utcoffset() is None:
            raise ValueError("observed_at must be timezone-aware")
        if not math.isfinite(self.exposure_multiplier) or not (
            0.0 <= self.exposure_multiplier <= 1.0
        ):
            raise ValueError("exposure_multiplier must be finite and in [0, 1]")
        encoded = json.dumps(
            self._identity_payload(),
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode()
        object.__setattr__(self, "decision_id", hashlib.sha256(encoded).hexdigest())

    def _identity_payload(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "observed_at": self.observed_at.isoformat(),
            "posterior_fingerprint": self.posterior_fingerprint,
            "policy_ids": list(self.policy_ids),
            "incumbent_strategy_id": self.incumbent_strategy_id,
            "challenger_strategy_id": self.challenger_strategy_id,
            "chosen_strategy_id": self.chosen_strategy_id,
            "chosen_policy_id": self.chosen_policy_id,
            "chosen_params": dict(self.chosen_params),
            "handover_state": self.handover_state.value,
            "reason": self.reason,
            "allow_new_exposure": self.allow_new_exposure,
            "exposure_multiplier": self.exposure_multiplier,
            "candidate_score": self.candidate_score,
            "incumbent_score": self.incumbent_score,
            "position_owner_strategy_id": self.position_owner_strategy_id,
        }

    def to_dict(self) -> dict[str, Any]:
        return {"decision_id": self.decision_id, **self._identity_payload()}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> RoutingDecision:
        decision = cls(
            symbol=str(value["symbol"]),
            timeframe=str(value["timeframe"]),
            observed_at=datetime.fromisoformat(str(value["observed_at"])),
            posterior_fingerprint=str(value["posterior_fingerprint"]),
            policy_ids=tuple(value.get("policy_ids", ())),
            incumbent_strategy_id=value.get("incumbent_strategy_id"),
            challenger_strategy_id=value.get("challenger_strategy_id"),
            chosen_strategy_id=value.get("chosen_strategy_id"),
            chosen_policy_id=value.get("chosen_policy_id"),
            chosen_params=dict(value.get("chosen_params", {})),
            handover_state=HandoverState(str(value["handover_state"])),
            reason=str(value["reason"]),
            allow_new_exposure=bool(value["allow_new_exposure"]),
            exposure_multiplier=float(value["exposure_multiplier"]),
            candidate_score=(
                float(value["candidate_score"])
                if value.get("candidate_score") is not None
                else None
            ),
            incumbent_score=(
                float(value["incumbent_score"])
                if value.get("incumbent_score") is not None
                else None
            ),
            position_owner_strategy_id=value.get("position_owner_strategy_id"),
        )
        if value.get("decision_id") != decision.decision_id:
            raise ValueError("routing decision integrity failure")
        return decision


@dataclass
class _RouterState:
    incumbent_strategy_id: str | None = None
    active_policy_id: str | None = None
    pending_strategy_id: str | None = None
    pending_policy_id: str | None = None
    candidate_strategy_id: str | None = None
    candidate_count: int = 0
    dwell_bars: int = 0
    cooldown_remaining: int = 0
    last_observed_at: str | None = None
    last_observation_key: str | None = None
    last_decision: dict[str, Any] | None = None


class RouterStateStore:
    """Checksummed per-symbol router state for restart-safe replay."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, symbol: str, timeframe: str) -> Path:
        safe = symbol.replace("/", "_").replace(":", "_")
        return self.root / f"{safe}__{timeframe}.json"

    def load(self, symbol: str, timeframe: str) -> _RouterState:
        path = self._path(symbol, timeframe)
        if not path.exists():
            return _RouterState()
        stored = json.loads(path.read_text())
        payload = stored.get("state")
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        expected = hashlib.sha256(encoded.encode()).hexdigest()
        if not hmac_compare(expected, str(stored.get("checksum", ""))):
            raise ValueError("router state checksum mismatch")
        return _RouterState(**payload)

    def save(self, symbol: str, timeframe: str, state: _RouterState) -> None:
        payload = asdict(state)
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        stored = {
            "state": payload,
            "checksum": hashlib.sha256(encoded.encode()).hexdigest(),
        }
        path = self._path(symbol, timeframe)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(stored, sort_keys=True, separators=(",", ":")))
        tmp.replace(path)


def hmac_compare(left: str, right: str) -> bool:
    """Constant-time digest comparison without exposing signing internals."""
    import hmac

    return hmac.compare_digest(left, right)


class AdaptiveStrategyRouter:
    """Route one pair using signed policies and a full regime posterior."""

    def __init__(
        self,
        policy_registry: SelectionPolicyRegistry,
        *,
        verification_key: bytes,
        key_id: str,
        environment: Environment = Environment.RESEARCH,
        state_store: RouterStateStore,
        audit_path: Path,
        config: AdaptiveRouterConfig | None = None,
    ) -> None:
        if not verification_key or not key_id.strip():
            raise ValueError("verification key and key_id are required")
        self.policy_registry = policy_registry
        self.verification_key = verification_key
        self.key_id = key_id
        self.environment = environment
        self.state_store = state_store
        self.audit_path = Path(audit_path)
        self.audit_path.parent.mkdir(parents=True, exist_ok=True)
        self.config = config or AdaptiveRouterConfig()

    def _verified_policies(
        self,
        symbol: str,
        timeframe: str,
        posterior: RegimePosterior,
        now: datetime,
    ) -> dict[str, SelectionPolicyArtifact]:
        policies: dict[str, SelectionPolicyArtifact] = {}
        for regime, probability in posterior.as_mapping.items():
            if probability <= 0.0:
                continue
            policy = self.policy_registry.get_active_verified(
                symbol,
                timeframe,
                regime,
                key=self.verification_key,
                key_id=self.key_id,
                now=now,
                max_age_days=self.config.max_policy_age_days,
            )
            if policy is not None:
                policies[regime] = policy
        return policies

    @staticmethod
    def _policy_score(policy: SelectionPolicyArtifact) -> float | None:
        raw = policy.scores.get(
            "selection_score", policy.scores.get("median_test_sharpe")
        )
        if raw is None or not math.isfinite(float(raw)):
            return None
        return float(raw)

    def _score_strategies(
        self,
        posterior: RegimePosterior,
        policies: Mapping[str, SelectionPolicyArtifact],
    ) -> tuple[dict[str, float], dict[str, SelectionPolicyArtifact], float]:
        scores: dict[str, float] = {}
        representative: dict[str, SelectionPolicyArtifact] = {}
        contribution: dict[str, float] = {}
        coverage = 0.0
        for regime, policy in policies.items():
            probability = posterior.as_mapping[regime]
            score = self._policy_score(policy)
            if score is None:
                continue
            coverage += probability
            strategy_id = policy.incumbent.strategy_id
            weighted = probability * score
            scores[strategy_id] = scores.get(strategy_id, 0.0) + weighted
            if weighted > contribution.get(strategy_id, float("-inf")):
                contribution[strategy_id] = weighted
                representative[strategy_id] = policy
        return scores, representative, coverage

    def _observation_key(
        self,
        *,
        symbol: str,
        timeframe: str,
        observed_at: datetime,
        posterior: RegimePosterior,
        position_is_flat: bool,
        position_owner_strategy_id: str | None,
    ) -> str:
        payload = {
            "symbol": symbol,
            "timeframe": timeframe,
            "observed_at": observed_at.isoformat(),
            "posterior": posterior.fingerprint,
            "position_is_flat": position_is_flat,
            "position_owner_strategy_id": position_owner_strategy_id,
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(encoded.encode()).hexdigest()

    def _persist_decision(
        self,
        state: _RouterState,
        decision: RoutingDecision,
        observation_key: str,
    ) -> RoutingDecision:
        state.last_observed_at = decision.observed_at.isoformat()
        state.last_observation_key = observation_key
        state.last_decision = decision.to_dict()
        self.state_store.save(decision.symbol, decision.timeframe, state)
        with self.audit_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(decision.to_dict(), sort_keys=True) + "\n")
        return decision

    def _decision(
        self,
        *,
        symbol: str,
        timeframe: str,
        observed_at: datetime,
        posterior: RegimePosterior,
        policies: Mapping[str, SelectionPolicyArtifact],
        state: _RouterState,
        challenger: str | None,
        chosen: str | None,
        chosen_policy: SelectionPolicyArtifact | None,
        handover: HandoverState,
        reason: str,
        allow_new_exposure: bool,
        exposure_multiplier: float,
        candidate_score: float | None,
        incumbent_score: float | None,
        position_owner_strategy_id: str | None,
    ) -> RoutingDecision:
        return RoutingDecision(
            symbol=symbol,
            timeframe=timeframe,
            observed_at=observed_at,
            posterior_fingerprint=posterior.fingerprint,
            policy_ids=tuple(sorted({p.policy_id for p in policies.values()})),
            incumbent_strategy_id=state.incumbent_strategy_id,
            challenger_strategy_id=challenger,
            chosen_strategy_id=chosen,
            chosen_policy_id=chosen_policy.policy_id if chosen_policy else None,
            chosen_params=dict(chosen_policy.incumbent.params) if chosen_policy else {},
            handover_state=handover,
            reason=reason,
            allow_new_exposure=allow_new_exposure,
            exposure_multiplier=exposure_multiplier,
            candidate_score=candidate_score,
            incumbent_score=incumbent_score,
            position_owner_strategy_id=position_owner_strategy_id,
        )

    def route(
        self,
        *,
        symbol: str,
        timeframe: str,
        posterior: RegimePosterior,
        observed_at: datetime,
        position_is_flat: bool,
        position_owner_strategy_id: str | None = None,
    ) -> RoutingDecision:
        """Return one idempotent decision for a closed-bar observation."""
        if observed_at.tzinfo is None or observed_at.utcoffset() is None:
            raise ValueError("observed_at must be timezone-aware")
        state = self.state_store.load(symbol, timeframe)
        observation_key = self._observation_key(
            symbol=symbol,
            timeframe=timeframe,
            observed_at=observed_at,
            posterior=posterior,
            position_is_flat=position_is_flat,
            position_owner_strategy_id=position_owner_strategy_id,
        )
        if state.last_observation_key == observation_key and state.last_decision:
            return RoutingDecision.from_dict(state.last_decision)
        if state.last_observed_at is not None:
            previous_time = datetime.fromisoformat(state.last_observed_at)
            if observed_at <= previous_time:
                raise ValueError("routing observations must be strictly chronological")

        policies = self._verified_policies(symbol, timeframe, posterior, observed_at)
        scores, representatives, coverage = self._score_strategies(posterior, policies)

        owner = None if position_is_flat else position_owner_strategy_id
        if not position_is_flat and owner is None:
            decision = self._decision(
                symbol=symbol,
                timeframe=timeframe,
                observed_at=observed_at,
                posterior=posterior,
                policies=policies,
                state=state,
                challenger=None,
                chosen=None,
                chosen_policy=None,
                handover=HandoverState.WAIT_FLAT,
                reason="OPEN_POSITION_OWNER_MISSING",
                allow_new_exposure=False,
                exposure_multiplier=0.0,
                candidate_score=None,
                incumbent_score=None,
                position_owner_strategy_id=None,
            )
            return self._persist_decision(state, decision, observation_key)

        posterior_ready = posterior.is_production_ready(
            now=observed_at,
            max_age_seconds=self.config.max_posterior_age_seconds,
            max_ood_score=self.config.max_ood_score,
        )
        uncertainty_reason = None
        if not posterior_ready:
            uncertainty_reason = "POSTERIOR_STALE_OOD_OR_UNVERSIONED"
        elif posterior.normalized_entropy > self.config.entropy_threshold:
            uncertainty_reason = "POSTERIOR_HIGH_ENTROPY"
        elif coverage < self.config.min_policy_coverage:
            uncertainty_reason = "SIGNED_POLICY_COVERAGE_INSUFFICIENT"
        elif not scores:
            uncertainty_reason = "NO_VALID_SIGNED_POLICY"
        if uncertainty_reason is not None:
            chosen_policy = representatives.get(owner) if owner else None
            decision = self._decision(
                symbol=symbol,
                timeframe=timeframe,
                observed_at=observed_at,
                posterior=posterior,
                policies=policies,
                state=state,
                challenger=None,
                chosen=owner,
                chosen_policy=chosen_policy,
                handover=(
                    HandoverState.WAIT_FLAT if owner else HandoverState.SWITCH_PENDING
                ),
                reason=uncertainty_reason,
                allow_new_exposure=False,
                exposure_multiplier=0.0,
                candidate_score=None,
                incumbent_score=scores.get(state.incumbent_strategy_id or ""),
                position_owner_strategy_id=owner,
            )
            return self._persist_decision(state, decision, observation_key)

        best_strategy = max(scores, key=scores.__getitem__)
        best_score = scores[best_strategy]
        best_policy = representatives[best_strategy]
        incumbent = state.incumbent_strategy_id
        incumbent_score = scores.get(incumbent) if incumbent else None

        # A pending switch cannot activate until the old position is flat.
        if state.pending_strategy_id is not None:
            if not position_is_flat:
                decision = self._decision(
                    symbol=symbol,
                    timeframe=timeframe,
                    observed_at=observed_at,
                    posterior=posterior,
                    policies=policies,
                    state=state,
                    challenger=state.pending_strategy_id,
                    chosen=owner,
                    chosen_policy=representatives.get(owner or ""),
                    handover=HandoverState.WAIT_FLAT,
                    reason="POSITION_OWNER_PINNED_UNTIL_FLAT",
                    allow_new_exposure=False,
                    exposure_multiplier=0.0,
                    candidate_score=scores.get(state.pending_strategy_id),
                    incumbent_score=incumbent_score,
                    position_owner_strategy_id=owner,
                )
                return self._persist_decision(state, decision, observation_key)
            if best_strategy == state.pending_strategy_id:
                decision = self._decision(
                    symbol=symbol,
                    timeframe=timeframe,
                    observed_at=observed_at,
                    posterior=posterior,
                    policies=policies,
                    state=state,
                    challenger=best_strategy,
                    chosen=best_strategy,
                    chosen_policy=best_policy,
                    handover=HandoverState.ACTIVATE,
                    reason="PENDING_SWITCH_ACTIVATED_AFTER_FLAT",
                    allow_new_exposure=True,
                    exposure_multiplier=min(
                        best_policy.risk_cap, posterior.conviction_multiplier
                    ),
                    candidate_score=best_score,
                    incumbent_score=incumbent_score,
                    position_owner_strategy_id=None,
                )
                state.incumbent_strategy_id = best_strategy
                state.active_policy_id = best_policy.policy_id
                state.pending_strategy_id = None
                state.pending_policy_id = None
                state.candidate_strategy_id = None
                state.candidate_count = 0
                state.dwell_bars = 0
                state.cooldown_remaining = self.config.cooldown_bars
                return self._persist_decision(state, decision, observation_key)
            state.pending_strategy_id = None
            state.pending_policy_id = None

        if incumbent == best_strategy:
            state.candidate_strategy_id = None
            state.candidate_count = 0
            state.dwell_bars += 1
            state.cooldown_remaining = max(0, state.cooldown_remaining - 1)
            state.active_policy_id = best_policy.policy_id
            decision = self._decision(
                symbol=symbol,
                timeframe=timeframe,
                observed_at=observed_at,
                posterior=posterior,
                policies=policies,
                state=state,
                challenger=None,
                chosen=owner or incumbent,
                chosen_policy=best_policy,
                handover=HandoverState.STABLE,
                reason="INCUMBENT_RETAINED",
                allow_new_exposure=position_is_flat or owner == incumbent,
                exposure_multiplier=min(
                    best_policy.risk_cap, posterior.conviction_multiplier
                ),
                candidate_score=best_score,
                incumbent_score=best_score,
                position_owner_strategy_id=owner,
            )
            return self._persist_decision(state, decision, observation_key)

        if incumbent is not None and incumbent_score is not None:
            required_delta = max(
                abs(incumbent_score) * self.config.score_margin,
                self.config.min_score_delta,
            )
            if best_score - incumbent_score < required_delta:
                state.dwell_bars += 1
                state.cooldown_remaining = max(0, state.cooldown_remaining - 1)
                decision = self._decision(
                    symbol=symbol,
                    timeframe=timeframe,
                    observed_at=observed_at,
                    posterior=posterior,
                    policies=policies,
                    state=state,
                    challenger=best_strategy,
                    chosen=owner or incumbent,
                    chosen_policy=representatives.get(incumbent),
                    handover=HandoverState.STABLE,
                    reason="CHALLENGER_SCORE_MARGIN_INSUFFICIENT",
                    allow_new_exposure=position_is_flat or owner == incumbent,
                    exposure_multiplier=min(
                        representatives[incumbent].risk_cap,
                        posterior.conviction_multiplier,
                    ),
                    candidate_score=best_score,
                    incumbent_score=incumbent_score,
                    position_owner_strategy_id=owner,
                )
                return self._persist_decision(state, decision, observation_key)
            if (
                state.dwell_bars < self.config.min_dwell_bars
                or state.cooldown_remaining > 0
            ):
                state.dwell_bars += 1
                state.cooldown_remaining = max(0, state.cooldown_remaining - 1)
                decision = self._decision(
                    symbol=symbol,
                    timeframe=timeframe,
                    observed_at=observed_at,
                    posterior=posterior,
                    policies=policies,
                    state=state,
                    challenger=best_strategy,
                    chosen=owner or incumbent,
                    chosen_policy=representatives.get(incumbent),
                    handover=HandoverState.STABLE,
                    reason="MIN_DWELL_OR_COOLDOWN_ACTIVE",
                    allow_new_exposure=position_is_flat or owner == incumbent,
                    exposure_multiplier=min(
                        representatives[incumbent].risk_cap,
                        posterior.conviction_multiplier,
                    ),
                    candidate_score=best_score,
                    incumbent_score=incumbent_score,
                    position_owner_strategy_id=owner,
                )
                return self._persist_decision(state, decision, observation_key)

        if state.candidate_strategy_id == best_strategy:
            state.candidate_count += 1
        else:
            state.candidate_strategy_id = best_strategy
            state.candidate_count = 1
        if state.candidate_count < self.config.persistence_bars:
            decision = self._decision(
                symbol=symbol,
                timeframe=timeframe,
                observed_at=observed_at,
                posterior=posterior,
                policies=policies,
                state=state,
                challenger=best_strategy,
                chosen=owner or incumbent,
                chosen_policy=representatives.get(owner or incumbent or ""),
                handover=HandoverState.SWITCH_PENDING,
                reason="CHALLENGER_PERSISTENCE_PENDING",
                allow_new_exposure=False,
                exposure_multiplier=0.0,
                candidate_score=best_score,
                incumbent_score=incumbent_score,
                position_owner_strategy_id=owner,
            )
            return self._persist_decision(state, decision, observation_key)

        if not position_is_flat:
            state.pending_strategy_id = best_strategy
            state.pending_policy_id = best_policy.policy_id
            decision = self._decision(
                symbol=symbol,
                timeframe=timeframe,
                observed_at=observed_at,
                posterior=posterior,
                policies=policies,
                state=state,
                challenger=best_strategy,
                chosen=owner,
                chosen_policy=representatives.get(owner or ""),
                handover=HandoverState.WAIT_FLAT,
                reason="POSITION_OWNER_PINNED_UNTIL_FLAT",
                allow_new_exposure=False,
                exposure_multiplier=0.0,
                candidate_score=best_score,
                incumbent_score=incumbent_score,
                position_owner_strategy_id=owner,
            )
            return self._persist_decision(state, decision, observation_key)

        decision = self._decision(
            symbol=symbol,
            timeframe=timeframe,
            observed_at=observed_at,
            posterior=posterior,
            policies=policies,
            state=state,
            challenger=best_strategy,
            chosen=best_strategy,
            chosen_policy=best_policy,
            handover=HandoverState.ACTIVATE,
            reason="CHALLENGER_ACTIVATED",
            allow_new_exposure=True,
            exposure_multiplier=min(
                best_policy.risk_cap, posterior.conviction_multiplier
            ),
            candidate_score=best_score,
            incumbent_score=incumbent_score,
            position_owner_strategy_id=None,
        )
        state.incumbent_strategy_id = best_strategy
        state.active_policy_id = best_policy.policy_id
        state.candidate_strategy_id = None
        state.candidate_count = 0
        state.dwell_bars = 0
        state.cooldown_remaining = self.config.cooldown_bars
        return self._persist_decision(state, decision, observation_key)


@dataclass(frozen=True)
class AdaptiveForecastResult:
    """A routed forecast plus immutable policy/descriptor attribution."""

    decision: RoutingDecision
    forecast: Forecast | None
    policy_id: str | None
    strategy_descriptor_id: str | None
    reason: str

    @property
    def executable(self) -> bool:
        return self.forecast is not None and self.decision.allow_new_exposure


class AdaptiveForecastRuntime:
    """Resolve a routing decision into a canonical, parameter-bound forecast.

    This component intentionally emits forecasts only. Order sizing and submission
    remain owned by the independent portfolio/risk/execution authorities.
    """

    def __init__(
        self,
        policy_registry: SelectionPolicyRegistry,
        *,
        verification_key: bytes,
        key_id: str,
        environment: Environment = Environment.RESEARCH,
    ) -> None:
        if not verification_key or not key_id.strip():
            raise ValueError("verification key and key_id are required")
        self.policy_registry = policy_registry
        self.verification_key = verification_key
        self.key_id = key_id
        self.environment = environment
        self._cache: dict[str, tuple[StrategyDescriptor, LegacyDataFrameAdapter]] = {}

    def _resolve(
        self, decision: RoutingDecision
    ) -> tuple[SelectionPolicyArtifact, StrategyDescriptor, LegacyDataFrameAdapter]:
        if decision.chosen_policy_id is None or decision.chosen_strategy_id is None:
            raise ValueError("routing decision has no chosen policy/strategy")
        policy = self.policy_registry.get(decision.chosen_policy_id)
        if policy is None:
            raise ValueError("chosen policy is missing from registry")
        signature = self.policy_registry.get_signature(policy.policy_id, self.key_id)
        if (
            signature is None
            or signature.key_id != self.key_id
            or not signature.verify(policy, key=self.verification_key)
        ):
            raise ValueError("chosen policy signature is missing or invalid")
        if not policy.is_valid(decision.observed_at):
            raise ValueError("chosen policy is inactive or outside its validity window")
        if policy.incumbent.strategy_id != decision.chosen_strategy_id:
            raise ValueError("routing decision strategy does not match chosen policy")
        if dict(policy.incumbent.params) != dict(decision.chosen_params):
            raise ValueError("routing decision parameters do not match chosen policy")
        cached = self._cache.get(policy.policy_id)
        if cached is None:
            # Lazy import avoids the authority package <-> canonical registry
            # import cycle while keeping the runtime resolver explicit.
            from trading_agent.strategies.canonical.candidates import (
                build_parameterized_adapter,
            )
            from trading_agent.strategies.canonical.registry import (
                RegistryIntegrityError,
            )

            try:
                descriptor, adapter = build_parameterized_adapter(
                    policy.incumbent.strategy_id, policy.incumbent.params
                )
            except (KeyError, RegistryIntegrityError, ValueError) as exc:
                raise ValueError(f"chosen strategy cannot be resolved: {exc}") from exc
            if descriptor.code_sha != policy.incumbent.code_sha:
                raise ValueError(
                    "policy strategy code_sha does not match allowlisted code"
                )
            if (
                descriptor.research_only
                and self.environment is not Environment.RESEARCH
            ):
                raise ValueError(
                    "research_only strategy is blocked outside the research environment"
                )
            cached = (descriptor, adapter)
            self._cache[policy.policy_id] = cached
        return policy, cached[0], cached[1]

    def forecast(
        self,
        decision: RoutingDecision,
        observation: MarketObservation,
    ) -> AdaptiveForecastResult:
        if observation.symbol != decision.symbol:
            raise ValueError("observation symbol does not match routing decision")
        if observation.observed_at != decision.observed_at:
            raise ValueError("observation timestamp does not match routing decision")
        if not decision.allow_new_exposure:
            return AdaptiveForecastResult(
                decision=decision,
                forecast=None,
                policy_id=decision.chosen_policy_id,
                strategy_descriptor_id=None,
                reason=decision.reason,
            )
        policy, descriptor, adapter = self._resolve(decision)
        if observation.symbol not in descriptor.supported_symbols:
            raise ValueError("strategy descriptor does not support observation symbol")
        forecast = adapter.forecast(observation)
        return AdaptiveForecastResult(
            decision=decision,
            forecast=forecast,
            policy_id=policy.policy_id,
            strategy_descriptor_id=descriptor.descriptor_id,
            reason="ROUTED_FORECAST_READY",
        )


__all__ = [
    "AdaptiveForecastResult",
    "AdaptiveForecastRuntime",
    "AdaptiveRouterConfig",
    "AdaptiveStrategyRouter",
    "HandoverState",
    "RouterStateStore",
    "RoutingDecision",
]
