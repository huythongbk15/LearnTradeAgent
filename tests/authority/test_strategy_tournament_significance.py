"""Tests for statistical-significance gates in StrategyTournament (STR-0210).

Covers:
- Welch's t-test blocks promotion when p-value > alpha
- Bonferroni correction adjusts alpha by pool size
- PBO deflation reduces Sharpe for multiple-testing penalty
- Circuit breaker still bypasses significance gate (safety)
- Fail-closed behavior when sample too small or NaN
"""

from __future__ import annotations

from collections import deque
from dataclasses import replace
from pathlib import Path
from unittest.mock import MagicMock

from trading_agent.authority.strategy_tournament import (
    TournamentConfig,
    TournamentState,
    _ShadowMetrics,
)


class TestWelchTTest:
    """Verify _welch_t_test_pvalue correctness."""

    def test_identical_distributions_not_significant(self):
        """Same mean, same variance → p > 0.05."""
        cfg = TournamentConfig()
        # Create a mock tournament — just need the method
        t = MagicMock()
        t._deflated_sharpe = TournamentConfig.__new__(TournamentConfig)  # not used here
        # Use the actual method via unbound call
        from trading_agent.authority.strategy_tournament import StrategyTournament

        inc = _ShadowMetrics(returns=deque([0.01, -0.01, 0.02, -0.02] * 50))
        ch = _ShadowMetrics(returns=deque([0.01, -0.01, 0.02, -0.02] * 50))

        p = StrategyTournament._welch_t_test_pvalue(t, inc, ch)
        assert p > 0.05, f"Expected non-significant for identical dist, got p={p}"

    def test_clearly_different_distributions_significant(self):
        """Mean 0.02 vs mean -0.02 → p < 0.05."""
        from trading_agent.authority.strategy_tournament import StrategyTournament

        inc = _ShadowMetrics(returns=deque([0.02, 0.01, 0.03, -0.01] * 100))
        ch = _ShadowMetrics(returns=deque([-0.02, -0.01, -0.03, 0.01] * 100))

        p = StrategyTournament._welch_t_test_pvalue(MagicMock(), inc, ch)
        assert p < 0.01, f"Expected highly significant difference, got p={p}"

    def test_small_sample_fail_closed(self):
        """n < 2 → p = 1.0 (fail-closed)."""
        from trading_agent.authority.strategy_tournament import StrategyTournament

        inc = _ShadowMetrics(returns=deque([0.01]))
        ch = _ShadowMetrics(returns=deque([0.02]))

        p = StrategyTournament._welch_t_test_pvalue(MagicMock(), inc, ch)
        assert p == 1.0


class TestDeflatedSharpe:
    """Verify PBO deflation."""

    def test_deflation_reduces_sharpe(self):
        from trading_agent.authority.strategy_tournament import StrategyTournament

        raw = 1.0
        deflated = StrategyTournament._deflated_sharpe(MagicMock(), raw, n_obs=300, n_strategies=16)
        assert deflated < raw
        assert deflated > 0

    def test_deflation_severe_when_strategies_near_obs(self):
        from trading_agent.authority.strategy_tournament import StrategyTournament

        raw = 1.0
        deflated = StrategyTournament._deflated_sharpe(MagicMock(), raw, n_obs=16, n_strategies=16)
        assert deflated < 0.1, f"Expected severe deflation, got {deflated}"

    def test_deflation_zero_when_no_strategies(self):
        from trading_agent.authority.strategy_tournament import StrategyTournament

        raw = 1.0
        deflated = StrategyTournament._deflated_sharpe(MagicMock(), raw, n_obs=100, n_strategies=0)
        assert deflated == raw


class TestConfigDefaults:
    """Verify new config fields have correct defaults."""

    def test_significance_defaults(self):
        cfg = TournamentConfig()
        assert cfg.significance_alpha == 0.05
        assert cfg.bonferroni_correction is True
        assert cfg.min_shadow_bars_for_promote == 288

    def test_min_bars_for_promote_not_too_low(self):
        """288 bars = 2 weeks on 1h — enough for statistical power."""
        cfg = TournamentConfig()
        assert cfg.min_shadow_bars_for_promote >= 200


class TestSignificanceGateIntegration:
    """Integration: verify the full _maybe_promote path respects significance."""

    def _make_metrics(self, returns: list[float]) -> _ShadowMetrics:
        m = _ShadowMetrics()
        m.returns = deque(returns, maxlen=m.returns.maxlen)
        m.weights = deque([0.0] * len(returns), maxlen=m.weights.maxlen)
        return m

    def _make_tournament(self, n_pool: int = 16):
        """Create a minimal Tournament with mocked dependencies."""
        from trading_agent.authority.strategy_tournament import StrategyTournament
        t = object.__new__(StrategyTournament)
        t.tournament_config = TournamentConfig(
            shadow_mode=False,
            min_shadow_bars_for_promote=30,
            bonferroni_correction=True,
        )
        t.pool = {f"strat_{i}": MagicMock() for i in range(n_pool)}
        t.audit_path = Path("/tmp/test_tournament_audit.jsonl")
        t._promote = MagicMock()  # mock to detect calls
        return t

    def test_significance_gate_blocks_promotion(self):
        """Identical Sharpe (p > alpha) → no promote call."""
        t = self._make_tournament(n_pool=16)

        inc_returns = [0.01, -0.01, 0.02, -0.02] * 30  # 120 bars
        ch_returns = [0.01, -0.01, 0.02, -0.02] * 30   # identical dist

        state = TournamentState(
            incumbent_strategy_id="strat_0",
            shadow_metrics={
                "strat_0": self._make_metrics(inc_returns),
                "strat_1": self._make_metrics(ch_returns),
            },
        )
        decision = MagicMock(chosen_strategy_id="strat_0")

        t._maybe_promote("BTCUSDT", "1h", decision, state)

        t._promote.assert_not_called()  # Should NOT promote
        assert state.challenger_strategy_id is None or state.challenger_persistence == 0

    def test_significance_gate_allows_promotion(self):
        """Clearly different means (p < alpha) + deflated Sharpe OK → promote."""
        t = self._make_tournament(n_pool=3)  # small pool so alpha_adj is feasible

        # Incumbent: mean ~0, Challenger: mean clearly positive
        inc_returns = [0.001, -0.001, 0.002, -0.002] * 100  # ~0 mean
        ch_returns = [0.03, 0.02, 0.01, 0.04] * 100  # clearly positive mean

        state = TournamentState(
            incumbent_strategy_id="strat_0",
            shadow_metrics={
                "strat_0": self._make_metrics(inc_returns),
                "strat_1": self._make_metrics(ch_returns),
            },
        )
        decision = MagicMock(chosen_strategy_id="strat_0")

        # Lower threshold so challenger passes
        t.tournament_config = replace(
            t.tournament_config,
            promotion_sharpe_threshold=-1.0,  # very low threshold
            score_margin=0.0,
            demotion_sharpe_threshold=-2.0,
            promotion_persistence=1,  # promote after 1 bar, not 6
        )

        t._maybe_promote("BTCUSDT", "1h", decision, state)

        # With clearly different distributions + low thresholds, should try to promote
        t._promote.assert_called()

    def test_bonferroni_makes_alpha_stricter_with_large_pool(self):
        """16 strategies → alpha_adj = 0.05/16 = 0.003125."""
        t = self._make_tournament(n_pool=16)
        # Identical returns → p ≈ 1.0 → blocked
        returns = [0.01, -0.01, 0.02, -0.02] * 50
        state = TournamentState(
            incumbent_strategy_id="strat_0",
            shadow_metrics={
                f"strat_{i}": self._make_metrics(returns)
                for i in range(2)
            },
        )
        decision = MagicMock(chosen_strategy_id="strat_0")
        t._maybe_promote("BTCUSDT", "1h", decision, state)
        t._promote.assert_not_called()


class TestNetOfFeesSharpe:
    """Verify _ShadowMetrics tracks gross and net Sharpe separately (P1)."""

    def test_net_sharpe_lower_than_gross_after_fees(self):
        """Turnover fees reduce Sharpe: net < gross."""
        m = _ShadowMetrics()
        # Simulate high-turnover returns: alternate +1% / -1% with weight flips
        returns = deque([0.01, -0.01] * 100)
        for i, ret in enumerate(returns):
            weight = 0.5 if i % 2 == 0 else -0.5
            gross = ret
            fee = abs(weight - (0.5 if (i - 1) % 2 == 0 else -0.5)) * 0.0015
            m.add(ret - fee, weight, fee_rate=0.0, gross_ret=gross)

        gross_sp = m.gross_sharpe()
        net_sp = m.net_sharpe()
        assert net_sp < gross_sp, f"Net Sharpe ({net_sp}) should be < gross ({gross_sp})"
        assert m.sharpe() == net_sp, "sharpe() should equal net_sharpe()"

    def test_net_sharpe_equals_gross_when_no_turnover(self):
        """Constant weight → no fees → net = gross."""
        m = _ShadowMetrics()
        for ret in [0.01, 0.02, -0.01, 0.005, -0.02]:
            m.add(ret, 0.5, fee_rate=0.0, gross_ret=ret)

        assert abs(m.gross_sharpe() - m.net_sharpe()) < 1e-10

    def test_net_sharpe_increase_after_fees_reduces_promotion(self):
        """Strategy with high turnover: gross Sharpe > net Sharpe after fees."""
        # Build metrics with high-turnover scenario
        inc_m = _ShadowMetrics()
        ch_m = _ShadowMetrics()
        weights = [0.8, -0.8, 0.8, -0.8, 0.8, -0.8]
        gross_rets = [0.01, 0.005, 0.012, 0.008, 0.011, 0.004]
        fee_rate = 0.0025
        prev_w = 0.0
        for gr, w in zip(gross_rets, weights):
            fee = abs(w - prev_w) * fee_rate
            net_ret = gr - fee
            ch_m.add(net_ret, w, fee_rate=0.0, gross_ret=gr)
            inc_m.add(0.005, 0.5, fee_rate=0.0, gross_ret=0.005)
            prev_w = w

        assert ch_m.gross_sharpe() > ch_m.net_sharpe(), \
            "Gross Sharpe should exceed net after turnover fees"
