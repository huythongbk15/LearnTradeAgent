"""Wave D — post-trade adverse-selection analytics (spec §16).

PostTradeImpactReport: records mid at t0/+100ms/+1s/+5s/+30s per fill,
groups by side/quantity/spread/depth/imbalance/volatility/aggressiveness/
order type, and flags bad timing / too-aggressive / predictable adverse
movement / poor-liquidity regime.
"""

from __future__ import annotations

import pytest

from trading_agent.execution.algorithms.adverse_selection import (
    PostTradeImpactReport,
    adverse_move_bps,
    mid_windows,
    record_from_fill,
)
from trading_agent.execution.simulator.models import Fill, SimSide


def make_fill(
    *,
    side: SimSide = SimSide.BUY,
    price: float = 100.0,
    mid_before: float = 100.0,
    mid_after: float = 100.5,
    aggressor: str = "market",
    qty: float = 1000.0,
) -> Fill:
    return Fill(
        order_id=f"F{mid_before}",
        bar_index=1,
        timestamp=None,
        side=side,
        quantity=qty,
        price=price,
        fee=0.0,
        fee_asset="quote",
        aggressor=aggressor,
        level_price=price,
        mid_before=mid_before,
        mid_after=mid_after,
    )


class TestMidWindows:
    def test_linear_interpolation(self):
        w = mid_windows(100.0, 101.0)
        assert w["mid_t0"] == 100.0
        assert w["mid_t+100ms"] == 100.1
        assert w["mid_t+1s"] == 100.25
        assert w["mid_t+5s"] == 100.5
        assert w["mid_t+30s"] == 101.0

    def test_invalid_mid(self):
        with pytest.raises(ValueError):
            mid_windows(0.0, 1.0)


class TestAdverseMove:
    def test_buy_adverse_when_mid_rises(self):
        # Buy, mid moves up after fill → we overpaid → positive adverse.
        assert adverse_move_bps(100.0, 100.1, SimSide.BUY) == pytest.approx(10.0)
        assert adverse_move_bps(100.0, 99.9, SimSide.BUY) == pytest.approx(-10.0)

    def test_sell_adverse_when_mid_falls(self):
        # Sell, mid moves down after fill → bad → positive adverse.
        assert adverse_move_bps(100.0, 99.9, SimSide.SELL) == pytest.approx(10.0)
        assert adverse_move_bps(100.0, 100.1, SimSide.SELL) == pytest.approx(-10.0)


class TestRecordFromFill:
    def test_builds_record(self):
        rec = record_from_fill(
            make_fill(mid_before=100.0, mid_after=100.5),
            spread_bps=5.0,
            depth=10_000.0,
            book_imbalance=0.1,
            volatility_bps=20.0,
        )
        assert rec.side == SimSide.BUY
        assert rec.adverse_bps == pytest.approx(50.0)
        assert rec.quantity_bucket == "medium"
        assert rec.spread_bucket == "normal"
        assert rec.imbalance_bucket == "neutral"
        assert rec.volatility_bucket == "mid"
        assert rec.aggressiveness_bucket == "aggressive"
        assert rec.windows()["mid_t+1s"] == pytest.approx(100.125)

    def test_buckets(self):
        rec = record_from_fill(
            make_fill(qty=50.0),
            spread_bps=50.0,
            depth=100.0,
            book_imbalance=0.9,
            volatility_bps=100.0,
            order_type="limit",
        )
        assert rec.quantity_bucket == "small"
        assert rec.spread_bucket == "wide"
        assert rec.imbalance_bucket == "bid_heavy"
        assert rec.volatility_bucket == "high"
        assert rec.aggressiveness_bucket == "aggressive"  # aggressor default market
        assert rec.order_type == "limit"


class TestReport:
    def _report_with_records(self, n: int = 8):
        report = PostTradeImpactReport()
        for i in range(n):
            buy = i % 2 == 0
            rec = record_from_fill(
                make_fill(
                    side=SimSide.BUY if buy else SimSide.SELL,
                    mid_before=100.0,
                    mid_after=100.0 + (0.01 if buy else -0.01) * (1 + i % 3),
                    aggressor="market" if i % 3 else "limit_passive",
                    qty=1000.0 + i * 500.0,
                ),
                spread_bps=5.0,
                depth=10_000.0,
                book_imbalance=0.0,
                volatility_bps=20.0,
            )
            report.add_record(rec)
        return report

    def test_grouping_by_side(self):
        report = self._report_with_records()
        groups = report.group_by("side")
        assert set(groups) == {"buy", "sell"}
        assert groups["buy"].count >= 1

    def test_grouping_unknown_key(self):
        with pytest.raises(ValueError):
            self._report_with_records().group_by("nope")

    def test_empty_report(self):
        report = PostTradeImpactReport()
        det = report.detect()
        assert not det.any_flag
        assert report.count == 0

    def test_bad_timing_detected(self):
        report = PostTradeImpactReport(bad_timing_threshold_bps=5.0)
        for i in range(8):
            report.add_record(
                record_from_fill(
                    make_fill(mid_before=100.0, mid_after=100.05 + 0.001 * i),
                    spread_bps=5.0,
                    depth=10_000.0,
                    book_imbalance=0.0,
                    volatility_bps=20.0,
                )
            )
        det = report.detect()
        assert det.bad_timing

    def test_too_aggressive_detected(self):
        report = PostTradeImpactReport(aggressive_gap_bps=3.0)
        for _ in range(4):
            report.add_record(
                record_from_fill(make_fill(aggressor="market", mid_after=100.05))
            )
        for _ in range(4):
            report.add_record(
                record_from_fill(make_fill(aggressor="limit_passive", mid_after=100.005))
            )
        det = report.detect()
        assert det.too_aggressive

    def test_not_too_aggressive_when_gap_small(self):
        report = PostTradeImpactReport(aggressive_gap_bps=10.0)
        for _ in range(4):
            report.add_record(record_from_fill(make_fill(aggressor="market", mid_after=100.05)))
        for _ in range(4):
            report.add_record(record_from_fill(make_fill(aggressor="limit_passive", mid_after=100.05)))
        assert not report.detect().too_aggressive

    def test_predictable_adverse_move(self):
        # Make the t+1s move strongly predictive of the t+30s move:
        # deterministic progression: mid_after = mid_t0 + (move*4) so that
        # the 1s window (25%) correlates perfectly with the final move.
        report = PostTradeImpactReport(predict_corr_threshold=0.9, min_group_size=4)
        for k in range(10):
            move = 0.001 * k
            mid_after = 100.0 + move * 4.0  # 1s window = 25% * 4*move = move
            report.add_record(
                record_from_fill(make_fill(mid_before=100.0, mid_after=mid_after))
            )
        det = report.detect()
        assert det.predictable_adverse_move

    def test_poor_liquidity_regime(self):
        report = PostTradeImpactReport(liquidity_gap_bps=3.0)
        for _ in range(4):
            report.add_record(
                record_from_fill(
                    make_fill(mid_after=100.01),
                    spread_bps=2.0,
                )
            )
        for _ in range(4):
            report.add_record(
                record_from_fill(
                    make_fill(mid_after=100.1),
                    spread_bps=50.0,
                )
            )
        det = report.detect()
        assert det.poor_liquidity_regime

    def test_to_dict(self):
        d = self._report_with_records().to_dict()
        assert d["fill_count"] == 8
        assert d["algorithms_version"]
        assert "detection" in d
        assert "groups" in d
        assert "side" in d["groups"]
        assert "aggressiveness" in d["groups"]

    def test_deterministic_report(self):
        r1 = self._report_with_records()
        r2 = self._report_with_records()
        assert r1.to_dict() == r2.to_dict()
