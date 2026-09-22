"""
AC05 Evidence Script — Statistics and Cost Correctness.

Independent oracle: computes Sharpe, CAGR, MDD, drawdown duration, and
cost attribution from raw trade/equity data by hand — then compares with
the system's calculate_performance_metrics / calculate_cost_attribution.
Also verifies non-finite rejection and zero-trade handling.
"""
import json
import math
import sys
from datetime import timedelta
from pathlib import Path

# ── Independent oracle ─────────────────────────────────────────────────
def oracle_sharpe(returns: list[float], periods_per_year: float) -> float:
    """Sharpe = (mean of returns with initial prepended) / std * sqrt(P).
    System prepends initial_capital to compute return series.
    """
    n = len(returns)
    if n < 2:
        return 0.0
    mean = sum(returns) / n
    var = sum((r - mean) ** 2 for r in returns) / n  # population std (ddof=0) like numpy
    std = math.sqrt(var)
    if std == 0:
        return 0.0
    return mean / std * math.sqrt(periods_per_year)

def oracle_cagr(first: float, last: float, years: float) -> float:
    if years <= 0 or first <= 0:
        return 0.0
    return (last / first) ** (1.0 / years) - 1.0

def oracle_max_drawdown(equity: list[float]) -> float:
    """Max drawdown as percentage."""
    peak = equity[0]
    max_dd = 0.0
    for v in equity:
        peak = max(peak, v)
        dd = peak - v
        max_dd = max(max_dd, dd / peak if peak > 0 else 0.0)
    return max_dd * 100.0

def oracle_max_drawdown_duration(equity: list[float]) -> int:
    """Longest period below peak (in bars)."""
    peak = equity[0]
    duration = 0
    max_duration = 0
    for v in equity:
        peak = max(peak, v)
        if v < peak:
            duration += 1
        else:
            duration = 0
        max_duration = max(max_duration, duration)
    return max_duration


# ── System under test ──────────────────────────────────────────────────
from trading_agent.backtest.reporting import (
    calculate_performance_metrics,
    calculate_cost_attribution,
)
from trading_agent.backtest.report_v2 import validate_report_v2, SCHEMA_VERSION, _RECONCILIATION_ABS_TOL, _RECONCILIATION_REL_TOL


results = []
def check(name, cond, detail=""):
    status = "PASS" if cond else "FAIL"
    results.append({"name": name, "status": status, "detail": detail})
    print(f"[{status}] {name}: {detail}")
    if not cond:
        raise SystemExit(f"AC05 evidence FAIL: {name}")

# ── C1-C3: Stats accuracy with hand-computed oracle ────────────────────
# 10-bar equity curve, simple numbers we can verify by hand
equity_curve = [
    ("2024-01-01", 10000.0),
    ("2024-01-02", 10200.0),
    ("2024-01-03", 10100.0),
    ("2024-01-04", 10300.0),
    ("2024-01-05", 10150.0),
    ("2024-01-06", 10500.0),
    ("2024-01-07", 10400.0),
    ("2024-01-08", 10600.0),
    ("2024-01-09", 10350.0),
    ("2024-01-10", 10800.0),
]
equity_values = [e[1] for e in equity_curve]
initial_capital = 10000.0
# System computes: periods_per_year = timedelta(days=365) / timedelta(days=1) = 365
periods_per_year = 365.0
years = len(equity_values) / periods_per_year

# System prepends initial_capital: with_initial = [initial, v0, v1, ...]
with_initial = [initial_capital] + equity_values
returns = [(with_initial[i] - with_initial[i-1]) / with_initial[i-1]
           for i in range(1, len(with_initial))]

# System computation
system_metrics = calculate_performance_metrics(
    equity_curve=equity_curve,
    initial_capital=initial_capital,
    timeframe_delta=timedelta(days=1),
    trades=[],
)

# Oracle
oracle_cagr = oracle_cagr(equity_values[0], equity_values[-1], years)
oracle_sharpe = oracle_sharpe(returns, periods_per_year)
oracle_mdd = oracle_max_drawdown(equity_values)
oracle_dd_dur = oracle_max_drawdown_duration(equity_values)

check("C1_cagr", abs(system_metrics.get("cagr_pct", 0) - oracle_cagr * 100) < 0.1,
      f"system={system_metrics.get('cagr_pct', 0):.4f}, oracle={oracle_cagr*100:.4f}")
check("C2_sharpe", abs(system_metrics.get("sharpe", 0) - oracle_sharpe) < 0.01,
      f"system={system_metrics.get('sharpe', 0):.4f}, oracle={oracle_sharpe:.4f}")
check("C3_mdd", abs(system_metrics.get("max_drawdown_pct", 0) - oracle_mdd) < 0.01,
      f"system={system_metrics.get('max_drawdown_pct', 0):.4f}, oracle={oracle_mdd:.4f}")
check("C3_dd_duration", system_metrics.get("longest_drawdown_bars", 0) == oracle_dd_dur,
      f"system={system_metrics.get('longest_drawdown_bars', 0)}, oracle={oracle_dd_dur}")

# ── C4: Cost reconciliation oracle ─────────────────────────────────────
trades = [
    {"quantity": 100, "entry_price": 50.0, "exit_price": 52.0, "pnl": 200.0,
     "entry_fee": 1.0, "exit_fee": 1.0,
     "metadata": {"simulation": {"entry_reference_price": 50.5, "exit_reference_price": 51.5}}},
    {"quantity": 50, "entry_price": 100.0, "exit_price": 98.0, "pnl": -100.0,
     "entry_fee": 0.5, "exit_fee": 0.5,
     "metadata": {"simulation": {"entry_reference_price": 99.5, "exit_reference_price": 99.0}}},
]

costs = calculate_cost_attribution(trades)

# Oracle: net_pnl = 200 - 100 = 100
oracle_net = sum(t["pnl"] for t in trades)
# commission = 1 + 1 + 0.5 + 0.5 = 3
oracle_commission = sum(t["entry_fee"] + t["exit_fee"] for t in trades)
# slippage: trade1: 100*(50.0-50.5)=0 (negative, clamped to 0) + 100*(51.5-52.0)=0 → 0
# trade2: 50*(100-99.5)=25 + 50*(99.0-98.0)=50 → 75
oracle_slippage = max(0, 100*(50.0-50.5)) + max(0, 100*(51.5-52.0))
oracle_slippage += max(0, 50*(100.0-99.5)) + max(0, 50*(99.0-98.0))
oracle_total_cost = oracle_commission + oracle_slippage
oracle_gross_alpha = oracle_net + oracle_total_cost

check("C4_net_pnl", abs(costs["net_pnl"] - oracle_net) < 0.001,
      f"system={costs['net_pnl']}, oracle={oracle_net}")
check("C4_commission", abs(costs["commission"] - oracle_commission) < 0.001,
      f"system={costs['commission']}, oracle={oracle_commission}")
check("C4_slippage", abs(costs["slippage"] - oracle_slippage) < 0.001,
      f"system={costs['slippage']}, oracle={oracle_slippage}")
check("C4_total_cost", abs(costs["total_cost"] - oracle_total_cost) < 0.001,
      f"system={costs['total_cost']}, oracle={oracle_total_cost}")
check("C4_gross_alpha", abs(costs["gross_alpha_pnl"] - oracle_gross_alpha) < 0.001,
      f"system={costs['gross_alpha_pnl']}, oracle={oracle_gross_alpha}")
check("C4_complete", costs["complete"] is True, f"missing={costs['missing_reference_trades']}")
scale = abs(oracle_gross_alpha)
tol = max(_RECONCILIATION_ABS_TOL, _RECONCILIATION_REL_TOL * scale)
check("C4_recon_tolerance", abs(costs["reconciliation_error"]) < tol,
      f"error={costs['reconciliation_error']:.3e}, tol={tol:.3e}")

# ── C5: Non-finite numbers rejected ───────────────────────────────────
good_report = {
    "schema_version": SCHEMA_VERSION,
    "report_type": "backtest",
    "status": "passed",
    "symbol": "ADA_USDT",
    "timeframe": "1h",
    "final_equity": 10800.0,
    "total_return_pct": 8.0,
    "sharpe": 7.1110,
    "max_drawdown_pct": 2.3585,
    "total_trades": 0,
    "win_rate_pct": 0.0,
    "data_manifest_id": "sha256:" + "a" * 64,
    "feature_artifact_id": "sha256:" + "b" * 64,
    "active_config": {"config_id": "test"},
    "execution_health": {"errors": [], "warnings": [],
                         "unknown_orders": 0, "manual_interventions": 0,
                         "unprotected_positions": [], "trade_evidence_complete": True},
    "simulation_window": {"accepted": True},
    "data_quality": {"window": {"accepted": True}},
    "cost_attribution": {"complete": True, "missing_reference_trades": 0,
                         "gross_alpha_pnl": 0.0, "commission": 0.0,
                         "slippage": 0.0, "spread": 0.0, "market_impact": 0.0,
                         "total_cost": 0.0, "net_pnl": 0.0,
                         "reconciliation_error": 0.0},
    "benchmarks": {"fixed_allocation_buy_and_hold": {"total_return_pct": 5.0}},
}
violations_good = validate_report_v2(good_report)
check("C5_valid_report_ok", len(violations_good) == 0, f"violations={violations_good}")

# NaN in final_equity should be rejected
bad_report = dict(good_report, final_equity=float("nan"))
violations_bad = validate_report_v2(bad_report)
check("C5_nan_rejected", len(violations_bad) > 0,
      f"NaN equity rejected: {violations_bad}")

# NaN in sharpe should be rejected
bad_report2 = dict(good_report, sharpe=float("nan"))
violations_bad2 = validate_report_v2(bad_report2)
check("C5_nan_sharpe_rejected", len(violations_bad2) > 0,
      f"NaN sharpe rejected: {violations_bad2}")

# ── C6: Zero trades handling ────────────────────────────────────────────
violations_zero = validate_report_v2(good_report)
check("C6_zero_trades_valid", len(violations_zero) == 0, f"violations={violations_zero}")

# ── Summary ────────────────────────────────────────────────────────────
all_pass = all(r["status"] == "PASS" for r in results)
print(f"\n{'='*60}")
print(f"AC05 Evidence: {'ALL PASS' if all_pass else 'FAIL'} ({len(results)} checks)")
print(f"{'='*60}")

evidence = {
    "ac_id": "AC05",
    "oracle": "independent: Sharpe/CAGR/MDD/dd_duration/cost_attribution by hand",
    "cases": results,
    "all_pass": all_pass,
    "total_checks": len(results),
    "passed": sum(1 for r in results if r["status"] == "PASS"),
}
out = Path("/tmp/ac05_evidence.json")
out.write_text(json.dumps(evidence, indent=2, default=str))
print(f"Evidence written to {out}")
sys.exit(0 if all_pass else 1)
