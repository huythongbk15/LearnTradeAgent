"""
Auto-Rebalancer - Calendar & Threshold Based Rebalancing

Supports:
- Calendar-based rebalancing (daily, weekly, monthly, quarterly)
- Threshold-based rebalancing (drift tolerance bands)
- CPPI (Constant Proportion Portfolio Insurance)
- Transaction cost awareness
- Tax-lot optimization (future)
"""

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from enum import Enum
from typing import Any, Callable


from trading_agent.exchanges.models import Symbol, Position, Order, OrderSide, OrderType
from trading_agent.portfolio.risk_budgeting import RiskBudgeter, RiskBudgetMethod

logger = logging.getLogger(__name__)


class RebalanceTrigger(str, Enum):
    """What triggered the rebalance"""

    CALENDAR = "calendar"
    THRESHOLD = "threshold"
    MANUAL = "manual"
    CPPI = "cppi"
    RISK_BUDGET = "risk_budget"


@dataclass
class RebalanceConfig:
    """Configuration for rebalancing"""

    # Calendar settings
    calendar_enabled: bool = True
    calendar_frequency: str = "monthly"  # daily, weekly, monthly, quarterly
    calendar_day: int = 1  # Day of month for monthly, day of week for weekly (0=Mon)

    # Threshold settings
    threshold_enabled: bool = True
    threshold_band_pct: float = 0.05  # 5% drift tolerance
    threshold_check_interval: timedelta = timedelta(hours=1)

    # CPPI settings
    cppi_enabled: bool = False
    cppi_multiplier: float = 3.0
    cppi_floor_pct: float = 0.8  # 80% of peak

    # Risk budget settings
    risk_budget_enabled: bool = True
    risk_budget_method: RiskBudgetMethod = RiskBudgetMethod.EQUAL_RISK_CONTRIBUTION
    risk_budget_frequency: str = "monthly"

    # Execution settings
    min_trade_size: Decimal = Decimal("10")  # Minimum trade in quote currency
    max_turnover_pct: float = 0.5  # Max 50% portfolio turnover per rebalance
    transaction_cost_bps: float = 10.0  # 10 bps per trade
    allow_fractional: bool = True

    # Risk limits
    max_position_pct: float = 0.3  # Max 30% in single position
    max_leverage: float = 1.0


@dataclass
class RebalanceEvent:
    """Record of a rebalance event"""

    timestamp: datetime
    trigger: RebalanceTrigger
    target_weights: dict[Symbol, Decimal]
    current_weights: dict[Symbol, Decimal]
    trades: list[dict]  # symbol, side, size, price, cost
    turnover: Decimal
    estimated_cost: Decimal
    success: bool
    error: str | None = None


class RebalanceStrategy(ABC):
    """Abstract base for rebalancing strategies"""

    @abstractmethod
    async def calculate_target_weights(
        self,
        current_positions: dict[Symbol, Position],
        prices: dict[Symbol, Decimal],
        portfolio_value: Decimal,
        config: RebalanceConfig,
    ) -> dict[Symbol, Decimal]:
        pass

    @abstractmethod
    def should_rebalance(
        self,
        current_weights: dict[Symbol, Decimal],
        target_weights: dict[Symbol, Decimal],
        config: RebalanceConfig,
        last_rebalance: datetime | None,
    ) -> tuple[bool, RebalanceTrigger]:
        pass


class CalendarRebalanceStrategy(RebalanceStrategy):
    """Calendar-based rebalancing"""

    def __init__(self):
        self._last_rebalance: datetime | None = None

    async def calculate_target_weights(
        self,
        current_positions: dict[Symbol, Position],
        prices: dict[Symbol, Decimal],
        portfolio_value: Decimal,
        config: RebalanceConfig,
    ) -> dict[Symbol, Decimal]:
        # Equal weight by default, can be overridden
        n = len(current_positions)
        if n == 0:
            return {}
        weight = Decimal("1") / n
        return {sym: weight for sym in current_positions.keys()}

    def should_rebalance(
        self,
        current_weights: dict[Symbol, Decimal],
        target_weights: dict[Symbol, Decimal],
        config: RebalanceConfig,
        last_rebalance: datetime | None,
    ) -> tuple[bool, RebalanceTrigger]:
        if not config.calendar_enabled:
            return False, RebalanceTrigger.CALENDAR

        now = datetime.now()
        if last_rebalance is None:
            return True, RebalanceTrigger.CALENDAR

        freq = config.calendar_frequency
        if freq == "daily":
            return (now - last_rebalance).days >= 1, RebalanceTrigger.CALENDAR
        elif freq == "weekly":
            return (now - last_rebalance).days >= 7, RebalanceTrigger.CALENDAR
        elif freq == "monthly":
            # Check if we're past the calendar day in a new month
            if now.month != last_rebalance.month or now.year != last_rebalance.year:
                return now.day >= config.calendar_day, RebalanceTrigger.CALENDAR
            return False, RebalanceTrigger.CALENDAR
        elif freq == "quarterly":
            if (now.month - 1) // 3 != (
                last_rebalance.month - 1
            ) // 3 or now.year != last_rebalance.year:
                return now.day >= config.calendar_day, RebalanceTrigger.CALENDAR
            return False, RebalanceTrigger.CALENDAR

        return False, RebalanceTrigger.CALENDAR


class ThresholdRebalanceStrategy(RebalanceStrategy):
    """Threshold-based rebalancing (drift tolerance)"""

    def __init__(self):
        self._last_check: datetime | None = None

    async def calculate_target_weights(
        self,
        current_positions: dict[Symbol, Position],
        prices: dict[Symbol, Decimal],
        portfolio_value: Decimal,
        config: RebalanceConfig,
    ) -> dict[Symbol, Decimal]:
        # Use risk budgeting for target weights
        risk_budgeter = RiskBudgeter(method=config.risk_budget_method)
        # Need returns data - simplified: equal weight
        n = len(current_positions)
        if n == 0:
            return {}
        weight = Decimal("1") / n
        return {sym: weight for sym in current_positions.keys()}

    def should_rebalance(
        self,
        current_weights: dict[Symbol, Decimal],
        target_weights: dict[Symbol, Decimal],
        config: RebalanceConfig,
        last_rebalance: datetime | None,
    ) -> tuple[bool, RebalanceTrigger]:
        if not config.threshold_enabled:
            return False, RebalanceTrigger.THRESHOLD

        now = datetime.now()
        if (
            self._last_check
            and (now - self._last_check) < config.threshold_check_interval
        ):
            return False, RebalanceTrigger.THRESHOLD
        self._last_check = now

        # Check max drift
        max_drift = Decimal("0")
        for symbol, target in target_weights.items():
            current = current_weights.get(symbol, Decimal("0"))
            drift = abs(current - target)
            max_drift = max(max_drift, drift)

        return max_drift >= Decimal(
            str(config.threshold_band_pct)
        ), RebalanceTrigger.THRESHOLD


class CPPIRebalanceStrategy(RebalanceStrategy):
    """Constant Proportion Portfolio Insurance"""

    def __init__(self):
        self._peak_value: Decimal = Decimal("0")
        self._floor_value: Decimal = Decimal("0")

    async def calculate_target_weights(
        self,
        current_positions: dict[Symbol, Position],
        prices: dict[Symbol, Decimal],
        portfolio_value: Decimal,
        config: RebalanceConfig,
    ) -> dict[Symbol, Decimal]:
        # Update peak
        if portfolio_value > self._peak_value:
            self._peak_value = portfolio_value
            self._floor_value = self._peak_value * Decimal(str(config.cppi_floor_pct))

        # Calculate cushion
        cushion = portfolio_value - self._floor_value
        if cushion <= 0:
            # At or below floor - move to cash
            return {}

        # Risky allocation
        risky_allocation = cushion * Decimal(str(config.cppi_multiplier))
        risky_weight = min(risky_allocation / portfolio_value, Decimal("1"))

        # Distribute among risky assets (equal weight for now)
        n = len(current_positions)
        if n == 0:
            return {}

        weight_per_asset = risky_weight / n
        return {sym: weight_per_asset for sym in current_positions.keys()}

    def should_rebalance(
        self,
        current_weights: dict[Symbol, Decimal],
        target_weights: dict[Symbol, Decimal],
        config: RebalanceConfig,
        last_rebalance: datetime | None,
    ) -> tuple[bool, RebalanceTrigger]:
        if not config.cppi_enabled:
            return False, RebalanceTrigger.CPPI

        # CPPI rebalances when cushion changes significantly
        # Check daily
        if last_rebalance is None:
            return True, RebalanceTrigger.CPPI
        return (datetime.now() - last_rebalance).days >= 1, RebalanceTrigger.CPPI


class RiskBudgetRebalanceStrategy(RebalanceStrategy):
    """Risk budget based rebalancing"""

    def __init__(self):
        self._last_rebalance: datetime | None = None

    async def calculate_target_weights(
        self,
        current_positions: dict[Symbol, Position],
        prices: dict[Symbol, Decimal],
        portfolio_value: Decimal,
        config: RebalanceConfig,
    ) -> dict[Symbol, Decimal]:
        # Would use actual returns data in production
        # For now, equal weight
        n = len(current_positions)
        if n == 0:
            return {}
        weight = Decimal("1") / n
        return {sym: weight for sym in current_positions.keys()}

    def should_rebalance(
        self,
        current_weights: dict[Symbol, Decimal],
        target_weights: dict[Symbol, Decimal],
        config: RebalanceConfig,
        last_rebalance: datetime | None,
    ) -> tuple[bool, RebalanceTrigger]:
        if not config.risk_budget_enabled:
            return False, RebalanceTrigger.RISK_BUDGET

        now = datetime.now()
        if last_rebalance is None:
            return True, RebalanceTrigger.RISK_BUDGET

        freq = config.risk_budget_frequency
        if freq == "monthly":
            return (
                now.month != last_rebalance.month or now.year != last_rebalance.year,
                RebalanceTrigger.RISK_BUDGET,
            )
        elif freq == "quarterly":
            return (now.month - 1) // 3 != (
                last_rebalance.month - 1
            ) // 3 or now.year != last_rebalance.year, RebalanceTrigger.RISK_BUDGET

        return False, RebalanceTrigger.RISK_BUDGET


class AutoRebalancer:
    """
    Main Auto-Rebalancer

    Combines multiple rebalancing strategies:
    - Calendar-based
    - Threshold-based (drift tolerance)
    - CPPI (downside protection)
    - Risk Budget (periodic optimization)

    Features:
    - Transaction cost awareness
    - Turnover limits
    - Minimum trade size enforcement
    - Execution integration ready
    """

    def __init__(
        self,
        config: RebalanceConfig | None = None,
        execute_callback: Callable[[list[Order]], Any] | None = None,
    ):
        self.config = config or RebalanceConfig()
        self.execute_callback = execute_callback

        # Strategies
        self._strategies: dict[RebalanceTrigger, RebalanceStrategy] = {
            RebalanceTrigger.CALENDAR: CalendarRebalanceStrategy(),
            RebalanceTrigger.THRESHOLD: ThresholdRebalanceStrategy(),
            RebalanceTrigger.CPPI: CPPIRebalanceStrategy(),
            RebalanceTrigger.RISK_BUDGET: RiskBudgetRebalanceStrategy(),
        }

        self._last_rebalance: datetime | None = None
        self._rebalance_history: list[RebalanceEvent] = []
        self._enabled = True

    def enable(self) -> None:
        self._enabled = True

    def disable(self) -> None:
        self._enabled = False

    def is_enabled(self) -> bool:
        return self._enabled

    async def check_and_rebalance(
        self,
        positions: dict[Symbol, Position],
        prices: dict[Symbol, Decimal],
    ) -> RebalanceEvent | None:
        """Check all strategies and rebalance if needed"""
        if not self._enabled:
            return None

        portfolio_value = sum(p.notional for p in positions.values())
        if portfolio_value == 0:
            return None

        current_weights = self._calculate_weights(positions, prices, portfolio_value)

        # Check each strategy
        for trigger, strategy in self._strategies.items():
            should, actual_trigger = strategy.should_rebalance(
                current_weights, {}, self.config, self._last_rebalance
            )

            if should:
                return await self._execute_rebalance(
                    positions, prices, portfolio_value, current_weights, actual_trigger
                )

        return None

    async def force_rebalance(
        self,
        positions: dict[Symbol, Position],
        prices: dict[Symbol, Decimal],
        trigger: RebalanceTrigger = RebalanceTrigger.MANUAL,
    ) -> RebalanceEvent:
        """Force a rebalance"""
        portfolio_value = sum(p.notional for p in positions.values())
        current_weights = self._calculate_weights(positions, prices, portfolio_value)
        return await self._execute_rebalance(
            positions, prices, portfolio_value, current_weights, trigger
        )

    async def _execute_rebalance(
        self,
        positions: dict[Symbol, Position],
        prices: dict[Symbol, Decimal],
        portfolio_value: Decimal,
        current_weights: dict[Symbol, Decimal],
        trigger: RebalanceTrigger,
    ) -> RebalanceEvent:
        """Execute the rebalance"""
        strategy = self._strategies.get(
            trigger, self._strategies[RebalanceTrigger.CALENDAR]
        )
        target_weights = await strategy.calculate_target_weights(
            positions, prices, portfolio_value, self.config
        )

        # Generate trades
        trades = self._generate_trades(
            positions, prices, portfolio_value, current_weights, target_weights
        )

        # Apply constraints
        trades = self._apply_constraints(trades, portfolio_value)

        if not trades:
            return RebalanceEvent(
                timestamp=datetime.now(),
                trigger=trigger,
                target_weights=target_weights,
                current_weights=current_weights,
                trades=[],
                turnover=Decimal("0"),
                estimated_cost=Decimal("0"),
                success=True,
            )

        # Create orders
        orders = self._create_orders(trades, prices)

        # Execute if callback provided
        success = True
        error = None
        if self.execute_callback:
            try:
                await self.execute_callback(orders)
            except Exception as e:
                success = False
                error = str(e)
                logger.error(f"Rebalance execution failed: {e}")

        # Calculate metrics
        turnover = sum(abs(t["size"] * t["price"]) for t in trades)
        estimated_cost = turnover * Decimal(
            str(self.config.transaction_cost_bps / 10000)
        )

        event = RebalanceEvent(
            timestamp=datetime.now(),
            trigger=trigger,
            target_weights=target_weights,
            current_weights=current_weights,
            trades=trades,
            turnover=turnover,
            estimated_cost=estimated_cost,
            success=success,
            error=error,
        )

        self._last_rebalance = event.timestamp
        self._rebalance_history.append(event)

        logger.info(
            f"Rebalance executed: trigger={trigger.value}, trades={len(trades)}, "
            f"turnover={turnover:.2f}, cost={estimated_cost:.2f}, success={success}"
        )

        return event

    def _calculate_weights(
        self,
        positions: dict[Symbol, Position],
        prices: dict[Symbol, Decimal],
        portfolio_value: Decimal,
    ) -> dict[Symbol, Decimal]:
        weights = {}
        for symbol, pos in positions.items():
            price = prices.get(symbol, pos.mark_price)
            weights[symbol] = (
                (pos.size * price) / portfolio_value
                if portfolio_value > 0
                else Decimal("0")
            )
        return weights

    def _generate_trades(
        self,
        positions: dict[Symbol, Position],
        prices: dict[Symbol, Decimal],
        portfolio_value: Decimal,
        current_weights: dict[Symbol, Decimal],
        target_weights: dict[Symbol, Decimal],
    ) -> list[dict]:
        trades = []
        all_symbols = set(current_weights.keys()) | set(target_weights.keys())

        for symbol in all_symbols:
            current_w = current_weights.get(symbol, Decimal("0"))
            target_w = target_weights.get(symbol, Decimal("0"))
            diff = target_w - current_w

            if abs(diff) < Decimal("0.001"):  # 0.1% threshold
                continue

            price = prices.get(
                symbol,
                positions.get(
                    symbol,
                    Position(
                        symbol=symbol,
                        size=Decimal(0),
                        entry_price=Decimal(0),
                        mark_price=Decimal(0),
                    ),
                ).mark_price,
            )

            if price == 0:
                continue

            # Size in quote currency
            trade_value = diff * portfolio_value
            trade_size = trade_value / price

            # Minimum trade size check
            if abs(trade_value) < self.config.min_trade_size:
                continue

            side = OrderSide.BUY if trade_size > 0 else OrderSide.SELL
            trades.append(
                {
                    "symbol": symbol,
                    "side": side,
                    "size": abs(trade_size),
                    "price": price,
                    "value": abs(trade_value),
                    "weight_diff": float(diff),
                }
            )

        return trades

    def _apply_constraints(
        self, trades: list[dict], portfolio_value: Decimal
    ) -> list[dict]:
        # Max turnover
        total_turnover = sum(t["value"] for t in trades)
        max_turnover = portfolio_value * Decimal(str(self.config.max_turnover_pct))

        if total_turnover > max_turnover:
            # Scale down proportionally
            scale = max_turnover / total_turnover
            for t in trades:
                t["size"] *= scale
                t["value"] *= scale

        # Max position size check would go here
        # (need to simulate post-trade weights)

        return trades

    def _create_orders(
        self, trades: list[dict], prices: dict[Symbol, Decimal]
    ) -> list[Order]:
        orders = []
        for t in trades:
            order = Order(
                id=f"rebal_{t['symbol'].base}_{datetime.now().timestamp()}",
                symbol=t["symbol"],
                side=t["side"],
                type=OrderType.MARKET,  # Could use LIMIT with slippage buffer
                size=t["size"],
            )
            orders.append(order)
        return orders

    def get_history(self, limit: int = 100) -> list[RebalanceEvent]:
        return self._rebalance_history[-limit:]

    def get_last_rebalance(self) -> datetime | None:
        return self._last_rebalance

    def get_status(self) -> dict:
        return {
            "enabled": self._enabled,
            "last_rebalance": self._last_rebalance.isoformat()
            if self._last_rebalance
            else None,
            "total_rebalances": len(self._rebalance_history),
            "config": {
                "calendar": self.config.calendar_enabled,
                "threshold": self.config.threshold_enabled,
                "cppi": self.config.cppi_enabled,
                "risk_budget": self.config.risk_budget_enabled,
            },
        }


# Convenience function
async def create_rebalancer(
    config: RebalanceConfig | None = None,
    execute_callback: Callable[[list[Order]], Any] | None = None,
) -> AutoRebalancer:
    return AutoRebalancer(config, execute_callback)
