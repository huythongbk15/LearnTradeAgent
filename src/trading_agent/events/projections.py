"""Event projections for read models."""

from abc import ABC, abstractmethod
from decimal import Decimal
from typing import Optional

from trading_agent.events.models import Event, EventType, TradeEvent, SignalEvent, RiskEvent, OrderEvent, PositionEvent, PortfolioEvent


class Projection(ABC):
    """Base class for event projections (read models)."""
    
    @abstractmethod
    async def project(self, event: Event) -> None:
        """Process an event and update projection."""
        pass
    
    @abstractmethod
    async def get_state(self) -> dict:
        """Get current projection state."""
        pass


class TradeProjection(Projection):
    """Projection for trade history and P&L."""
    
    def __init__(self):
        self.trades: list[TradeEvent] = []
        self.pnl_by_symbol: dict[str, Decimal] = {}
        self.pnl_by_strategy: dict[str, Decimal] = {}
        self.total_fees: Decimal = Decimal(0)
        self.win_count = 0
        self.loss_count = 0
    
    async def project(self, event: Event) -> None:
        if isinstance(event, TradeEvent):
            self.trades.append(event)
            
            # Calculate P&L
            pnl = (event.price * event.size) if event.side == "sell" else -(event.price * event.size)
            pnl -= event.fee
            
            self.pnl_by_symbol[event.symbol] = self.pnl_by_symbol.get(event.symbol, Decimal(0)) + pnl
            self.pnl_by_strategy[event.strategy_id] = self.pnl_by_strategy.get(event.strategy_id, Decimal(0)) + pnl
            self.total_fees += event.fee
            
            if pnl > 0:
                self.win_count += 1
            elif pnl < 0:
                self.loss_count += 1
    
    async def get_state(self) -> dict:
        total_trades = len(self.trades)
        wins = self.win_count
        losses = self.loss_count
        win_rate = wins / (wins + losses) if (wins + losses) > 0 else 0
        
        return {
            "total_trades": total_trades,
            "total_fees": str(self.total_fees),
            "win_count": wins,
            "loss_count": losses,
            "win_rate": win_rate,
            "pnl_by_symbol": {k: str(v) for k, v in self.pnl_by_symbol.items()},
            "pnl_by_strategy": {k: str(v) for k, v in self.pnl_by_strategy.items()},
        }


class PositionProjection(Projection):
    """Projection for current positions."""
    
    def __init__(self):
        self.positions: dict[str, PositionEvent] = {}
    
    async def project(self, event: Event) -> None:
        if isinstance(event, PositionEvent):
            key = f"{event.symbol}:{event.strategy_id}"
            if event.size == 0:
                self.positions.pop(key, None)
            else:
                self.positions[key] = event
    
    async def get_state(self) -> dict:
        return {
            "positions": {
                k: {
                    "symbol": v.symbol,
                    "size": str(v.size),
                    "entry_price": str(v.entry_price),
                    "mark_price": str(v.mark_price),
                    "unrealized_pnl": str(v.unrealized_pnl),
                    "realized_pnl": str(v.realized_pnl),
                    "leverage": str(v.leverage),
                }
                for k, v in self.positions.items()
            }
        }


class PortfolioProjection(Projection):
    """Projection for portfolio state."""
    
    def __init__(self):
        self.snapshots: list[PortfolioEvent] = []
        self.current: Optional[PortfolioEvent] = None
    
    async def project(self, event: Event) -> None:
        if isinstance(event, PortfolioEvent):
            self.snapshots.append(event)
            self.current = event
    
    async def get_state(self) -> dict:
        if not self.current:
            return {}
        
        return {
            "portfolio_id": self.current.portfolio_id,
            "total_value": str(self.current.total_value),
            "cash": str(self.current.cash),
            "positions_value": str(self.current.positions_value),
            "drawdown_pct": str(self.current.drawdown_pct),
            "strategy_weights": {k: str(v) for k, v in self.current.strategy_weights.items()},
            "snapshots_count": len(self.snapshots),
        }


class RiskProjection(Projection):
    """Projection for risk monitoring."""
    
    def __init__(self):
        self.checks: list[RiskEvent] = []
        self.breaches: list[RiskEvent] = []
        self.current_metrics: dict[str, Decimal] = {}
        self.alerts: list[dict] = []
    
    async def project(self, event: Event) -> None:
        if isinstance(event, RiskEvent):
            self.checks.append(event)
            self.current_metrics[event.metric] = event.value
            
            if not event.passed or event.event_type == EventType.RISK_LIMIT_BREACH:
                self.breaches.append(event)
                self.alerts.append({
                    "timestamp": event.timestamp.isoformat(),
                    "check_type": event.check_type,
                    "metric": event.metric,
                    "value": str(event.value),
                    "threshold": str(event.threshold),
                    "symbol": event.symbol,
                })
                # Keep last 100 alerts
                if len(self.alerts) > 100:
                    self.alerts = self.alerts[-100:]
    
    async def get_state(self) -> dict:
        return {
            "total_checks": len(self.checks),
            "breaches": len(self.breaches),
            "current_metrics": {k: str(v) for k, v in self.current_metrics.items()},
            "recent_alerts": self.alerts[-10:],
        }


class OrderProjection(Projection):
    """Projection for order tracking."""
    
    def __init__(self):
        self.orders: dict[str, OrderEvent] = {}
        self.fill_rate = 0.0
    
    async def project(self, event: Event) -> None:
        if isinstance(event, OrderEvent):
            self.orders[event.order_id] = event
            
            # Update fill rate
            filled = sum(1 for o in self.orders.values() if o.status == "filled")
            total = len(self.orders)
            self.fill_rate = filled / total if total > 0 else 0
    
    async def get_state(self) -> dict:
        status_counts = {}
        for o in self.orders.values():
            status_counts[o.status] = status_counts.get(o.status, 0) + 1
        
        return {
            "total_orders": len(self.orders),
            "fill_rate": self.fill_rate,
            "status_counts": status_counts,
            "recent_orders": [
                {
                    "order_id": o.order_id,
                    "symbol": o.symbol,
                    "side": o.side,
                    "status": o.status,
                    "filled_pct": float(o.filled_size / o.size * 100) if o.size > 0 else 0,
                }
                for o in sorted(self.orders.values(), key=lambda x: x.timestamp, reverse=True)[:10]
            ],
        }


class SignalProjection(Projection):
    """Projection for signal tracking."""
    
    def __init__(self):
        self.signals: list[SignalEvent] = []
        self.by_strategy: dict[str, list[SignalEvent]] = {}
        self.by_symbol: dict[str, list[SignalEvent]] = {}
    
    async def project(self, event: Event) -> None:
        if isinstance(event, SignalEvent):
            self.signals.append(event)
            
            if event.strategy_id not in self.by_strategy:
                self.by_strategy[event.strategy_id] = []
            self.by_strategy[event.strategy_id].append(event)
            
            if event.symbol not in self.by_symbol:
                self.by_symbol[event.symbol] = []
            self.by_symbol[event.symbol].append(event)
    
    async def get_state(self) -> dict:
        # Recent signals
        recent = sorted(self.signals, key=lambda x: x.timestamp, reverse=True)[:20]
        
        # Signal distribution
        type_counts = {}
        for s in self.signals:
            type_counts[s.signal_type] = type_counts.get(s.signal_type, 0) + 1
        
        return {
            "total_signals": len(self.signals),
            "signal_types": type_counts,
            "by_strategy": {k: len(v) for k, v in self.by_strategy.items()},
            "by_symbol": {k: len(v) for k, v in self.by_symbol.items()},
            "recent": [
                {
                    "symbol": s.symbol,
                    "signal_type": s.signal_type,
                    "strength": s.strength,
                    "strategy_id": s.strategy_id,
                    "timestamp": s.timestamp.isoformat(),
                }
                for s in recent
            ],
        }