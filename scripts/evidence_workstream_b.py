#!/usr/bin/env python3
"""Workstream B — Real nested WFO campaign trên data thực (BTC/ETH/SOL @ 1h).

Chạy nested WFO trên locked scope (3 pairs × 3 strategies × 3 cost scenarios)
với frozen holdout từ data/research_manifest.json.

Mỗi spec chạy trong một subprocess riêng biệt (subprocess.Popen) để
tận dụng song song trên 12 cores. Cell-level backtests chạy inline
(không spawn subprocess mỗi cell) để tránh overhead.

Evidence output: /tmp/ac_workstream_b.json
"""
from __future__ import annotations

import json
import math
import os
import pickle
import subprocess
import sys
import time
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import patch

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

# ── Monkeypatch: disable per-cell multiprocessing (inline cells) ─────────
from trading_agent.backtest import tournament as _tournament
from trading_agent.backtest import nested_wfo as _nested_wfo

_orig_run_cell = _tournament.run_cell


def _fast_run_cell(spec, *, out_root=None, start=0, end=None, **kwargs):
    """Inline cell runner — no subprocess spawn, runs _run_cell_impl directly."""
    kwargs.pop("_use_multiprocessing", None)
    kwargs["_use_multiprocessing"] = False
    kwargs.pop("_resource_budget", None)
    return _orig_run_cell(spec, out_root=out_root, start=start, end=end, **kwargs)


_tournament.run_cell = _fast_run_cell
_nested_wfo.run_cell = _fast_run_cell
_nested_wfo.run_cell.__module__ = _orig_run_cell.__module__


from trading_agent.backtest.nested_wfo import (
    WFOResult,
    WFOSpec,
    run_nested_wfo,
    _build_portfolio_selection_result,
)
from trading_agent.backtest.tournament import (
    SCENARIO_BASE,
    SCENARIO_DOUBLE,
    SCENARIO_SLIPPAGE_STRESS,
    CostScenario,
    EvaluationCellSpec,
    run_cell,
)
from trading_agent.backtest.synthetic_data import seed_safe
from trading_agent.backtest.scope_lock import r04_default_scope
from trading_agent.alpha_research.holdout import load_manifest, holdout_window
from trading_agent.data.storage import load_ohlcv

# ── Locked scope constants ───────────────────────────────────────────────

LOCKED_PAIRS = ("BTC/USDT", "ETH/USDT", "SOL/USDT")
LOCKED_STRATEGIES = ("rsi", "ma_adx", "enhanced_ma")
LOCKED_TIMEFRAME = "1h"

COST_SCENARIOS: tuple[CostScenario, ...] = (
    SCENARIO_BASE,
    SCENARIO_DOUBLE,
    SCENARIO_SLIPPAGE_STRESS,
)

# Param grids (single value each for tractable runtime)
PARAM_GRIDS: dict[str, dict[str, list[Any]]] = {
    "rsi": {"period": [14], "oversold": [30], "overbought": [70]},
    "ma_adx": {"fast_period": [20], "slow_period": [80], "adx_threshold": [25]},
    "enhanced_ma": {"fast_period": [20], "slow_period": [80], "adx_threshold": [25]},
}


def _build_specs() -> list[WFOSpec]:
    """Build WFOSpec for every locked pair x strategy."""
    specs: list[WFOSpec] = []
    for pair in LOCKED_PAIRS:
        for strategy in LOCKED_STRATEGIES:
            grid = PARAM_GRIDS.get(strategy, {})
            spec = WFOSpec(
                strategy_id=strategy,
                symbol=pair,
                timeframe=LOCKED_TIMEFRAME,
                param_grid=grid,
                cost_scenarios=COST_SCENARIOS,
                train_months=12,
                val_months=3,
                test_months=3,
                step_months=3,
                min_trades_per_fold=10,
                min_oos_trades=30,
                search_family="workstream_b",
                seed=seed_safe(strategy, pair),
                evidence_class="REAL_MARKET",
                registry_path=f"data/wvo/workstream_b_{strategy}_{pair.replace('/', '_')}.sqlite3",
            )
            specs.append(spec)
    return specs


# ── Independent Oracle ───────────────────────────────────────────────────

def _oracle_sharpe(returns: list[float]) -> float:
    """Independent Sharpe: mean / std of per-fold returns (annualized for 1h)."""
    if len(returns) < 2:
        return 0.0
    arr = np.array(returns, dtype=float)
    mean = arr.mean()
    std = arr.std(ddof=1)
    if std == 0:
        return 0.0
    return float(mean / std * math.sqrt(252 * 24))


def _oracle_profit_factor(pnls: list[float]) -> float:
    """Independent PF: gross wins / |gross losses|."""
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    gw = sum(wins) if wins else 0.0
    gl = abs(sum(losses)) if losses else 1e-9
    return gw / gl if gl > 0 else (float("inf") if gw > 0 else 0.0)


def _coerce_num(v: Any) -> float | None:
    if v is None:
        return None
    if isinstance(v, str):
        try:
            return float(v)
        except ValueError:
            return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _oracle_max_drawdown(equity: list[float]) -> float:
    """Independent MDD from equity curve (percentage)."""
    if not equity:
        return 0.0
    arr = np.array(equity, dtype=float)
    peak = np.maximum.accumulate(arr)
    drawdowns = (arr - peak) / peak
    return float(-np.min(drawdowns)) * 100.0 if len(np.unique(peak)) > 1 else 0.0


def _dsr_from_skew(skew: float, n: int) -> float:
    """DSR approximation from skewness."""
    if n < 2:
        return 0.0
    denom = math.sqrt((1.0 + skew ** 2 / n) / n)
    return float(skew / denom) if denom != 0 else 0.0


def _oracle_from_outer_results(result: WFOResult) -> dict[str, Any]:
    """Independent oracle: recompute metrics from outer fold artifacts.

    Reads report.json from each outer fold's EvaluationArtifact, extracts
    per-trade PnL, and recomputes Sharpe/PF/MDD/DSR independently.
    Does NOT trust the WFO module's internal metrics.
    """
    pnls: list[float] = []
    fold_returns: list[float] = []
    fold_trades: list[int] = []
    positive_folds = 0
    total_folds = 0
    equity_curve: list[float] = [10000.0]

    for outer in result.outer_results:
        if not outer.artifact or not outer.artifact.report_path:
            continue
        report_path = Path(outer.artifact.report_path)
        if not report_path.exists():
            continue
        try:
            report = json.loads(report_path.read_text())
        except Exception:
            continue

        trades = report.get("trades") or []
        fold_pnl = 0.0
        for t in trades:
            pnl = t.get("pnl")
            if pnl is not None:
                fold_pnl += float(pnl)

        fold_return_pct = _coerce_num(report.get("total_return_pct")) or 0.0
        fold_trade_count = int(report.get("total_trades", len(trades)))

        pnls.append(fold_pnl)
        fold_returns.append(fold_return_pct)
        fold_trades.append(fold_trade_count)
        total_folds += 1
        if fold_return_pct > 0:
            positive_folds += 1
        equity_curve.append(equity_curve[-1] + fold_pnl)

    total_pnl = sum(pnls) if pnls else 0.0
    total_trades = sum(fold_trades) if fold_trades else 0
    median_return = float(np.median(fold_returns)) if fold_returns else 0.0
    median_pnl = float(np.median(pnls)) if pnls else 0.0
    positive_pct = (positive_folds / total_folds * 100.0) if total_folds else 0.0
    sharpe = _oracle_sharpe(fold_returns)
    pf = _oracle_profit_factor(pnls)
    mdd = _oracle_max_drawdown(equity_curve)

    if len(pnls) >= 2 and np.std(pnls, ddof=1) > 0:
        mean_pnl = np.mean(pnls)
        std_pnl = np.std(pnls, ddof=1)
        skew = float(np.mean(((pnls - mean_pnl) / std_pnl) ** 3))
        dsr = _dsr_from_skew(skew, len(pnls))
    else:
        dsr = 0.0

    return {
        "total_oos_net_pnl": total_pnl,
        "oracle_pnls": pnls,
        "oracle_fold_returns": fold_returns,
        "median_return_pct": median_return,
        "median_net_pnl": median_pnl,
        "total_oos_trades": total_trades,
        "positive_folds_pct": positive_pct,
        "n_outer_folds": total_folds,
        "oracle_sharpe": sharpe,
        "oracle_profit_factor": pf,
        "oracle_max_drawdown_pct": mdd,
        "oracle_dsr": dsr,
    }


def _oracle_portfolio(results: list[WFOResult]) -> dict[str, Any]:
    """Independent oracle at portfolio level: aggregate all member metrics."""
    all_pnls: list[float] = []
    all_fold_returns: list[float] = []
    all_trade_counts: list[int] = []
    positive_pairs = 0
    total_pairs = 0
    per_member = []

    for result in results:
        oracle = _oracle_from_outer_results(result)
        all_pnls.extend(oracle.get("oracle_pnls", []))
        all_fold_returns.extend(oracle.get("oracle_fold_returns", []))
        all_trade_counts.append(oracle["total_oos_trades"])
        total_pairs += 1
        if oracle["median_return_pct"] > 0:
            positive_pairs += 1

        per_member.append({
            "candidate": f"{result.spec.strategy_id}::{result.spec.symbol}",
            "oracle_metrics": {
                "total_oos_net_pnl": oracle["total_oos_net_pnl"],
                "median_return_pct": oracle["median_return_pct"],
                "total_oos_trades": oracle["total_oos_trades"],
                "oracle_sharpe": oracle["oracle_sharpe"],
                "oracle_profit_factor": oracle["oracle_profit_factor"],
                "oracle_max_drawdown_pct": oracle["oracle_max_drawdown_pct"],
                "positive_folds_pct": oracle["positive_folds_pct"],
                "n_outer_folds": oracle["n_outer_folds"],
                "oracle_dsr": oracle["oracle_dsr"],
            },
            "wfo_aggregate_metrics": result.aggregate_metrics,
            "passes_hard_gates": result.passes_hard_gates,
            "gate_failures": result.gate_failures,
            "final_holdout": result.final_holdout,
        })

    total_pnl = sum(all_pnls) if all_pnls else 0.0
    total_trades = sum(all_trade_counts) if all_trade_counts else 0
    positive_pct = (positive_pairs / total_pairs * 100.0) if total_pairs else 0.0
    median_return = float(np.median(all_fold_returns)) if all_fold_returns else 0.0
    sharpe = _oracle_sharpe(all_fold_returns)
    pf = _oracle_profit_factor(all_pnls)

    member_pnls = [m["oracle_metrics"]["total_oos_net_pnl"] for m in per_member]
    positive_member_pnls = [p for p in member_pnls if p > 0]
    concentration = (
        max(positive_member_pnls) / sum(positive_member_pnls) * 100.0
        if positive_member_pnls else None
    )

    # Cost stress from sensitivity
    cost_2x_total = 0.0
    slip_stress_total = 0.0
    any_real_sens = False
    real_stat_hardening = False

    for result in results:
        sens = result.aggregate_metrics.get("sensitivity", {})
        if sens.get("real_computed"):
            any_real_sens = True
            c2x = sens.get("cost_2x", {})
            if c2x:
                c2x_agg = c2x.get("aggregate", {})
                # Prefer total_net_pnl; fall back to summing per-fold net_pnl
                net = _coerce_num(c2x_agg.get("total_net_pnl"))
                if net is None:
                    folds = c2x.get("folds", {})
                    net = sum(
                        _coerce_num(f.get("net_pnl")) or 0.0
                        for f in folds.values()
                    ) if folds else 0.0
                if net is not None:
                    cost_2x_total += net
            slip = sens.get("slippage_stress", {})
            if slip:
                slip_agg = slip.get("aggregate", {})
                net = _coerce_num(slip_agg.get("total_net_pnl"))
                if net is None:
                    folds = slip.get("folds", {})
                    # Try summing net_pnl from folds; if missing, sum return_pct as proxy
                    fold_nets = [_coerce_num(f.get("net_pnl")) for f in folds.values()]
                    if any(n is not None for n in fold_nets):
                        net = sum(n or 0.0 for n in fold_nets)
                    else:
                        ret_pcts = [_coerce_num(f.get("return_pct")) for f in folds.values()]
                        net = sum(r or 0.0 for r in ret_pcts) if ret_pcts else 0.0
                if net is not None:
                    slip_stress_total += net

    for result in results:
        sh = result.statistical_hardening
        if sh.get("dsr") is not None and sh.get("pbo") is not None:
            real_stat_hardening = True
            break

    return {
        "total_oos_net_pnl": total_pnl,
        "total_oos_trades": total_trades,
        "positive_pairs_pct": positive_pct,
        "median_pair_return_pct": median_return,
        "concentration_pct": concentration,
        "oracle_sharpe": sharpe,
        "oracle_profit_factor": pf,
        "cost_2x_total_net_pnl": cost_2x_total,
        "slippage_stress_total_net_pnl": slip_stress_total,
        "real_sensitivity_computed": any_real_sens,
        "real_statistical_hardening": real_stat_hardening,
        "n_members": total_pairs,
        "per_member": per_member,
    }


# ── Evidence checks ──────────────────────────────────────────────────────

def _check_holdout_protection(results: list[WFOResult]) -> list[dict]:
    """Negative case: verify holdout was frozen and runs on real manifest window."""
    checks: list[dict] = []

    try:
        manifest = load_manifest()
        hs, he = holdout_window(manifest)
        checks.append({
            "check_id": "C_neg_holdout_manifest_loaded",
            "oracle": "manifest integrity (SHA-256 verified)",
            "observed": True,
            "threshold": True,
            "verdict": "PASS",
            "reason": f"Manifest loaded; holdout {hs.date()}..{he.date()}",
        })
    except Exception as e:
        checks.append({
            "check_id": "C_neg_holdout_manifest_loaded",
            "oracle": "manifest integrity",
            "observed": str(e),
            "threshold": True,
            "verdict": "FAIL",
            "reason": f"Cannot load holdout manifest: {e}",
        })
        return checks

    for result in results:
        sm = getattr(result, "study_manifest", None)
        if sm is not None:
            checks.append({
                "check_id": f"C_holdout_provenance::{result.spec.strategy_id}::{result.spec.symbol}",
                "oracle": "study manifest identity",
                "observed": {
                    "evidence_class": sm.evidence_class,
                    "worktree_dirty": sm.worktree_dirty,
                    "code_sha_prefix": sm.strategy_code_sha[:16],
                    "data_sha_prefix": sm.data_manifest_sha[:16],
                },
                "threshold": "REAL_MARKET + clean worktree + hashes present",
                "verdict": "PASS" if (
                    sm.evidence_class == "REAL_MARKET"
                    and not sm.worktree_dirty
                    and sm.strategy_code_sha
                    and sm.data_manifest_sha
                ) else "FAIL",
                "reason": "Study must have real evidence_class, clean worktree, provenance hashes",
            })

        fh = getattr(result, "final_holdout", None)
        if fh is not None:
            status = fh.get("status")
            hw = fh.get("holdout_window")
            checks.append({
                "check_id": f"C_holdout_one_shot::{result.spec.strategy_id}::{result.spec.symbol}",
                "oracle": "holdout execution record",
                "observed": {"status": status, "holdout_window": hw},
                "threshold": "COMPLETED + frozen window",
                "verdict": "PASS" if (status == "COMPLETED" and hw) else "FAIL",
                "reason": f"Holdout must COMPLETED on frozen manifest window (got {status})",
            })
    return checks


def _check_scope_lock(scope, results: list[WFOResult]) -> list[dict]:
    """Verify all executed specs are within the locked scope."""
    checks: list[dict] = []
    all_within_scope = True
    for result in results:
        pair_ok = result.spec.symbol in scope.pairs
        strat_ok = result.spec.strategy_id in scope.strategies
        cost_ok = all(c.name in scope.cost_scenarios for c in result.spec.cost_scenarios)
        within = pair_ok and strat_ok and cost_ok
        if not within:
            all_within_scope = False
        checks.append({
            "check_id": f"C_scope_lock::{result.spec.strategy_id}::{result.spec.symbol}",
            "oracle": "scope constants comparison",
            "observed": {"pair_ok": pair_ok, "strat_ok": strat_ok, "cost_ok": cost_ok},
            "threshold": "all True",
            "verdict": "PASS" if within else "FAIL",
            "reason": "Spec must match locked scope (R04 scope enforcement)",
        })
    checks.append({
        "check_id": "C_scope_lock::all_members",
        "oracle": "aggregate",
        "observed": {"within_scope": all_within_scope, "n": len(results)},
        "threshold": True,
        "verdict": "PASS" if all_within_scope else "FAIL",
        "reason": "All members must be within locked scope",
    })
    return checks


def _independent_gate_check(oracle_result: dict) -> list[dict]:
    """Check hard gates independently from oracle-computed metrics."""
    checks: list[dict] = []

    def _check(name, value, threshold, op, reason):
        if value is None or (isinstance(value, (float,)) and not math.isfinite(value)):
            verdict = "INVALID"
        elif op == ">":
            verdict = "PASS" if value > threshold else "FAIL"
        elif op == ">=":
            verdict = "PASS" if value >= threshold else "FAIL"
        elif op == "<=":
            verdict = "PASS" if value <= threshold else "FAIL"
        else:
            verdict = "INVALID"
        checks.append({
            "check_id": name,
            "oracle": "independent_recompute",
            "observed": value,
            "threshold": threshold,
            "op": op,
            "verdict": verdict,
            "reason": reason,
        })

    oracle = oracle_result
    all_members_pass = True
    for member in oracle["per_member"]:
        m = member["oracle_metrics"]
        if not member["passes_hard_gates"]:
            all_members_pass = False
        _check(f"member_return_positive::{member['candidate']}",
               m["total_oos_net_pnl"], 0.0, ">",
               "Per-member OOS net PnL must be positive")
        _check(f"member_sharpe_ge_080::{member['candidate']}",
               m["oracle_sharpe"], 0.80, ">=",
               "Per-member OOS Sharpe (annualized) must be >= 0.80")
        _check(f"member_pf_ge_120::{member['candidate']}",
               m["oracle_profit_factor"], 1.20, ">=",
               "Per-member OOS PF must be >= 1.20")
        _check(f"member_mdd_le_10pct::{member['candidate']}",
               m["oracle_max_drawdown_pct"], 10.0, "<=",
               "Per-member OOS max drawdown must be <= 10%")
        _check(f"member_trades_ge_30::{member['candidate']}",
               float(m["total_oos_trades"]), 30.0, ">=",
               "Per-member must have >= 30 OOS trades")
        _check(f"member_positive_folds_ge_60pct::{member['candidate']}",
               m["positive_folds_pct"], 60.0, ">=",
               "Per-member must have >= 60% positive outer folds")

    _check("portfolio_positive_pairs_ge_60pct",
           oracle["positive_pairs_pct"], 60.0, ">=",
           ">=60% of pair-strategy candidates must have positive OOS return")
    _check("portfolio_median_return_positive",
           oracle["median_pair_return_pct"], 0.0, ">",
           "Portfolio median pair return must be > 0%")
    _check("portfolio_concentration_le_35pct",
           oracle["concentration_pct"], 35.0, "<=",
           "Largest positive pair contribution must be <= 35%")
    _check("portfolio_total_trades_ge_200",
           float(oracle["total_oos_trades"]), 200.0, ">=",
           "Portfolio must have >= 200 total OOS trades")
    _check("cost_2x_net_positive",
           oracle["cost_2x_total_net_pnl"], 0.0, ">",
           "Cost 2x stress: total portfolio net PnL must be positive")
    _check("slippage_stress_net_positive",
           oracle["slippage_stress_total_net_pnl"], 0.0, ">",
           "Slippage stress: total portfolio net PnL must be positive")
    _check("real_sensitivity_computed",
           1.0 if oracle["real_sensitivity_computed"] else 0.0, 1.0, ">=",
           "Sensitivity analysis must be real (not synthetic placeholder)")
    _check("real_statistical_hardening",
           1.0 if oracle["real_statistical_hardening"] else 0.0, 1.0, ">=",
           "Statistical hardening (DSR/PBO) must be real")
    _check("all_members_pass_hard_gates",
           1.0 if all_members_pass else 0.0, 1.0, ">=",
           "Every member pair/strategy must pass its own hard gates")

    return checks


# ── Spec runner (called as subprocess) ────────────────────────────────────

def run_single_spec(spec_index: int) -> None:
    """Run one spec by index, pickle result to file. Used by subprocess.Popen."""
    specs = _build_specs()
    spec = specs[spec_index]
    out_root = Path(f"/tmp/wvo_workstream_b/{spec.strategy_id}__{spec.symbol.replace('/', '_')}__{spec.timeframe}")
    out_root.mkdir(parents=True, exist_ok=True)

    result = run_nested_wfo(
        spec,
        out_root=out_root,
        run_holdout=True,
        real_sensitivity=True,
        cell_runner=_fast_run_cell,
    )

    result_file = out_root / "wfo_result.pkl"
    with open(result_file, "wb") as f:
        pickle.dump(result, f, protocol=pickle.HIGHEST_PROTOCOL)
    print(f"[spec {spec_index}] {spec.strategy_id}::{spec.symbol} DONE — verdict={result.passes_hard_gates}")


# ── Main ─────────────────────────────────────────────────────────────────

def main() -> int:
    out_root = Path("/tmp/wvo_workstream_b")
    out_root.mkdir(parents=True, exist_ok=True)

    print("=== Workstream B: Real nested WFO campaign (parallel subprocesses) ===")
    scope = r04_default_scope()
    print(f"  Scope: {scope.scope_id[:32]}...")
    print(f"  Pairs: {scope.pairs}")
    print(f"  Strategies: {scope.strategies}")
    print(f"  Cost scenarios: {scope.cost_scenarios}")

    specs = _build_specs()
    print(f"  Specs: {len(specs)}")
    n_workers = min(len(specs), 4)
    print(f"  Parallel workers: {n_workers}")

    t0 = time.time()

    # Spawn subprocess per spec — redirect stdout to per-spec log files
    # (NOT pipes: p.wait() without draining causes pipe-buffer deadlock)
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    procs: list[subprocess.Popen] = []
    log_fds: list[Any] = []

    for i, spec in enumerate(specs):
        # Skip specs that already have pickle (idempotent restart)
        spec_dir = Path(f"/tmp/wvo_workstream_b/{spec.strategy_id}__{spec.symbol.replace('/', '_')}__{spec.timeframe}")
        if (spec_dir / "wfo_result.pkl").exists():
            print(f"  SKIP spec {i}: {spec.strategy_id}::{spec.symbol} (pickle exists)")
            continue
        while len(procs) >= n_workers:
            for p in procs:
                if p.poll() is not None:
                    procs.remove(p)
            time.sleep(1)
        log_fd = open(f"/tmp/wvo_workstream_b/spec_{i}_stdout.log", "w")
        log_fds.append(log_fd)
        p = subprocess.Popen(
            [sys.executable, __file__, "--spec", str(i)],
            stdout=log_fd,
            stderr=subprocess.STDOUT,
            env=env,
            cwd=str(ROOT),
        )
        procs.append(p)
        print(f"  Spawned spec {i}: {spec.strategy_id}::{spec.symbol}")

    # Wait for all (communicate drains pipes; safe even with file redirection)
    for p in procs:
        p.communicate()
    for log_fd in log_fds:
        log_fd.close()
    print(f"  All subprocesses complete in {time.time()-t0:.0f}s")

    # Collect results
    results: list[WFOResult] = []
    for i, spec in enumerate(specs):
        out_root = Path(f"/tmp/wvo_workstream_b/{spec.strategy_id}__{spec.symbol.replace('/', '_')}__{spec.timeframe}")
        result_file = out_root / "wfo_result.pkl"
        if result_file.exists():
            with open(result_file, "rb") as f:
                results.append(pickle.load(f))
        else:
            print(f"  WARNING: No result file for spec {i} ({spec.strategy_id}::{spec.symbol})")

    MIN_RESULTS_FOR_PORTFOLIO = 3
    if len(results) < MIN_RESULTS_FOR_PORTFOLIO:
        print(f"  ERROR: Only {len(results)}/{len(specs)} results collected (minimum {MIN_RESULTS_FOR_PORTFOLIO} required)")
        return 1
    elif len(results) < len(specs):
        print(f"  WARNING: Partial completion: {len(results)}/{len(specs)} results collected (minimum {MIN_RESULTS_FOR_PORTFOLIO} — proceeding with partial portfolio)")
        completed_specs = [s.strategy_id + "::" + s.symbol for s in specs if
                           (Path(f"/tmp/wvo_workstream_b/{s.strategy_id}__{s.symbol.replace('/', '_')}__{s.timeframe}/wfo_result.pkl")).exists()]
        print(f"  Completed specs: {completed_specs}")

    # Assemble portfolio result
    portfolio_result = _build_portfolio_selection_result(
        results, run_holdout=True, out_root=out_root
    )
    elapsed = time.time() - t0
    print(f"  Portfolio verdict: {portfolio_result.verdict}")
    print(f"  Total runtime: {elapsed:.0f}s ({n_workers} parallel subprocesses)")

    # Independent Oracle verification
    print("\n--- Independent Oracle Verification ---")
    oracle_result = _oracle_portfolio(results)

    gate_checks = _independent_gate_check(oracle_result)
    holdout_checks = _check_holdout_protection(results)
    scope_checks = _check_scope_lock(scope, results)

    all_checks = gate_checks + holdout_checks + scope_checks
    passed = sum(1 for c in all_checks if c["verdict"] == "PASS")
    failed = sum(1 for c in all_checks if c["verdict"] == "FAIL")
    invalid = sum(1 for c in all_checks if c["verdict"] == "INVALID")

    wfo_passes = portfolio_result.passes_hard_gates
    oracle_passes = failed == 0 and invalid == 0

    conclusion_reasons = [c["reason"] for c in all_checks if c["verdict"] in ("FAIL", "INVALID")]
    conclusion = "FINAL_PASS" if (oracle_passes and wfo_passes) else "NO_TRADE"

    evidence = {
        "workstream": "B",
        "ac_id": "Workstream_B_S3",
        "oracle": (
            "independent_recompute: reads raw trade-level PnL from "
            "outer-fold report.json; recomputes Sharpe/PF/MDD/DSR from "
            "scratch; does NOT trust nested_wfo internal metrics"
        ),
        "scope": scope.to_dict(),
        "n_specs": len(specs),
        "n_parallel_workers": n_workers,
        "specs": [
            {"strategy": s.strategy_id, "symbol": s.symbol,
             "param_grid": s.param_grid,
             "cost_scenarios": [c.name for c in s.cost_scenarios],
             "evidence_class": s.evidence_class}
            for s in specs
        ],
        "portfolio_verdict_wfo": portfolio_result.verdict,
        "portfolio_verdict_oracle": "PASS" if oracle_passes else "FAIL",
        "wfo_passes_hard_gates": wfo_passes,
        "oracle_passes_hard_gates": oracle_passes,
        "elapsed_seconds": round(elapsed, 1),
        "oracle_metrics": {k: v for k, v in oracle_result.items() if k != "per_member"},
        "per_member": oracle_result["per_member"],
        "gate_checks": all_checks,
        "total_checks": len(all_checks),
        "passed": passed,
        "failed": failed,
        "invalid": invalid,
        "conclusion": conclusion,
        "conclusion_reasons": conclusion_reasons if conclusion != "FINAL_PASS" else [],
        "created_at": datetime.now(UTC).isoformat(),
    }

    out_file = Path("/tmp/ac_workstream_b.json")
    out_file.write_text(
        json.dumps(evidence, indent=2, default=str, allow_nan=False),
        encoding="utf-8",
    )
    print(f"\nEvidence written: {out_file}")
    print(f"\n=== WORKSTREAM B CONCLUSION: {conclusion} ===")
    print(f"Total checks: {len(all_checks)}, PASS: {passed}, FAIL: {failed}, INVALID: {invalid}")

    return 0 if conclusion == "FINAL_PASS" else 1


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--spec":
        run_single_spec(int(sys.argv[2]))
    else:
        sys.exit(main())
