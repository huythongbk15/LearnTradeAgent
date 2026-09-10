#!/usr/bin/env python3
"""
Research Protocol — Defines the unified evaluation protocol for all strategies.

Extends WFOSpec with research-specific configuration:
- Multi-cost scenarios (1x/2x/slip_stress)
- Regime breakdown settings
- Portfolio analysis settings
- Cross-sectional variants (long-only / long-short)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from trading_agent.backtest.nested_wfo import WFOSpec
from trading_agent.backtest.tournament import CostScenario, DEFAULT_SCENARIOS


@dataclass
class ResearchProtocol:
    """Unified research protocol for evaluating strategy candidates.

    This wraps WFOSpec configuration plus research-specific settings
    for portfolio analysis, regime breakdown, and statistical hardening.
    """

    # ── Core WFO settings ──────────────────────────────────────────────────
    strategy_id: str
    symbol: str
    timeframe: str

    # ── Asset universe (for cross-sectional strategies) ──────────────────
    asset_universe: list[str] = field(default_factory=lambda: ["BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT", "XRP/USDT"])

    # ── Fold structure ────────────────────────────────────────────────────
    train_months: int = 12
    val_months: int = 3
    test_months: int = 3
    step_months: int = 3

    # ── Cost scenarios ────────────────────────────────────────────────────
    cost_scenarios: tuple[CostScenario, ...] = DEFAULT_SCENARIOS

    # ── Statistical hardening ─────────────────────────────────────────────
    compute_dsr: bool = True
    compute_pbo: bool = True
    compute_cpcv: bool = True
    block_bootstrap_iters: int = 1000

    # ── Regime breakdown ──────────────────────────────────────────────────
    regime_breakdown: bool = True
    regime_columns: tuple[str, ...] = ("trend_regime", "vol_regime", "trend_dir")

    # ── Portfolio analysis ────────────────────────────────────────────────
    compute_correlation: bool = True
    correlation_window: int = 14  # 14 periods for rolling correlation
    compute_marginal_sharpe: bool = True

    # ── Cross-sectional variants ────────────────────────────────────────
    cs_variant: str | None = None  # "long_only" | "long_short" | None

    # ── Execution settings ────────────────────────────────────────────────
    seed: int = 42
    min_oos_trades: int = 30
    workers: int = 4
    run_holdout: bool = True
    real_sensitivity: bool = True

    # ── Output paths ──────────────────────────────────────────────────────
    registry_path: str = "data/wfo/research.sqlite3"
    search_family: str = "research_protocol"
    evaluator_version: str = "v1"
    out_root: str = "data/backtests/wfo/research"

    def to_wfo_spec(self, param_grid: dict[str, list[Any]]) -> WFOSpec:
        """Convert to WFOSpec for the WFO runner."""
        return WFOSpec(
            strategy_id=self.strategy_id,
            symbol=self.symbol,
            timeframe=self.timeframe,
            param_grid=param_grid,
            cost_scenarios=self.cost_scenarios,
            train_months=self.train_months,
            val_months=self.val_months,
            test_months=self.test_months,
            step_months=self.step_months,
            registry_path=self.registry_path,
            search_family=f"{self.search_family}_{self.strategy_id}_{self.symbol}_{self.timeframe}",
            evaluator_version=self.evaluator_version,
            seed=self.seed,
            min_oos_trades=self.min_oos_trades,
            evidence_class="REAL_MARKET",
        )

    def regime_labels(self) -> list[str]:
        """All regime labels used in breakdown analysis."""
        return [
            "trending_up", "trending_down",
            "ranging_up", "ranging_down",
            "high_vol", "low_vol",
            "crash", "recovery",
            "unknown",
        ]

    def to_dict(self) -> dict[str, Any]:
        return {
            "strategy_id": self.strategy_id,
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "asset_universe": self.asset_universe,
            "train_months": self.train_months,
            "val_months": self.val_months,
            "test_months": self.test_months,
            "cost_scenarios": [c.name for c in self.cost_scenarios],
            "compute_dsr": self.compute_dsr,
            "compute_pbo": self.compute_pbo,
            "regime_breakdown": self.regime_breakdown,
            "compute_correlation": self.compute_correlation,
            "cs_variant": self.cs_variant,
        }
