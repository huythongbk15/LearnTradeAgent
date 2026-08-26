"""Canonical backtest evidence helpers.

This module deliberately contains no strategy or broker logic.  It turns trusted
OHLCV, equity and trade ledgers into deterministic, auditable report sections.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Literal, Mapping, Sequence

import numpy as np
import polars as pl

GapPolicy = Literal["record", "reject"]


def fingerprint_payload(payload: Mapping[str, Any]) -> str:
    """Return a stable SHA-256 identity for a JSON-compatible payload."""
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


@dataclass(frozen=True)
class DataQualityReport:
    """Point-in-time OHLCV validation result."""

    status: str
    accepted: bool
    gap_policy: GapPolicy
    row_count: int
    start_at: str
    end_at: str
    expected_interval_seconds: int
    duplicate_timestamps: int
    out_of_order_rows: int
    gap_count: int
    missing_bar_count: int
    gaps: tuple[dict[str, Any], ...]
    null_counts: dict[str, int]
    invalid_price_rows: int
    invalid_volume_rows: int
    invalid_ohlc_rows: int
    fingerprint: str

    def to_dict(self) -> dict[str, Any]:
        """Serialize the report without losing its evidence fingerprint."""
        return {
            "status": self.status,
            "accepted": self.accepted,
            "gap_policy": self.gap_policy,
            "row_count": self.row_count,
            "start_at": self.start_at,
            "end_at": self.end_at,
            "expected_interval_seconds": self.expected_interval_seconds,
            "duplicate_timestamps": self.duplicate_timestamps,
            "out_of_order_rows": self.out_of_order_rows,
            "gap_count": self.gap_count,
            "missing_bar_count": self.missing_bar_count,
            "gaps": [dict(gap) for gap in self.gaps],
            "null_counts": dict(self.null_counts),
            "invalid_price_rows": self.invalid_price_rows,
            "invalid_volume_rows": self.invalid_volume_rows,
            "invalid_ohlc_rows": self.invalid_ohlc_rows,
            "fingerprint": self.fingerprint,
        }


class DataQualityError(ValueError):
    """Raised when OHLCV does not satisfy the configured evidence policy."""

    def __init__(self, report: DataQualityReport):
        self.report = report
        super().__init__(
            "OHLCV data-quality gate failed: "
            f"status={report.status}, gaps={report.gap_count}, "
            f"duplicates={report.duplicate_timestamps}, "
            f"out_of_order={report.out_of_order_rows}, "
            f"invalid_prices={report.invalid_price_rows}, "
            f"invalid_ohlc={report.invalid_ohlc_rows}"
        )


def assess_ohlcv(
    df: pl.DataFrame,
    *,
    expected_interval: timedelta,
    gap_policy: GapPolicy = "record",
) -> DataQualityReport:
    """Validate OHLCV and either record or reject positive timestamp gaps.

    Duplicate/out-of-order timestamps, nulls, invalid prices/volume and impossible
    OHLC relationships always fail.  A positive gap can be preserved only when the
    caller explicitly selects ``record``; no values are silently imputed.
    """
    if gap_policy not in {"record", "reject"}:
        raise ValueError(f"unsupported gap policy: {gap_policy!r}")
    required = ("timestamp", "open", "high", "low", "close", "volume")
    missing = [column for column in required if column not in df.columns]
    if missing:
        raise ValueError(f"OHLCV is missing required columns: {missing}")
    if df.is_empty():
        raise ValueError("OHLCV must contain at least one row")

    expected_seconds = int(expected_interval.total_seconds())
    if expected_seconds <= 0:
        raise ValueError("expected_interval must be positive")

    timestamps = df["timestamp"].to_list()
    duplicate_timestamps = len(timestamps) - len(set(timestamps))
    out_of_order_rows = 0
    gaps: list[dict[str, Any]] = []
    missing_bar_count = 0
    for previous, current in zip(timestamps, timestamps[1:], strict=False):
        delta_seconds = int((current - previous).total_seconds())
        if delta_seconds <= 0:
            out_of_order_rows += 1
            continue
        if delta_seconds == expected_seconds:
            continue
        estimated_missing = max(0, math.ceil(delta_seconds / expected_seconds) - 1)
        missing_bar_count += estimated_missing
        gaps.append(
            {
                "previous_at": previous.isoformat(),
                "current_at": current.isoformat(),
                "delta_seconds": delta_seconds,
                "estimated_missing_bars": estimated_missing,
            }
        )

    null_counts = {column: int(df[column].null_count()) for column in required}
    invalid_price_rows = int(
        df.select(
            pl.any_horizontal(
                [(pl.col(column) <= 0).fill_null(False) for column in ("open", "high", "low", "close")]
            )
            .sum()
            .alias("invalid")
        ).item()
    )
    invalid_volume_rows = int(
        df.select(((pl.col("volume") < 0).fill_null(False)).sum()).item()
    )
    invalid_ohlc_rows = int(
        df.select(
            (
                (pl.col("high") < pl.max_horizontal("open", "close", "low"))
                | (pl.col("low") > pl.min_horizontal("open", "close", "high"))
            )
            .fill_null(False)
            .sum()
            .alias("invalid")
        ).item()
    )

    hard_failure = bool(
        duplicate_timestamps
        or out_of_order_rows
        or sum(null_counts.values())
        or invalid_price_rows
        or invalid_volume_rows
        or invalid_ohlc_rows
    )
    gap_failure = bool(gaps and gap_policy == "reject")
    accepted = not hard_failure and not gap_failure
    if hard_failure:
        status = "failed_invalid_ohlcv"
    elif gap_failure:
        status = "failed_gap_policy"
    elif gaps:
        status = "accepted_with_recorded_gaps"
    else:
        status = "passed"

    evidence: dict[str, Any] = {
        "status": status,
        "accepted": accepted,
        "gap_policy": gap_policy,
        "row_count": len(df),
        "start_at": timestamps[0].isoformat(),
        "end_at": timestamps[-1].isoformat(),
        "expected_interval_seconds": expected_seconds,
        "duplicate_timestamps": duplicate_timestamps,
        "out_of_order_rows": out_of_order_rows,
        "gap_count": len(gaps),
        "missing_bar_count": missing_bar_count,
        "gaps": gaps,
        "null_counts": null_counts,
        "invalid_price_rows": invalid_price_rows,
        "invalid_volume_rows": invalid_volume_rows,
        "invalid_ohlc_rows": invalid_ohlc_rows,
    }
    report = DataQualityReport(
        **evidence,
        fingerprint=fingerprint_payload(evidence),
    )
    if not accepted:
        raise DataQualityError(report)
    return report


def _equity_statistics(
    values: np.ndarray,
    *,
    initial_capital: float,
    periods_per_year: float,
) -> dict[str, float]:
    if values.size == 0:
        raise ValueError("equity series must not be empty")
    if initial_capital <= 0 or not math.isfinite(initial_capital):
        raise ValueError("initial_capital must be finite and positive")
    if periods_per_year <= 0 or not math.isfinite(periods_per_year):
        raise ValueError("periods_per_year must be finite and positive")
    if not np.all(np.isfinite(values)) or np.any(values <= 0):
        raise ValueError("equity values must be finite and positive")

    with_initial = np.concatenate(([initial_capital], values))
    returns = np.diff(with_initial) / with_initial[:-1]
    mean_return = float(returns.mean()) if returns.size else 0.0
    return_std = float(returns.std()) if returns.size else 0.0
    sharpe = (
        mean_return / return_std * math.sqrt(periods_per_year)
        if return_std > 0
        else 0.0
    )
    downside = np.minimum(returns, 0.0)
    downside_deviation = float(math.sqrt(float(np.mean(downside**2)))) if returns.size else 0.0
    sortino = (
        mean_return / downside_deviation * math.sqrt(periods_per_year)
        if downside_deviation > 0
        else 0.0
    )
    peaks = np.maximum.accumulate(with_initial)
    drawdowns = (peaks - with_initial) / peaks
    max_drawdown_pct = float(drawdowns.max() * 100)
    longest_drawdown_bars = 0
    current_drawdown_bars = 0
    for drawdown in drawdowns[1:]:
        if drawdown > 0:
            current_drawdown_bars += 1
            longest_drawdown_bars = max(longest_drawdown_bars, current_drawdown_bars)
        else:
            current_drawdown_bars = 0

    years = values.size / periods_per_year
    total_return_pct = float((values[-1] / initial_capital - 1.0) * 100)
    cagr_pct = (
        float(((values[-1] / initial_capital) ** (1.0 / years) - 1.0) * 100)
        if years > 0
        else 0.0
    )
    calmar = cagr_pct / max_drawdown_pct if max_drawdown_pct > 0 else 0.0
    return {
        "final_equity": float(values[-1]),
        "total_return_pct": total_return_pct,
        "cagr_pct": cagr_pct,
        "annualized_volatility_pct": return_std * math.sqrt(periods_per_year) * 100,
        "sharpe": float(sharpe),
        "sortino": float(sortino),
        "max_drawdown_pct": max_drawdown_pct,
        "calmar": float(calmar),
        "longest_drawdown_bars": longest_drawdown_bars,
    }


def calculate_cost_attribution(
    trades: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Reconcile net P&L to reference-price alpha and modeled execution costs."""
    net_pnl = 0.0
    commission = 0.0
    slippage = 0.0
    missing_reference_trades = 0
    for trade in trades:
        quantity = float(trade.get("quantity") or 0.0)
        entry_price = float(trade.get("entry_price") or 0.0)
        exit_price = float(trade.get("exit_price") or 0.0)
        net_pnl += float(trade.get("pnl") or 0.0)
        commission += float(trade.get("entry_fee") or 0.0) + float(
            trade.get("exit_fee") or 0.0
        )
        metadata = trade.get("metadata")
        simulation = metadata.get("simulation") if isinstance(metadata, Mapping) else None
        if not isinstance(simulation, Mapping):
            missing_reference_trades += 1
            continue
        entry_reference = simulation.get("entry_reference_price")
        exit_reference = simulation.get("exit_reference_price")
        if entry_reference is None or exit_reference is None:
            missing_reference_trades += 1
            continue
        slippage += quantity * max(0.0, entry_price - float(entry_reference))
        slippage += quantity * max(0.0, float(exit_reference) - exit_price)

    spread = 0.0
    impact = 0.0
    total_cost = commission + slippage + spread + impact
    gross_alpha_pnl = net_pnl + total_cost
    reconciliation_error = gross_alpha_pnl - total_cost - net_pnl
    return {
        "complete": missing_reference_trades == 0,
        "missing_reference_trades": missing_reference_trades,
        "gross_alpha_pnl": float(gross_alpha_pnl),
        "commission": float(commission),
        "slippage": float(slippage),
        "spread": spread,
        "market_impact": impact,
        "total_cost": float(total_cost),
        "net_pnl": float(net_pnl),
        "reconciliation_error": float(reconciliation_error),
    }


def calculate_performance_metrics(
    equity_curve: Sequence[tuple[str, float]],
    *,
    initial_capital: float,
    timeframe_delta: timedelta,
    trades: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Calculate report-v2 portfolio and trade statistics."""
    periods_per_year = timedelta(days=365).total_seconds() / timeframe_delta.total_seconds()
    values = np.asarray([equity for _, equity in equity_curve], dtype=np.float64)
    metrics: dict[str, Any] = _equity_statistics(
        values,
        initial_capital=initial_capital,
        periods_per_year=periods_per_year,
    )

    ordered_trades = sorted(trades, key=lambda trade: str(trade.get("exit_time") or ""))
    pnls = [float(trade.get("pnl") or 0.0) for trade in ordered_trades]
    wins = [pnl for pnl in pnls if pnl > 0]
    losses = [pnl for pnl in pnls if pnl < 0]
    gross_profit = float(sum(wins))
    gross_loss = float(abs(sum(losses)))
    reason_counts: dict[str, int] = {}
    holding_bars = 0
    turnover_notional = 0.0
    consecutive_losses = 0
    max_consecutive_losses = 0
    for trade, pnl in zip(ordered_trades, pnls, strict=True):
        reason = str(trade.get("reason") or "unknown")
        reason_counts[reason] = reason_counts.get(reason, 0) + 1
        metadata = trade.get("metadata")
        simulation = metadata.get("simulation") if isinstance(metadata, Mapping) else None
        if isinstance(simulation, Mapping):
            holding_bars += int(simulation.get("holding_bars") or 0)
            entry_reference = float(
                simulation.get("entry_reference_price") or trade.get("entry_price") or 0.0
            )
            exit_reference = float(
                simulation.get("exit_reference_price") or trade.get("exit_price") or 0.0
            )
        else:
            entry_reference = float(trade.get("entry_price") or 0.0)
            exit_reference = float(trade.get("exit_price") or 0.0)
        quantity = float(trade.get("quantity") or 0.0)
        turnover_notional += quantity * (entry_reference + exit_reference)
        if pnl < 0:
            consecutive_losses += 1
            max_consecutive_losses = max(max_consecutive_losses, consecutive_losses)
        else:
            consecutive_losses = 0

    total_trades = len(pnls)
    net_pnl = float(sum(pnls))
    best_trade = max(pnls, default=0.0)
    metrics.update(
        {
            "total_trades": total_trades,
            "winning_trades": len(wins),
            "losing_trades": len(losses),
            "win_rate_pct": len(wins) / total_trades * 100 if total_trades else 0.0,
            "profit_factor": gross_profit / gross_loss if gross_loss > 0 else None,
            "gross_profit": gross_profit,
            "gross_loss": gross_loss,
            "net_pnl": net_pnl,
            "average_trade_pnl": net_pnl / total_trades if total_trades else 0.0,
            "average_win": gross_profit / len(wins) if wins else 0.0,
            "average_loss": -gross_loss / len(losses) if losses else 0.0,
            "best_trade_pnl": float(best_trade),
            "worst_trade_pnl": float(min(pnls, default=0.0)),
            "best_trade_net_pnl_contribution_pct": (
                best_trade / net_pnl * 100 if net_pnl > 0 else None
            ),
            "max_consecutive_losses": max_consecutive_losses,
            "turnover_x_initial_capital": turnover_notional / initial_capital,
            "time_in_market_pct": (
                min(100.0, holding_bars / max(1, len(equity_curve)) * 100)
            ),
            "exit_reason_counts": reason_counts,
        }
    )
    return metrics


def calendar_returns(
    equity_curve: Sequence[tuple[str, float]], *, initial_capital: float
) -> dict[str, float]:
    """Return linked calendar-year returns from an hourly/daily equity curve."""
    year_ends: list[tuple[str, float]] = []
    for timestamp, equity in equity_curve:
        year = str(timestamp)[:4]
        if year_ends and year_ends[-1][0] == year:
            year_ends[-1] = (year, float(equity))
        else:
            year_ends.append((year, float(equity)))
    result: dict[str, float] = {}
    previous = initial_capital
    for year, ending_equity in year_ends:
        result[year] = (ending_equity / previous - 1.0) * 100
        previous = ending_equity
    return result


def fixed_allocation_buy_and_hold(
    close_prices: Sequence[float],
    *,
    entry_reference_price: float,
    initial_capital: float,
    allocation_pct: float,
    commission_rate: float,
    slippage_rate: float,
    timeframe_delta: timedelta,
) -> dict[str, Any]:
    """Calculate an auditable, one-entry/one-exit fixed-allocation benchmark."""
    if not close_prices:
        raise ValueError("close_prices must not be empty")
    if not 0 < allocation_pct <= 1:
        raise ValueError("allocation_pct must be in (0, 1]")
    if entry_reference_price <= 0:
        raise ValueError("entry_reference_price must be positive")
    if not 0 <= commission_rate < 1 or not 0 <= slippage_rate < 1:
        raise ValueError("commission and slippage rates must be in [0, 1)")

    reference_notional = initial_capital * allocation_pct
    quantity = reference_notional / entry_reference_price
    entry_fill = entry_reference_price * (1.0 + slippage_rate)
    entry_cost = quantity * entry_fill
    entry_fee = entry_cost * commission_rate
    cash = initial_capital - entry_cost - entry_fee
    if cash < 0:
        raise ValueError("allocation plus entry costs exceeds initial capital")

    values = cash + quantity * np.asarray(close_prices, dtype=np.float64)
    exit_reference = float(close_prices[-1])
    exit_fill = exit_reference * (1.0 - slippage_rate)
    exit_fee = quantity * exit_fill * commission_rate
    values[-1] = cash + quantity * exit_fill - exit_fee
    periods_per_year = timedelta(days=365).total_seconds() / timeframe_delta.total_seconds()
    result: dict[str, Any] = _equity_statistics(
        values,
        initial_capital=initial_capital,
        periods_per_year=periods_per_year,
    )
    result.update(
        {
            "name": "fixed_allocation_buy_and_hold",
            "allocation_pct": allocation_pct * 100,
            "entry_reference_price": entry_reference_price,
            "exit_reference_price": exit_reference,
            "entry_fill_price": entry_fill,
            "exit_fill_price": exit_fill,
            "commission": entry_fee + exit_fee,
            "slippage": quantity * (entry_fill - entry_reference_price)
            + quantity * (exit_reference - exit_fill),
        }
    )
    return result
