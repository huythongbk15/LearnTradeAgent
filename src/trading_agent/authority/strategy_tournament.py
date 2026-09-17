"""Strategy Tournament Engine — multi-strategy dynamic selection with shadow scoring.

Extends :class:`AdaptiveStrategyRouter` (not replaces) to evaluate a pool of
strategies per market regime.  In **shadow mode** (default, kill-switch ON) the
tournament logs every strategy's live performance without executing
non-chosen strategies — no portfolio changes occur.  In **live mode**
(``TOURNAMENT_SHADOW_MODE=0``) the tournament auto-promotes / de-motes the
incumbent strategy when a challenger's rolling Sharpe consistently
outperforms it.

The class reuses :class:`HandoverState` and the existing position-switching
infrastructure from :class:`AdaptiveExecutionBridge` (~85 % infra reuse).
"""

from __future__ import annotations

import hashlib
import json
import math
import os
from collections import deque
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from trading_agent.authority.adaptive_router import (
    AdaptiveStrategyRouter,
    AdaptiveRouterConfig,
    RouterStateStore,
    RoutingDecision,
)
from trading_agent.authority.config import Environment
from trading_agent.ml.regime_detection import RegimePosterior
from trading_agent.research.forecast import Forecast, MarketObservation
from trading_agent.research.selection_policy import (
    ParamArtifact,
    PolicyStatus,
    SelectionPolicyArtifact,
    SelectionPolicyRegistry,
)
from trading_agent.strategies.canonical.candidates import FIRST_WAVE_DESCRIPTORS
from trading_agent.strategies.canonical.descriptor import StrategyDescriptor

# ── Config ───────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class TournamentConfig:
    """Tunable thresholds for tournament auto-promotion / de-promotion."""

    shadow_mode: bool = True  # Kill switch: True = shadow only, no live changes
    shadow_lookback: int = 1440  # Bars of shadow history (~2 months on 1 h)
    min_shadow_bars: int = 288  # Min bars before considering promotion (~2 weeks)
    promotion_sharpe_threshold: float = 0.20
    promotion_return_threshold: float = 0.03
    demotion_sharpe_threshold: float = -0.10
    score_margin: float = 0.10  # Min Sharpe delta above incumbent to promote
    promotion_persistence: int = 6  # Consecutive bars above threshold
    # ── Production risk controls ──
    max_drawdown_limit: float = 0.30  # Demote strategy if drawdown exceeds 30 %
    position_size_cap: float = 0.85  # Cap single-strategy weight at 85 %
    sharpe_circuit_breaker: float = -0.50  # Demote incumbent if Sharpe drops below this
    circuit_breaker_lookback: int = 288  # Bars over which to evaluate circuit breaker (2 weeks on 1 h)

    def __post_init__(self) -> None:
        if self.shadow_lookback <= 0:
            raise ValueError("shadow_lookback must be positive")
        if self.min_shadow_bars <= 0:
            raise ValueError("min_shadow_bars must be positive")
        if self.promotion_persistence <= 0:
            raise ValueError("promotion_persistence must be positive")


# ── Shadow metrics tracker ──────────────────────────────────────────────


@dataclass
class _ShadowMetrics:
    """Rolling per-strategy metrics accumulated during shadow mode."""

    returns: deque[float] = field(default_factory=lambda: deque(maxlen=1440))
    weights: deque[float] = field(default_factory=lambda: deque(maxlen=1440))
    consecutive_up: int = 0
    consecutive_down: int = 0
    promoted: bool = False
    _peak_cum: float = 1.0  # Peak cumulative return for drawdown calc

    @property
    def n(self) -> int:
        return len(self.returns)

    def sharpe(self) -> float:
        """Annualised Sharpe assuming 1 h bars (8760 bars / year)."""
        if len(self.returns) < 2:
            return 0.0
        mean = sum(self.returns) / len(self.returns)
        var = sum((r - mean) ** 2 for r in self.returns) / (len(self.returns) - 1)
        std = math.sqrt(var)
        if std == 0.0:
            return 0.0
        return mean / std * math.sqrt(8760)

    def total_return(self) -> float:
        """Cumulative shadow return."""
        if not self.returns:
            return 0.0
        prod = 1.0
        for r in self.returns:
            prod *= 1.0 + r
        return prod - 1.0

    def trade_count(self) -> int:
        """Count bars with non-trivial position (|weight| > 1 %)."""
        return sum(1 for w in self.weights if abs(w) > 0.01)

    @property
    def max_drawdown(self) -> float:
        """Peak-to-trough drawdown of cumulative returns (0.0 = no drawdown)."""
        cum = 1.0
        peak = 1.0
        max_dd = 0.0
        for r in self.returns:
            cum *= 1.0 + r
            peak = max(peak, cum)
            dd = (cum - peak) / peak if peak > 0 else 0.0
            max_dd = min(max_dd, dd)
        return max_dd

    def add(self, ret: float, weight: float) -> None:
        self.returns.append(ret)
        self.weights.append(weight)


# ── Tournament state store ────────────────────────────────────────────────


@dataclass
class TournamentState:
    """Per-symbol tournament state, persisted for restart-safe replay."""

    incumbent_strategy_id: str | None = None
    challenger_strategy_id: str | None = None
    challenger_persistence: int = 0
    shadow_metrics: dict[str, _ShadowMetrics] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "incumbent_strategy_id": self.incumbent_strategy_id,
            "challenger_strategy_id": self.challenger_strategy_id,
            "challenger_persistence": self.challenger_persistence,
            "shadow_metrics": {
                sid: {
                    "returns": list(m.returns),
                    "weights": list(m.weights),
                    "consecutive_up": m.consecutive_up,
                    "consecutive_down": m.consecutive_down,
                    "promoted": m.promoted,
                }
                for sid, m in self.shadow_metrics.items()
            },
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> TournamentState:
        state = cls()
        state.incumbent_strategy_id = payload.get("incumbent_strategy_id")
        state.challenger_strategy_id = payload.get("challenger_strategy_id")
        state.challenger_persistence = payload.get("challenger_persistence", 0)
        raw = payload.get("shadow_metrics", {})
        for sid, data in raw.items():
            metrics = _ShadowMetrics()
            metrics.returns = deque(data.get("returns", []), maxlen=1440)
            metrics.weights = deque(data.get("weights", []), maxlen=1440)
            metrics.consecutive_up = data.get("consecutive_up", 0)
            metrics.consecutive_down = data.get("consecutive_down", 0)
            metrics.promoted = data.get("promoted", False)
            state.shadow_metrics[sid] = metrics
        return state


class TournamentStateStore:
    """Persisted tournament state per symbol / timeframe."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, symbol: str, timeframe: str) -> Path:
        safe = symbol.replace("/", "_").replace(":", "_")
        return self.root / f"{safe}__{timeframe}.json"

    def load(self, symbol: str, timeframe: str) -> TournamentState:
        path = self._path(symbol, timeframe)
        if not path.exists():
            return TournamentState()
        stored = json.loads(path.read_text())
        return TournamentState.from_dict(stored.get("state", {}))

    def save(self, symbol: str, timeframe: str, state: TournamentState) -> None:
        payload = {
            "incumbent_strategy_id": state.incumbent_strategy_id,
            "challenger_strategy_id": state.challenger_strategy_id,
            "challenger_persistence": state.challenger_persistence,
            "shadow_metrics": {
                sid: {
                    "returns": list(m.returns),
                    "weights": list(m.weights),
                    "consecutive_up": m.consecutive_up,
                    "consecutive_down": m.consecutive_down,
                    "promoted": m.promoted,
                }
                for sid, m in state.shadow_metrics.items()
            },
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        stored = {"state": payload, "checksum": hashlib.sha256(encoded.encode()).hexdigest()}
        path = self._path(symbol, timeframe)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(stored, sort_keys=True, separators=(",", ":")))
        tmp.replace(path)


# ── Tournament ──────────────────────────────────────────────────────────


class StrategyTournament(AdaptiveStrategyRouter):
    """Dynamic multi-strategy selection with shadow-mode auto-promotion.

    Extends :class:`AdaptiveStrategyRouter` — all existing routing logic
    (signed policies, handover state, fail-closed gates) is preserved.
    This class adds:

    * **Shadow tracking** — every strategy in ``pool`` is evaluated on each
      observation; returns are accumulated in a rolling window.
    * **Auto-promotion** — when a challenger's rolling Sharpe consistently
      exceeds the incumbent's by ``score_margin`` for
      ``promotion_persistence`` bars, the policy registry is updated.
    * **Kill switch** — ``TOURNAMENT_SHADOW_MODE=1`` (default) disables all
      live portfolio changes; only shadow metrics are logged.

    Pool defaults to :data:`FIRST_WAVE_DESCRIPTORS` (7 strategies).
    ``funding_carry`` and ``regime_switching`` can be excluded via
    ``exclude`` to honour WFO gate failures.
    """

    def __init__(
        self,
        policy_registry: SelectionPolicyRegistry,
        *,
        verification_key: bytes,
        key_id: str,
        environment: Environment = Environment.RESEARCH,
        state_store: RouterStateStore,
        audit_path: Path,
        tournament_state_root: Path,
        config: AdaptiveRouterConfig | None = None,
        tournament_config: TournamentConfig | None = None,
        pool: dict[str, StrategyDescriptor] | None = None,
        exclude: tuple[str, ...] = (),
    ) -> None:
        super().__init__(
            policy_registry=policy_registry,
            verification_key=verification_key,
            key_id=key_id,
            environment=environment,
            state_store=state_store,
            audit_path=audit_path,
            config=config,
        )

        self.pool: dict[str, StrategyDescriptor] = {
            k: v for k, v in (pool or FIRST_WAVE_DESCRIPTORS).items()
            if k not in exclude
        }
        self.tournament_config = tournament_config or TournamentConfig()
        self.tournament_state_store = TournamentStateStore(tournament_state_root)

        # Resolve kill switch from environment (override for safety tests)
        env_shadow = os.getenv("TOURNAMENT_SHADOW_MODE", "")
        if env_shadow:
            self.tournament_config = replace(
                self.tournament_config,
                shadow_mode=(env_shadow.lower() in ("1", "true", "yes"))
            )

        # Per-symbol live state
        self._live_state: dict[tuple[str, str], TournamentState] = {}

    # ── Public API ──────────────────────────────────────────────────────

    def route(
        self,
        *,
        symbol: str,
        timeframe: str,
        posterior: RegimePosterior,
        observed_at: datetime,
        position_is_flat: bool,
        position_owner_strategy_id: str | None = None,
        observation: MarketObservation | None = None,
        bar_return: float | None = None,
    ) -> RoutingDecision:
        """Route one observation through the tournament.

        Extra params ``observation`` and ``bar_return`` enable shadow scoring
        of all pool strategies.  When either is ``None``, only the parent
        routing logic runs (backward-compatible with ``AdaptiveStrategyRouter``).
        """
        decision = super().route(
            symbol=symbol,
            timeframe=timeframe,
            posterior=posterior,
            observed_at=observed_at,
            position_is_flat=position_is_flat,
            position_owner_strategy_id=position_owner_strategy_id,
        )

        # Shadow-track all strategies if we have observation data
        if observation is not None and bar_return is not None:
            shadow_returns = self._shadow_score_all(
                symbol, timeframe, observation, bar_return
            )
            self._update_shadow_metrics(
                symbol, timeframe, decision, shadow_returns, bar_return
            )

            # Auto-promote / de-promote when not in shadow mode
            state = self._live_state.get((symbol, timeframe))
            if state is not None:
                if not self.tournament_config.shadow_mode:
                    self._maybe_promote(symbol, timeframe, decision, state)
                self.tournament_state_store.save(symbol, timeframe, state)

        return decision

    def shadow_forecast(
        self, strategy_id: str, observation: MarketObservation
    ) -> Forecast | None:
        """Compute a shadow forecast for *any* pool strategy (no execution).

        Params are pulled from the active policy in the registry for this
        symbol / timeframe.  Falls back to descriptor defaults.
        """
        from trading_agent.strategies.canonical.candidates import (
            build_parameterized_adapter,
            validate_params,
        )

        descriptor = self.pool.get(strategy_id)
        if descriptor is None:
            return None

        params: dict[str, Any] = {}

        # Try to get params from active policy (verified signature)
        try:
            tf = observation.features.get("timeframe", "1h")
            for regime in ("trend", "mean_reversion", "high_vol", "crisis", "other"):
                policy = self.policy_registry.get_active_verified(
                    symbol=observation.symbol,
                    timeframe=tf,
                    regime=regime,
                    key=self.verification_key,
                    key_id=self.key_id,
                    now=datetime.now(UTC),
                )
                if policy and policy.incumbent.strategy_id == strategy_id:
                    params = dict(policy.incumbent.params)
                    break
        except Exception:
            pass

        if not params:
            params = validate_params(strategy_id, None)

        try:
            _, adapter = build_parameterized_adapter(strategy_id, params)
            return adapter.forecast(observation)
        except Exception:
            return None

    # ── Shadow scoring ──────────────────────────────────────────────────

    def _shadow_score_all(
        self,
        symbol: str,
        timeframe: str,
        observation: MarketObservation,
        bar_return: float,
    ) -> dict[str, float]:
        """Return {strategy_id: shadow_return} for every pool strategy."""
        shadow: dict[str, float] = {}
        for sid in self.pool:
            fc = self.shadow_forecast(sid, observation)
            if fc is None:
                shadow[sid] = 0.0
                continue
            # Shadow return = signal direction × bar return × conviction
            signal = fc.expected_excess_return
            # Preserve signal direction: BUY(+0.01)→+1, SELL(-0.01)→-1
            weight = max(-1.0, min(1.0, signal * 100)) if signal != 0 else 0.0
            # Apply position size cap from risk config
            cap = self.tournament_config.position_size_cap
            weight = max(-cap, min(cap, weight))
            shadow[sid] = bar_return * weight
        return shadow

    def _update_shadow_metrics(
        self,
        symbol: str,
        timeframe: str,
        decision: RoutingDecision,
        shadow_returns: dict[str, float],
        bar_return: float,
    ) -> None:
        key = (symbol, timeframe)
        state = self._live_state.setdefault(key, TournamentState())
        state.incumbent_strategy_id = decision.chosen_strategy_id or state.incumbent_strategy_id

        for sid, ret in shadow_returns.items():
            metrics = state.shadow_metrics.get(sid)
            if metrics is None:
                metrics = _ShadowMetrics()
                state.shadow_metrics[sid] = metrics
            weight = 1.0 if decision.chosen_strategy_id == sid else 0.0
            metrics.add(ret, weight)

    # ── Promotion / de-promotion ─────────────────────────────────────────

    def _maybe_promote(
        self,
        symbol: str,
        timeframe: str,
        decision: RoutingDecision,
        state: TournamentState,
    ) -> None:
        """Check promotion / de-promotion conditions and update registry."""
        cfg = self.tournament_config
        incumbent = state.incumbent_strategy_id or decision.chosen_strategy_id

        # Skip if not enough data
        inc_metrics = state.shadow_metrics.get(incumbent) if incumbent else None
        if inc_metrics is None or inc_metrics.n < cfg.min_shadow_bars:
            return

        inc_sharpe = inc_metrics.sharpe()
        inc_dd = inc_metrics.max_drawdown
        best_sid: str | None = None
        best_sharpe: float = -999.0

        for sid, metrics in state.shadow_metrics.items():
            if sid == incumbent or metrics.n < cfg.min_shadow_bars:
                continue
            # Skip challengers that breach drawdown limit
            if metrics.max_drawdown < -cfg.max_drawdown_limit:
                continue
            sharpe = metrics.sharpe()
            if sharpe > best_sharpe:
                best_sharpe = sharpe
                best_sid = sid

        # ── Circuit breaker: demote incumbent if Sharpe drops below threshold
        #     or drawdown exceeds limit ──
        circuit_breaker_triggered = (
            inc_sharpe < cfg.sharpe_circuit_breaker
            or inc_dd < -cfg.max_drawdown_limit
        )

        if circuit_breaker_triggered and best_sid is not None:
            # Immediate demotion (no persistence wait)
            state.challenger_strategy_id = best_sid
            self._promote(symbol, timeframe, state, incumbent, best_sid)
            return

        if best_sid is None:
            return

        # Promote challenger if it consistently outperforms
        if (
            best_sharpe - inc_sharpe >= cfg.score_margin
            and best_sharpe >= cfg.promotion_sharpe_threshold
        ):
            if inc_sharpe >= cfg.demotion_sharpe_threshold:
                # Challenger beats incumbent + margin
                state.challenger_persistence += 1
                state.challenger_strategy_id = best_sid
                if state.challenger_persistence >= cfg.promotion_persistence:
                    self._promote(symbol, timeframe, state, incumbent, best_sid)
            else:
                # Incumbent below threshold — immediate promotion
                state.challenger_strategy_id = best_sid
                self._promote(symbol, timeframe, state, incumbent, best_sid)
        else:
            state.challenger_persistence = 0

    def _promote(
        self,
        symbol: str,
        timeframe: str,
        state: TournamentState,
        old_incumbent: str | None,
        new_incumbent: str,
    ) -> None:
        """Swap incumbent strategy in the policy registry."""
        # Look up the challenger's WFO score and update policies
        for regime in (  # iterate canonical regime keys
            "trend", "mean_reversion", "high_vol", "crisis", "other"
        ):
            policy = self.policy_registry.get_active_verified(
                symbol=symbol,
                timeframe=timeframe,
                regime=regime,
                key=self.verification_key,
                key_id=self.key_id,
                now=datetime.now(UTC),
            )
            if policy is None:
                continue
            # New policy with challenger as incumbent
            new_policy = SelectionPolicyArtifact(
                symbol=policy.symbol,
                timeframe=policy.timeframe,
                regime=policy.regime,
                incumbent=ParamArtifact(
                    strategy_id=new_incumbent,
                    params=dict(policy.incumbent.params),
                    code_sha=policy.incumbent.code_sha,
                ),
                challengers=policy.challengers,
                scores=policy.scores,
                evidence_ids=policy.evidence_ids,
                validity_start=policy.validity_start,
                validity_end=policy.validity_end,
                risk_cap=policy.risk_cap,
                status=PolicyStatus.ACTIVE,
                created_at=datetime.now(UTC),
                activated_at=datetime.now(UTC),
                activated_by="tournament",
                activation_ticket="AUTO-PROMOTE",
                policy_commit_sha=policy.policy_commit_sha,
                policy_data_manifest_sha=policy.policy_data_manifest_sha,
                policy_feature_manifest_sha=policy.policy_feature_manifest_sha,
                policy_release_digest=policy.policy_release_digest,
                promotion_stage=policy.promotion_stage,
                previous_policy_id=policy.policy_id,
            )
            self.policy_registry.add(new_policy)
            self.policy_registry.deprecate(policy.policy_id)
            # Persist audit entry
            with self.audit_path.open("a", encoding="utf-8") as fh:
                entry = {
                    "event": "TOURNAMENT_PROMOTION",
                    "symbol": symbol,
                    "timeframe": timeframe,
                    "old_incumbent": old_incumbent,
                    "new_incumbent": new_incumbent,
                    "regime": regime,
                    "timestamp": datetime.now(UTC).isoformat(),
                }
                fh.write(json.dumps(entry, sort_keys=True) + "\n")

        state.incumbent_strategy_id = new_incumbent
        state.challenger_persistence = 0
        state.challenger_strategy_id = None
        for m in state.shadow_metrics.values():
            m.promoted = False
        state.shadow_metrics[new_incumbent].promoted = True


__all__ = [
    "StrategyTournament",
    "TournamentConfig",
    "TournamentState",
    "TournamentStateStore",
    "_ShadowMetrics",
]
