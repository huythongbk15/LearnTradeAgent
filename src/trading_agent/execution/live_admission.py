"""Promoted-strategy preparation for live brokers, with no paper/broker order IO.

The resolver, decision authority, shared portfolio allocator, exposure authority
and real OrderPlanner are composed here. Submission remains exclusively in the
runner's CanonicalExecutionService. Missing authority/provenance never gets a
fabricated risk decision or an ALLOW fallback.
"""

from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
import hashlib

from trading_agent.authority.config import AuthorityConfig, Environment
from trading_agent.authority.decision import (
    DecisionAuthority,
    DecisionInput,
    TargetExposure,
)
from trading_agent.authority.exposure import ExposureAuthority, ExposureValidationInput
from trading_agent.authority.portfolio import (
    AllocationRequest,
    PortfolioAllocator,
    PortfolioSnapshot,
)
from trading_agent.authority.resolver import RuntimeStrategyResolver
from trading_agent.execution.batch_models import MarketDataInput
from trading_agent.execution.canonical.instrument_registry import get_instrument_rules
from trading_agent.execution.canonical.market_observation import (
    EnrichedMarketObservation,
)
from trading_agent.execution.canonical.order_planner import (
    CurrentPortfolioState,
    MarketPrice,
    OrderPlanner,
    OrderPlanningResult,
)
from trading_agent.execution.canonical.risk_decision import (
    OperationalEvidenceWaiver,
    UnifiedRiskDecision,
)
from trading_agent.execution.lifecycle.lifecycle import (
    ExecutionLifecycle,
    ExposureEffect,
    TrustedPrice,
)
from trading_agent.execution.live_safety import LiveRiskLimits, LiveSafetyError
from trading_agent.execution.permission import (
    PermissionContext,
    evaluate_order_permission,
)
from trading_agent.research.forecast import TargetExposure as PlannerTarget
from trading_agent.strategies.canonical import build_default_registry


@dataclass(frozen=True)
class LiveBuyAdmission:
    risk_decision: UnifiedRiskDecision
    planning: OrderPlanningResult
    permission_context: PermissionContext

    def __post_init__(self):
        if not isinstance(self.risk_decision, UnifiedRiskDecision):
            raise LiveSafetyError("BUY admission requires a UnifiedRiskDecision")
        if not isinstance(self.planning, OrderPlanningResult):
            raise LiveSafetyError(
                "BUY admission requires canonical OrderPlanner output"
            )
        if not isinstance(self.permission_context, PermissionContext):
            raise LiveSafetyError("BUY admission requires a PermissionContext")


class PromotedLiveAdmissionProducer:
    """Prepare a whole reconciled batch from authoritative promotion stores."""

    decision_reason = "PROMOTED_STRATEGY"
    # Only the testnet operational producer sets a waiver.
    _evidence_waiver: OperationalEvidenceWaiver | None = None

    def __init__(
        self,
        resolver: RuntimeStrategyResolver,
        *,
        config: AuthorityConfig | None = None,
    ):
        if not isinstance(resolver, RuntimeStrategyResolver):
            raise TypeError("producer requires RuntimeStrategyResolver")
        self.resolver = resolver
        self.config = config or resolver.config

    def produce(
        self,
        *,
        inputs: dict[str, MarketDataInput],
        quotes: dict[str, TrustedPrice],
        snapshot: PortfolioSnapshot,
        allocations: dict[str, float],
        lifecycle: ExecutionLifecycle,
        environment: Environment,
        limits: LiveRiskLimits,
        states: dict[str, dict],
        expected_artifact_ids: dict[str, str] | None = None,
        expected_strategy_name: str | None = None,
        expected_parameters: dict | None = None,
    ) -> list[dict]:
        if self.config.environment != environment:
            raise LiveSafetyError(
                "producer authority environment does not match execution"
            )
        if not snapshot.new_exposure_allowed:
            raise LiveSafetyError(
                "live admission requires reconciled, fully valued portfolio truth"
            )
        prepared = {}
        requests = []
        now = datetime.now(UTC)
        # Resolve the required universe BEFORE any submission. No partial
        # missing-binding batch is silently treated as a qualified universe.
        runtimes = {
            symbol: self.resolver.resolve_for(symbol, "1h", environment)
            for symbol in sorted(allocations)
        }
        missing = [symbol for symbol, runtime in runtimes.items() if runtime is None]
        if missing:
            raise LiveSafetyError(
                "no eligible promoted runtime for: " + ", ".join(missing)
            )
        if environment in {Environment.CANARY, Environment.PRODUCTION} and (
            not expected_artifact_ids
            or set(expected_artifact_ids) != set(allocations)
            or not expected_strategy_name
            or expected_parameters is None
        ):
            raise LiveSafetyError(
                "mainnet admission requires signed evidence runtime bindings"
            )
        self._verify_runtime_sources(runtimes)
        for symbol, runtime in runtimes.items():
            if expected_artifact_ids is not None and (
                expected_artifact_ids.get(symbol) != runtime.artifact_id
                or runtime.strategy_name != expected_strategy_name
                or runtime.parameters != expected_parameters
            ):
                raise LiveSafetyError(
                    "promoted runtime differs from signed strategy evidence"
                )
        decision_authority = DecisionAuthority(self.config)
        for symbol, runtime in runtimes.items():
            market = inputs[symbol]
            if market.symbol != symbol or market.timeframe != "1h":
                raise LiveSafetyError(
                    "market provenance subject does not match promoted runtime"
                )
            quote = quotes[symbol]
            if not quote.is_fresh(60.0):
                raise LiveSafetyError(
                    "promoted producer requires a fresh trusted quote"
                )
            row = market.data.tail(1).to_dicts()[0]
            opened = row.get("timestamp")
            if not isinstance(opened, datetime) or opened.utcoffset() is None:
                raise LiveSafetyError("live candle timestamp must be timezone-aware")
            closed = opened + timedelta(hours=1)
            if closed > now or (now - closed).total_seconds() > 5400:
                raise LiveSafetyError("live producer requires a recent closed candle")
            observation_id = hashlib.sha256(
                f"{symbol}|1h|{closed.isoformat()}|{market.data_manifest_id}".encode()
            ).hexdigest()
            observation = EnrichedMarketObservation(
                symbol=symbol,
                observed_at=market.loaded_at,
                open=float(row["open"]),
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
                volume=float(row["volume"]),
                bar_open_at=opened,
                bar_close_at=closed,
                is_closed=True,
                observation_id=observation_id,
                venue="binance",
                timeframe="1h",
                source=market.source,
                data_manifest_id=market.data_manifest_id,
                feature_artifact_id=market.feature_artifact_id,
            )
            output = runtime.execute(
                market.data,
                snapshot,
                observation_id=observation_id,
                data_manifest_id=market.data_manifest_id,
                feature_artifact_id=market.feature_artifact_id,
            )
            if str(output.signal).upper() == "HOLD":
                continue
            current = (
                float(snapshot.positions.get(symbol, 0.0))
                * quote.price
                / snapshot.equity
            )
            decision = decision_authority.decide(
                DecisionInput(
                    strategy_output=output,
                    symbol=symbol,
                    timeframe="1h",
                    current_price=quote.price,
                    current_exposure=current,
                    equity=snapshot.equity,
                    available_cash=snapshot.available_cash,
                    portfolio_value=snapshot.equity,
                    observation_id=observation_id,
                )
            )
            risk = decision.risk_decision
            if not risk.forecast_fingerprint:
                raise LiveSafetyError(
                    "promoted strategy has no attributable forecast fingerprint"
                )
            sell = str(output.signal).upper() == "SELL"
            target = (
                0.0
                if sell
                else min(
                    float(allocations[symbol]),
                    risk.allowed_target_exposure,
                    current
                    + limits.effective_max_order_notional(snapshot.equity)
                    / ((1 + limits.max_price_deviation_pct) * snapshot.equity),
                )
            )
            if not sell and target <= current + 1e-12:
                continue
            prepared[symbol] = (runtime, observation, decision, current, target, sell)
            if not sell:
                requests.append(
                    AllocationRequest(
                        strategy_id=runtime.artifact_id,
                        symbol=symbol,
                        risk_decision=risk,
                        current_exposure=current,
                        equity=snapshot.equity,
                        available_cash=snapshot.available_cash,
                        portfolio_exposure=snapshot.gross_exposure,
                        symbol_total_exposure=snapshot.symbol_exposures.get(
                            symbol, current
                        ),
                        desired_exposure=target - current,
                        causation_chain=decision.causation_chain,
                        # Conservative default: one correlated spot universe.
                        correlation_cluster="spot_universe",
                    )
                )
        allocated = PortfolioAllocator(self.config).allocate_batch(requests, snapshot)
        approvals = {entry.symbol: entry for entry in allocated.entries}
        decisions = []
        for symbol, (
            runtime,
            observation,
            decision,
            current,
            target,
            sell,
        ) in prepared.items():
            risk = decision.risk_decision
            entry = approvals.get(symbol)
            if not sell:
                if entry is None or entry.approved <= 1e-12:
                    continue
                target = min(target, current + entry.approved)
            exposure = ExposureAuthority(self.config).validate(
                ExposureValidationInput(
                    target_exposure=TargetExposure(
                        target_exposure_pct=target,
                        max_new_exposure_pct=max(0.0, target - current),
                        reduce_only=sell,
                        confidence=decision.target_exposure.confidence,
                    ),
                    symbol=symbol,
                    strategy_id=runtime.artifact_id,
                    current_exposure=current,
                    portfolio_exposure=snapshot.gross_exposure,
                    strategy_exposure=current,
                    equity=snapshot.equity,
                    available_cash=snapshot.available_cash,
                    correlation_exposure=snapshot.gross_exposure,
                    causation_chain=decision.causation_chain
                    if sell
                    else entry.causation_chain,
                )
            )
            if not exposure.allowed:
                raise LiveSafetyError(
                    "live exposure authority blocked: " + exposure.reason
                )
            risk = replace(
                risk,
                allowed_target_exposure=exposure.allowed_target_exposure,
                max_new_exposure=exposure.allowed_max_new_exposure,
                reduce_only=sell,
                authority_chain=tuple(exposure.causation_chain.links),
                evidence_waiver=self._evidence_waiver,
            )
            quote = quotes[symbol]
            portfolio = CurrentPortfolioState(
                symbol=symbol,
                current_exposure=current,
                equity=snapshot.equity,
                available_cash=snapshot.available_cash,
                existing_quantity=snapshot.positions.get(symbol, 0.0),
            )
            planning = OrderPlanner(get_instrument_rules(symbol)).plan(
                target=PlannerTarget(
                    symbol=symbol,
                    exposure=exposure.allowed_target_exposure,
                    horizon=1,
                    forecast_fingerprint=risk.forecast_fingerprint,
                    model_artifact_id=risk.model_artifact_id,
                    risk_decision_id=risk.decision_id,
                ),
                risk_decision=risk,
                observation=observation,
                portfolio=portfolio,
                price=MarketPrice(
                    symbol=symbol, mid=quote.price, ask=quote.price, bid=quote.price
                ),
                tolerance=0.0,
            )
            if not planning.requires_order:
                continue
            intent = planning.intent
            if not sell:
                context = PermissionContext(
                    execution_health=lifecycle.state.execution_health,
                    exposure_effect=ExposureEffect.INCREASE,
                    risk_decision=risk,
                    trusted_price=quote,
                    manual_blocked=lifecycle.state.manual_blocked,
                    reconciliation_state=lifecycle.state.reconciliation.value,
                    order_side="buy",
                    order_size=intent.quantity,
                )
                permission = evaluate_order_permission(context)
                if not permission.allowed():
                    raise LiveSafetyError(
                        "promoted BUY permission blocked: " + permission.reason.value
                    )
            else:
                context = None
            decisions.append(
                {
                    "market_symbol": symbol,
                    "action": "SELL" if sell else "BUY",
                    "qty": intent.quantity,
                    "signal_price": observation.close,
                    "candle_timestamp": observation.bar_open_at,
                    "atr": states[symbol].get("atr"),
                    "observed_high": states[symbol].get("recent_high"),
                    "reason": self.decision_reason,
                    "risk_decision": risk,
                    "order_planning": planning,
                    "permission_context": context,
                }
            )
        return sorted(
            decisions,
            key=lambda item: (item["action"] != "SELL", item["market_symbol"]),
        )

    def _verify_runtime_sources(self, runtimes: dict) -> None:
        """Every runtime must be the registered current source of its artifact."""
        registry = build_default_registry()
        artifact_store = getattr(self.resolver, "_artifact_store", None)
        for runtime in runtimes.values():
            artifact = (
                artifact_store.get(runtime.artifact_id) if artifact_store else None
            )
            if (
                artifact is None
                or artifact.code_sha
                != registry.describe(runtime.strategy_name).code_sha
            ):
                raise LiveSafetyError(
                    "promoted runtime does not match registered current source"
                )
