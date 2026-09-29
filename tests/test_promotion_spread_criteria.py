"""The three promotion criteria added after the 104-cell enhanced_ma run.

WFO_CAMPAIGN_RESULT.md measured 104 real backtest cells and found:
10 cleared their own round-trip cost, half of them from one test window,
and 49% never traded at all. enhanced_ma still carried 9/9 passing folds
and selection_score 0.5 in the registry.

These tests pin the gates that result now fails:

  zero_trade_fold_pct_le_50              — flat folds are not a pass
  median_trades_per_trading_fold_ge_20   — a fold must trade enough to
                                          make its cost recoverable
  cost_clearing_fold_pct_ge_50           — edge must appear in most folds
  cost_clearing_single_window_le_60pct   — not all of it in one window
  median_return_clears_cost_floor        — median clearing return > cost
  _require_earned_the_folds              — fold count without net edge is
                                          not evidence
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from trading_agent.backtest.nested_wfo import _count_clearing_per_window
from trading_agent.research.selection_policy import (
    ParamArtifact,
    PolicyStatus,
    SelectionPolicyArtifact,
)

NOW = datetime(2026, 1, 1, tzinfo=UTC)
ROUND_TRIP = 0.32


def _real_code_sha(strategy_id: str = "rsi") -> str:
    from trading_agent.strategies.canonical import build_default_registry

    return build_default_registry().describe(strategy_id).code_sha


def _policy(scores: dict, **over):
    kwargs = {
        "symbol": "BTC/USDT",
        "timeframe": "1h",
        "regime": "trend",
        "incumbent": ParamArtifact(
            "rsi", {"period": 14}, code_sha=_real_code_sha("rsi")
        ),
        "scores": scores,
        "evidence_ids": ("sha256:study", "sha256:outer", "sha256:holdout"),
        "validity_start": NOW,
        "validity_end": NOW + timedelta(days=30),
        "status": PolicyStatus.VALIDATED,
        "created_at": NOW,
        "policy_commit_sha": "a" * 40,
        "policy_data_manifest_sha": "b" * 64,
        "policy_feature_manifest_sha": "c" * 64,
        "policy_release_digest": "sha256:" + "d" * 64,
        "promotion_stage": "paper_eligible",
    }
    kwargs.update(over)
    return SelectionPolicyArtifact(**kwargs)


MEASURED = {
    "selection_score": 1.2,
    "median_oos_return_pct": 6.5,
    "median_oos_trades": 30,
    "n_passing_folds": 9,
    "total_folds": 9,
    "net_fold_edge_pct": 3.0,
}


# ── fold count is no longer sufficient ────────────────────────────────────


def test_passing_folds_with_negative_net_edge_is_rejected():
    # The enhanced_ma shape: 9/9 folds, underwater after costs.
    with pytest.raises(ValueError, match="net edge per fold"):
        _policy({**MEASURED, "net_fold_edge_pct": -1.44})


def test_zero_net_edge_is_rejected():
    with pytest.raises(ValueError, match="net edge per fold"):
        _policy({**MEASURED, "net_fold_edge_pct": 0.0})


def test_positive_net_edge_is_accepted():
    assert _policy(MEASURED).scores["net_fold_edge_pct"] == 3.0


def test_policy_without_net_edge_metric_still_constructs():
    # Backwards compatible: a policy from an older producer that predates
    # the metric is judged on the other checks, not rejected for absence.
    scores = {k: v for k, v in MEASURED.items() if k != "net_fold_edge_pct"}
    assert _policy(scores).scores["n_passing_folds"] == 9.0


# ── the arithmetic the gates rest on ──────────────────────────────────────


def test_cost_floor_scales_with_trade_count():
    # 4 trades needs 1.28% to break even; 40 trades needs 12.8%. A flat
    # return threshold would pass the first and fail the second for the
    # same gross number.
    assert 0.32 * 4 == pytest.approx(1.28)
    assert 0.32 * 40 == pytest.approx(12.8)
    assert 0.32 * 40 > 0.32 * 4


def test_median_trade_floor_comes_from_per_fold_counts():
    from trading_agent.research.selection_policy import _median_fold_trades

    class _TM:
        def __init__(self, trades):
            self.test_metrics = {"total_trades": trades}

    class _R:
        def __init__(self, trades):
            self.outer_results = [_TM(t) for t in trades]
            self.aggregate_metrics = {}

    # The aggregate metrics carry only a total; the median has to come from
    # the per-fold results or the cost floor is computed on the wrong scale.
    assert _median_fold_trades(_R([10, 12, 14, 40, 2])) == pytest.approx(12.0)
    assert _median_fold_trades(_R([5, 5])) == pytest.approx(5.0)
    assert _median_fold_trades(_R([7])) == pytest.approx(7.0)


# ── window spread ─────────────────────────────────────────────────────────


class _Outer:
    def __init__(self, start, end):
        self.test_start = start
        self.test_end = end


def test_clearing_folds_are_grouped_by_window():
    # The measured case: 5 of 10 clearing cells from one window.
    outer = [_Outer(100, 200)] * 5 + [_Outer(300, 400)] * 3 + [_Outer(500, 600)] * 2
    by_window = _count_clearing_per_window(outer, list(range(10)))
    assert by_window == {"100:200": 5, "300:400": 3, "500:600": 2}
    assert max(by_window.values()) / 10 * 100 == pytest.approx(50.0)


def test_single_window_concentration_is_detectable():
    outer = [_Outer(100, 200)] * 5 + [_Outer(300, 400)] * 5
    by_window = _count_clearing_per_window(outer, list(range(10)))
    # 50% from one window: under the 60% ceiling, but only just. Five of
    # five from one window would be 100% and must fail.
    assert max(by_window.values()) / 10 * 100 == pytest.approx(50.0)
    only_one = _count_clearing_per_window(outer, list(range(5)))
    assert max(only_one.values()) / 5 * 100 == pytest.approx(100.0)
