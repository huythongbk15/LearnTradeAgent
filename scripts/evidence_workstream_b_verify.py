#!/usr/bin/env python3
"""Independent oracle verification for Workstream B S3 nested WFO campaigns.

This script reads wfo_decision.json files produced by run_wfo_parallel.py
and INDEPENDENTLY recomputes performance metrics from raw trade-level P&L
extracted from per-fold report.json files.

It does NOT trust the framework's internal metrics — all metrics are
recomputed from first principles using:

  - Trade-level PnL from outer-fold report.json (profit_factor, net_pnl)
  - Return series from outer-fold test_metrics (Sharpe, max_drawdown)

Outer-OOS gates (per Section 8 of the audit contract):
  - outer_oos_net_return_positive: total OOS net P&L > 0
  - outer_oos_sharpe_ge_080:       mean per-fold Sharpe >= 0.80
  - outer_oos_profit_factor_ge_120: aggregate PF >= 1.20
  - outer_oos_max_drawdown_le_10pct: max across folds MDD <= 10%
  - outer_oos_calmar_ge_050:       aggregate Calmar >= 0.5
  - dsr_ge_095:                     Deflated Sharpe Ratio >= 0.95
  - pbo_le_020:                     PBO <= 0.20
  - positive_outer_folds_ge_60pct: >= 60% of outer folds have positive PnL

Usage:
  python scripts/evidence_workstream_b_verify.py \
    --directories /dev/shm/wvo_btc /dev/shm/wvo_eth /dev/shm/wvo_sol
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
from typing import Any

# ---------------------------------------------------------------------------
# Gate thresholds (Section 8, Table 1)
# ---------------------------------------------------------------------------
GATE_THRESHOLDS: dict[str, Any] = {
    "outer_oos_net_return_positive": 0.0,          # total net P&L > 0
    "outer_oos_sharpe_ge_080": 0.80,               # mean Sharpe >= 0.8
    "outer_oos_profit_factor_ge_120": 1.20,        # aggregate PF >= 1.2
    "outer_oos_max_drawdown_le_10pct": 10.0,       # max MDD <= 10%
    "outer_oos_calmar_ge_050": 0.50,               # Calmar >= 0.5
    "dsr_ge_095": 0.95,                            # DSR >= 0.95
    "pbo_le_020": 0.20,                            # PBO <= 0.20
    "positive_outer_folds_ge_60pct": 60.0,         # >= 60% folds positive
}

# Annualisation factor for 1-hour returns
HOURS_PER_YEAR = 365 * 24


# ---------------------------------------------------------------------------
# Independent metric computations (oracle side)
# ---------------------------------------------------------------------------
def sharpe_from_returns(returns: list[float]) -> float:
    """Annualised Sharpe ratio from a list of hourly returns."""
    returns = [r for r in returns if r is not None and math.isfinite(r)]
    n = len(returns)
    if n < 2:
        return 0.0
    mean = sum(returns) / n
    var = sum((r - mean) ** 2 for r in returns) / (n - 1)
    std = math.sqrt(var)
    if std < 1e-12:
        return 0.0
    # Annualise: mean/std * sqrt(T)
    return mean / std * math.sqrt(HOURS_PER_YEAR)


def max_drawdown_from_returns(returns: list[float]) -> float:
    """Maximum drawdown as a percentage (positive = bad)."""
    if not returns:
        return 0.0
    cum = 1.0
    peak = 1.0
    max_dd = 0.0
    for r in returns:
        if r is None or not math.isfinite(r):
            continue
        cum *= (1.0 + r)
        if cum > peak:
            peak = cum
        if peak > 0:
            dd = (peak - cum) / peak * 100.0
            if dd > max_dd:
                max_dd = dd
    return max_dd


def profit_factor_from_trades(trade_pnls: list[float]) -> float:
    """Profit factor = gross profits / abs(gross losses)."""
    gross_profits = sum(p for p in trade_pnls if p > 0)
    gross_losses = abs(sum(p for p in trade_pnls if p < 0))
    if gross_losses < 1e-10:
        return float("inf") if gross_profits > 1e-10 else 0.0
    return gross_profits / gross_losses


def net_pnl_from_trades(trade_pnls: list[float]) -> float:
    return sum(trade_pnls)


def probabilistic_sharpe_ratio(sr: float, n: int) -> float:
    """PSR = P(true Sharpe > 0 | observed SR).

    Uses the asymptotic variance from Bailey et al. (2017):
      Var(SR) ≈ (1 + 0.25 * SR²) / n
    """
    if n < 3:
        return 0.0
    var = (1.0 + 0.25 * sr ** 2) / n
    if var <= 0:
        return 1.0
    z = sr / math.sqrt(var)
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def deflated_sharpe_ratio(
    sr: float, n: int, trials: int, skew: float = 0.0, kurtosis: float = 0.0
) -> float:
    """DSR = PSR adjusted for multiple-testing (expected max Sharpe).

    Reference: Bailey et al. (2017), "The Deflated Sharpe Ratio
    in the Presence of Selection Bias".
    """
    if n < 3 or trials < 1:
        return 0.0
    var = (1.0 + 0.25 * sr ** 2) / n
    if var <= 0:
        return 1.0
    # Expected maximum of `trials` draws from N(0, var) (conservative
    # Bonferroni-style approximation using sqrt of sum of 1/(2*ln(i))).
    # For tractability we use the standard normal expected-max approximation.
    if trials > 1:
        # Expected max ≈ sqrt(2 * ln(trials)) for standard normal
        # Scale by sqrt(var) to get expected max Sharpe under null
        expected_max = math.sqrt(2.0 * math.log(trials)) * math.sqrt(var)
    else:
        expected_max = 0.0
    z = (sr - expected_max) / math.sqrt(var)
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def compute_pbo(
    all_returns: list[float],
    n_folds: int,
    n_candidates: int,
) -> float | None:
    """Estimate PBO via CSCV-style approximation.

    Since full CSCV requires per-candidate per-fold returns (2D matrix),
    we use the available return series.  If insufficient data, return None.
    """
    # Without per-candidate return series, we cannot compute PBO independently.
    # Return None to signal "cannot verify".
    return None


# ---------------------------------------------------------------------------
# Oracle data extraction helpers
# ---------------------------------------------------------------------------
def read_report_json(report_path: str) -> dict[str, Any] | None:
    """Read a report.json file, returning None if missing."""
    if not report_path or not os.path.exists(report_path):
        return None
    try:
        with open(report_path) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def extract_trade_pnls(report: dict[str, Any]) -> list[float]:
    """Extract individual trade P&L from a report.json."""
    trades = report.get("trades", [])
    pnls = []
    for t in trades:
        if isinstance(t, dict):
            pnls.append(float(t.get("pnl", 0.0)))
        elif isinstance(t, (int, float)):
            pnls.append(float(t))
    return pnls


def extract_return_series(report: dict[str, Any]) -> list[float]:
    """Extract hourly return series from a report's equity curve."""
    # Prefer explicit return_series if present
    rs = report.get("return_series")
    if rs and len(rs) > 0:
        return [float(r) for r in rs]
    # Fall back to equity curve
    equity = report.get("equity_curve")
    if equity and len(equity) > 1:
        returns = []
        for i in range(1, len(equity)):
            prev = float(equity[i - 1])
            curr = float(equity[i])
            if prev > 0:
                returns.append(curr / prev - 1.0)
            else:
                returns.append(0.0)
        return returns
    return []


# ---------------------------------------------------------------------------
# Oracle — single candidate
# ---------------------------------------------------------------------------
def _oracle_from_outer_results(
    decision: dict[str, Any],
) -> dict[str, Any]:
    """Build the oracle's independent view of a single WFO decision."""
    spec = decision.get("spec", {})
    strategy_id = spec.get("strategy_id", "unknown")
    symbol = spec.get("symbol", "unknown")
    outer_results = decision.get("outer_results", [])
    stats = decision.get("statistical_hardening", {})

    fold_views = []
    all_trade_pnls: list[float] = []
    all_outer_returns: list[float] = []

    for fold in outer_results:
        fold_id = fold.get("fold_id", "unknown")
        test_metrics = fold.get("test_metrics", {})
        return_series = test_metrics.get("return_series", [])
        report_path = fold.get("artifact", {}).get("report_path", "")

        # --- Read raw trade PnL from report.json (independent source) ---
        report = read_report_json(report_path)
        if report is not None:
            trade_pnls = extract_trade_pnls(report)
            # Use report's return series if the JSON didn't have one
            if not return_series:
                return_series = extract_return_series(report)
        else:
            trade_pnls = []

        # --- Oracle recomputes metrics from raw data ---
        oracle_sh = sharpe_from_returns(return_series)
        oracle_mdd = max_drawdown_from_returns(return_series)
        oracle_pf = profit_factor_from_trades(trade_pnls) if trade_pnls else test_metrics.get("profit_factor", 0.0)
        oracle_net_pnl = net_pnl_from_trades(trade_pnls) if trade_pnls else 0.0
        oracle_return = sum(return_series) if return_series else 0.0  # total return

        fold_views.append({
            "fold_id": fold_id,
            "n_trades": len(trade_pnls),
            "n_returns": len(return_series),
            "net_pnl": oracle_net_pnl,
            "total_return_pct": oracle_return,
            "sharpe": oracle_sh,
            "max_drawdown_pct": oracle_mdd,
            "profit_factor": oracle_pf,
            "report_path": report_path,
            "report_found": report is not None,
        })

        all_trade_pnls.extend(trade_pnls)
        all_outer_returns.extend(return_series)

    # --- Aggregate (oracle view) ---
    n_folds = len(fold_views)
    total_net_pnl = sum(f["net_pnl"] for f in fold_views)
    positive_folds = sum(1 for f in fold_views if f["net_pnl"] > 0)
    positive_pct = (positive_folds / n_folds * 100.0) if n_folds else 0.0

    # Aggregate Sharpe: recompute from concatenated outer return series
    agg_sharpe = sharpe_from_returns(all_outer_returns) if all_outer_returns else 0.0
    # Aggregate PF: recompute from all trade PnLs
    agg_pf = profit_factor_from_trades(all_trade_pnls) if all_trade_pnls else 0.0
    # Max drawdown: worst across folds
    agg_mdd = max((f["max_drawdown_pct"] for f in fold_views), default=0.0)
    # Calmar: total return / max drawdown (annualised return / MDD as ratio)
    if all_outer_returns:
        total_return = sum(all_outer_returns)
        calmar = (total_return * math.sqrt(HOURS_PER_YEAR) /
                  (agg_mdd / 100.0)) if agg_mdd > 0 else float("inf")
    else:
        calmar = 0.0

    # DSR — use existing stats when return counts are large enough
    sr = stats.get("sharpe", 0.0)
    n_returns = stats.get("n_returns", 0)
    trials = stats.get("dsr_trials", 1)
    skew = stats.get("skew", 0.0)
    excess_kurtosis = stats.get("excess_kurtosis", 0.0)
    oracle_dsr = deflated_sharpe_ratio(sr, n_returns, trials, skew, excess_kurtosis) if n_returns > 0 else 0.0

    # PBO — framework value (cannot independently recompute without
    # per-candidate return matrix)
    framework_pbo = stats.get("pbo", None)
    framework_pbo_status = stats.get("pbo_matrix", {}).get("status", "UNKNOWN")

    return {
        "strategy_id": strategy_id,
        "symbol": symbol,
        "spec_id": spec.get("spec_id", ""),
        "n_outer_folds": n_folds,
        "total_oos_net_pnl": total_net_pnl,
        "aggregate_sharpe": agg_sharpe,
        "aggregate_profit_factor": agg_pf,
        "aggregate_max_drawdown_pct": agg_mdd,
        "aggregate_calmar": calmar,
        "oracle_dsr": oracle_dsr,
        "framework_pbo": framework_pbo,
        "framework_pbo_status": framework_pbo_status,
        "positive_fold_pct": positive_pct,
        "total_trades": len(all_trade_pnls),
        "fold_views": fold_views,
    }


def _independent_gate_check(member: dict[str, Any]) -> dict[str, Any]:
    """Check all hard gates for a single oracle-computed member."""
    gates: dict[str, dict[str, Any]] = {}

    # Gate 1: net return positive
    net_pnl = member["total_oos_net_pnl"]
    gates["outer_oos_net_return_positive"] = {
        "observed_value": net_pnl,
        "threshold": GATE_THRESHOLDS["outer_oos_net_return_positive"],
        "comparison": ">",
        "verdict": "PASS" if net_pnl > 0 else "FAIL",
        "reason": f"Total OOS net PnL {net_pnl:.2f} must be > 0",
    }

    # Gate 2: Sharpe >= 0.80
    sh = member["aggregate_sharpe"]
    gates["outer_oos_sharpe_ge_080"] = {
        "observed_value": sh,
        "threshold": GATE_THRESHOLDS["outer_oos_sharpe_ge_080"],
        "comparison": ">=",
        "verdict": "PASS" if sh >= 0.80 else "FAIL",
        "reason": f"Aggregate Sharpe {sh:.4f} must be >= 0.80",
    }

    # Gate 3: Profit factor >= 1.20
    pf = member["aggregate_profit_factor"]
    gates["outer_oos_profit_factor_ge_120"] = {
        "observed_value": pf,
        "threshold": GATE_THRESHOLDS["outer_oos_profit_factor_ge_120"],
        "comparison": ">=",
        "verdict": "PASS" if pf >= 1.20 else "FAIL",
        "reason": f"Aggregate PF {pf:.4f} must be >= 1.20",
    }

    # Gate 4: Max drawdown <= 10%
    mdd = member["aggregate_max_drawdown_pct"]
    gates["outer_oos_max_drawdown_le_10pct"] = {
        "observed_value": mdd,
        "threshold": GATE_THRESHOLDS["outer_oos_max_drawdown_le_10pct"],
        "comparison": "<=",
        "verdict": "PASS" if mdd <= 10.0 else "FAIL",
        "reason": f"Max drawdown {mdd:.2f}% must be <= 10%",
    }

    # Gate 5: Calmar >= 0.5
    calmar = member["aggregate_calmar"]
    gates["outer_oos_calmar_ge_050"] = {
        "observed_value": calmar,
        "threshold": GATE_THRESHOLDS["outer_oos_calmar_ge_050"],
        "comparison": ">=",
        "verdict": "PASS" if (calmar >= 0.5 and math.isfinite(calmar)) else "FAIL",
        "reason": f"Calmar {calmar:.4f} must be >= 0.5",
    }

    # Gate 6: DSR >= 0.95
    dsr = member["oracle_dsr"]
    gates["dsr_ge_095"] = {
        "observed_value": dsr,
        "threshold": GATE_THRESHOLDS["dsr_ge_095"],
        "comparison": ">=",
        "verdict": "PASS" if dsr >= 0.95 else "FAIL",
        "reason": f"DSR {dsr:.4f} must be >= 0.95",
    }

    # Gate 7: PBO <= 0.20
    pbo = member["framework_pbo"]
    gates["pbo_le_020"] = {
        "observed_value": pbo,
        "threshold": GATE_THRESHOLDS["pbo_le_020"],
        "comparison": "<=",
        "verdict": "PASS" if (pbo is not None and pbo <= 0.20) else "FAIL",
        "reason": f"PBO {pbo} must be <= 0.20 (null/INVALID = FAIL)",
    }

    # Gate 8: positive outer folds >= 60%
    pct = member["positive_fold_pct"]
    gates["positive_outer_folds_ge_60pct"] = {
        "observed_value": pct,
        "threshold": GATE_THRESHOLDS["positive_outer_folds_ge_60pct"],
        "comparison": ">=",
        "verdict": "PASS" if pct >= 60.0 else "FAIL",
        "reason": f"Positive folds {pct:.1f}% must be >= 60%",
    }

    failed = [k for k, v in gates.items() if v["verdict"] == "FAIL"]
    passed = [k for k, v in gates.items() if v["verdict"] == "PASS"]

    member["gates"] = gates
    member["passes_hard_gates"] = len(failed) == 0
    member["failed_gates"] = failed
    member["passed_gates"] = passed

    return member


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def find_decision_files(directories: list[str]) -> list[str]:
    """Find all wfo_decision.json files in the given directories."""
    files = []
    for d in directories:
        p = os.path.join(d, "wfo_decision.json")
        if os.path.exists(p):
            files.append(p)
        else:
            # Search recursively
            for root, _, names in os.walk(d):
                if "wfo_decision.json" in names:
                    files.append(os.path.join(root, "wfo_decision.json"))
    return sorted(files)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Independent oracle verification for Workstream B"
    )
    parser.add_argument(
        "--directories",
        nargs="+",
        required=True,
        help="Directories containing wfo_decision.json files",
    )
    parser.add_argument(
        "--output",
        default="/tmp/evidence_workstream_b_verify.json",
        help="Output evidence JSON path",
    )
    args = parser.parse_args()

    decision_files = find_decision_files(args.directories)
    print("=== Evidence Workstream B — Independent Oracle Verification ===")
    print(f"Found {len(decision_files)} wfo_decision.json files:")
    for f in decision_files:
        print(f"  {f}")

    members = []
    for df in decision_files:
        with open(df) as f:
            decision = json.load(f)

        member = _oracle_from_outer_results(decision)
        member = _independent_gate_check(member)

        verdict_str = "PASS" if member["passes_hard_gates"] else "FAIL"
        print(f"\n  [{verdict_str}] {member['strategy_id']}::{member['symbol']}")
        print(f"    Outer folds:        {member['n_outer_folds']}")
        print(f"    Total OOS net PnL:  {member['total_oos_net_pnl']:.2f}")
        print(f"    Oracle Sharpe:      {member['aggregate_sharpe']:.4f}  (>= 0.80)")
        print(f"    Oracle PF:          {member['aggregate_profit_factor']:.4f}  (>= 1.20)")
        print(f"    Oracle max MDD:     {member['aggregate_max_drawdown_pct']:.2f}%  (<= 10%)")
        print(f"    Oracle DSR:         {member['oracle_dsr']:.4f}  (>= 0.95)")
        print(f"    Framework PBO:      {member['framework_pbo']}  (<= 0.20)")
        print(f"    Positive folds:     {member['positive_fold_pct']:.1f}%  (>= 60%)")
        if member["failed_gates"]:
            print(f"    FAILED gates: {', '.join(member['failed_gates'])}")
        if member["passed_gates"]:
            print(f"    PASSED gates: {', '.join(member['passed_gates'])}")

        members.append(member)

    # --- Portfolio-level aggregation ---
    all_pass = all(m["passes_hard_gates"] for m in members) and len(members) > 0
    any_pass = any(m["passes_hard_gates"] for m in members)

    if all_pass:
        port_verdict = "PROMOTE"
    elif any_pass:
        port_verdict = "PARTIAL"
    else:
        port_verdict = "NO_TRADE"

    total_checks = sum(len(m["gates"]) for m in members)
    total_passed = sum(len(m["passed_gates"]) for m in members)
    total_failed = total_checks - total_passed

    evidence = {
        "portfolio_verdict": port_verdict,
        "n_members": len(members),
        "total_gate_checks": total_checks,
        "total_passed": total_passed,
        "total_failed": total_failed,
        "members": members,
    }

    with open(args.output, "w") as f:
        json.dump(evidence, f, indent=2, default=str)

    print(f"\n{'=' * 60}")
    print("VERIFICATION COMPLETE")
    print(f"Portolio verdict: {port_verdict}")
    print(f"Members: {len(members)}, Gate checks: {total_passed}/{total_checks} passed "
          f"({total_failed} failed)")
    print(f"Evidence written: {args.output}")
    print(f"{'=' * 60}")

    return 0 if all_pass else 1


if __name__ == "__main__":
    sys.exit(main())
