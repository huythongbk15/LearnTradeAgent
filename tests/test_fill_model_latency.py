"""Tests for FillModel latency model."""

from trading_agent.execution.simulator.fill_model import FillModel
from trading_agent.execution.simulator.models import SimulationConfig


class TestLatencyFillModel:
    def test_total_latency_ms(self):
        cfg = SimulationConfig(
            submit_latency_ms=50, ack_latency_ms=20, network_latency_ms=30
        )
        fm = FillModel(cfg)
        assert fm.total_latency_ms == 100.0

    def test_fill_probability_low_latency(self):
        cfg = SimulationConfig(
            submit_latency_ms=50, ack_latency_ms=20, network_latency_ms=30
        )
        fm = FillModel(cfg)
        # 100ms latency on 1h bar → nearly certain fill
        assert fm.fill_probability(3_600_000) > 0.99

    def test_fill_probability_decays_with_shorter_bars(self):
        cfg = SimulationConfig(
            submit_latency_ms=2000, ack_latency_ms=1500, network_latency_ms=1500
        )
        fm = FillModel(cfg)
        prob_1h = fm.fill_probability(3_600_000)
        prob_1m = fm.fill_probability(60_000)
        assert prob_1m < prob_1h
        assert prob_1m < 0.90

    def test_fill_probability_floor(self):
        cfg = SimulationConfig(
            submit_latency_ms=60_000, ack_latency_ms=60_000, network_latency_ms=60_000
        )
        fm = FillModel(cfg)
        # Even with huge latency, fill prob floor at 0.01
        assert fm.fill_probability(60_000) >= 0.01

    def test_latency_fill_adjustment(self):
        cfg = SimulationConfig(
            submit_latency_ms=2000, ack_latency_ms=1500, network_latency_ms=1500
        )
        fm = FillModel(cfg)
        fp, adv = fm.latency_fill_adjustment(60_000)
        assert 0 < fp < 1
        assert adv > 0

    def test_passive_fill_prob_reduced_by_latency(self):
        """fill_limit passive fill rate is reduced by latency factor."""
        cfg_low = SimulationConfig(passive_fill_prob=0.3)
        fm_low = FillModel(cfg_low)
        assert fm_low.fill_probability(3_600_000) > 0.99
        effective_low = cfg_low.passive_fill_prob * fm_low.fill_probability(3_600_000)
        assert effective_low > 0.29

        cfg_high = SimulationConfig(
            passive_fill_prob=0.3,
            submit_latency_ms=2000, ack_latency_ms=1500, network_latency_ms=1500,
        )
        fm_high = FillModel(cfg_high)
        prob = fm_high.fill_probability(300_000)
        effective_high = cfg_high.passive_fill_prob * prob
        assert effective_high < 0.3
        assert effective_high > 0.01
