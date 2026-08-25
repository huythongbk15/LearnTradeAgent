"""MultiPairRuntime — unified multi-pair execution over promoted artifacts.

Milestone C: ONE runtime loop that trades N pairs through the SAME canonical
authority chain used for a single pair:

    resolver.list_bindings(env)          → what CAN be traded (promotion store)
    PortfolioAllocator.reconcile()       → budgets corrected vs exchange truth
    engine.execute_promoted_strategy()   → per-pair canonical execution

Design rules:
- Sequential loop against ONE shared engine/exchange → shared portfolio truth.
  (Parallelism across pairs would race the shared PortfolioAllocator and
  exchange balances; correctness first.)
- Per-pair error isolation: one failing pair never aborts the cycle.
- Fail-closed: no binding discovery without an eligible promotion record;
  no execution without market data.
- Every allocation decision happens AFTER reconcile() — no stale budget.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Callable

logger = logging.getLogger(__name__)

# Provider returns OHLCV DataFrame (polars/pandas-like with
# timestamp/open/high/low/close/volume) for (symbol, timeframe), or None.
MarketDataProvider = Callable[[str, str], Any | None]


@dataclass(frozen=True)
class PairCycleResult:
    """Outcome of one pair's execution within a cycle."""

    symbol: str
    timeframe: str
    status: str  # ok | zero_allocation | blocked | no_data | error
    orders_count: int
    detail: str = ""


@dataclass(frozen=True)
class CycleReport:
    """Aggregated outcome of one multi-pair cycle."""

    environment: str
    started_at: datetime
    finished_at: datetime
    equity_before: float
    equity_after: float
    results: tuple[PairCycleResult, ...] = field(default_factory=tuple)

    @property
    def total_orders(self) -> int:
        return sum(r.orders_count for r in self.results)

    @property
    def errors(self) -> tuple[PairCycleResult, ...]:
        return tuple(r for r in self.results if r.status == "error")

    def to_dict(self) -> dict[str, Any]:
        return {
            "environment": self.environment,
            "started_at": self.started_at.isoformat(),
            "finished_at": self.finished_at.isoformat(),
            "equity_before": self.equity_before,
            "equity_after": self.equity_after,
            "total_orders": self.total_orders,
            "results": [
                {
                    "symbol": r.symbol,
                    "timeframe": r.timeframe,
                    "status": r.status,
                    "orders_count": r.orders_count,
                    "detail": r.detail,
                }
                for r in self.results
            ],
        }


class MultiPairRuntime:
    """Runs all promoted (symbol, timeframe) bindings through the authority chain."""

    def __init__(self, engine: Any):
        """
        Args:
            engine: ExecutionEngine wired with resolver + instrument_rules
                    (i.e., supports execute_promoted_strategy).
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

    # ── Binding discovery ──────────────────────────────────────────────

    def discover_bindings(self, environment: str | Any) -> list[tuple[str, str]]:
        """Enumerate tradeable (symbol, timeframe) bindings for environment."""
        env = (
            environment
            if not isinstance(environment, str)
            else __import__(
                "trading_agent.authority.config", fromlist=["Environment"]
            ).Environment(environment.lower())
        )
        return self.engine.resolver.list_bindings(env)

    # ── Shared portfolio reconciliation ────────────────────────────────

    def _live_symbol_exposures(self, equity: float) -> dict[str, float]:
        """symbol -> notional/equity from exchange positions (single truth)."""
        exposures: dict[str, float] = {}
        if equity <= 0:
            return exposures
        for pos in self.engine.exchange.get_all_positions():
            if not pos.is_active or pos.quantity <= 0:
                continue
            price = float(self.engine.exchange._last_price_cache.get(pos.symbol, 0.0))
            if price <= 0:
                logger.warning(
                    "reconcile: no cached price for %s — exposure counted as 0",
                    pos.symbol,
                )
                continue
            exposures[pos.symbol] = exposures.get(pos.symbol, 0.0) + (
                pos.quantity * price / equity
            )
        return exposures

    def reconcile_portfolio(self) -> dict[str, Any]:
        """Correct allocator budgets against live positions. Call once per cycle."""
        equity = float(self.engine.exchange.get_total_equity())
        live = self._live_symbol_exposures(equity)
        audit = self.engine.portfolio_allocator.reconcile(live)
        logger.info(
            "Portfolio reconcile: %d symbols held, audit=%s", len(live), audit
        )
        return audit

    # ── Observation building ───────────────────────────────────────────

    def _build_observation(self, symbol: str, timeframe: str, df: Any) -> Any:
        """EnrichedMarketObservation from the LAST CLOSED bar of df."""
        from trading_agent.execution.canonical.market_observation import (
            EnrichedMarketObservation,
        )

        tail = df.tail(1).to_dicts()[0]
        observed_at = datetime.now(UTC)

        bar_ts = tail.get("timestamp")
        if isinstance(bar_ts, datetime):
            bar_close_at = bar_ts if bar_ts.tzinfo else bar_ts.replace(tzinfo=UTC)
        else:
            bar_close_at = observed_at

        return EnrichedMarketObservation(
            symbol=symbol,
            observed_at=observed_at,
            open=float(tail["open"]),
            high=float(tail["high"]),
            low=float(tail["low"]),
            close=float(tail["close"]),
            volume=float(tail.get("volume", 0.0)),
            features={},
            timeframe=timeframe,
            bar_close_at=bar_close_at,
            is_closed=True,
            data_manifest_id="multipair_runtime",
            feature_artifact_id="multipair_runtime",
        )

    # ── Cycle ──────────────────────────────────────────────────────────

    def run_cycle(
        self,
        environment: str | Any = "paper",
        market_data_provider: MarketDataProvider | None = None,
        bindings_override: list[tuple[str, str]] | None = None,
    ) -> CycleReport:
        """Execute ONE full cycle across all eligible pairs.

        Args:
            environment: runtime environment (str or Environment enum).
            market_data_provider: callable(symbol, timeframe) -> OHLCV df|None.
                Required — fail-closed: no fabricated data.
            bindings_override: explicit subset; default = discover_bindings().
        """
        started_at = datetime.now(UTC)
        env_name = (
            environment.lower()
            if isinstance(environment, str)
            else environment.value
        )

        if market_data_provider is None:
            raise ValueError("market_data_provider is required (fail-closed)")

        equity_before = float(self.engine.exchange.get_total_equity())

        # 1. Reconcile shared portfolio BEFORE any allocation this cycle
        try:
            self.reconcile_portfolio()
        except Exception as e:
            logger.error("Portfolio reconcile failed: %s", e, exc_info=True)

        # 2. Discover bindings (or use explicit override)
        bindings = (
            list(bindings_override)
            if bindings_override is not None
            else self.discover_bindings(environment)
        )
        results: list[PairCycleResult] = []

        # 3. Sequential loop — shared engine, deterministic order
        for symbol, timeframe in sorted(bindings):
            result = self._execute_pair(
                symbol=symbol,
                timeframe=timeframe,
                environment=environment,
                provider=market_data_provider,
            )
            results.append(result)

        finished_at = datetime.now(UTC)
        equity_after = float(self.engine.exchange.get_total_equity())

        return CycleReport(
            environment=env_name,
            started_at=started_at,
            finished_at=finished_at,
            equity_before=equity_before,
            equity_after=equity_after,
            results=tuple(results),
        )

    def _execute_pair(
        self,
        symbol: str,
        timeframe: str,
        environment: str | Any,
        provider: MarketDataProvider,
    ) -> PairCycleResult:
        """Run one pair through the canonical chain with error isolation."""
        try:
            df = provider(symbol, timeframe)
            if df is None or len(df) == 0:
                return PairCycleResult(
                    symbol=symbol,
                    timeframe=timeframe,
                    status="no_data",
                    orders_count=0,
                    detail="provider returned no market data",
                )

            observation = self._build_observation(symbol, timeframe, df)

            # Seed fresh price so DecisionAuthority sees a tradable quote
            close_price = float(observation.close)
            self.engine.update_prices({symbol: close_price})

            orders = self.engine.execute_promoted_strategy(
                symbol=symbol,
                timeframe=timeframe,
                environment=environment,
                observation=observation,
                market_data=df,
            )
            return PairCycleResult(
                symbol=symbol,
                timeframe=timeframe,
                status="ok",
                orders_count=len(orders),
            )
        except Exception as e:
            logger.error(
                "Pair %s %s failed: %s", symbol, timeframe, e, exc_info=True
            )
            return PairCycleResult(
                symbol=symbol,
                timeframe=timeframe,
                status="error",
                orders_count=0,
                detail=str(e)[:500],
            )


__all__ = ["MultiPairRuntime", "CycleReport", "PairCycleResult"]
