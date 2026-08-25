"""Batch execution domain models — Milestone C (multi-pair portfolio batch).

ONE PORTFOLIO MEANS ONE DECISION BATCH. These immutable models carry the
entire cycle through its explicit stages:

    reconcile → load ALL → resolve ALL → prepare ALL → allocate ALL (batch)
    → finalize ALL → plan ALL → preflight ALL → reductions → refresh
    → increases (barrier-aware) → protection → final reconcile

Design rules:
- Frozen dataclasses: a prepared decision must never mutate silently after
  the authority chain produced it.
- Real provenance only: MarketDataInput carries data_manifest_id /
  feature_artifact_id from the actual provider; magic IDs are forbidden.
- UNKNOWN broker truth is an execution barrier, never "zero risk".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any

# ── Broker submit states that block further risk ────────────────────────

_BARRIER_STATES = frozenset({"UNKNOWN"})
_PARTIAL_STATES = frozenset({"PARTIALLY_FILLED"})


def is_execution_barrier(submit_result: Any) -> bool:
    """True when a broker submission result must STOP remaining exposure
    increases and force reconciliation before any new risk.

    Barrier semantics (Milestone C):
    - ``UNKNOWN``: broker truth not observed — barrier.
    - ``PARTIALLY_FILLED`` whose lifecycle order is still non-terminal:
      unresolved truth — barrier.
    Everything else is either terminal success or a clean local rejection.
    """
    state = getattr(submit_result, "state", None)
    state_name = getattr(state, "value", str(state)) if state is not None else ""
    if state_name in _BARRIER_STATES:
        return True
    if state_name in _PARTIAL_STATES:
        # Partial fill is only safe once the lifecycle records a resolved,
        # final truth for the intent. Non-terminal partial → barrier.
        lifecycle_state = getattr(submit_result, "_lifecycle_order_state", None)
        if lifecycle_state is None:
            return True  # cannot prove resolution → fail closed
        status = str(getattr(lifecycle_state, "status", "")).lower()
        return status not in {"filled", "canceled", "rejected", "manual"}
    return False


# ── Market data provenance (C10) ─────────────────────────────────────────


@dataclass(frozen=True)
class MarketDataInput:
    """Typed market-data input with REAL provider provenance.

    A plain OHLCV frame is not enough to enter the authority chain: every
    observation must be traceable to what the provider actually returned.
    If ``data_manifest_id`` is missing/empty the binding fails closed.
    """

    symbol: str
    timeframe: str
    data: Any  # OHLCV DataFrame (polars/pandas-like)
    data_manifest_id: str
    feature_artifact_id: str | None  # None = raw features only (explicit)
    loaded_at: datetime
    source: str

    def __post_init__(self) -> None:
        if not self.data_manifest_id or not self.data_manifest_id.strip():
            raise ValueError(
                f"MarketDataInput[{self.symbol} {self.timeframe}]: "
                "data_manifest_id is required (no fabricated provenance)"
            )
        if not self.source or not self.source.strip():
            raise ValueError(
                f"MarketDataInput[{self.symbol} {self.timeframe}]: source is required"
            )


def wrap_market_data(
    symbol: str,
    timeframe: str,
    df: Any,
    source: str = "legacy_provider",
) -> MarketDataInput | None:
    """Wrap a legacy provider dataframe into a typed MarketDataInput.

    Provenance is derived from the DATA ITSELF (symbol/timeframe/last closed
    bar timestamp) — real, content-addressed identity of what was loaded,
    never a magic constant like ``"multipair_runtime"``.

    Returns None when the frame is empty/unusable (fail-closed upstream).
    """
    if df is None or len(df) == 0:
        return None
    try:
        tail_ts = df.tail(1).to_dicts()[0].get("timestamp")
    except Exception:
        tail_ts = None
    manifest = f"{source}:{symbol}:{timeframe}:{tail_ts}"
    return MarketDataInput(
        symbol=symbol,
        timeframe=timeframe,
        data=df,
        data_manifest_id=manifest,
        feature_artifact_id=None,  # explicit raw-feature state; no fake ID
        loaded_at=datetime.now(UTC),
        source=source,
    )


# ── Shared portfolio snapshot / target vector live in authority.portfolio ──


# ── Pair preparation / planning / preflight ─────────────────────────────


class PlannedAction(str, Enum):
    NO_ORDER = "NO_ORDER"
    REDUCTION = "REDUCTION"
    INCREASE = "INCREASE"
    BLOCKED = "BLOCKED"


@dataclass(frozen=True)
class PairPreparedDecision:
    """Everything decided for one pair BEFORE any allocation/broker I/O.

    Produced by ExecutionEngine.prepare_promoted_strategy(): resolver +
    StrategyOutput + DecisionAuthority have run; allocation, exposure
    validation, planning and submission have NOT.
    """

    symbol: str
    timeframe: str
    artifact_id: str
    strategy_name: str
    observation: Any = None  # EnrichedMarketObservation
    strategy_output: Any = None  # StrategyOutput
    risk_decision: Any = None  # UnifiedRiskDecision (decision-authority stage)
    requested_target_exposure: float = 0.0  # from DecisionAuthority (pre-allocation)
    current_exposure: float = 0.0
    signal: str = "HOLD"  # HOLD | BUY | SELL
    causation_chain: Any = None  # CausationChain through decision authority
    current_price: float = 0.0
    equity: float = 0.0
    available_cash: float = 0.0
    current_quantity: float = 0.0
    total_portfolio_exposure: float = 0.0
    prepare_status: str = "ok"  # ok | hold | no_price | bad_observation
    prepared_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass(frozen=True)
class FinalizedPairDecision:
    """PairPreparedDecision + batch-approved target + ExposureAuthority pass."""

    prepared: PairPreparedDecision
    approved_target_exposure: float
    risk_decision: Any  # UnifiedRiskDecision with exposure caps + full chain
    target: Any  # authority TargetExposure post-exposure-validation
    causation_chain: Any  # combined decision+allocation+exposure chain
    no_change: bool = False  # approved target == current exposure


@dataclass(frozen=True)
class PairOrderPlan:
    """Planned order for one pair — created BEFORE any broker I/O."""

    symbol: str
    timeframe: str
    action: PlannedAction
    finalized: FinalizedPairDecision | None
    intent: Any | None  # canonical OrderIntent (None for NO_ORDER/BLOCKED)
    intent_id: str | None
    side: str | None  # "buy" | "sell"
    quantity: float
    limit_price: float | None
    instrument_rule_id: str | None
    idempotency_key: str | None
    detail: str = ""


@dataclass(frozen=True)
class BatchPreflightResult:
    """Result of simulating the WHOLE batch before first BUY broker I/O."""

    passed: bool
    reduction_plans: tuple[PairOrderPlan, ...]
    increase_plans: tuple[PairOrderPlan, ...]
    blocked_plans: tuple[PairOrderPlan, ...]
    reasons: dict[str, str]  # symbol -> blocking reason
    simulated_final_cash: float = 0.0
    simulated_final_gross: float = 0.0
    checks_run: tuple[str, ...] = ()


@dataclass(frozen=True)
class PlannedSubmissionOutcome:
    """What actually happened when ONE planned order met the broker."""

    plan: PairOrderPlan
    order: Any | None  # legacy Order view
    submit_state: str  # BrokerSubmitState value
    barrier: bool  # True ⇒ stop remaining increases + reconcile
    submitted: bool  # reached the gateway at all
    protection_submitted: bool = False
