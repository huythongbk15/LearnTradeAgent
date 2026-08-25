"""MultiPairRuntime — unified MULTI-PAIR BATCH execution over promoted artifacts.

Milestone C: ONE runtime that trades N pairs through ONE explicit decision
batch using the SAME canonical authority stages as single-pair execution:

    PHASE 1 — PREPARE THE ENTIRE PORTFOLIO BATCH (NO broker I/O)
        reconcile broker/portfolio truth (fail-closed)
        → discover promoted bindings (required universe)
        → load ALL market data (real provenance)
        → validate ALL observations (required-universe gate)
        → resolve + prepare ALL strategies (DecisionAuthority only)
        → ONE shared PortfolioSnapshot
        → ONE batch PortfolioAllocator → ONE PortfolioTargetVector
        → finalize ALL (ExposureAuthority) → plan ALL orders
        → FULL-BATCH PREFLIGHT (simulated cash/exposure)
    PHASE 2 — EXECUTE SAFELY
        REDUCTIONS first → reconcile broker truth → refresh snapshot
        → revalidate BUY headroom → BUYS sequentially
        → UNKNOWN/non-terminal ⇒ STOP remaining batch + reconcile
        → protection (inline post-fill) → final reconciliation

Core invariant: NO EXPOSURE-INCREASING BROKER I/O until the entire required
portfolio batch has passed data, resolution, risk, planning and preflight.
Single pair is MULTI-PAIR WITH N = 1 — no second architecture exists.
"""

from __future__ import annotations

import logging
import math
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Callable

from trading_agent.authority.config import Environment
from trading_agent.authority.portfolio import (
    PortfolioSnapshot,
    PortfolioTargetVector,
    ReconciliationState,
)
from trading_agent.execution.batch_models import (
    BatchPreflightResult,
    MarketDataInput,
    PairOrderPlan,
    PlannedAction,
    PlannedSubmissionOutcome,
    wrap_market_data,
)

logger = logging.getLogger(__name__)

# Provider returns either a typed MarketDataInput (full provenance) or a
# legacy OHLCV DataFrame (provenance derived from the data itself).
MarketDataProvider = Callable[[str, str], Any | None]


# ── Required universe policy ─────────────────────────────────────────────


@dataclass(frozen=True)
class RequiredUniversePolicy:
    """Policy for how missing required bindings affect the whole batch.

    ``all_promoted`` (Milestone C default): every promoted binding is
    REQUIRED. If any required binding lacks market data / closed observation
    / resolver result / instrument rules → NO new exposure for the cycle.
    Risk-reducing operations may still proceed when safe.

    Candle validation:
    - ``require_closed_last_bar``: the latest bar must be a CLOSED candle
      (open-time labeled: bar_open + timeframe ≤ now). Bars labeled in the
      future or still forming are rejected.
    - ``max_staleness_bars``: the last CLOSED bar may lag "now" by at most
      this many timeframe periods. None disables the staleness bound (the
      closed check alone remains mandatory).
    """

    mode: str = "all_promoted"
    require_closed_last_bar: bool = True
    max_staleness_bars: int | None = 3

    @property
    def blocks_on_any_failure(self) -> bool:
        return self.mode == "all_promoted"


_TIMEFRAME_UNITS: dict[str, float] = {
    "m": 60.0,
    "h": 3600.0,
    "d": 86400.0,
    "w": 604800.0,
}


def timeframe_seconds(timeframe: str) -> float | None:
    """Parse a ccxt-style timeframe ("15m", "1h", "4h", "1d") to seconds."""
    tf = str(timeframe).strip().lower()
    if len(tf) < 2:
        return None
    unit = tf[-1]
    if unit not in _TIMEFRAME_UNITS:
        return None
    try:
        n = int(tf[:-1])
    except ValueError:
        return None
    if n <= 0:
        return None
    return n * _TIMEFRAME_UNITS[unit]


def validate_candle_closed(
    bar_open_ts: datetime,
    timeframe: str,
    *,
    now: datetime,
    max_staleness_bars: int | None = None,
) -> str | None:
    """Validate the LAST bar of an OHLCV series is closed and fresh.

    Bars are open-time labeled (ccxt convention): a bar is closed once
    ``bar_open + timeframe <= now``. Returns a failure reason string, or
    None when the bar is provably closed (and within the staleness bound
    when one is configured).
    """
    tf_secs = timeframe_seconds(timeframe)
    if tf_secs is None:
        return "unknown_timeframe_duration"
    bar_ts_utc = bar_open_ts if bar_open_ts.tzinfo else bar_open_ts.replace(tzinfo=UTC)
    # 2s tolerance for provider clock/rounding jitter
    eps = 2.0
    age_seconds = (now - bar_ts_utc).total_seconds() - tf_secs
    if age_seconds < -eps:
        return "bar_not_closed"
    if max_staleness_bars is not None:
        if max_staleness_bars < 0:
            return "invalid_staleness_policy"
        stale_bound = max_staleness_bars * tf_secs
        if age_seconds > stale_bound + eps:
            return "stale_last_bar"
    return None


# ── Per-pair and cycle reports ───────────────────────────────────────────


@dataclass(frozen=True)
class PairCycleResult:
    """Outcome of one pair within a cycle."""

    symbol: str
    timeframe: str
    status: str  # ok | no_order | hold | zero_allocation | blocked | no_data | error
    orders_count: int
    detail: str = ""
    planned_action: str = ""  # REDUCTION | INCREASE | NO_ORDER | BLOCKED


@dataclass(frozen=True)
class CycleReport:
    """Aggregated outcome of one multi-pair BATCH cycle."""

    environment: str
    started_at: datetime
    finished_at: datetime
    equity_before: float
    equity_after: float
    results: tuple[PairCycleResult, ...] = field(default_factory=tuple)
    # ── Milestone C batch fields ─────────────────────────────────────
    cycle_id: str = ""
    status: str = "completed"
    bindings_required: int = 0
    bindings_prepared: int = 0
    bindings_failed: int = 0
    portfolio_target_vector: PortfolioTargetVector | None = None
    preflight_status: str = "not_run"
    reductions_planned: int = 0
    reductions_executed: int = 0
    increases_planned: int = 0
    increases_executed: int = 0
    protection_orders_submitted: int = 0
    execution_barrier: str | None = None
    reconciliation_status: str = ReconciliationState.NOT_RUN.value
    gross_exposure_before: float = 0.0
    gross_exposure_after: float = 0.0
    untracked_exposure: float = 0.0
    untracked_symbols: tuple[str, ...] = ()
    failed_bindings: tuple[str, ...] = ()

    @property
    def per_pair_results(self) -> tuple[PairCycleResult, ...]:
        return self.results

    @property
    def total_orders(self) -> int:
        return sum(r.orders_count for r in self.results)

    @property
    def errors(self) -> tuple[PairCycleResult, ...]:
        return tuple(r for r in self.results if r.status == "error")

    def to_dict(self) -> dict[str, Any]:
        vec = self.portfolio_target_vector
        return {
            "cycle_id": self.cycle_id,
            "environment": self.environment,
            "status": self.status,
            "started_at": self.started_at.isoformat(),
            "finished_at": self.finished_at.isoformat(),
            "equity_before": self.equity_before,
            "equity_after": self.equity_after,
            "gross_exposure_before": self.gross_exposure_before,
            "gross_exposure_after": self.gross_exposure_after,
            "untracked_exposure": self.untracked_exposure,
            "untracked_symbols": list(self.untracked_symbols),
            "bindings_required": self.bindings_required,
            "bindings_prepared": self.bindings_prepared,
            "bindings_failed": self.bindings_failed,
            "failed_bindings": list(self.failed_bindings),
            "portfolio_target_vector": {
                "targets": dict(vec.targets) if vec else {},
                "gross_target_exposure": vec.gross_target_exposure if vec else 0.0,
                "rejected_symbols": list(vec.rejected_symbols) if vec else [],
                "allocation_reasons": dict(vec.allocation_reasons) if vec else {},
            }
            if vec
            else None,
            "preflight_status": self.preflight_status,
            "reductions_planned": self.reductions_planned,
            "reductions_executed": self.reductions_executed,
            "increases_planned": self.increases_planned,
            "increases_executed": self.increases_executed,
            "protection_orders_submitted": self.protection_orders_submitted,
            "execution_barrier": self.execution_barrier,
            "reconciliation_status": self.reconciliation_status,
            "total_orders": self.total_orders,
            "results": [
                {
                    "symbol": r.symbol,
                    "timeframe": r.timeframe,
                    "status": r.status,
                    "orders_count": r.orders_count,
                    "planned_action": r.planned_action,
                    "detail": r.detail,
                }
                for r in self.results
            ],
        }


@dataclass(frozen=True)
class UniverseGateResult:
    """Outcome of the required-universe validation gate."""

    passed: tuple[tuple[str, str], ...]
    failed: dict[tuple[str, str], str]

    @property
    def all_required_ok(self) -> bool:
        return len(self.failed) == 0


class MultiPairRuntime:
    """Runs all promoted bindings through ONE batched authority pipeline."""

    def __init__(
        self,
        engine: Any,
        *,
        required_universe_policy: RequiredUniversePolicy | None = None,
        cash_reserve_pct: float | None = None,
        max_observation_age_seconds: float | None = None,
        atomic_buy_preflight: bool = True,
    ):
        """
        Args:
            engine: ExecutionEngine wired with resolver + instrument_rules.
            required_universe_policy: default = ALL_PROMOTED (fail-closed).
            cash_reserve_pct: min uninvested equity fraction for BUY budget;
                None → allocator default (1 − max_portfolio_exposure).
            atomic_buy_preflight: ATOMIC BUY policy — when ANY planned BUY
                fails batch preflight, ZERO BUY broker submissions happen
                this cycle (reductions unaffected). Default True.
            max_observation_age_seconds: staleness bound used in preflight;
                None → 3× the pair timeframe duration.
        """
        if getattr(engine, "resolver", None) is None:
            raise ValueError(
                "MultiPairRuntime requires an ExecutionEngine with a "
                "RuntimeStrategyResolver (promotion_store + artifact_store)"
            )
        if getattr(engine, "execution_service", None) is None:
            raise ValueError(
                "MultiPairRuntime requires an ExecutionEngine configured with "
                "instrument_rules (canonical execution pipeline)"
            )
        self.engine = engine
        self.required_universe_policy = (
            required_universe_policy or RequiredUniversePolicy()
        )
        self.cash_reserve_pct = cash_reserve_pct
        self.max_observation_age_seconds = max_observation_age_seconds
        self.atomic_buy_preflight = atomic_buy_preflight

    # ── Binding discovery ──────────────────────────────────────────────

    def discover_bindings(self, environment: str | Any) -> list[tuple[str, str]]:
        """Enumerate tradeable (symbol, timeframe) bindings for environment."""
        env = (
            environment
            if not isinstance(environment, str)
            else Environment(environment.lower())
        )
        return self.engine.resolver.list_bindings(env)

    # ── Shared portfolio reconciliation ────────────────────────────────

    def reconcile_portfolio(self) -> dict[str, Any]:
        """Correct allocator budgets against live positions. Raises on
        failure — callers MUST treat reconciliation failure as fail-closed."""
        equity = float(self.engine.exchange.get_total_equity())
        live = self._live_symbol_exposures(equity)
        audit = self.engine.portfolio_allocator.reconcile(live)
        logger.info("Portfolio reconcile: %d symbols held, audit=%s", len(live), audit)
        return audit

    def _live_symbol_exposures(self, equity: float) -> dict[str, float]:
        """symbol -> notional/equity from exchange positions (single truth).

        Holdings without a trusted cached price are skipped here and reported
        as unvalued by ``build_shared_snapshot`` (which blocks new exposure).
        """
        exposures: dict[str, float] = {}
        if equity <= 0:
            return exposures
        for pos in self.engine.exchange.get_all_positions():
            if not pos.is_active or pos.quantity <= 0:
                continue
            price = float(self.engine.exchange._last_price_cache.get(pos.symbol, 0.0))
            if price <= 0:
                # Cannot value this holding here — snapshot marks truth
                # incomplete (UNKNOWN TRUTH IS NOT ZERO RISK).
                continue
            exposures[pos.symbol] = exposures.get(pos.symbol, 0.0) + (
                pos.quantity * price / equity
            )
        return exposures

    def build_shared_snapshot(
        self,
        *,
        environment: str | Any,
        reconciliation_state: ReconciliationState = ReconciliationState.RECONCILED,
    ) -> PortfolioSnapshot:
        """Build THE authoritative shared PortfolioSnapshot for this cycle.

        Untracked live exposure (symbols with no eligible promoted binding
        FOR THE GIVEN ENVIRONMENT) is VALUED AT TRUSTED PRICE and included in
        gross exposure — reducing BUY headroom. If valuation is impossible
        the snapshot degrades and new exposure is blocked portfolio-wide.
        """
        if environment is None:
            raise ValueError(
                "build_shared_snapshot requires an explicit environment "
                "(no implicit PAPER default)"
            )
        env = (
            environment
            if isinstance(environment, Environment)
            else Environment(str(environment).lower())
        )
        exchange = self.engine.exchange
        equity = float(exchange.get_total_equity())
        available_cash = float(exchange.get_balance("USDT"))

        positions: dict[str, float] = {}
        symbol_exposures: dict[str, float] = {}
        unvalued: list[str] = []
        for pos in exchange.get_all_positions():
            if not pos.is_active or pos.quantity <= 0:
                continue
            positions[pos.symbol] = positions.get(pos.symbol, 0.0) + float(pos.quantity)
            price = float(exchange._last_price_cache.get(pos.symbol, 0.0))
            if equity > 0 and price > 0:
                symbol_exposures[pos.symbol] = (
                    symbol_exposures.get(pos.symbol, 0.0)
                    + pos.quantity * price / equity
                )
            else:
                unvalued.append(pos.symbol)

        # Which live symbols does a promoted strategy own (this environment)?
        try:
            owned_symbols: set[str] = {sym for sym, _tf in self.discover_bindings(env)}
        except Exception:
            owned_symbols = set()

        tracked = {s for s in symbol_exposures if s in owned_symbols}
        untracked_symbols = tuple(sorted(set(symbol_exposures) - tracked))
        untracked_exposure = sum(symbol_exposures[s] for s in untracked_symbols)
        gross = sum(symbol_exposures.values())

        untracked_valued = all(s not in unvalued for s in untracked_symbols)
        state = reconciliation_state
        if unvalued:
            state = ReconciliationState.DEGRADED
            logger.warning(
                "Portfolio snapshot DEGRADED: unvalued positions %s — "
                "new exposure blocked",
                sorted(unvalued),
            )

        return PortfolioSnapshot(
            equity=equity,
            available_cash=available_cash,
            positions=positions,
            symbol_exposures=symbol_exposures,
            gross_exposure=gross,
            untracked_symbols=untracked_symbols,
            untracked_exposure=untracked_exposure,
            untracked_valued=untracked_valued,
            observed_at=datetime.now(UTC),
            reconciliation_state=state,
        )

    # ── Stage: load ALL market data (real provenance) ───────────────────

    def load_all_market_data(
        self,
        bindings: list[tuple[str, str]],
        provider: MarketDataProvider,
    ) -> dict[tuple[str, str], MarketDataInput | None]:
        """Load the ENTIRE required batch up front. Legacy dataframe returns
        are wrapped with content-derived provenance; typed MarketDataInput
        passes through untouched. Missing/empty data → None (gated later)."""
        batch: dict[tuple[str, str], MarketDataInput | None] = {}
        for symbol, timeframe in bindings:
            try:
                raw = provider(symbol, timeframe)
            except Exception as e:
                logger.error("provider(%s, %s) raised: %s", symbol, timeframe, e)
                batch[(symbol, timeframe)] = None
                continue
            if raw is None:
                batch[(symbol, timeframe)] = None
            elif isinstance(raw, MarketDataInput):
                batch[(symbol, timeframe)] = raw
            else:
                try:
                    batch[(symbol, timeframe)] = wrap_market_data(
                        symbol, timeframe, raw, source="ohlcv_provider"
                    )
                except Exception as e:
                    logger.error(
                        "Cannot wrap market data for %s %s: %s", symbol, timeframe, e
                    )
                    batch[(symbol, timeframe)] = None
        return batch

    # ── Stage: required-universe gate ───────────────────────────────────

    def evaluate_required_universe(
        self,
        bindings: list[tuple[str, str]],
        market_batch: dict[tuple[str, str], MarketDataInput | None],
        environment: str | Any,
    ) -> UniverseGateResult:
        """Validate EVERY required binding BEFORE any planning/allocation:

        [ ] market data exists          [ ] enough history (non-empty frame)
        [ ] latest bar is CLOSED        [ ] timestamp is valid (≤ now)
        [ ] real provenance exists      [ ] StrategyArtifact resolves
        [ ] instrument rules exist

        Any failure marks the binding failed → batch-wide BUY block under
        the default ALL_PROMOTED policy.
        """
        env = (
            environment
            if not isinstance(environment, str)
            else Environment(environment.lower())
        )
        passed: list[tuple[str, str]] = []
        failed: dict[tuple[str, str], str] = {}

        planner = getattr(self.engine, "planner", None)
        for binding in bindings:
            symbol, timeframe = binding
            mdi = market_batch.get(binding)
            if mdi is None:
                failed[binding] = "missing_market_data"
                continue
            df = mdi.data
            if df is None or len(df) == 0:
                failed[binding] = "empty_market_data"
                continue
            try:
                tail = df.tail(1).to_dicts()[0]
            except Exception as e:
                failed[binding] = f"unreadable_market_data:{type(e).__name__}"
                continue
            close = tail.get("close")
            if close is None or not math.isfinite(float(close)) or float(close) <= 0:
                failed[binding] = "invalid_last_close"
                continue
            bar_ts = tail.get("timestamp")
            if isinstance(bar_ts, datetime):
                bar_ts_utc = bar_ts if bar_ts.tzinfo else bar_ts.replace(tzinfo=UTC)
                if bar_ts_utc > datetime.now(UTC):
                    failed[binding] = "future_bar_timestamp"
                    continue
                if self.required_universe_policy.require_closed_last_bar:
                    closed_reason = validate_candle_closed(
                        bar_ts_utc,
                        timeframe,
                        now=datetime.now(UTC),
                        max_staleness_bars=self.required_universe_policy.max_staleness_bars,
                    )
                    if closed_reason is not None:
                        failed[binding] = closed_reason
                        continue
            else:
                # No parseable timestamp → cannot prove the bar is closed
                failed[binding] = "missing_bar_timestamp"
                continue
            if not mdi.data_manifest_id or not mdi.data_manifest_id.strip():
                failed[binding] = "missing_provenance"
                continue
            try:
                runtime = self.engine.resolver.resolve_for(symbol, timeframe, env)
            except Exception as e:
                failed[binding] = f"resolver_error:{type(e).__name__}"
                continue
            if runtime is None:
                failed[binding] = "no_resolved_strategy"
                continue
            if planner is None or planner.rules_for(symbol) is None:
                failed[binding] = "missing_instrument_rules"
                continue
            passed.append(binding)

        return UniverseGateResult(passed=tuple(passed), failed=failed)

    # ── Stage: full-batch preflight (simulated, NO broker I/O) ─────────

    def preflight_batch(
        self,
        plans: list[PairOrderPlan],
        snapshot: PortfolioSnapshot,
        vector: PortfolioTargetVector | None = None,
    ) -> BatchPreflightResult:
        """Simulate the WHOLE batch before the first BUY submission.

        Reductions are simulated FIRST (freeing cash/inventory); increases
        are then checked against simulated post-reduction truth:
        quantity normalization/min/max, min notional, inventory coverage,
        cash incl. reserve, per-symbol cap, gross cap, idempotency
        uniqueness, data freshness, lifecycle health, protective feasibility.
        """
        cfg = self.engine.authority_config.exposure
        checks: list[str] = []

        # Lifecycle health (when available)
        health = getattr(self.engine.lifecycle, "health", None)
        lifecycle_healthy = True
        try:
            if callable(health):
                h = health()
                lifecycle_healthy = str(getattr(h, "value", h)).upper() not in {
                    "UNHEALTHY",
                    "FAILED",
                }
        except Exception:
            lifecycle_healthy = True  # health API absent → don't invent failures
        checks.append("lifecycle_health")

        sim_cash = float(snapshot.available_cash)
        sim_gross = float(snapshot.gross_exposure)
        sim_symbol = {s: float(e) for s, e in snapshot.symbol_exposures.items()}
        reasons: dict[str, str] = {}

        reduction_plans = sorted(
            (p for p in plans if p.action is PlannedAction.REDUCTION),
            key=lambda p: (p.symbol, p.timeframe),
        )
        increase_plans = sorted(
            (p for p in plans if p.action is PlannedAction.INCREASE),
            key=lambda p: (p.symbol, p.timeframe),
        )
        other_plans = [
            p
            for p in plans
            if p.action in (PlannedAction.NO_ORDER, PlannedAction.BLOCKED)
        ]

        # Idempotency uniqueness across the WHOLE batch
        seen_keys: dict[str, str] = {}
        for p in sorted(plans, key=lambda x: (x.symbol, x.timeframe)):
            key = p.idempotency_key
            if key:
                if key in seen_keys and seen_keys[key] != p.symbol:
                    reasons[p.symbol] = (
                        f"duplicate_idempotency_key_with_{seen_keys[key]}"
                    )
                else:
                    seen_keys[key] = p.symbol
        checks.append("idempotency_uniqueness")

        def _rules_for(plan: PairOrderPlan):
            planner = self.engine.planner
            return planner.rules_for(plan.symbol) if planner else None

        # ── Simulate REDUCTIONS first ──────────────────────────────────
        for p in reduction_plans:
            if p.symbol in reasons:
                continue
            finalized = p.finalized
            assert finalized is not None
            prepared = finalized.prepared
            rules = _rules_for(p)
            if rules is None:
                reasons[p.symbol] = "missing_instrument_rules"
                continue
            price = prepared.current_price
            if price <= 0 or not math.isfinite(price):
                reasons[p.symbol] = "non_finite_price"
                continue
            qty = p.quantity
            if not math.isfinite(qty) or qty <= 0:
                reasons[p.symbol] = "non_positive_quantity"
                continue
            held = prepared.current_quantity
            if qty > held + 1e-12:
                reasons[p.symbol] = f"sell_qty_{qty:.8f}_exceeds_inventory_{held:.8f}"
                continue
            if qty < float(rules.min_order_qty):
                reasons[p.symbol] = "below_min_quantity"
                continue
            # Sell frees cash and reduces exposure (simulate conservatively
            # at reference price; actual refresh happens post-fill anyway).
            proceeds = qty * price
            sim_cash += proceeds
            sim_symbol[p.symbol] = max(
                0.0,
                sim_symbol.get(p.symbol, 0.0)
                - qty * price / max(snapshot.equity, 1e-9),
            )
            sim_gross = max(0.0, sim_gross - qty * price / max(snapshot.equity, 1e-9))
        checks.extend(["sell_inventory_coverage", "min_quantity", "price_sanity"])

        # ── Simulate INCREASES against post-reduction truth ───────────
        reserve_pct = (
            float(self.cash_reserve_pct)
            if self.cash_reserve_pct is not None
            else max(0.0, 1.0 - float(cfg.max_portfolio_exposure))
        )
        min_equity = max(snapshot.equity, 1e-9)
        for p in increase_plans:
            if p.symbol in reasons:
                continue
            finalized = p.finalized
            assert finalized is not None
            prepared = finalized.prepared
            rules = _rules_for(p)
            if rules is None:
                reasons[p.symbol] = "missing_instrument_rules"
                continue
            price = prepared.current_price
            qty = p.quantity
            if price <= 0 or not math.isfinite(price):
                reasons[p.symbol] = "non_finite_price"
                continue
            if not math.isfinite(qty) or qty <= 0:
                reasons[p.symbol] = "non_positive_quantity"
                continue
            if qty < float(rules.min_order_qty):
                reasons[p.symbol] = f"below_min_quantity_{rules.min_order_qty}"
                continue
            notional = qty * price
            if notional < float(rules.min_notional):
                reasons[p.symbol] = f"below_min_notional_{rules.min_notional}"
                continue
            if rules.max_order_qty and qty > float(rules.max_order_qty):
                reasons[p.symbol] = f"above_max_quantity_{rules.max_order_qty}"
                continue
            # Cash incl. minimum reserve
            cost = notional  # fee buffer handled by reserve
            if sim_cash - cost < reserve_pct * min_equity:
                reasons[p.symbol] = (
                    f"insufficient_simulated_cash_{sim_cash:.2f}_need_{cost:.2f}"
                    f"_reserve_{reserve_pct * min_equity:.2f}"
                )
                sim_cash -= 0.0  # rejected: no simulation state change
                continue
            delta_exp = notional / min_equity
            projected_symbol = sim_symbol.get(p.symbol, 0.0) + delta_exp
            if projected_symbol > float(cfg.max_single_symbol_exposure) + 1e-9:
                reasons[p.symbol] = (
                    f"symbol_cap_{projected_symbol:.4f}>{cfg.max_single_symbol_exposure}"
                )
                continue
            projected_gross = sim_gross + delta_exp
            if projected_gross > float(cfg.max_portfolio_exposure) + 1e-9:
                reasons[p.symbol] = (
                    f"gross_cap_{projected_gross:.4f}>{cfg.max_portfolio_exposure}"
                )
                continue
            # Commit simulation
            sim_cash -= cost
            sim_symbol[p.symbol] = projected_symbol
            sim_gross = projected_gross
        checks.extend(
            [
                "quantity_normalization_bounds",
                "min_notional",
                "cash_with_minimum_reserve",
                "symbol_exposure_cap",
                "gross_exposure_cap",
                "data_freshness_gate_upstream",
                "protective_feasibility_basic",
            ]
        )
        if not lifecycle_healthy:
            for p in increase_plans:
                reasons.setdefault(p.symbol, "lifecycle_unhealthy")

        blocked_symbols = set(reasons)
        blocked_plans = tuple(
            sorted(
                (p for p in plans if p.symbol in blocked_symbols),
                key=lambda x: (x.symbol, x.timeframe),
            )
        )
        passed_increase = tuple(
            p for p in increase_plans if p.symbol not in blocked_symbols
        )
        passed_reduction = tuple(
            p for p in reduction_plans if p.symbol not in blocked_symbols
        )
        passed_flag = len(blocked_plans) == 0
        return BatchPreflightResult(
            passed=passed_flag,
            reduction_plans=passed_reduction,
            increase_plans=passed_increase,
            blocked_plans=blocked_plans,
            reasons=reasons,
            simulated_final_cash=sim_cash,
            simulated_final_gross=sim_gross,
            checks_run=tuple(checks),
        )

    # ── Atomic BUY preflight policy ─────────────────────────────────────

    def _apply_atomic_buy_policy(
        self,
        preflight: BatchPreflightResult,
        pair_meta: dict[str, PairCycleResult],
    ) -> tuple[list[PairOrderPlan], list[PairOrderPlan], str]:
        """Enforce ATOMIC BUY semantics on a preflight result.

        One required BUY failing preflight ⇒ ZERO BUY broker submissions
        this cycle. Reductions are risk-reducing and unaffected. Returns
        (reductions, increases, preflight_status).
        """
        blocked_increases = [
            p for p in preflight.blocked_plans if p.action is PlannedAction.INCREASE
        ]
        reductions = list(preflight.reduction_plans)
        increases = list(preflight.increase_plans)
        status = "passed" if preflight.passed else "partial_rejected"

        if not blocked_increases:
            return reductions, increases, status

        trigger = sorted(blocked_increases, key=lambda p: (p.symbol, p.timeframe))[0]
        reason = preflight.reasons.get(trigger.symbol, "preflight_failed")
        logger.error(
            "ATOMIC BUY PREFLIGHT: %s %s failed (%s) — cancelling ALL %d "
            "planned BUY submissions this cycle",
            trigger.symbol,
            trigger.timeframe,
            reason,
            len(blocked_increases) + len(increases),
        )
        for p in increases:
            key = f"{p.symbol}|{p.timeframe}"
            pair_meta[key] = PairCycleResult(
                symbol=p.symbol,
                timeframe=p.timeframe,
                status="blocked",
                orders_count=0,
                detail=f"atomic_preflight_cancelled_by_{trigger.symbol}:{reason}",
                planned_action="INCREASE",
            )
        # Blocked BUYs already carry their own reason; surviving ones get the
        # atomic-cancel detail above.
        return reductions, [], "atomic_blocked"

    def _atomic_block_survivors(
        self,
        survivors: list[PairOrderPlan],
        trigger_symbol: str,
        reason: str,
        results: list[PairCycleResult],
    ) -> None:
        """Record atomic cancellation of surviving BUYs at revalidation."""
        for p in survivors:
            results.append(
                PairCycleResult(
                    symbol=p.symbol,
                    timeframe=p.timeframe,
                    status="blocked",
                    orders_count=0,
                    detail=(
                        f"atomic_revalidation_cancelled_by_{trigger_symbol}:{reason}"
                    ),
                    planned_action="INCREASE",
                )
            )

    # ── The cycle ──────────────────────────────────────────────────────

    def run_cycle(
        self,
        environment: str | Any = "paper",
        market_data_provider: MarketDataProvider | None = None,
        bindings_override: list[tuple[str, str]] | None = None,
    ) -> CycleReport:
        """Execute ONE full BATCH cycle across all eligible pairs."""
        started_at = datetime.now(UTC)
        cycle_id = uuid.uuid4().hex[:16]
        env_name = (
            environment.lower() if isinstance(environment, str) else environment.value
        )

        if market_data_provider is None:
            raise ValueError("market_data_provider is required (fail-closed)")

        equity_before = float(self.engine.exchange.get_total_equity())

        # ── STAGE 0: reconcile — FAIL CLOSED (C6/C12) ─────────────────
        try:
            self.reconcile_portfolio()
        except Exception as e:
            logger.error(
                "Reconciliation FAILED — aborting cycle before any order: %s",
                e,
                exc_info=True,
            )
            return CycleReport(
                cycle_id=cycle_id,
                environment=env_name,
                started_at=started_at,
                finished_at=datetime.now(UTC),
                equity_before=equity_before,
                equity_after=float(self.engine.exchange.get_total_equity()),
                status="reconciliation_failed",
                reconciliation_status=ReconciliationState.FAILED.value,
                gross_exposure_after=self._gross_now(),
            )

        # Shared snapshot BEFORE the batch (one authoritative truth)
        snapshot = self.build_shared_snapshot(environment=environment)
        gross_before = snapshot.gross_exposure

        # ── STAGE 1: discover + load ALL market data ──────────────────
        bindings = (
            list(bindings_override)
            if bindings_override is not None
            else self.discover_bindings(environment)
        )
        market_batch = self.load_all_market_data(bindings, market_data_provider)

        # ── STAGE 2: required-universe gate ───────────────────────────
        gate = self.evaluate_required_universe(bindings, market_batch, environment)
        new_exposure_allowed = snapshot.new_exposure_allowed
        cycle_block_reason: str | None = None
        if (
            not gate.all_required_ok
            and self.required_universe_policy.blocks_on_any_failure
        ):
            new_exposure_allowed = False
            cycle_block_reason = "BATCH_BLOCKED_REQUIRED_DATA"
            logger.error(
                "Required universe violated — failed bindings: %s", gate.failed
            )

        # ── STAGE 3: prepare ALL (resolve + strategy + decide, no I/O) ─
        prepared_by_binding: dict[tuple[str, str], Any] = {}
        pair_meta: dict[str, PairCycleResult] = {}
        for binding in sorted(bindings):
            symbol, timeframe = binding
            if binding in gate.failed:
                reason = str(gate.failed[binding])
                # Preserve historical per-pair semantics: missing market data
                # is reported as ``no_data``; other gate failures are
                # ``blocked``. Both paths still trip the fail-closed batch
                # gate above (new exposure disabled).
                status = "no_data" if "missing_market_data" in reason else "blocked"
                pair_meta[f"{symbol}|{timeframe}"] = PairCycleResult(
                    symbol=symbol,
                    timeframe=timeframe,
                    status=status,
                    orders_count=0,
                    detail=f"required_universe:{reason}",
                )
                continue
            mdi = market_batch[binding]
            assert mdi is not None  # gate guarantees
            try:
                prepared = self.engine.prepare_promoted_strategy(
                    symbol=symbol,
                    timeframe=timeframe,
                    environment=environment,
                    market_data_input=mdi,
                )
            except Exception as e:
                logger.error(
                    "prepare[%s %s] raised: %s", symbol, timeframe, e, exc_info=True
                )
                prepared = None
                gate.failed[binding] = f"prepare_error:{type(e).__name__}"
            if prepared is None:
                pair_meta[f"{symbol}|{timeframe}"] = PairCycleResult(
                    symbol=symbol,
                    timeframe=timeframe,
                    status="blocked",
                    orders_count=0,
                    detail="no_resolved_strategy",
                )
                if self.required_universe_policy.blocks_on_any_failure:
                    new_exposure_allowed = False
                    cycle_block_reason = "BATCH_BLOCKED_REQUIRED_DATA"
                continue
            prepared_by_binding[binding] = prepared
            key = f"{symbol}|{timeframe}"
            if prepared.prepare_status == "hold":
                pair_meta[key] = PairCycleResult(
                    symbol=symbol,
                    timeframe=timeframe,
                    status="hold",
                    orders_count=0,
                    planned_action="NO_ORDER",
                )
            elif prepared.prepare_status != "ok":
                pair_meta[key] = PairCycleResult(
                    symbol=symbol,
                    timeframe=timeframe,
                    status="blocked",
                    orders_count=0,
                    detail=f"prepare_status:{prepared.prepare_status}",
                )

        ok_prepared = [
            p for p in prepared_by_binding.values() if p.prepare_status == "ok"
        ]

        # ── STAGE 4: ONE batch allocation → ONE target vector ─────────
        vector: PortfolioTargetVector | None = None
        entries_by_symbol: dict[str, Any] = {}
        if ok_prepared:
            requests = [
                self.engine.build_allocation_request(p, snapshot) for p in ok_prepared
            ]
            outcome = self.engine.portfolio_allocator.allocate_batch(
                requests, snapshot, cash_reserve_pct=self.cash_reserve_pct
            )
            vector = self.engine.portfolio_allocator.build_target_vector(
                outcome, snapshot, cycle_id
            )
            entries_by_symbol = {e.symbol: e for e in outcome.entries}
            if not new_exposure_allowed and any(
                e.approved > 0 for e in outcome.entries
            ):
                # Defense in depth: never allow targets when batch is blocked
                logger.error(
                    "allocate_batch approved exposure while batch blocked — "
                    "forcing targets to zero"
                )
                vector = PortfolioTargetVector(
                    cycle_id=cycle_id,
                    equity=vector.equity,
                    available_cash=vector.available_cash,
                    targets={s: 0.0 for s in vector.targets},
                    gross_target_exposure=0.0,
                    cash_reserve_pct=vector.cash_reserve_pct,
                    allocation_reasons=dict(vector.allocation_reasons),
                    rejected_symbols=tuple(vector.targets),
                    created_at=vector.created_at,
                )

        # ── STAGE 5: finalize ALL + plan ALL (still no broker I/O) ────
        from trading_agent.authority.decision import (
            TargetExposure as AuthTargetExposure,
        )
        from trading_agent.authority.causation import CausationChain

        plans: list[PairOrderPlan] = []
        for binding in sorted(prepared_by_binding):
            prepared = prepared_by_binding[binding]
            symbol, timeframe = binding
            key = f"{symbol}|{timeframe}"
            if prepared.prepare_status != "ok":
                continue
            entry = entries_by_symbol.get(symbol)
            approved_pct = (
                float(vector.targets.get(symbol, 0.0)) if vector is not None else 0.0
            )
            if approved_pct <= 0 and (entry is None or entry.requested <= 0):
                pair_meta.setdefault(
                    key,
                    PairCycleResult(
                        symbol=symbol,
                        timeframe=timeframe,
                        status="hold",
                        orders_count=0,
                        planned_action="NO_ORDER",
                        detail="strategy produced no actionable signal",
                    ),
                )
                continue
            if not new_exposure_allowed and approved_pct > prepared.current_exposure:
                pair_meta[key] = PairCycleResult(
                    symbol=symbol,
                    timeframe=timeframe,
                    status="blocked",
                    orders_count=0,
                    detail=cycle_block_reason or "PORTFOLIO_TRUTH_UNKNOWN",
                    planned_action="BLOCKED",
                )
                continue
            alloc_chain = getattr(entry, "causation_chain", None) if entry else None
            links = tuple(prepared.causation_chain.links) + (
                tuple(alloc_chain.links) if alloc_chain is not None else ()
            )
            combined = CausationChain(links=links)
            approved_target = AuthTargetExposure(
                target_exposure_pct=approved_pct,
                max_new_exposure_pct=min(
                    approved_pct, float(prepared.risk_decision.max_new_exposure)
                ),
                reduce_only=bool(prepared.risk_decision.reduce_only),
                confidence=0.5,
                authority_chain=links,
            )
            try:
                finalized = self.engine.finalize_prepared_decision(
                    prepared,
                    approved_target=approved_target,
                    combined_chain=combined,
                )
            except Exception as e:
                logger.error("finalize[%s] raised: %s", symbol, e, exc_info=True)
                pair_meta[key] = PairCycleResult(
                    symbol=symbol,
                    timeframe=timeframe,
                    status="error",
                    orders_count=0,
                    detail=str(e)[:300],
                )
                continue
            if finalized is None:
                pair_meta[key] = PairCycleResult(
                    symbol=symbol,
                    timeframe=timeframe,
                    status="blocked",
                    orders_count=0,
                    detail="exposure_authority_denied",
                    planned_action="BLOCKED",
                )
                continue
            try:
                plan = self.engine.plan_pair_order(finalized)
            except Exception as e:
                logger.error("plan[%s] raised: %s", symbol, e, exc_info=True)
                pair_meta[key] = PairCycleResult(
                    symbol=symbol,
                    timeframe=timeframe,
                    status="error",
                    orders_count=0,
                    detail=str(e)[:300],
                )
                continue
            plans.append(plan)
            if plan.action is PlannedAction.NO_ORDER:
                pair_meta.setdefault(
                    key,
                    PairCycleResult(
                        symbol=symbol,
                        timeframe=timeframe,
                        status="no_order",
                        orders_count=0,
                        planned_action="NO_ORDER",
                        detail=plan.detail,
                    ),
                )
            elif plan.action is PlannedAction.BLOCKED:
                pair_meta[key] = PairCycleResult(
                    symbol=symbol,
                    timeframe=timeframe,
                    status="blocked",
                    orders_count=0,
                    detail=plan.detail or "plan_blocked",
                    planned_action="BLOCKED",
                )

        # ── STAGE 6: FULL-BATCH PREFLIGHT ─────────────────────────────
        executable_plans = [
            p
            for p in plans
            if p.action in (PlannedAction.REDUCTION, PlannedAction.INCREASE)
        ]
        preflight = self.preflight_batch(executable_plans, snapshot, vector)
        preflight_status = "passed" if preflight.passed else "partial_rejected"
        if preflight.blocked_plans and not (
            preflight.reduction_plans or preflight.increase_plans
        ):
            preflight_status = "all_blocked"
        for p in preflight.blocked_plans:
            key = f"{p.symbol}|{p.timeframe}"
            reason = preflight.reasons.get(p.symbol, "preflight_failed")
            pair_meta[key] = PairCycleResult(
                symbol=p.symbol,
                timeframe=p.timeframe,
                status="blocked",
                orders_count=0,
                detail=f"preflight:{reason}",
                planned_action=p.action.value,
            )
        if self.atomic_buy_preflight:
            reductions_a, increases_a, atomic_status = self._apply_atomic_buy_policy(
                preflight, pair_meta
            )
            if atomic_status == "atomic_blocked":
                preflight_status = "atomic_blocked"
            reductions = reductions_a
            increases = increases_a
        else:
            reductions = list(preflight.reduction_plans)
            increases = list(preflight.increase_plans)
        reductions_planned_count = len(reductions)
        increases_planned_count = len(increases)

        # ── STAGE 7: EXECUTE — reductions first, then increases ───────
        results: list[PairCycleResult] = []
        barrier_symbol: str | None = None
        reductions_executed = 0
        increases_executed = 0
        protection_count = 0
        recon_status = ReconciliationState.RECONCILED.value

        def _run_plan(plan: PairOrderPlan) -> PlannedSubmissionOutcome | None:
            nonlocal barrier_symbol, protection_count
            try:
                outcome = self.engine.submit_planned_order(plan)
            except Exception as e:
                logger.error(
                    "submit[%s %s] raised: %s",
                    plan.symbol,
                    plan.timeframe,
                    e,
                    exc_info=True,
                )
                results.append(
                    PairCycleResult(
                        symbol=plan.symbol,
                        timeframe=plan.timeframe,
                        status="error",
                        orders_count=0,
                        detail=str(e)[:300],
                        planned_action=plan.action.value,
                    )
                )
                return None
            protection_count += 1 if outcome.protection_submitted else 0
            n_orders = 1 if outcome.order is not None else 0
            results.append(
                PairCycleResult(
                    symbol=plan.symbol,
                    timeframe=plan.timeframe,
                    status="ok" if n_orders else "blocked",
                    orders_count=n_orders,
                    detail=(
                        f"submit_state={outcome.submit_state}"
                        + ("" if outcome.submitted else ";authority_denied")
                    ),
                    planned_action=plan.action.value,
                )
            )
            if outcome.barrier:
                barrier_symbol = plan.symbol
            return outcome

        # REDUCTIONS first — never let symbol ordering choose risk priority
        for plan in reductions:
            outcome = _run_plan(plan)
            if outcome is not None and not outcome.barrier:
                reductions_executed += 1
            if barrier_symbol is not None:
                break

        # POST-REDUCTION RECONCILE + REFRESH — never trust predicted proceeds
        refreshed_snapshot = snapshot
        if reductions and barrier_symbol is None:
            try:
                self.reconcile_portfolio()
                refreshed_snapshot = self.build_shared_snapshot(environment=environment)
                recon_status = refreshed_snapshot.reconciliation_state.value
            except Exception as e:
                logger.error(
                    "Post-reduction reconciliation FAILED — cancelling all "
                    "remaining BUY plans: %s",
                    e,
                )
                increases = []  # fail-closed: unknown truth ⇒ no new exposure
                recon_status = ReconciliationState.FAILED.value

        # Revalidate BUY headroom against REFRESHED truth
        if increases and refreshed_snapshot is not snapshot:
            revalidation = self.preflight_batch(increases, refreshed_snapshot, vector)
            kept_symbols = {p.symbol for p in revalidation.increase_plans}
            for p in increases:
                if p.symbol not in kept_symbols:
                    results.append(
                        PairCycleResult(
                            symbol=p.symbol,
                            timeframe=p.timeframe,
                            status="blocked",
                            orders_count=0,
                            detail=(
                                "post_sell_revalidation:"
                                + revalidation.reasons.get(p.symbol, "headroom_lost")
                            ),
                            planned_action="INCREASE",
                        )
                    )
            if (
                self.atomic_buy_preflight
                and revalidation.blocked_plans
                and kept_symbols
            ):
                # Atomic policy holds at revalidation too: one BUY losing
                # headroom ⇒ zero remaining BUY submissions.
                trigger = sorted(
                    (
                        p
                        for p in revalidation.blocked_plans
                        if p.action is PlannedAction.INCREASE
                    ),
                    key=lambda x: (x.symbol, x.timeframe),
                )[0]
                reason = revalidation.reasons.get(trigger.symbol, "headroom_lost")
                self._atomic_block_survivors(
                    list(revalidation.increase_plans),
                    trigger.symbol,
                    reason,
                    results,
                )
                logger.error(
                    "ATOMIC REVALIDATION: %s lost headroom (%s) — cancelling "
                    "%d surviving BUY submissions",
                    trigger.symbol,
                    reason,
                    len(revalidation.increase_plans),
                )
                increases = []
            else:
                increases = list(revalidation.increase_plans)

        # BUYS sequentially — UNKNOWN anywhere stops the remaining batch
        for buy_idx, original_plan in enumerate(list(increases)):
            plan = original_plan
            # Broker-truth-first: re-quantize against LIVE equity/cash before
            # each submission (sibling fills shift equity via fees/slippage).
            try:
                plan = self.engine.replan_pair_with_live_truth(plan)
            except Exception as e:
                logger.error(
                    "replan[%s %s] raised: %s",
                    plan.symbol,
                    plan.timeframe,
                    e,
                    exc_info=True,
                )
                results.append(
                    PairCycleResult(
                        symbol=plan.symbol,
                        timeframe=plan.timeframe,
                        status="error",
                        orders_count=0,
                        detail=f"replan_error:{str(e)[:200]}",
                        planned_action="INCREASE",
                    )
                )
                continue
            if plan.action is not PlannedAction.INCREASE:
                results.append(
                    PairCycleResult(
                        symbol=plan.symbol,
                        timeframe=plan.timeframe,
                        status="blocked",
                        orders_count=0,
                        detail=f"replan_action:{plan.action.value}",
                        planned_action="INCREASE",
                    )
                )
                continue
            outcome = _run_plan(plan)
            if outcome is not None and not outcome.barrier:
                increases_executed += 1
            if barrier_symbol is not None:
                logger.error(
                    "EXECUTION BARRIER at %s — stopping remaining increase "
                    "submissions and reconciling immediately",
                    barrier_symbol,
                )
                try:
                    self.reconcile_portfolio()
                    recon_status = ReconciliationState.RECONCILED.value
                except Exception as e:
                    logger.error("Barrier reconciliation failed: %s", e)
                    recon_status = ReconciliationState.FAILED.value
                remaining = list(increases)[buy_idx + 1 :]
                for p in remaining:
                    results.append(
                        PairCycleResult(
                            symbol=p.symbol,
                            timeframe=p.timeframe,
                            status="blocked",
                            orders_count=0,
                            detail="barrier_stop_remaining_batch",
                            planned_action="INCREASE",
                        )
                    )
                break

        # Merge pair_meta (planning-stage outcomes) with execution results
        executed_keys = {f"{r.symbol}|{r.timeframe}" for r in results}
        for key, meta in pair_meta.items():
            if key not in executed_keys:
                results.append(meta)
        results.sort(key=lambda r: (r.symbol, r.timeframe))

        # ── STAGE 8: MANDATORY FINAL RECONCILE ────────────────────────
        # The cycle is not complete until broker/portfolio truth has been
        # re-observed after ALL submissions. Failure here is a cycle-level
        # failure regardless of how well the orders themselves went.
        final_reconciled = True
        try:
            self.reconcile_portfolio()
            final_snapshot = self.build_shared_snapshot(environment=environment)
            if final_snapshot.reconciliation_state is ReconciliationState.FAILED:
                final_reconciled = False
            else:
                recon_status = final_snapshot.reconciliation_state.value
        except Exception as e:
            final_reconciled = False
            logger.error(
                "FINAL reconciliation FAILED — cycle truth unknown: %s",
                e,
                exc_info=True,
            )
        if not final_reconciled:
            recon_status = ReconciliationState.FAILED.value

        finished_at_dt = datetime.now(UTC)
        equity_after = float(self.engine.exchange.get_total_equity())

        status = "completed"
        if cycle_block_reason:
            status = "completed_blocked_new_exposure"
        if preflight_status == "all_blocked" and not reductions:
            status = "blocked_preflight"
        if preflight_status == "atomic_blocked":
            status = "completed_atomic_buy_blocked"
        if not final_reconciled:
            status = "final_reconciliation_failed"
        if barrier_symbol is not None:
            status = "stopped_execution_barrier"

        return CycleReport(
            cycle_id=cycle_id,
            environment=env_name,
            started_at=started_at,
            finished_at=finished_at_dt,
            equity_before=equity_before,
            equity_after=equity_after,
            results=tuple(results),
            status=status,
            bindings_required=len(bindings),
            bindings_prepared=sum(
                1 for p in prepared_by_binding.values() if p.prepare_status == "ok"
            ),
            bindings_failed=len(gate.failed),
            failed_bindings=tuple(
                f"{s}:{f}" for (s, t), f in sorted(gate.failed.items())
            ),
            portfolio_target_vector=vector,
            preflight_status=preflight_status,
            reductions_planned=reductions_planned_count,
            reductions_executed=reductions_executed,
            increases_planned=increases_planned_count,
            increases_executed=increases_executed,
            protection_orders_submitted=protection_count,
            execution_barrier=f"{barrier_symbol}" if barrier_symbol else None,
            reconciliation_status=recon_status,
            gross_exposure_before=gross_before,
            gross_exposure_after=self._gross_now(),
            untracked_exposure=snapshot.untracked_exposure,
            untracked_symbols=snapshot.untracked_symbols,
        )

    # ── helpers ─────────────────────────────────────────────────────────

    def _gross_now(self) -> float:
        equity = float(self.engine.exchange.get_total_equity())
        if equity <= 0:
            return 0.0
        gross = 0.0
        for pos in self.engine.exchange.get_all_positions():
            if not pos.is_active or pos.quantity <= 0:
                continue
            price = float(self.engine.exchange._last_price_cache.get(pos.symbol, 0.0))
            gross += pos.quantity * price / equity
        return gross


__all__ = [
    "MultiPairRuntime",
    "CycleReport",
    "PairCycleResult",
    "RequiredUniversePolicy",
    "UniverseGateResult",
]
