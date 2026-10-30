"""Deterministic lifecycle exerciser for the testnet operational track.

It predicts nothing. Per authorized symbol it buys a small position when flat,
lets the runner protect it with an exchange-native stop, and sells it after a
fixed hold. Every ``tight_stop_every``-th lifecycle gets a stop close to the
price so that the venue, not the runner, closes it. Orders go through the same
admission pipeline as a promoted strategy (DecisionAuthority, allocator,
ExposureAuthority, OrderPlanner, permission) and the same execution path.

Its evidence is honestly UNKNOWN; the operator's authorization
(operational_authorization.py) waives it on testnet only. Nothing here is
research or promotion evidence, and the driver is not in the strategy
registry, so research campaigns cannot select it.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from pathlib import Path

from trading_agent.authority.config import AuthorityConfig, Environment
from trading_agent.authority.resolver import StrategyRuntime
from trading_agent.execution.canonical.instrument_registry import get_instrument_rules
from trading_agent.execution.canonical.risk_decision import OperationalEvidenceWaiver
from trading_agent.execution.live_admission import PromotedLiveAdmissionProducer
from trading_agent.execution.live_safety import LiveRiskLimits, LiveSafetyError
from trading_agent.execution.operational_authorization import (
    DRIVER,
    OperationalAuthorization,
)

TRACK = "operational"
# Ledger states that leave (or may still leave) a position from a BUY.
_OPENING_STATUSES = frozenset(
    {
        "reserved",
        "submitted",
        "acknowledged",
        "open",
        "partial",
        "filled",
        "reconciling",
        "manual_intervention",
        "unknown",
    }
)


def flat_below_usd(symbol: str) -> float:
    """A position worth less than the venue minimum cannot be sold: treat it as flat."""
    return max(1.0, get_instrument_rules(symbol).min_notional)


def driver_code_sha() -> str:
    """Identity of this driver's source; an authorization is bound to it."""
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


@dataclass(frozen=True)
class TrackEntry:
    entry_at: datetime
    index: int  # 0-based order of this lifecycle under the authorization


@dataclass(frozen=True)
class TrackLedgerView:
    lifecycles_opened: int
    latest_entries: dict[str, TrackEntry]


def track_ledger_view(
    order_ledger: dict, *, symbols: tuple[str, ...], issued_at: datetime
) -> TrackLedgerView:
    """Lifecycles opened since the authorization, from the signed order ledger.

    While the track is the only BUY source on the account, every BUY on an
    authorized symbol since ``issued_at`` is one of its lifecycles.
    """
    buys = []
    for record in order_ledger.values():
        if not isinstance(record, dict):
            continue
        if str(record.get("side") or "").upper() != "BUY":
            continue
        if record.get("symbol") not in symbols:
            continue
        status = str(record.get("status") or "").lower()
        filled = float(record.get("filled_quantity") or 0.0)
        if status not in _OPENING_STATUSES and filled <= 0:
            continue
        try:
            created = datetime.fromisoformat(str(record.get("created_at")))
        except ValueError:
            continue
        if created.utcoffset() is None or created < issued_at:
            continue
        buys.append((created, str(record["symbol"])))
    buys.sort()
    latest = {
        symbol: TrackEntry(entry_at=created, index=index)
        for index, (created, symbol) in enumerate(buys)
    }
    return TrackLedgerView(lifecycles_opened=len(buys), latest_entries=latest)


def entry_block_reason(
    authorization: OperationalAuthorization,
    view: TrackLedgerView,
    *,
    daily_loss_usd: float,
    run_failure: str | None,
) -> str | None:
    """Why the track must not open anything this cycle (exits continue).

    ``run_failure`` describes any execution run since the authorization that
    did not complete (or why that cannot be checked); it stays a block until
    the operator re-authorizes.
    """
    if run_failure is not None:
        return f"{run_failure}; review it, then re-authorize"
    if view.lifecycles_opened >= authorization.max_lifecycles:
        return (
            f"lifecycle cap reached "
            f"({view.lifecycles_opened}/{authorization.max_lifecycles})"
        )
    if daily_loss_usd > authorization.max_daily_loss_usd:
        return (
            f"daily loss ${daily_loss_usd:.2f} exceeds "
            f"${authorization.max_daily_loss_usd:.2f}"
        )
    return None


def plan_entries(
    authorization: OperationalAuthorization,
    view: TrackLedgerView,
    *,
    held_symbols: set[str],
    block_reason: str | None,
) -> frozenset[str]:
    """Flat symbols allowed to open a lifecycle this cycle, within every cap."""
    if block_reason is not None:
        return frozenset()
    slots = min(
        authorization.max_open_positions - len(held_symbols),
        authorization.max_lifecycles - view.lifecycles_opened,
    )
    flat = [symbol for symbol in authorization.symbols if symbol not in held_symbols]
    return frozenset(flat[: max(0, slots)])


def tight_stop_symbols(
    authorization: OperationalAuthorization,
    view: TrackLedgerView,
    *,
    held_symbols: set[str],
) -> set[str]:
    """Held symbols whose current lifecycle should be closed by the venue stop."""
    every = authorization.tight_stop_every
    if every <= 0:
        return set()
    return {
        symbol
        for symbol in held_symbols
        if symbol in view.latest_entries
        and view.latest_entries[symbol].index % every == every - 1
    }


@dataclass(frozen=True)
class ExerciserSignal:
    signal: str
    confidence: float
    target_exposure_pct: float
    reduce_only: bool


class LifecycleExerciser:
    """Buy when flat (if allowed), sell once the hold has elapsed, else hold."""

    def __init__(
        self,
        *,
        symbol: str,
        max_order_usd: float,
        hold: timedelta,
        entry_at: datetime | None,
        may_enter: bool,
        now: datetime,
        flat_below_usd: float,
    ) -> None:
        self.symbol = symbol
        self.max_order_usd = max_order_usd
        self.hold = hold
        self.entry_at = entry_at
        self.may_enter = may_enter
        self.now = now
        self.flat_below_usd = flat_below_usd

    def generate_signal_with_position(self, market_data, portfolio_state) -> ExerciserSignal:
        quantity = float(portfolio_state.positions.get(self.symbol, 0.0))
        price = float(market_data["close"].tail(1)[0])
        if quantity * price < self.flat_below_usd:
            if not self.may_enter:
                return ExerciserSignal("HOLD", 1.0, 0.0, False)
            # The order notional is capped by the authorized limits downstream;
            # this only has to ask for at least that much.
            target = min(1.0, 2 * self.max_order_usd / portfolio_state.equity)
            return ExerciserSignal("BUY", 1.0, target, False)
        # A position the track did not open is closed rather than adopted.
        if self.entry_at is None or self.now - self.entry_at >= self.hold:
            return ExerciserSignal("SELL", 1.0, 0.0, True)
        return ExerciserSignal("HOLD", 1.0, 0.0, False)


class OperationalRuntimeResolver:
    """Resolve the exerciser for every authorized symbol on testnet."""

    def __init__(
        self,
        authorization: OperationalAuthorization,
        *,
        view: TrackLedgerView,
        entry_symbols: frozenset[str],
        now: datetime,
    ) -> None:
        self.authorization = authorization
        self.view = view
        self.entry_symbols = entry_symbols
        self.now = now
        self.config = AuthorityConfig(environment=Environment.TESTNET)
        self.artifact_id = "ops-" + hashlib.sha256(
            f"{authorization.authorization_id}|{authorization.driver_code_sha}".encode()
        ).hexdigest()[:28]
        self.parameters = {
            "hold_minutes": authorization.hold_minutes,
            "max_order_usd": authorization.max_order_usd,
            "tight_stop_every": authorization.tight_stop_every,
            "tight_stop_pct": authorization.tight_stop_pct,
        }

    def resolve_for(
        self, symbol: str, timeframe: str, environment: Environment
    ) -> StrategyRuntime | None:
        if (
            environment is not Environment.TESTNET
            or timeframe != "1h"
            or symbol not in self.authorization.symbols
        ):
            return None
        entry = self.view.latest_entries.get(symbol)
        exerciser = LifecycleExerciser(
            symbol=symbol,
            max_order_usd=self.authorization.max_order_usd,
            hold=timedelta(minutes=self.authorization.hold_minutes),
            entry_at=entry.entry_at if entry else None,
            may_enter=symbol in self.entry_symbols,
            now=self.now,
            flat_below_usd=flat_below_usd(symbol),
        )
        fingerprint = hashlib.sha256(
            f"{self.artifact_id}|{symbol}|{timeframe}".encode()
        ).hexdigest()
        return StrategyRuntime(
            strategy=exerciser,
            artifact_id=self.artifact_id,
            strategy_name=DRIVER,
            symbol=symbol,
            timeframe=timeframe,
            environment=Environment.TESTNET,
            parameters=dict(self.parameters),
            promoted_at=self.authorization.issued_at,
            promotion_stage="OPERATIONAL_TESTNET",
            artifact_metadata={
                # Honest labels: the driver has no forecast to calibrate.
                "calibration_state": "UNKNOWN",
                "ood_state": "UNKNOWN",
                "regime_state": "UNKNOWN",
                "calibration_ece": 1.0,
                "ood_score": 1.0,
                "regime_entropy": 1.0,
                "interval_width": 1.0,
                "forecast_fingerprint": fingerprint,
                "track": TRACK,
                "authorization_id": self.authorization.authorization_id,
            },
        )


class OperationalLiveAdmissionProducer(PromotedLiveAdmissionProducer):
    """The promoted-strategy pipeline, fed by the exerciser, on testnet only."""

    decision_reason = "OPERATIONAL_EXERCISER"

    def __init__(self, resolver: OperationalRuntimeResolver) -> None:
        if not isinstance(resolver, OperationalRuntimeResolver):
            raise TypeError("producer requires OperationalRuntimeResolver")
        self.resolver = resolver
        self.config = resolver.config
        if self.config.environment is not Environment.TESTNET:
            raise LiveSafetyError("the operational track is testnet only")
        self._evidence_waiver = OperationalEvidenceWaiver(
            authorization_id=resolver.authorization.authorization_id,
            environment="testnet",
            expires_at=resolver.authorization.expires_at,
        )

    def _verify_runtime_sources(self, runtimes: dict[str, StrategyRuntime]) -> None:
        if self.resolver.authorization.driver_code_sha != driver_code_sha():
            raise LiveSafetyError("operational driver source changed since authorization")
        for runtime in runtimes.values():
            if (
                runtime.strategy_name != DRIVER
                or runtime.artifact_id != self.resolver.artifact_id
                or runtime.environment is not Environment.TESTNET
            ):
                raise LiveSafetyError("runtime is not the authorized operational driver")


@dataclass(frozen=True)
class OperationalTrack:
    """The operator-authorized exerciser as resolved for one runner cycle."""

    authorization: OperationalAuthorization
    view: TrackLedgerView
    held: frozenset[str]
    entries: frozenset[str]
    entries_blocked: str | None
    producer: OperationalLiveAdmissionProducer

    def limits(self, limits: LiveRiskLimits) -> LiveRiskLimits:
        return replace(
            limits,
            max_order_notional_usd=min(
                limits.max_order_notional_usd, self.authorization.max_order_usd
            ),
        )

    def audit_details(self) -> dict:
        return {
            "authorization_id": self.authorization.authorization_id,
            "expires_at": self.authorization.expires_at.isoformat(),
            "lifecycles_opened": self.view.lifecycles_opened,
            "max_lifecycles": self.authorization.max_lifecycles,
            "held": sorted(self.held),
            "may_open": sorted(self.entries),
            "entries_blocked": self.entries_blocked,
        }
