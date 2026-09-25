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
import logging
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
from trading_agent.llm.context_enrichment import MarketContext
from trading_agent.research.forecast import Forecast, MarketObservation
from trading_agent.research.selection_policy import (
    ParamArtifact,
    PolicyStatus,
    SelectionPolicyArtifact,
    SelectionPolicyRegistry,
)
from trading_agent.strategies.canonical.candidates import FIRST_WAVE_DESCRIPTORS
from trading_agent.strategies.canonical.descriptor import StrategyDescriptor
from trading_agent.exchanges.health_monitor import HealthMonitor

logger = logging.getLogger(__name__)

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
    # ── Net-of-fees Sharpe gate (P1) ─────────────────────────────────────
    # Gross Sharpe must exceed this for promotion consideration;
    # net-of-fees Sharpe must exceed promotion_net_sharpe_threshold.
    promotion_net_sharpe_threshold: float = 1.5
    # ── Production risk controls ──
    max_drawdown_limit: float = 0.30  # Demote strategy if drawdown exceeds 30 %
    position_size_cap: float = 0.85  # Cap single-strategy weight at 85 %
    sharpe_circuit_breaker: float = -0.50  # Demote incumbent if Sharpe drops below this
    circuit_breaker_lookback: int = 288  # Bars over which to evaluate circuit breaker (2 weeks on 1 h)
    circuit_breaker_warmup: int = 288  # Additional bars after min_shadow_bars before circuit breaker activates
    min_shadow_bars_for_promote: int = 288  # Minimum shadow bars before considering promotion
    significance_alpha: float = 0.05  # Statistical significance level (Welch's t-test)
    bonferroni_correction: bool = True  # Adjust alpha by number of strategies in pool
    # ── Transaction cost model for shadow scoring (net-of-fees Sharpe) ──
    commission_fee: float = 0.001     # 0.1 % per side (entry + exit)
    slippage_fee: float = 0.0005      # 0.05 % per side
    spread_bps: float = 5.0           # 5 bps = 0.05 % per side

    @property
    def total_fee_rate(self) -> float:
        """Total per-side fee rate: round-trip cost = 2 × this."""
        return self.commission_fee + self.slippage_fee + self.spread_bps / 10_000.0

    def __post_init__(self) -> None:
        if self.shadow_lookback <= 0:
            raise ValueError("shadow_lookback must be positive")
        if self.min_shadow_bars <= 0:
            raise ValueError("min_shadow_bars must be positive")
        if self.promotion_persistence <= 0:
            raise ValueError("promotion_persistence must be positive")
        if self.circuit_breaker_warmup < 0:
            raise ValueError("circuit_breaker_warmup must be non-negative")
        if self.commission_fee < 0 or self.commission_fee > 0.1:
            raise ValueError("commission_fee must be in [0, 0.1]")
        if self.slippage_fee < 0 or self.slippage_fee > 0.1:
            raise ValueError("slippage_fee must be in [0, 0.1]")
        if self.spread_bps < 0 or self.spread_bps > 1_000:
            raise ValueError("spread_bps must be in [0, 1000]")


# ── Shadow metrics tracker ──────────────────────────────────────────────


@dataclass
class _ShadowMetrics:
    """Rolling per-strategy metrics accumulated during shadow mode."""

    returns: deque[float] = field(default_factory=lambda: deque(maxlen=1440))
    weights: deque[float] = field(default_factory=lambda: deque(maxlen=1440))
    gross_returns: deque[float] = field(default_factory=lambda: deque(maxlen=1440))
    consecutive_up: int = 0
    consecutive_down: int = 0
    promoted: bool = False
    _peak_cum: float = 1.0  # Peak cumulative return for drawdown calc
    _prev_weight: float = 0.0  # Last weight, for turnover-fee computation

    @property
    def n(self) -> int:
        return len(self.returns)

    def sharpe(self) -> float:
        """Net-of-fees annualised Sharpe assuming 1 h bars (8760 bars / year).

        The stored *returns* deque contains net-of-fees returns (fees already
        deducted in ``add()`` via the turnover cost model).
        """
        if len(self.returns) < 2:
            return 0.0
        mean = sum(self.returns) / len(self.returns)
        var = sum((r - mean) ** 2 for r in self.returns) / (len(self.returns) - 1)
        std = math.sqrt(var)
        if std == 0.0:
            return 0.0
        return mean / std * math.sqrt(8760)

    def gross_sharpe(self) -> float:
        """Gross (pre-fees) annualised Sharpe — for shadow Sharpe delta reporting."""
        if len(self.gross_returns) < 2:
            return 0.0
        mean = sum(self.gross_returns) / len(self.gross_returns)
        var = sum((r - mean) ** 2 for r in self.gross_returns) / (len(self.gross_returns) - 1)
        std = math.sqrt(var)
        if std == 0.0:
            return 0.0
        return mean / std * math.sqrt(8760)

    def net_sharpe(self) -> float:
        """Net-of-fees Sharpe (alias for sharpe())."""
        return self.sharpe()

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

    def add(self, ret: float, weight: float, fee_rate: float = 0.0, gross_ret: float | None = None) -> None:
        """Append a (net-of-fees) return for this strategy.

        When *fee_rate* > 0 the turnover since the last bar is charged:
        ``fee = abs(weight - prev_weight) * fee_rate`` is subtracted from
        *ret* before it is stored, producing a net-of-fees Sharpe.

        *gross_ret* (if provided) is stored separately for gross_sharpe()
        reporting.  When omitted, *ret* is used as both gross and net.
        """
        turnover = abs(weight - self._prev_weight)
        fee = turnover * fee_rate if fee_rate > 0 else 0.0
        net = ret - fee
        self.returns.append(net)
        self.weights.append(weight)
        self.gross_returns.append(gross_ret if gross_ret is not None else ret)
        self._prev_weight = weight


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
                    "gross_returns": list(m.gross_returns),
                    "consecutive_up": m.consecutive_up,
                    "consecutive_down": m.consecutive_down,
                    "promoted": m.promoted,
                    "prev_weight": m._prev_weight,
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
            metrics.gross_returns = deque(data.get("gross_returns", []), maxlen=1440)
            metrics.consecutive_up = data.get("consecutive_up", 0)
            metrics.consecutive_down = data.get("consecutive_down", 0)
            metrics.promoted = data.get("promoted", False)
            metrics._prev_weight = data.get("prev_weight", 0.0)
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
                    "gross_returns": list(m.gross_returns),
                    "consecutive_up": m.consecutive_up,
                    "consecutive_down": m.consecutive_down,
                    "promoted": m.promoted,
                    "prev_weight": m._prev_weight,
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
        health_monitor: HealthMonitor | None = None,
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

        # SelectionAudit — immutable audit trail for routing decisions
        from trading_agent.authority.selection_audit import SelectionAudit
        audit_dir = audit_path.parent / "audit"
        self.audit_store = SelectionAudit(audit_dir / "tournament_audit.sqlite3")

        # Portfolio risk gate — cross-asset exposure caps and circuit breaker
        from trading_agent.authority.portfolio_risk_gate import PortfolioRiskGate
        self.portfolio_risk_gate = PortfolioRiskGate(audit_store=self.audit_store)

        # Exchange health monitor — gates promotion when exchange is unhealthy
        self.health_monitor = health_monitor

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
        market_context: MarketContext | None = None,
    ) -> RoutingDecision:
        """Route one observation through the tournament.

        Extra params ``observation`` and ``bar_return`` enable shadow scoring
        of all pool strategies.  When either is ``None``, only the parent
        routing logic runs (backward-compatible with ``AdaptiveStrategyRouter``).

        ``market_context`` is optional LLM enrichment metadata (advisory only).
        """
        decision = super().route(
            symbol=symbol,
            timeframe=timeframe,
            posterior=posterior,
            observed_at=observed_at,
            position_is_flat=position_is_flat,
            position_owner_strategy_id=position_owner_strategy_id,
            market_context=market_context,
        )

        # Shadow-track all strategies if we have observation data
        shadow_returns: dict[str, tuple[float, float, float]] | None = None
        if observation is not None and bar_return is not None:
            shadow_returns = self._shadow_score_all(
                symbol, timeframe, observation, bar_return
            )
            self._update_shadow_metrics(
                symbol, timeframe, decision, shadow_returns, bar_return
            )

            # Initialize incumbent from active policy if not set yet
            state = self._live_state.get((symbol, timeframe))
            if state is not None and state.incumbent_strategy_id is None:
                self._init_incumbent_from_policy(symbol, timeframe, posterior, state)

            # Auto-promote / de-promote when not in shadow mode
            if state is not None:
                if not self.tournament_config.shadow_mode:
                    self._maybe_promote(symbol, timeframe, decision, state)
                self.tournament_state_store.save(symbol, timeframe, state)

        # ── Portfolio risk gate: cross-asset exposure caps + circuit breaker ─
        # Pass strategy's realized return (not market return) for accurate portfolio Sharpe
        strategy_return = None
        if shadow_returns is not None and decision.chosen_strategy_id:
            net_ret, _, _ = shadow_returns.get(decision.chosen_strategy_id, (0.0, 0.0, 0.0))
            strategy_return = net_ret
        
        decision = self.portfolio_risk_gate.evaluate(
            symbol=symbol,
            timeframe=timeframe,
            decision=decision,
            posterior=posterior,
            market_context=market_context,
            symbol_bar_return=bar_return,
            strategy_return=strategy_return,
        )

        # ── SelectionAudit: immutable decision trail ───────────────────
        try:
            shadow_sharpe = None
            shadow_delta = None
            state = self._live_state.get((symbol, timeframe))
            if state is not None and state.incumbent_strategy_id in state.shadow_metrics:
                inc_metrics = state.shadow_metrics[state.incumbent_strategy_id]
                shadow_sharpe = inc_metrics.sharpe()  # net-of-fees (primary metric)
                shadow_net_sharpe = inc_metrics.net_sharpe()
                shadow_gross_sharpe = inc_metrics.gross_sharpe()
                if state.shadow_metrics:
                    all_sharpes = {
                        sid: m.sharpe() for sid, m in state.shadow_metrics.items()
                    }
                    if all_sharpes:
                        best_sharpe = max(all_sharpes.values())
                        shadow_delta = best_sharpe - shadow_sharpe

            self.audit_store.append(
                decision=decision,
                posterior=posterior,
                regime_tags=(market_context.regime_tags
                             if market_context is not None else {}),
                anomaly_flags=(list(market_context.anomaly_flags)
                              if market_context is not None else []),
                confidence_adjustment=(market_context.confidence_adjustment
                                      if market_context is not None else 1.0),
                cross_asset_signals=(dict(market_context.cross_asset_signals)
                                    if market_context is not None else {}),
                reasoning_snippet=decision.reason,
                shadow_sharpe=shadow_sharpe,
                shadow_sharpe_delta_vs_incumbent=shadow_delta,
                shadow_net_sharpe=shadow_net_sharpe,
                shadow_gross_sharpe=shadow_gross_sharpe,
            )
        except Exception as e:
            logger.debug(f"SelectionAudit append failed: {e}")
        # ────────────────────────────────────────────────────────────────

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
    ) -> dict[str, tuple[float, float, float]]:
        """Return {strategy_id: (net_return, weight, gross_return)} for every pool strategy.

        Turnover fees are deducted from the gross shadow return so that
        the Sharpe ratio computed from ``_ShadowMetrics`` is net-of-fees.
        """
        shadow: dict[str, tuple[float, float, float]] = {}
        fee_rate = self.tournament_config.total_fee_rate
        state = self._live_state.get((symbol, timeframe))
        for sid in self.pool:
            fc = self.shadow_forecast(sid, observation)
            if fc is None:
                shadow[sid] = (0.0, 0.0, 0.0)
                continue
            # Shadow return = signal direction × bar return × conviction
            signal = fc.expected_excess_return
            # Preserve signal direction: BUY(+0.01)→+1, SELL(-0.01)→-1
            weight = max(-1.0, min(1.0, signal * 100)) if signal != 0 else 0.0
            # Apply position size cap from risk config
            cap = self.tournament_config.position_size_cap
            weight = max(-cap, min(cap, weight))
            gross_return = bar_return * weight
            # Deduct turnover fee: |Δweight| × per-side fee rate
            prev_weight = 0.0
            if state is not None and sid in state.shadow_metrics:
                prev_weight = state.shadow_metrics[sid]._prev_weight
            turnover_fee = abs(weight - prev_weight) * fee_rate
            net_return = gross_return - turnover_fee
            shadow[sid] = (net_return, weight, gross_return)
        return shadow

    def _update_shadow_metrics(
        self,
        symbol: str,
        timeframe: str,
        decision: RoutingDecision,
        shadow_returns: dict[str, tuple[float, float, float]],
        bar_return: float,
    ) -> None:
        key = (symbol, timeframe)
        state = self._live_state.setdefault(key, TournamentState())
        # Only update incumbent if a strategy was actually chosen
        if decision.chosen_strategy_id is not None:
            state.incumbent_strategy_id = decision.chosen_strategy_id

        for sid, (net_ret, weight, gross_ret) in shadow_returns.items():
            metrics = state.shadow_metrics.get(sid)
            if metrics is None:
                metrics = _ShadowMetrics()
                state.shadow_metrics[sid] = metrics
            # Track what-if performance for ALL strategies so challengers
            # accumulate metrics and can be promoted.
            # Fees are already deducted in _shadow_score_all; add() also
            # guards against double-counting via _prev_weight tracking.
            metrics.add(net_ret, weight, fee_rate=0.0, gross_ret=gross_ret)

    # ── Promotion / de-promotion ─────────────────────────────────────────

    def _init_incumbent_from_policy(self, symbol, timeframe, posterior, state):
        """Initialize incumbent_strategy_id from active policy if None."""
        if state.incumbent_strategy_id is not None:
            return
        regime = max(posterior.as_mapping, key=posterior.as_mapping.get)
        active_policy = self.policy_registry.get_active(symbol, timeframe, regime)
        if active_policy is not None and active_policy.incumbent:
            state.incumbent_strategy_id = active_policy.incumbent.strategy_id

    def _maybe_promote(
        self,
        symbol: str,
        timeframe: str,
        decision: RoutingDecision,
        state: TournamentState,
    ) -> None:
        """Check promotion / de-promotion conditions and update registry.

        **Statistical rigour (STR-0210):** A challenger must pass TWO gates
        before promotion:

        1. **Significance gate** — Welch's t-test on per-bar returns must
           reject H₀ (equal mean returns) at ``alpha / n_strategies``
           (Bonferroni correction for multiple testing across the pool).
        2. **PBO gate** — Sharpe ratio is deflated by a probabilistic-best-
           optimization penalty proportional to the pool size and sample
           size.  Promotion only proceeds if the deflated Sharpe exceeds
           the threshold.

        Circuit-breaker demotion (incumbent Sharpe < -0.50 or drawdown >
        30 %) skips the significance test — an under-water incumbent is
        replaced immediately as a safety measure.

        **Health gate (P1):** Before any promotion or replacement, the
        target exchange must be ``HEALTHY`` (not degraded or down).  If
        ``self.health_monitor`` is set and the exchange is unhealthy,
        promotion is blocked with an audit-log entry.
        """
        cfg = self.tournament_config
        incumbent = state.incumbent_strategy_id or decision.chosen_strategy_id
        inc_metrics = state.shadow_metrics.get(incumbent) if incumbent else None
        if inc_metrics is None or inc_metrics.n < cfg.min_shadow_bars_for_promote:
            return

        # ── Exchange health gate (P1) ────────────────────────────────────────
        if self.health_monitor is not None:
            exchange_name = decision.exchange_name or "binance"
            if not self.health_monitor.is_healthy(exchange_name):
                unhealthy = self.health_monitor.get_unhealthy()
                self._log_audit_event(
                    symbol, timeframe, "HEALTH_GATE_BLOCK",
                    incumbent=incumbent, challenger=None,
                    reason=f"Exchange {exchange_name} unhealthy (degraded/down)",
                    exchange_status=self.health_monitor.get_exchange_status(exchange_name),
                    unhealthy_exchanges=unhealthy,
                    statistical_check="bypassed (infrastructure safety)",
                )
                return  # Block promotion — infrastructure unsafe

        inc_sharpe = inc_metrics.sharpe()
        # ── Best challenger search ─────────────────────────────────────────
        inc_dd = inc_metrics.max_drawdown
        inc_n = inc_metrics.n

        best_sid: str | None = None
        best_sharpe: float = -999.0
        best_metrics: _ShadowMetrics | None = None

        for sid, metrics in state.shadow_metrics.items():
            if sid == incumbent or metrics.n < cfg.min_shadow_bars_for_promote:
                continue
            # Skip challengers that breach drawdown limit
            if metrics.max_drawdown < -cfg.max_drawdown_limit:
                continue
            sharpe = metrics.sharpe()
            if sharpe > best_sharpe:
                best_sharpe = sharpe
                best_sid = sid
                best_metrics = metrics

        # ── Circuit breaker: demote incumbent if Sharpe drops below threshold
        #     or drawdown exceeds limit — immediate replacement (no stats gate)
        # Skip circuit breaker during warmup period for statistical stability
        circuit_breaker_active = inc_n >= (cfg.min_shadow_bars_for_promote + cfg.circuit_breaker_warmup)
        circuit_breaker_triggered = circuit_breaker_active and (
            inc_sharpe < cfg.sharpe_circuit_breaker
            or inc_dd < -cfg.max_drawdown_limit
        )

        if circuit_breaker_triggered and best_sid is not None:
            self._log_audit_event(
                symbol, timeframe, "CIRCUIT_BREAKER_DEMOTE",
                incumbent=incumbent, challenger=best_sid,
                inc_sharpe=inc_sharpe, challenger_sharpe=best_sharpe,
                reason="Sharpe < circuit_breaker or drawdown > max_drawdown_limit",
                statistical_check="bypassed (safety)",
            )
            state.challenger_strategy_id = best_sid
            self._promote(symbol, timeframe, state, incumbent, best_sid)
            return

        if best_sid is None:
            return

        # ── Significance gate (STR-0210a): Welch's t-test ───────────────
        if best_metrics is not None:
            p_value = self._welch_t_test_pvalue(inc_metrics, best_metrics)
            n_pool = len(self.pool)
            alpha = cfg.significance_alpha
            if cfg.bonferroni_correction:
                alpha_adj = alpha / max(n_pool, 1)
            else:
                alpha_adj = alpha

            significant = p_value < alpha_adj

            if not significant:
                self._log_audit_event(
                    symbol, timeframe, "SIGNIFICANCE_GATE_BLOCK",
                    incumbent=incumbent, challenger=best_sid,
                    inc_sharpe=inc_sharpe, challenger_sharpe=best_sharpe,
                    p_value=p_value, alpha=alpha_adj, n_pool=n_pool,
                    n_inc=inc_n, n_challenger=best_metrics.n,
                    reason="Welch's t-test did not reject H₀ at adjusted alpha",
                )
                return  # Blocked — not statistically significant

            self._log_audit_event(
                symbol, timeframe, "SIGNIFICANCE_GATE_PASS",
                incumbent=incumbent, challenger=best_sid,
                p_value=p_value, alpha=alpha_adj, n_pool=n_pool,
                reason="Welch's t-test rejected H₀",
            )

        # ── PBO gate (STR-0210b): Deflated Sharpe ────────────────────────
        if best_metrics is None:
            return  # Fail-closed — no valid challenger metrics

        deflated_best, deflated_inc = self._deflated_sharpe_pair(
            best_sharpe, best_metrics.n, inc_sharpe, inc_n
        )

        # ── Net-of-fees Sharpe gate (P1) ──────────────────────────────
        # Challenger must maintain net Sharpe > threshold after fees
        best_net_sharpe = best_metrics.net_sharpe()
        if best_net_sharpe < cfg.promotion_net_sharpe_threshold:
            self._log_audit_event(
                symbol, timeframe, "NET_SHARPE_GATE_BLOCK",
                incumbent=incumbent, challenger=best_sid,
                challenger_gross_sharpe=best_sharpe,
                challenger_net_sharpe=best_net_sharpe,
                threshold=cfg.promotion_net_sharpe_threshold,
                reason="Net-of-fees Sharpe below promotion threshold",
            )
            return

        eff_score_margin = cfg.score_margin
        if deflated_best - deflated_inc < eff_score_margin:
            return  # Challenger does not meaningfully outperform after deflation

        # Promote challenger if it consistently outperforms
        if (
            deflated_best >= cfg.promotion_sharpe_threshold
            and deflated_best - deflated_inc >= eff_score_margin
        ):
            if deflated_inc >= cfg.demotion_sharpe_threshold:
                state.challenger_persistence += 1
                state.challenger_strategy_id = best_sid
                if state.challenger_persistence >= cfg.promotion_persistence:
                    self._promote(symbol, timeframe, state, incumbent, best_sid)
            else:
                state.challenger_strategy_id = best_sid
                self._promote(symbol, timeframe, state, incumbent, best_sid)
        else:
            state.challenger_persistence = 0

    def _welch_t_test_pvalue(
        self, inc: _ShadowMetrics, challenger: _ShadowMetrics
    ) -> float:
        """Two-sided Welch's t-test on per-bar returns.

        Tests H₀: μ_incumbent = μ_challenger.
        Returns p-value; smaller → more confident the means differ.
        """
        from scipy import stats

        inc_rets = list(inc.returns)
        ch_ret = list(challenger.returns)

        if len(inc_rets) < 2 or len(ch_ret) < 2:
            return 1.0  # Not enough data → fail-closed (p=1 → not significant)

        inc_mean = sum(inc_rets) / len(inc_rets)
        ch_mean = sum(ch_ret) / len(ch_ret)

        inc_var = sum((r - inc_mean) ** 2 for r in inc_rets) / (len(inc_rets) - 1)
        ch_var = sum((r - ch_mean) ** 2 for r in ch_ret) / (len(ch_ret) - 1)

        try:
            _, p_value = stats.ttest_ind_from_stats(
                mean1=ch_mean, std1=math.sqrt(ch_var), nobs1=len(ch_ret),
                mean2=inc_mean, std2=math.sqrt(inc_var), nobs2=len(inc_rets),
                equal_var=False,  # Welch's
            )
            if p_value is None or (isinstance(p_value, float) and math.isnan(p_value)):
                return 1.0
            return float(p_value)
        except Exception:
            return 1.0  # Fail-closed

    def _deflated_sharpe(
        self, raw_sharpe: float, n_obs: int, n_strategies: int
    ) -> float:
        """Apply Probabilistic Best Optimization (PBO) deflation.

        Uses the deflated Sharpe formula from Bailey et al. (2016):
        Sharpe_deflated = Sharpe_raw * (1 - sqrt(n_strategies / n_obs))

        When n_strategies << n_obs, deflation is small. When the ratio is
        high (more strategies than observations), deflation is severe.
        """
        if n_obs < 2 or n_strategies < 1:
            return raw_sharpe
        ratio = min(n_strategies / n_obs, 1.0)
        deflation_factor = 1.0 - math.sqrt(ratio)
        return raw_sharpe * deflation_factor

    def _deflated_sharpe_pair(
        self, challenger_sharpe: float, challenger_n: int,
        incumbent_sharpe: float, incumbent_n: int,
    ) -> tuple[float, float]:
        """Compute deflated Sharpe for both challenger and incumbent."""
        n_strategies = len(self.pool)
        def_challenger = self._deflated_sharpe(challenger_sharpe, challenger_n, n_strategies)
        def_incumbent = self._deflated_sharpe(incumbent_sharpe, incumbent_n, n_strategies)
        return def_challenger, def_incumbent

    def _log_audit_event(
        self,
        symbol: str,
        timeframe: str,
        event: str,
        **details: Any,
    ) -> None:
        """Write a structured audit entry for statistical gate decisions."""
        entry = {
            "event": f"TOURNAMENT_{event}",
            "symbol": symbol,
            "timeframe": timeframe,
            "timestamp": datetime.now(UTC).isoformat(),
            **details,
        }
        with self.audit_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, sort_keys=True) + "\n")

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
                max_age_days=self.config.max_policy_age_days,
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
            # Deprecate old policy so it's no longer active
            expired = policy.expire(datetime.now(UTC))
            self.policy_registry.add(expired)
            # Persist audit entry
            self._log_audit_event(
                symbol, timeframe, "PROMOTION",
                old_incumbent=old_incumbent,
                new_incumbent=new_incumbent,
                regime=regime,
                challenger_params=dict(policy.incumbent.params),
                code_sha=policy.incumbent.code_sha,
                promotion_stage=policy.promotion_stage,
            )

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
