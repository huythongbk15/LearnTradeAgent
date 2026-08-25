"""Portfolio-level backtest engine (Milestone D).

Reuses the ENTIRE live decision-authority chain and swaps ONLY the clock and
the broker:

    HistoricalMarketClock
        ↓  bars closed at timestamp t (signal = close[t])
    MarketDataInput per binding          (content-addressed provenance)
        ↓
    RuntimeStrategyResolver              ← REUSED
        ↓
    StrategyRuntime                      ← REUSED (engine.prepare_promoted_strategy)
        ↓
    StrategyOutputs[t]                   ← REUSED (PairPreparedDecision)
        ↓
    ONE PortfolioSnapshot[t]             ← built from SHARED sim ledgers
        ↓
    PortfolioAllocator.allocate_batch()  ← REUSED
        ↓
    PortfolioTargetVector[t]             ← REUSED
        ↓
    risk / planner / preflight           ← REUSED (ExposureAuthority, OrderPlanner,
                                            MultiPairRuntime.preflight_batch)
        ↓
    QUEUED ORDERS
        ↓
    HistoricalSimulationBroker           ← deterministic fills at earliest t+1
        ↓
    shared cash / positions ledger       ← ONE pool
        ↓
    PortfolioSnapshot[t+1]

No-lookahead invariants
-----------------------
- Signals only ever see bars whose CLOSE time ≤ simulated now t.
- An order queued at decision time t is filled with the OPEN price of the
  first bar of that binding that OPENS at or after t — never with any price
  from a bar that had not opened yet.
- Fills are recorded at the first timeline point strictly AFTER the queueing
  decision time.
"""

from __future__ import annotations

import logging
import math
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import polars as pl

from trading_agent.authority.config import Environment
from trading_agent.authority.portfolio import (
    PortfolioSnapshot,
    PortfolioTargetVector,
    ReconciliationState,
)
from trading_agent.execution.batch_models import PlannedAction, wrap_market_data
from trading_agent.execution.multi_pair_runtime import (
    MultiPairRuntime,
    validate_candle_closed,
)

logger = logging.getLogger(__name__)


# ── Clock ────────────────────────────────────────────────────────────────


def _tf_seconds(timeframe: str) -> int:
    units = {"m": 60, "h": 3600, "d": 86400}
    unit = timeframe[-1].lower()
    if unit not in units or not timeframe[:-1].isdigit():
        raise ValueError(f"unsupported timeframe {timeframe!r}")
    return int(timeframe[:-1]) * units[unit]


@dataclass(frozen=True)
class _Bar:
    open_ts: datetime
    close_ts: datetime
    open: float
    high: float
    low: float
    close: float


class HistoricalMarketClock:
    """Synchronized multi-pair clock over historical bars.

    Bars are labeled by their OPEN timestamp; a bar is CLOSED at
    ``open_ts + timeframe``. The simulation timeline is the sorted union of
    all bindings' bar-close times, so multi-timeframe portfolios advance in
    lockstep without lookahead.
    """

    def __init__(self, bars: dict[tuple[str, str], pl.DataFrame]):
        self.bars_raw = dict(bars)
        self._bars: dict[tuple[str, str], list[_Bar]] = {}
        close_times: set[datetime] = set()
        for binding, df in bars.items():
            symbol, timeframe = binding
            tf_sec = _tf_seconds(timeframe)
            rows: list[_Bar] = []
            for r in df.to_dicts():
                ts = r["timestamp"]
                if isinstance(ts, datetime):
                    ts = ts if ts.tzinfo else ts.replace(tzinfo=UTC)
                else:
                    raise ValueError(f"{binding}: bar timestamp missing/not datetime")
                rows.append(
                    _Bar(
                        open_ts=ts,
                        close_ts=ts + timedelta(seconds=tf_sec),
                        open=float(r["open"]),
                        high=float(r["high"]),
                        low=float(r["low"]),
                        close=float(r["close"]),
                    )
                )
            if not rows:
                raise ValueError(f"{binding}: empty bars")
            rows.sort(key=lambda b: b.open_ts)
            # Reject duplicate open timestamps (ambiguous history).
            opens = [b.open_ts for b in rows]
            if len(set(opens)) != len(opens):
                raise ValueError(f"{binding}: duplicate bar timestamps")
            self._bars[binding] = rows
            close_times.update(b.close_ts for b in rows)

        self.timeline: tuple[datetime, ...] = tuple(sorted(close_times))

    def closed_bars(self, binding: tuple[str, str], now: datetime) -> list[_Bar]:
        """All bars fully closed at ``now`` (close_ts ≤ now)."""
        return [b for b in self._bars[binding] if b.close_ts <= now]

    def slice_upto(self, binding: tuple[str, str], now: datetime) -> pl.DataFrame:
        """DataFrame of bars closed at/before ``now`` (signal input)."""
        rows = self.closed_bars(binding, now)
        return pl.DataFrame(
            {
                "timestamp": [b.open_ts for b in rows],
                "open": [b.open for b in rows],
                "high": [b.high for b in rows],
                "low": [b.low for b in rows],
                "close": [b.close for b in rows],
                "volume": [self.bars_raw_volume(binding, b.open_ts) for b in rows],
            }
        )

    def bars_raw_volume(self, binding: tuple[str, str], open_ts: datetime) -> float:
        df = self.bars_raw[binding]
        try:
            match = df.filter(pl.col("timestamp") == open_ts)
            if len(match) == 0:
                return 0.0
            return float(match.to_dicts()[0].get("volume", 0.0))
        except Exception:
            return 0.0

    def fill_for(
        self,
        binding: tuple[str, str],
        decision_time: datetime,
        by_time: datetime,
    ) -> tuple[datetime, float] | None:
        """Earliest no-lookahead fill info for an order queued at
        ``decision_time``.

        Uses the OPEN of the FIRST bar that OPENS at/after the decision
        moment. Returns ``(fill_eligible_time, open_price)`` where
        ``fill_eligible_time`` is that bar's close time (first moment the
        broker may report the fill). None when no such bar exists by
        ``by_time`` (order expires unfilled).
        """
        for b in self._bars[binding]:
            if b.open_ts >= decision_time and b.close_ts <= by_time:
                return b.close_ts, b.open
        return None

    def last_close(self, binding: tuple[str, str], now: datetime) -> float | None:
        rows = self.closed_bars(binding, now)
        return rows[-1].close if rows else None


# ── Simulation broker ────────────────────────────────────────────────────


@dataclass(frozen=True)
class SimFill:
    idempotency_key: str
    symbol: str
    side: str  # "buy" | "sell"
    quantity: float
    price: float  # actual fill price incl. adverse slippage
    fee: float
    slippage_cost: float
    notional: float
    decision_time: datetime
    fill_time: datetime


@dataclass
class QueuedOrder:
    idempotency_key: str
    symbol: str
    side: str
    quantity: float
    reference_price: float
    decision_time: datetime
    # Execution phase: "reduction" (risk-reducing) settles BEFORE any
    # "increase" so freed cash/inventory is usable in the same batch.
    phase: str = "increase"


class HistoricalSimulationBroker:
    """Deterministic market-order simulator with ONE shared cash/positions
    ledger. Fees are proportional (bps); slippage is applied adversely by a
    fixed bps. No randomness anywhere."""

    def __init__(
        self,
        initial_cash: float,
        *,
        fee_bps: float = 10.0,
        slippage_bps: float = 5.0,
    ):
        if initial_cash <= 0:
            raise ValueError("initial_cash must be > 0")
        self.cash = float(initial_cash)
        self.initial_cash = float(initial_cash)
        self.positions: dict[str, dict[str, float]] = {}  # sym -> qty, avg_px
        self.fee_bps = float(fee_bps)
        self.slippage_bps = float(slippage_bps)
        self._queued: list[QueuedOrder] = []
        self._seen_keys: set[str] = set()
        self.fills: list[SimFill] = []
        self.turnover_history: list[float] = []
        self.cost_history: list[float] = []

    def queue(self, order: QueuedOrder) -> bool:
        """Queue one order. Duplicate idempotency keys are ignored."""
        if order.idempotency_key in self._seen_keys:
            return False
        self._seen_keys.add(order.idempotency_key)
        self._queued.append(order)
        return True

    @property
    def pending_count(self) -> int:
        return len(self._queued)

    # ── queued reservations (orders awaiting earliest-t+1 fills) ────────

    def reserved_cash(self) -> float:
        """Cash committed to pending BUYs (notional + fee + slippage est.

        Decisions made while a BUY is unfilled must not double-spend the
        same cash — mirrors live broker reserved-balance semantics.
        """
        total = 0.0
        for o in self._queued:
            if o.side != "buy":
                continue
            est_notional = o.quantity * o.reference_price
            fee = est_notional * self.fee_bps / 10_000.0
            slip = est_notional * self.slippage_bps / 10_000.0
            total += est_notional + fee + slip
        return total

    def reserved_inventory(self, symbol: str) -> float:
        """Quantity committed to pending SELLs for ``symbol``.

        While a SELL is unfilled the planner must treat that inventory as
        already spoken for (prevents duplicate-reduction drift).
        """
        return sum(
            o.quantity for o in self._queued if o.symbol == symbol and o.side == "sell"
        )

    def settle(
        self,
        now: datetime,
        clock: HistoricalMarketClock,
        bindings: list[tuple[str, str]],
    ) -> list[SimFill]:
        """Fill every queued order eligible at ``now`` (earliest t+1).

        Fill priority: REDUCTION before INCREASE (risk reduction is never
        starved by new exposure), then per-symbol FIFO by decision time.
        """
        filled: list[SimFill] = []
        remaining: list[QueuedOrder] = []

        def _sort_key(o: QueuedOrder) -> tuple:
            return (
                0 if o.phase == "reduction" else 1,
                o.symbol,
                o.decision_time,
            )

        for order in sorted(self._queued, key=_sort_key):
            binding = None
            for b in bindings:
                if b[0] == order.symbol:
                    binding = b
                    break
            assert binding is not None
            info = clock.fill_for(binding, order.decision_time, now)
            if info is None:
                remaining.append(order)  # not eligible yet → keep waiting
                continue
            _, raw_open = info
            slip = self.slippage_bps / 10_000.0
            if order.side == "buy":
                price = raw_open * (1.0 + slip)
            else:
                price = raw_open * (1.0 - slip)

            # ── ACTUAL-FILL SAFETY GUARDS (deterministic, no negatives) ──
            if order.side == "sell":
                held = self.positions.get(order.symbol, {}).get("qty", 0.0)
                if held <= 0:
                    # nothing to sell — never fabricate synthetic proceeds
                    # REJECTED ORDER DROPPED permanently (not re-queued).
                    logger.warning(
                        "settle SELL rejected: zero inventory for %s; order dropped.",
                        order.symbol,
                    )
                    continue
                # cap at real inventory (oversized/reduce-only safety)
                qty = min(order.quantity, held)
            else:
                qty = order.quantity

            if qty <= 0:
                # nothing to fill — drop
                continue

            notional = qty * price
            fee = notional * self.fee_bps / 10_000.0
            slip_cost = qty * raw_open * slip

            if order.side == "buy":
                total_cost = notional + fee
                # Actual-fill cash guard: never let aggregate fills drive cash
                # negative. Because fills settle sequentially in phase order,
                # this check is ALREADY aggregate-aware (earlier fills reduced
                # self.cash for later ones). Covers gap-up + multi-BUY cases.
                if self.cash < total_cost:
                    logger.warning(
                        "settle BUY rejected: cash %.4f < cost %.4f "
                        "(gap-up / overspend guard); order dropped.",
                        self.cash,
                        total_cost,
                    )
                    # REJECTED ORDER DROPPED permanently (not re-queued).
                    continue
                self.cash -= total_cost
                pos = self.positions.setdefault(
                    order.symbol, {"qty": 0.0, "avg_px": 0.0}
                )
                total_qty = pos["qty"] + qty
                pos["avg_px"] = (
                    (pos["avg_px"] * pos["qty"] + price * qty) / total_qty
                    if total_qty > 0
                    else 0.0
                )
                pos["qty"] = total_qty
            else:
                # oversized SELL → filled partial (qty capped above); the
                # remainder is DISCARDED (do not re-queue → no infinite
                # sell pressure on vanished inventory).
                proceeds = notional - fee
                self.cash += proceeds
                pos = self.positions.setdefault(
                    order.symbol, {"qty": 0.0, "avg_px": 0.0}
                )
                pos["qty"] = max(0.0, pos["qty"] - qty)
                if pos["qty"] <= 1e-12:
                    pos["qty"] = 0.0
                    pos["avg_px"] = 0.0
            fill = SimFill(
                idempotency_key=order.idempotency_key,
                symbol=order.symbol,
                side=order.side,
                quantity=qty,
                price=price,
                fee=fee,
                slippage_cost=slip_cost,
                notional=notional,
                decision_time=order.decision_time,
                fill_time=info[0],
            )
            self.fills.append(fill)
            self.turnover_history.append(notional)
            self.cost_history.append(fee + slip_cost)
            filled.append(fill)
        self._queued = remaining
        return filled

    def equity(self, prices: dict[str, float]) -> float:
        total = self.cash
        for sym, pos in self.positions.items():
            px = prices.get(sym)
            if px is not None:
                total += pos["qty"] * px
            else:
                total += pos["qty"] * pos["avg_px"]
        return total


# ── Result containers ────────────────────────────────────────────────────


@dataclass(frozen=True)
class StepRecord:
    step_time: datetime
    equity: float
    cash: float
    gross_exposure: float
    symbol_exposures: dict[str, float]
    targets: dict[str, float]
    queued_orders: int
    fills_this_step: int
    cycle_status: str


@dataclass
class PortfolioBacktestResult:
    initial_capital: float
    final_equity: float
    equity_curve: list[tuple[datetime, float]] = field(default_factory=list)
    drawdown_series: list[tuple[datetime, float]] = field(default_factory=list)
    exposure_history: list[tuple[datetime, float, dict[str, float]]] = field(
        default_factory=list
    )
    turnover_history: list[tuple[datetime, float]] = field(default_factory=list)
    cost_history: list[tuple[datetime, float]] = field(default_factory=list)
    per_symbol_contribution: dict[str, float] = field(default_factory=dict)
    trades: list[SimFill] = field(default_factory=list)
    steps: list[StepRecord] = field(default_factory=list)
    target_vectors: list[PortfolioTargetVector] = field(default_factory=list)
    blocked_cycles: int = 0
    expired_orders: int = 0

    @property
    def max_drawdown(self) -> float:
        return max((dd for _, dd in self.drawdown_series), default=0.0)

    @property
    def total_return_pct(self) -> float:
        if self.initial_capital <= 0:
            return 0.0
        return (self.final_equity / self.initial_capital - 1.0) * 100.0

    @property
    def total_costs(self) -> float:
        return sum(c for _, c in self.cost_history)

    @property
    def total_turnover(self) -> float:
        return sum(v for _, v in self.turnover_history)

    def to_dict(self) -> dict[str, Any]:
        return {
            "initial_capital": self.initial_capital,
            "final_equity": self.final_equity,
            "total_return_pct": round(self.total_return_pct, 10),
            "max_drawdown": round(self.max_drawdown, 10),
            "total_turnover": round(self.total_turnover, 6),
            "total_costs": round(self.total_costs, 6),
            "per_symbol_contribution": {
                k: round(v, 8) for k, v in sorted(self.per_symbol_contribution.items())
            },
            "trades": [
                {
                    "symbol": f.symbol,
                    "side": f.side,
                    "quantity": round(f.quantity, 10),
                    "price": round(f.price, 10),
                    "fee": round(f.fee, 10),
                    "decision_time": f.decision_time.isoformat(),
                    "fill_time": f.fill_time.isoformat(),
                }
                for f in self.trades
            ],
            "equity_curve_tail": [
                (t.isoformat(), round(e, 6)) for t, e in self.equity_curve[-5:]
            ],
            "blocked_cycles": self.blocked_cycles,
            "expired_orders": self.expired_orders,
            "n_steps": len(self.steps),
        }


# ── Engine ───────────────────────────────────────────────────────────────


class PortfolioBacktestEngine:
    """Portfolio backtest over the FULL live decision-authority chain.

    Only the clock (HistoricalMarketClock) and the broker
    (HistoricalSimulationBroker) are swapped; resolver, strategy runtime,
    strategy outputs, batch allocator, target vector, risk/planner and
    batch preflight are THE SAME components used by live paper trading.
    """

    def __init__(
        self,
        engine: Any,
        *,
        bars: dict[tuple[str, str], pl.DataFrame],
        initial_cash: float = 100_000.0,
        fee_bps: float = 10.0,
        slippage_bps: float = 5.0,
        cash_reserve_pct: float | None = None,
        required_universe_policy: Any | None = None,
    ):
        if getattr(engine, "resolver", None) is None:
            raise ValueError("engine must have a RuntimeStrategyResolver")
        self.engine = engine
        self.clock = HistoricalMarketClock(bars)
        self.bindings = sorted(self.clock._bars.keys())
        self.multipair = MultiPairRuntime(
            engine,
            required_universe_policy=required_universe_policy,
            cash_reserve_pct=cash_reserve_pct,
        )
        self.cash_reserve_pct = cash_reserve_pct
        self.fee_bps = fee_bps
        self.slippage_bps = slippage_bps
        self.initial_cash = float(initial_cash)

    # ── snapshot from SIMULATION ledgers (mirrors build_shared_snapshot) ──

    def _build_snapshot(
        self,
        now: datetime,
        broker: HistoricalSimulationBroker,
        environment: Environment,
    ) -> PortfolioSnapshot:
        prices: dict[str, float] = {}
        unvalued: list[str] = []
        for sym, tf in self.bindings:
            px = self.clock.last_close((sym, tf), now)
            if px is not None and px > 0:
                prices[sym] = px
        positions: dict[str, float] = {}
        symbol_exposures: dict[str, float] = {}
        gross = 0.0
        equity = broker.equity(prices)
        reserved_inventory_total = 0.0
        for sym, pos in broker.positions.items():
            if pos["qty"] <= 0:
                continue
            # Effective inventory = held − pending SELLs (reservation-aware).
            # Equity above is marked on FULL held qty (correct money value);
            # decision inputs see the effective qty so a queued reduction is
            # never double-counted by the planner.
            effective_qty = max(pos["qty"] - broker.reserved_inventory(sym), 0.0)
            reserved_inventory_total += pos["qty"] - effective_qty
            positions[sym] = effective_qty
            px = prices.get(sym)
            if px is not None and equity > 0:
                expo = effective_qty * px / equity
                symbol_exposures[sym] = expo
                gross += expo
            else:
                unvalued.append(sym)
        tracked_symbols = {s for s, _tf in self.bindings}
        untracked_symbols = tuple(sorted(set(symbol_exposures) - tracked_symbols))
        state = (
            ReconciliationState.DEGRADED if unvalued else ReconciliationState.RECONCILED
        )
        reserved_cash = broker.reserved_cash()
        return PortfolioSnapshot(
            equity=max(equity, 0.0),
            available_cash=max(broker.cash - reserved_cash, 0.0),
            positions=positions,
            symbol_exposures=symbol_exposures,
            gross_exposure=gross,
            untracked_symbols=untracked_symbols,
            untracked_exposure=0.0,
            untracked_valued=all(s not in unvalued for s in untracked_symbols),
            reserved_cash=reserved_cash,
            reserved_inventory=reserved_inventory_total,
            observed_at=now,
            reconciliation_state=state,
        )

    def _gate_binding(
        self,
        binding: tuple[str, str],
        mdi: Any,
        environment: Environment,
        now: datetime,
    ) -> str | None:
        """Same checks as MultiPairRuntime.evaluate_required_universe but
        against the SIMULATED clock (no real-now dependency)."""
        symbol, timeframe = binding
        if mdi is None:
            return "missing_market_data"
        df = mdi.data
        if df is None or len(df) == 0:
            return "empty_market_data"
        tail = df.tail(1).to_dicts()[0]
        close = tail.get("close")
        if close is None or not math.isfinite(float(close)) or float(close) <= 0:
            return "invalid_last_close"
        bar_ts = tail.get("timestamp")
        if not isinstance(bar_ts, datetime):
            return "missing_bar_timestamp"
        bar_ts_utc = bar_ts if bar_ts.tzinfo else bar_ts.replace(tzinfo=UTC)
        if bar_ts_utc > now:
            return "future_bar_timestamp"
        reason = validate_candle_closed(bar_ts_utc, timeframe, now=now)
        if reason is not None:
            return reason
        if not mdi.data_manifest_id or not mdi.data_manifest_id.strip():
            return "missing_provenance"
        try:
            runtime = self.engine.resolver.resolve_for(symbol, timeframe, environment)
        except Exception as e:
            return f"resolver_error:{type(e).__name__}"
        if runtime is None:
            return "no_resolved_strategy"
        planner = getattr(self.engine, "planner", None)
        if planner is None or planner.rules_for(symbol) is None:
            return "missing_instrument_rules"
        return None

    # ── main loop ────────────────────────────────────────────────────────

    def run(
        self,
        environment: str | Environment = "paper",
    ) -> PortfolioBacktestResult:
        env = (
            environment
            if isinstance(environment, Environment)
            else Environment(str(environment).lower())
        )
        broker = HistoricalSimulationBroker(
            self.initial_cash,
            fee_bps=self.fee_bps,
            slippage_bps=self.slippage_bps,
        )
        result = PortfolioBacktestResult(
            initial_capital=self.initial_cash,
            final_equity=self.initial_cash,
        )
        peak_equity = self.initial_cash
        warmup_ok = False

        for now in self.clock.timeline:
            # ── settle orders queued earlier (earliest t+1 fills) ──
            fills_now = broker.settle(now, self.clock, self.bindings)

            prices_now = {}
            for sym, tf in self.bindings:
                px = self.clock.last_close((sym, tf), now)
                if px is not None:
                    prices_now[sym] = px

            snapshot = self._build_snapshot(now, broker, env)

            # need enough history before strategies can emit signals
            min_needed = 2
            all_have_history = all(
                len(self.clock.closed_bars(b, now)) >= min_needed for b in self.bindings
            )
            if not all_have_history:
                warmup_ok = False
            else:
                warmup_ok = True

            targets: dict[str, float] = {}
            vector: PortfolioTargetVector | None = None
            queued_now = 0
            status = "ok"

            if warmup_ok:
                # ── market data per binding (content-addressed provenance) ──
                market_batch: dict[tuple[str, str], Any] = {}
                failed: dict[tuple[str, str], str] = {}
                passed: list[tuple[str, str]] = []
                for binding in self.bindings:
                    reason = None
                    try:
                        mdi = wrap_market_data(
                            binding[0],
                            binding[1],
                            self.clock.slice_upto(binding, now),
                            source="historical_backtest",
                        )
                    except Exception as e:
                        mdi = None
                        reason = f"wrap_error:{type(e).__name__}:{e}"
                    market_batch[binding] = mdi
                    if reason is None:
                        reason = self._gate_binding(binding, mdi, env, now)
                    if reason is not None:
                        failed[binding] = reason
                    else:
                        passed.append(binding)

                blocks_all = bool(failed)  # ALL_PROMOTED policy semantics
                new_exposure_allowed = snapshot.new_exposure_allowed and not blocks_all

                # ── prepare (resolve → runtime → outputs) ──
                prepared_by_binding: dict[tuple[str, str], Any] = {}
                for binding in passed:
                    symbol, timeframe = binding
                    try:
                        prepared = self.engine.prepare_promoted_strategy(
                            symbol=symbol,
                            timeframe=timeframe,
                            environment=env,
                            market_data_input=market_batch[binding],
                            portfolio_snapshot=snapshot,
                        )
                    except Exception as e:
                        logger.error("prepare[%s %s]: %s", symbol, timeframe, e)
                        prepared = None
                    if prepared is not None and prepared.prepare_status == "ok":
                        prepared_by_binding[binding] = prepared

                ok_prepared = list(prepared_by_binding.values())

                # ── ONE batch allocation → ONE target vector ──
                entries_by_symbol: dict[str, Any] = {}
                if ok_prepared:
                    requests = [
                        self.engine.build_allocation_request(p, snapshot)
                        for p in ok_prepared
                    ]
                    outcome = self.engine.portfolio_allocator.allocate_batch(
                        requests,
                        snapshot,
                        cash_reserve_pct=self.cash_reserve_pct,
                    )
                    cycle_id = uuid.uuid5(
                        uuid.NAMESPACE_URL,
                        f"pbt|{now.isoformat()}",
                    ).hex[:16]
                    vector = self.engine.portfolio_allocator.build_target_vector(
                        outcome, snapshot, cycle_id
                    )
                    entries_by_symbol = {e.symbol: e for e in outcome.entries}
                    targets = dict(vector.targets)
                    result.target_vectors.append(vector)
                    if not new_exposure_allowed and any(
                        e.approved > 0 for e in outcome.entries
                    ):
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
                        targets = dict(vector.targets)

                # ── finalize + plan (risk/planner reused) ──
                from trading_agent.authority.causation import CausationChain
                from trading_agent.authority.decision import (
                    TargetExposure as AuthTargetExposure,
                )

                plans: list[Any] = []
                for binding in sorted(prepared_by_binding):
                    prepared = prepared_by_binding[binding]
                    symbol, _timeframe = binding
                    entry = entries_by_symbol.get(symbol)
                    approved_pct = float(targets.get(symbol, 0.0))
                    if approved_pct <= 0 and (entry is None or entry.requested <= 0):
                        continue
                    if (
                        not new_exposure_allowed
                        and approved_pct > prepared.current_exposure
                    ):
                        continue
                    alloc_chain = (
                        getattr(entry, "causation_chain", None) if entry else None
                    )
                    links = tuple(prepared.causation_chain.links) + (
                        tuple(alloc_chain.links) if alloc_chain is not None else ()
                    )
                    combined = CausationChain(links=links)
                    approved_target = AuthTargetExposure(
                        target_exposure_pct=approved_pct,
                        max_new_exposure_pct=min(
                            approved_pct,
                            float(prepared.risk_decision.max_new_exposure),
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
                        if finalized is None:
                            continue
                        plan = self.engine.plan_pair_order(finalized)
                    except Exception as e:
                        logger.error("plan[%s]: %s", symbol, e)
                        continue
                    plans.append(plan)

                # ── full-batch preflight (REUSED verbatim) ──
                executable = [
                    p
                    for p in plans
                    if p.action in (PlannedAction.REDUCTION, PlannedAction.INCREASE)
                ]
                preflight = self.multipair.preflight_batch(executable, snapshot, vector)
                reductions, increases, atomic_status = (
                    self.multipair._apply_atomic_buy_policy(preflight, {})
                )
                if atomic_status == "atomic_blocked":
                    status = "atomic_blocked"
                elif not preflight.passed and not executable:
                    status = "blocked"

                # ── queue surviving orders → broker fills earliest t+1 ──
                for plan in reductions + increases:
                    side = "sell" if plan.action is PlannedAction.REDUCTION else "buy"
                    key = plan.idempotency_key or (
                        f"pbt_{now.strftime('%Y%m%d%H%M%S')}_{plan.symbol}"
                    )
                    queued_now += (
                        1
                        if broker.queue(
                            QueuedOrder(
                                idempotency_key=key,
                                symbol=plan.symbol,
                                side=side,
                                quantity=float(plan.quantity),
                                reference_price=float(
                                    plan.finalized.prepared.current_price
                                ),
                                decision_time=now,
                                phase=(
                                    "reduction"
                                    if plan.action is PlannedAction.REDUCTION
                                    else "increase"
                                ),
                            )
                        )
                        else 0
                    )

            # expire stuck orders that can never fill past data end? handled
            # naturally: they stay queued until timeline ends.
            equity_now = broker.equity(prices_now)
            result.equity_curve.append((now, equity_now))
            peak_equity = max(peak_equity, equity_now)
            dd = (peak_equity - equity_now) / peak_equity if peak_equity > 0 else 0.0
            result.drawdown_series.append((now, dd))
            result.exposure_history.append(
                (now, snapshot.gross_exposure, dict(snapshot.symbol_exposures))
            )
            step_fills = len([f for f in fills_now])
            result.steps.append(
                StepRecord(
                    step_time=now,
                    equity=equity_now,
                    cash=broker.cash,
                    gross_exposure=snapshot.gross_exposure,
                    symbol_exposures=dict(snapshot.symbol_exposures),
                    targets=dict(targets),
                    queued_orders=broker.pending_count,
                    fills_this_step=step_fills,
                    cycle_status=status,
                )
            )
            if status in {"blocked", "atomic_blocked"}:
                result.blocked_cycles += 1

        # final settle pass for anything still pending (fills up to last bar)
        if broker.pending_count:
            last_t = self.clock.timeline[-1]
            broker.settle(last_t + timedelta(seconds=1), self.clock, self.bindings)
            result.expired_orders = broker.pending_count

        # ── per-symbol contribution via EXACT cash-flow accounting ──
        # contribution(sym) = −Σ(buy outflows) + Σ(sell inflows) + qty_held×final_px
        # ⇒ Σ_sym contribution ≡ final_equity − initial_cash (identity holds
        #   to float precision; enforced by tests).
        for f in broker.fills:
            flow = -(f.notional + f.fee) if f.side == "buy" else (f.notional - f.fee)
            result.per_symbol_contribution[f.symbol] = (
                result.per_symbol_contribution.get(f.symbol, 0.0) + flow
            )
        prices_final = {
            sym: px
            for sym, tf in self.bindings
            if (px := self.clock.last_close((sym, tf), self.clock.timeline[-1]))
            is not None
        }
        for sym, pos in broker.positions.items():
            if pos["qty"] > 0:
                px = prices_final.get(sym, pos["avg_px"])
                result.per_symbol_contribution[sym] = (
                    result.per_symbol_contribution.get(sym, 0.0) + pos["qty"] * px
                )

        result.trades = list(broker.fills)
        result.turnover_history = [(f.fill_time, f.notional) for f in broker.fills]
        result.cost_history = [
            (f.fill_time, f.fee + f.slippage_cost) for f in broker.fills
        ]
        result.final_equity = broker.equity(prices_final)
        return result
