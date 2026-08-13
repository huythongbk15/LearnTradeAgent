"""
Performance Metrics — rolling calculations from trade database.

Provides both static (backtest-style) and rolling (live) metrics.
"""

from __future__ import annotations

import math
from typing import Any

from trading_agent.log_config import get_logger
from trading_agent.monitoring.database import (
    DEFAULT_DB_PATH,
    get_equity_curve,
    get_trade_stats,
    get_trades,
)

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Static metrics (from all available data)
# ---------------------------------------------------------------------------


def compute_static_metrics(
    symbol: str | None = None,
    db_path: str = DEFAULT_DB_PATH,
) -> dict[str, Any]:
    """Compute all static metrics from trade history."""
    stats = get_trade_stats(symbol, db_path)
    equity = get_equity_curve(limit=10000, db_path=db_path)

    metrics: dict[str, Any] = {
        "total_trades": stats.get("total_trades", 0),
        "wins": stats.get("wins", 0),
        "losses": stats.get("losses", 0),
        "win_rate": stats.get("win_rate", 0.0),
        "avg_win": stats.get("avg_win", 0.0),
        "avg_loss": stats.get("avg_loss", 0.0),
        "total_pnl": stats.get("total_pnl", 0.0),
        "avg_pnl_pct": stats.get("avg_pnl_pct", 0.0),
        "total_fees": stats.get("total_fees", 0.0),
        "profit_factor": stats.get("profit_factor", 0.0),
    }

    if equity:
        eq_values = [e["equity"] for e in equity]
        metrics["final_equity"] = eq_values[0] if eq_values else 0.0
        metrics["initial_equity"] = (
            eq_values[-1] if len(eq_values) > 1 else eq_values[0]
        )
        metrics["return_pct"] = _compute_return(
            metrics.get("initial_equity", 0), metrics.get("final_equity", 0)
        )
        metrics["max_drawdown_pct"] = _compute_max_drawdown(eq_values)
        metrics["sharpe_ratio"] = _compute_sharpe(eq_values)
        metrics["sortino_ratio"] = _compute_sortino(eq_values)

    return metrics


# ---------------------------------------------------------------------------
# Rolling metrics (last N trades / last N days)
# ---------------------------------------------------------------------------


def rolling_metrics(
    window: int = 30,
    db_path: str = DEFAULT_DB_PATH,
) -> dict[str, Any]:
    """Compute rolling metrics over the last N trades."""
    trades = get_trades(limit=window, db_path=db_path)
    closed = [t for t in trades if t.get("exit_price") is not None]

    if not closed:
        return {"window": window, "trades_in_window": 0}

    pnls = [t.get("pnl", 0) or 0 for t in closed]
    pnl_pcts = [t.get("pnl_pct", 0) or 0 for t in closed]
    wins = sum(1 for p in pnl_pcts if p > 0)
    losses = sum(1 for p in pnl_pcts if p < 0)

    avg_pnl = sum(pnl_pcts) / len(pnl_pcts) if pnl_pcts else 0
    std_pnl = _std(pnl_pcts)
    downside = _std([p for p in pnl_pcts if p < 0])

    return {
        "window": window,
        "trades_in_window": len(closed),
        "rolling_return_pct": round(sum(pnl_pcts) * 100, 2),
        "rolling_avg_pnl_pct": round(avg_pnl * 100, 4),
        "rolling_win_rate": round(wins / len(closed), 4) if closed else 0,
        "rolling_wins": wins,
        "rolling_losses": losses,
        "rolling_sharpe": round(_annualized_sharpe(avg_pnl, std_pnl), 2)
        if std_pnl > 0
        else 0,
        "rolling_sortino": round(_annualized_sharpe(avg_pnl, downside), 2)
        if downside > 0
        else 0,
    }


# ---------------------------------------------------------------------------
# Internal computation helpers
# ---------------------------------------------------------------------------


def _compute_return(start: float, end: float) -> float:
    if start == 0:
        return 0.0
    return round((end - start) / start * 100, 2)


def _compute_max_drawdown(values: list[float]) -> float:
    """Maximum peak-to-trough drawdown as percentage."""
    peak = values[0] if values else 0
    max_dd = 0.0
    for v in values:
        peak = max(peak, v)
        dd = (peak - v) / peak if peak > 0 else 0
        max_dd = max(max_dd, dd)
    return round(max_dd * 100, 2)


def _compute_sharpe(values: list[float]) -> float:
    """Daily Sharpe ratio from equity values (assumes frequent samples)."""
    if len(values) < 2:
        return 0.0
    returns = [
        (values[i] - values[i + 1]) / values[i + 1]
        for i in range(len(values) - 1)
        if values[i + 1] > 0
    ]
    if not returns:
        return 0.0
    avg_ret = sum(returns) / len(returns)
    std_ret = _std(returns)
    if std_ret == 0:
        return 0.0
    return round((avg_ret / std_ret) * math.sqrt(365), 2)  # annualized


def _compute_sortino(values: list[float]) -> float:
    """Sortino ratio (only downside deviation)."""
    if len(values) < 2:
        return 0.0
    returns = [
        (values[i] - values[i + 1]) / values[i + 1]
        for i in range(len(values) - 1)
        if values[i + 1] > 0
    ]
    if not returns:
        return 0.0
    avg_ret = sum(returns) / len(returns)
    downside = _std([r for r in returns if r < 0])
    if not downside or downside == 0:
        return 0.0
    return round((avg_ret / downside) * math.sqrt(365), 2)


def _annualized_sharpe(avg_return: float, std_return: float) -> float:
    """Annualized Sharpe from per-trade returns (approximate)."""
    if std_return == 0:
        return 0.0
    # sqrt(252) for daily, sqrt(52) for weekly, sqrt(12) for monthly
    return (avg_return / std_return) * math.sqrt(252)


def _std(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    var = sum((v - mean) ** 2 for v in values) / (len(values) - 1)
    return math.sqrt(var)
