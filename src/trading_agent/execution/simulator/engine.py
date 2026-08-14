"""MarketReplayEngine — event-driven Execution Simulator V2 orchestrator.

The engine replays OHLCV bars and drives orders through the versioned
fill/impact/fee models.  It is fully deterministic for a given seed and
market-data manifest.

Event loop per bar:

1. Build the book from the bar open (+ previous bar volume) — no look-ahead.
2. Let resting limit orders attempt passive fills.
3. Ask the strategy/order provider for new order intents (at bar open).
4. Submit orders through the latency model (network + submission).
5. Fill market orders against the book (sweep), place limit orders.
6. Apply fees, ledger accounting, impact and adverse selection.
7. Mark to market at bar close; decay impact.

An order provider is any callable ``(bar_index, engine) -> list[OrderIntent]``.
"""

from __future__ import annotations

import hashlib
import json
import random
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Callable

import polars as pl

from trading_agent.execution.simulator.fee_model import FeeModel
from trading_agent.execution.simulator.fill_model import FillModel, FillOutcome
from trading_agent.execution.simulator.impact_model import ImpactModel
from trading_agent.execution.simulator.ledger import ExecutionLedger
from trading_agent.execution.simulator.metrics import (
    ExecutionMetrics,
    attribution_report,
    compute_execution_metrics,
)
from trading_agent.execution.simulator.models import (
    Fill,
    OrderIntent,
    OrderResult,
    RejectReason,
    SimOrderStatus,
    SimOrderType,
    SimSide,
    SimulationConfig,
    quantize_qty,
)
from trading_agent.execution.simulator.orderbook import (
    OrderBookState,
    build_book_from_bar,
)
from trading_agent.execution.simulator.versions import model_versions


@dataclass
class SimulatedExecutionResult:
    """Complete, versioned result of a simulation run."""

    symbol: str
    config: SimulationConfig
    ledger: ExecutionLedger
    metrics: ExecutionMetrics
    equity_curve: list[float]
    fills: list[Fill]
    order_results: list[OrderResult]
    model_versions: dict[str, str] = field(default_factory=model_versions)
    data_manifest: str = ""
    theoretical_alpha_pnl: float = 0.0
    raw: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "config": self.config.to_dict(),
            "model_versions": self.model_versions,
            "data_manifest": self.data_manifest,
            "ledger": self.ledger.snapshot(),
            "metrics": self.metrics.to_dict(),
            "equity_curve": self.equity_curve,
            "fills": [f.to_dict() for f in self.fills],
            "orders": [o.to_dict() for o in self.order_results],
            "theoretical_alpha_pnl": self.theoretical_alpha_pnl,
            "raw": self.raw,
        }


class MarketReplayEngine:
    """Event-driven, deterministic execution simulator."""

    def __init__(
        self,
        df: pl.DataFrame,
        *,
        config: SimulationConfig,
        symbol: str = "",
        initial_cash: float = 10_000.0,
    ) -> None:
        required = {"timestamp", "open", "high", "low", "close", "volume"}
        missing = required - set(df.columns)
        if missing:
            raise ValueError(f"DataFrame missing OHLCV columns: {sorted(missing)}")
        if len(df) < 2:
            raise ValueError("at least 2 bars required for the simulator")
        config.validate()
        if initial_cash <= 0:
            raise ValueError(f"initial_cash must be positive, got {initial_cash}")

        self.df = df.sort("timestamp")
        self.config = config
        self.symbol = symbol or "SIM"
        self.initial_cash = initial_cash

        # Deterministic RNGs (never the global RNG).
        self._rng = random.Random(f"engine:{config.random_seed}")

        self.fill_model = FillModel(config)
        self.impact_model = ImpactModel(config)
        self.fee_model = FeeModel(config)
        self.ledger = ExecutionLedger(
            symbol=self.symbol, initial_cash_quote=initial_cash
        )

        # Runtime state.
        self.current_book: OrderBookState | None = None
        self._resting_limits: dict[str, OrderIntent] = {}
        self._current_impact_bps: float = 0.0
        self._pending_submissions: dict[str, tuple[OrderIntent, datetime]] = {}
        self._order_ids: set[str] = set()
        self._order_seq = 0

        self._opens = self.df["open"].to_numpy().astype(float)
        self._highs = self.df["high"].to_numpy().astype(float)
        self._lows = self.df["low"].to_numpy().astype(float)
        self._closes = self.df["close"].to_numpy().astype(float)
        self._volumes = self.df["volume"].to_numpy().astype(float)
        self._timestamps = self.df["timestamp"].to_list()

        if not self.config.market_data_manifest:
            self.config = SimulationConfig(
                **{
                    **self.config.fingerprint_dict(),
                    "market_data_manifest": self._compute_manifest(),
                    "random_seed": self.config.random_seed,
                }
            )
        self.data_manifest = self.config.market_data_manifest

    # ── Manifest ────────────────────────────────────────────────────────

    def _compute_manifest(self) -> str:
        """sha256 of the OHLCV payload (values only, stable ordering)."""
        df = self.df.sort("timestamp")
        payload = json.dumps(
            [
                [str(t), o, h, low, c, v]
                for t, o, h, low, c, v in zip(
                    df["timestamp"].to_list(),
                    df["open"].to_list(),
                    df["high"].to_list(),
                    df["low"].to_list(),
                    df["close"].to_list(),
                    df["volume"].to_list(),
                )
            ],
            separators=(",", ":"),
        ).encode()
        return hashlib.sha256(payload).hexdigest()

    # ── Order id ────────────────────────────────────────────────────────

    def _next_order_id(self) -> str:
        while True:
            self._order_seq += 1
            oid = f"sim_{self._order_seq:06d}"
            if oid not in self._order_ids:
                return oid

    def _volatility_bps(self, i: int) -> float:
        """Per-bar realized volatility in bps from the previous bar's range."""
        if i <= 0:
            return 20.0  # conservative default for the first bar
        hi, lo, prev_close = self._highs[i - 1], self._lows[i - 1], self._closes[i - 1]
        if prev_close <= 0 or hi <= 0 or lo <= 0:
            return 20.0
        return max((hi - lo) / prev_close * 10_000.0, 1.0)

    # Public accessors for execution algorithms (Wave D).  These are the only
    # market-observation channels a slice-selection algorithm should use.

    def bar_volume(self, bar_index: int) -> float:
        """Observed volume of a bar (clamped to the valid range)."""
        if bar_index < 0:
            bar_index = 0
        if bar_index >= len(self._volumes):
            bar_index = len(self._volumes) - 1
        return float(self._volumes[bar_index])

    def volatility_bps(self, bar_index: int) -> float:
        """Realized volatility estimate for a bar (public alias)."""
        return self._volatility_bps(bar_index)

    def market_snapshot(self, bar_index: int):
        """Current book-derived market snapshot for algorithm decisions."""
        from trading_agent.execution.algorithms.base import MarketSnapshot

        book = self.current_book
        if book is None:
            raise RuntimeError("no current book (engine not started)")
        return MarketSnapshot(
            mid=book.mid,
            spread_bps=book.spread_bps(),
            bid_depth=book.total_bid_size(),
            ask_depth=book.total_ask_size(),
            recent_volume=self.bar_volume(bar_index),
            volatility_bps=self.volatility_bps(bar_index),
        )

    # ── Main loop ───────────────────────────────────────────────────────

    def run(
        self,
        order_provider: Callable[[int, "MarketReplayEngine"], list[OrderIntent]],
        *,
        bars_per_year: float = 365.25 * 24,
    ) -> SimulatedExecutionResult:
        """Replay all bars and return a versioned result."""
        equity_curve: list[float] = []
        n = len(self.df)

        for i in range(n):
            ts = self._timestamps[i]
            book = build_book_from_bar(
                symbol=self.symbol,
                open_price=self._opens[i],
                previous_volume=self._volumes[i - 1] if i > 0 else self._volumes[i],
                config=self.config,
                sequence=i,
                timestamp=ts
                if isinstance(ts, datetime)
                else datetime.fromisoformat(str(ts)).replace(tzinfo=UTC),
            )
            self.current_book = book

            # 1. Resting limit orders attempt fills at this bar.
            self._process_resting_limits(i)

            # 2. Strategy intents at this bar's open.
            intents = order_provider(i, self) or []
            for intent in intents:
                self._submit(intent, i)

            # 3. Market orders (now acknowledged) sweep the book.
            self._process_pending_market(i)

            # 4. Mark to market at close.
            equity_curve.append(self.ledger.equity_at_mid(self._closes[i]))

            # 5. Decay impact into the next bar.
            self._current_impact_bps = self.impact_model.decay(
                self._current_impact_bps, 1
            )

        # Finalize metrics.
        metrics = compute_execution_metrics(
            self.ledger,
            config=self.config,
            equity_curve=equity_curve,
            bars_per_year=bars_per_year,
        )
        # Theoretical alpha PnL is provided by the caller when a strategy
        # bridge is used; attribution is finalized there.
        result = SimulatedExecutionResult(
            symbol=self.symbol,
            config=self.config,
            ledger=self.ledger,
            metrics=metrics,
            equity_curve=equity_curve,
            fills=list(self.ledger.fills),
            order_results=list(self.ledger.order_results.values()),
            data_manifest=self.data_manifest,
            raw={"bars": n, "resting_limits_at_end": len(self._resting_limits)},
        )
        return result

    # ── Submission / processing ─────────────────────────────────────────

    def _submit(self, intent: OrderIntent, bar_index: int) -> None:
        """Accept an order intent; apply latency; queue for execution."""
        if intent.order_id in self._order_ids:
            raise ValueError(
                f"duplicate order id {intent.order_id!r} (idempotency guard)"
            )
        self._order_ids.add(intent.order_id)

        qty = quantize_qty(intent.quantity, self.config.step_size)
        if qty <= 0:
            self._reject(intent, RejectReason.BELOW_MIN_QTY, bar_index)
            return
        if self.config.min_qty > 0 and qty < self.config.min_qty:
            self._reject(intent, RejectReason.BELOW_MIN_QTY, bar_index)
            return

        # Latency model: submission is delayed by network + submit latency.
        latency_s = (
            self.config.network_latency_ms + self.config.submit_latency_ms
        ) / 1000.0
        if intent.submit_latency_override_ms is not None:
            latency_s = intent.submit_latency_override_ms / 1000.0
        submit_ts = self._bar_ts(bar_index) + timedelta(seconds=latency_s)

        arrival_price = self.current_book.mid if self.current_book else None
        result = OrderResult(
            order_id=intent.order_id,
            intent=intent,
            status=SimOrderStatus.PENDING,
            submit_time=submit_ts,
            arrival_price=arrival_price,
            decision_price=arrival_price,  # updated by the strategy bridge if known
        )
        self.ledger.order_results[intent.order_id] = result

        if intent.order_type == SimOrderType.MARKET:
            # Market orders execute on ack (submit + ack latency).
            self._pending_submissions[intent.order_id] = (intent, submit_ts)
        else:
            # Limit orders rest at their limit price.
            self._resting_limits[intent.order_id] = intent
            result.status = SimOrderStatus.SUBMITTED
            result.submit_price = arrival_price

    def _process_pending_market(self, bar_index: int) -> None:
        """Fill acknowledged market orders against the current book."""
        ack_s = self.config.ack_latency_ms / 1000.0
        book = self.current_book
        if book is None:
            return
        for order_id, (intent, submit_ts) in list(self._pending_submissions.items()):
            del self._pending_submissions[order_id]
            result = self.ledger.order_results[order_id]

            # Fail closed: stale quote or sequence gap.
            if self.fill_model.check_stale(book, submit_ts.timestamp()):
                self._reject(intent, RejectReason.STALE_QUOTE, bar_index)
                continue
            if self.fill_model.check_sequence_gap(book):
                self._reject(intent, RejectReason.SEQUENCE_GAP, bar_index)
                continue

            # Min notional check at arrival price.
            if self.config.min_notional > 0 and book.mid > 0:
                if intent.quantity * book.mid < self.config.min_notional:
                    self._reject(intent, RejectReason.MIN_NOTIONAL, bar_index)
                    continue

            # Balance/inventory pre-checks (fail closed).
            if intent.side == SimSide.BUY:
                est_fee = intent.quantity * book.mid * self.config.taker_fee
                if not self.ledger.can_afford(
                    SimSide.BUY, intent.quantity, book.mid, est_fee
                ):
                    self._reject(intent, RejectReason.INSUFFICIENT_CASH, bar_index)
                    continue
            else:
                if not self.ledger.has_inventory(intent.quantity):
                    self._reject(intent, RejectReason.INSUFFICIENT_INVENTORY, bar_index)
                    continue

            impact = self.impact_model.temporary_impact_bps(
                intent.quantity,
                intent.side,
                book,
                self._volatility_bps(bar_index),
                self._current_impact_bps,
            )
            self._current_impact_bps = impact

            outcome = self.fill_model.fill_market(
                intent,
                book,
                bar_index,
                submit_ts + timedelta(seconds=ack_s),
                impact_bps=impact,
            )
            self._apply_outcome(intent, outcome, bar_index, is_maker=False)

    def _process_resting_limits(self, bar_index: int) -> None:
        """Attempt passive fills for resting limit orders at this bar."""
        book = self.current_book
        if book is None:
            return
        for order_id, intent in list(self._resting_limits.items()):
            result = self.ledger.order_results[order_id]
            outcome = self.fill_model.fill_limit(
                intent, book, bar_index, self._bar_ts(bar_index)
            )
            if outcome.status == SimOrderStatus.FILLED:
                del self._resting_limits[order_id]
                self._apply_outcome(intent, outcome, bar_index, is_maker=True)
            elif outcome.status == SimOrderStatus.REJECTED:
                del self._resting_limits[order_id]
                self._apply_outcome(intent, outcome, bar_index, is_maker=True)
            else:
                result.queue_approx = outcome.queue_approx

    def _apply_outcome(
        self,
        intent: OrderIntent,
        outcome: FillOutcome,
        bar_index: int,
        *,
        is_maker: bool,
    ) -> None:
        """Apply fills + fees to the ledger, record the order result."""
        result = self.ledger.order_results[intent.order_id]
        if outcome.status == SimOrderStatus.REJECTED:
            result.status = SimOrderStatus.REJECTED
            result.reject_reason = outcome.reject_reason
            self.ledger.record_order(result)
            return

        for fill in outcome.fills:
            fee = self.fee_model.compute_fee(fill, is_maker=is_maker)
            fill.fee = fee
            # Adverse selection: post-fill mid windows.
            adverse = self.impact_model.adverse_selection_bps(
                fill, aggressor_aggressive=not is_maker
            )
            windows = self.impact_model.post_fill_mid_windows(
                fill,
                self._highs[bar_index],
                self._lows[bar_index],
                adverse,
            )
            fill.mid_after = windows["mid_t+30s"]
            self.ledger.apply_fill(fill, fee)

        result.fills = list(outcome.fills)
        result.status = outcome.status
        result.reject_reason = outcome.reject_reason
        result.fill_vwap = result.avg_fill_price
        if result.fills:
            result.first_fill_time = result.fills[0].timestamp
            result.post_fill_mid = result.fills[-1].mid_after
        if result.submit_price is None:
            result.submit_price = result.arrival_price
        self.ledger.record_order(result)

    def _reject(
        self, intent: OrderIntent, reason: RejectReason, bar_index: int
    ) -> None:
        result = self.ledger.order_results.get(intent.order_id)
        if result is None:
            result = OrderResult(
                order_id=intent.order_id, intent=intent, status=SimOrderStatus.REJECTED
            )
            self.ledger.order_results[intent.order_id] = result
        result.status = SimOrderStatus.REJECTED
        result.reject_reason = reason
        if result.arrival_price is None and self.current_book is not None:
            result.arrival_price = self.current_book.mid
        self.ledger.record_order(result)

    def cancel_order(self, order_id: str, bar_index: int) -> OrderResult | None:
        """Cancel a resting limit order (with cancel latency)."""
        if order_id not in self._resting_limits:
            return None
        intent = self._resting_limits.pop(order_id)
        result = self.ledger.order_results[order_id]
        cancel_s = self.config.cancel_latency_ms / 1000.0
        result.status = SimOrderStatus.CANCELED
        result.cancel_time = self._bar_ts(bar_index) + timedelta(seconds=cancel_s)
        self.ledger.record_order(result)
        return result

    # ── Helpers ─────────────────────────────────────────────────────────

    def _bar_ts(self, bar_index: int) -> datetime:
        ts = self._timestamps[bar_index]
        if isinstance(ts, datetime):
            return ts
        return datetime.fromisoformat(str(ts)).replace(tzinfo=UTC)


def _engine_fixed_size(
    equity: float,
    price: float,
    atr: float | None,
    fraction: float = 0.1,
    max_position_pct: float = 1.0,
) -> float:
    """Mirror the vectorized BacktestEngine' s fixed sizing exactly.

    ``fraction`` is a *risk budget* (equity × fraction) divided by 2×ATR,
    capped by max position pct and a 5 % affordability buffer.  Keeping this
    identical lets the simulator produce a Reality Gap that isolates
    *execution* effects, not sizing differences.
    """
    risk_amount = equity * fraction
    if atr and atr > 0:
        position_size = risk_amount / (atr * 2.0)
    else:
        position_size = risk_amount / price
    position_size = min(position_size, equity * max_position_pct / price)
    position_size = min(position_size, equity * 0.95 / price)
    return max(position_size, 0.0)


def run_strategy_through_simulator(
    strategy,
    df: pl.DataFrame,
    *,
    symbol: str = "",
    timeframe: str = "1h",
    initial_cash: float = 10_000.0,
    config: SimulationConfig | None = None,
    fixed_position_pct: float = 0.1,
    bars_per_year: float = 365.25 * 24,
) -> SimulatedExecutionResult:
    """Run a strategy's signals through the Execution Simulator V2.

    Timing and sizing mirror the vectorized BacktestEngine exactly (signal at
    bar t → order at bar t+1 open; risk-budget sizing = equity×pct / (2×ATR),
    capped to 95 % of equity and max position pct) so the Reality Gap between
    idealized backtest and simulated execution isolates *execution* effects.

    Returns the simulation result with ``theoretical_alpha_pnl`` set from the
    idealized round-trip at bar-open mid prices with zero costs.
    """
    if config is None:
        config = SimulationConfig(random_seed=42)

    df = df.sort("timestamp")
    df = strategy.compute_indicators(df)
    signals = strategy.generate_signals(df)
    df = df.with_columns(signals.alias("signal"))

    signal_arr = df["signal"].to_numpy()
    closes = df["close"].to_numpy().astype(float)
    opens = df["open"].to_numpy().astype(float)
    atr_series = df["atr"].to_numpy() if "atr" in df.columns else None
    n = len(df)

    engine = MarketReplayEngine(
        df, config=config, symbol=symbol or strategy.name, initial_cash=initial_cash
    )

    def _atr(i: int) -> float | None:
        if atr_series is None:
            return None
        v = atr_series[i - 1] if i > 0 else None
        try:
            fv = float(v)
        except (TypeError, ValueError):
            return None
        return fv if fv > 0 else None

    def provider(i: int, eng: MarketReplayEngine) -> list[OrderIntent]:
        intents: list[OrderIntent] = []
        if i == 0:
            return intents
        prev_signal = signal_arr[i - 1]
        book = eng.current_book
        mid = book.mid if book else float(closes[i - 1])
        prev_close = float(closes[i - 1])
        open_qty = eng.ledger.inventory_base
        if prev_signal == 1 and open_qty <= 0:
            equity = eng.ledger.equity_at_mid(prev_close)
            qty = _engine_fixed_size(equity, mid, _atr(i), fraction=fixed_position_pct)
            affordable = eng.ledger.cash_quote / mid if mid > 0 else 0.0
            qty = min(qty, affordable)
            if qty > 0:
                intents.append(
                    OrderIntent(
                        order_id=eng._next_order_id(),
                        side=SimSide.BUY,
                        order_type=SimOrderType.MARKET,
                        quantity=qty,
                        metadata={"signal": float(prev_signal), "bar": i},
                    )
                )
        elif prev_signal == -1 and open_qty > 0:
            intents.append(
                OrderIntent(
                    order_id=eng._next_order_id(),
                    side=SimSide.SELL,
                    order_type=SimOrderType.MARKET,
                    quantity=open_qty,
                    metadata={"signal": float(prev_signal), "bar": i},
                )
            )
        return intents

    result = engine.run(provider, bars_per_year=bars_per_year)

    # Theoretical alpha PnL: idealized round-trip at bar-open mid prices with
    # zero costs — the same timing/sizing as the BacktestEngine with
    # commission/slippage/spread = 0.  The gap between this and the
    # simulator's realized PnL is the execution cost (spread + impact + fees
    # + delay + opportunity).
    theoretical_cash = float(initial_cash)
    theoretical_pos = 0.0
    for i in range(1, n):
        prev_signal = signal_arr[i - 1]
        open_mid = float(opens[i])
        if open_mid <= 0:
            continue
        if prev_signal == -1 and theoretical_pos > 0:
            theoretical_cash += theoretical_pos * open_mid
            theoretical_pos = 0.0
        elif prev_signal == 1 and theoretical_pos <= 0:
            prev_close = float(closes[i - 1])
            equity = theoretical_cash + theoretical_pos * prev_close
            qty = _engine_fixed_size(
                equity, open_mid, _atr(i), fraction=fixed_position_pct
            )
            affordable = theoretical_cash / open_mid
            qty = min(qty, affordable)
            theoretical_pos += qty
            theoretical_cash -= qty * open_mid
    theoretical_cash += theoretical_pos * float(closes[-1])
    result.theoretical_alpha_pnl = theoretical_cash - initial_cash
    attr = attribution_report(result.metrics, result.theoretical_alpha_pnl)
    result.metrics.attribution = attr
    return result
