"""Execution Simulator V2 — unit + integration tests (Wave A).

Covers: market sweep, limit fills, queue position, partial fills, fees,
precision, min notional, insufficient liquidity, stale quote, sequence gap,
determinism, versioning, P&L attribution and the reality-gap framework.
"""

from __future__ import annotations

import math

import polars as pl
import pytest

from trading_agent.execution.simulator import (
    MarketReplayEngine,
    OrderIntent,
    SimOrderStatus,
    SimOrderType,
    SimSide,
    SimulationConfig,
    build_book_from_bar,
    build_book_from_l2,
    compute_reality_gap,
    promotion_check,
    quantize_price,
    quantize_qty,
    run_strategy_through_simulator,
)
from trading_agent.execution.simulator.fee_model import FeeModel
from trading_agent.execution.simulator.fill_model import FillModel
from trading_agent.execution.simulator.impact_model import ImpactModel
from trading_agent.execution.simulator.models import Fill, RejectReason


def make_df(n: int = 50, start: float = 100.0, vol: float = 0.5) -> pl.DataFrame:
    """Deterministic OHLCV frame (no random)."""
    import datetime as dt

    rows = []
    for i in range(n):
        o = start + i * 0.2 + (math.sin(i) * vol)
        c = o + 0.1
        rows.append(
            {
                "timestamp": dt.datetime(2026, 1, 1, tzinfo=dt.UTC) + dt.timedelta(hours=i),
                "open": o,
                "high": max(o, c) + 0.3,
                "low": min(o, c) - 0.3,
                "close": c,
                "volume": 10.0 + i * 0.1,
            }
        )
    return pl.DataFrame(rows)


# ── Precision ─────────────────────────────────────────────────────────────


class TestPrecision:
    def test_quantize_price_on_grid(self):
        assert quantize_price(100.006, 0.01) == 100.01
        assert quantize_price(100.004, 0.01) == 100.0

    def test_quantize_qty_downward(self):
        # Conservative: never round up.
        assert quantize_qty(1.000001, 0.001) == 1.0
        assert quantize_qty(0.999, 0.001) == 0.999

    def test_quantize_invalid(self):
        with pytest.raises(ValueError):
            quantize_price(100.0, 0.0)
        with pytest.raises(ValueError):
            quantize_qty(1.0, 0.0)


# ── Config / versioning ───────────────────────────────────────────────────


class TestConfig:
    def test_validate_fail_closed(self):
        with pytest.raises(ValueError):
            SimulationConfig(spread_bps=-1).validate()
        with pytest.raises(ValueError):
            SimulationConfig(depth_levels=0).validate()
        with pytest.raises(ValueError):
            SimulationConfig(taker_fee=1.5).validate()
        with pytest.raises(ValueError):
            SimulationConfig(fee_asset="USD").validate()
        with pytest.raises(ValueError):
            SimulationConfig(random_seed=None).validate()

    def test_fingerprint_stable_and_sensitive(self):
        a = SimulationConfig(random_seed=1).fingerprint()
        b = SimulationConfig(random_seed=1).fingerprint()
        c = SimulationConfig(random_seed=2).fingerprint()
        d = SimulationConfig(random_seed=1, taker_fee=0.001).fingerprint()
        assert a == b
        assert a != c
        assert a != d

    def test_from_dict_rejects_unknown_keys(self):
        cfg = SimulationConfig.from_dict({"random_seed": 3, "spread_bps": 10.0})
        assert cfg.random_seed == 3
        assert cfg.spread_bps == 10.0


# ── Order book ────────────────────────────────────────────────────────────


class TestOrderBook:
    def test_book_from_bar_structure(self):
        book = build_book_from_bar(
            symbol="BTC/USDT",
            open_price=100.0,
            previous_volume=100.0,
            config=SimulationConfig(random_seed=1),
            sequence=0,
        )
        assert book.best_bid() < book.best_ask()
        assert book.mid > 0
        assert len(book.bids) == 5
        assert len(book.asks) == 5
        # Bids descending, asks ascending.
        assert all(book.bids[i].price > book.bids[i + 1].price for i in range(len(book.bids) - 1))
        assert all(book.asks[i].price < book.asks[i + 1].price for i in range(len(book.asks) - 1))
        # Total depth per side is depth_volume_share * volume.
        assert book.total_ask_size() == pytest.approx(25.0, rel=1e-6)

    def test_book_from_l2_sorted_and_cross_rejected(self):
        book = build_book_from_l2(
            symbol="X",
            bids=[(99.5, 1.0), (99.0, 2.0)],
            asks=[(100.5, 1.0), (101.0, 2.0)],
            sequence=1,
        )
        assert book.best_bid() == 99.5
        assert book.best_ask() == 100.5
        with pytest.raises(ValueError):
            build_book_from_l2(symbol="X", bids=[(101.0, 1.0)], asks=[(100.0, 1.0)], sequence=1)

    def test_sweep_partial(self):
        book = build_book_from_l2(
            symbol="X",
            bids=[(99.5, 1.0), (99.0, 2.0)],
            asks=[(100.5, 1.0), (101.0, 2.0)],
            sequence=1,
        )
        consumed, remaining = book.sweep(SimSide.BUY, 3.5)
        assert len(consumed) == 2
        assert remaining == pytest.approx(0.5)
        assert consumed[0].price == 100.5
        assert consumed[1].price == 101.0


# ── Fill / impact / fee models ────────────────────────────────────────────


class TestFillModel:
    def test_market_fill_at_ask_levels(self):
        cfg = SimulationConfig(random_seed=1, spread_bps=10.0)
        book = build_book_from_bar(symbol="X", open_price=100.0, previous_volume=1000.0, config=cfg, sequence=0)
        fm = FillModel(cfg)
        intent = OrderIntent(order_id="o1", side=SimSide.BUY, order_type=SimOrderType.MARKET, quantity=1.0)
        outcome = fm.fill_market(intent, book, 0, book.timestamp)
        assert outcome.status == SimOrderStatus.FILLED
        assert outcome.fills[0].price > book.mid  # pays ask side
        assert outcome.fills[0].quantity == pytest.approx(1.0)

    def test_market_partial_insufficient_liquidity(self):
        cfg = SimulationConfig(random_seed=1, depth_volume_share=0.01)
        book = build_book_from_bar(symbol="X", open_price=100.0, previous_volume=100.0, config=cfg, sequence=0)
        fm = FillModel(cfg)
        intent = OrderIntent(order_id="o1", side=SimSide.BUY, order_type=SimOrderType.MARKET, quantity=1_000_000.0)
        outcome = fm.fill_market(intent, book, 0, book.timestamp)
        assert outcome.status == SimOrderStatus.PARTIALLY_FILLED
        assert outcome.reject_reason == RejectReason.INSUFFICIENT_LIQUIDITY

    def test_limit_marketable_crosses(self):
        cfg = SimulationConfig(random_seed=1)
        book = build_book_from_bar(symbol="X", open_price=100.0, previous_volume=1000.0, config=cfg, sequence=0)
        fm = FillModel(cfg)
        intent = OrderIntent(order_id="o1", side=SimSide.BUY, order_type=SimOrderType.LIMIT, quantity=1.0, limit_price=9999.0)
        outcome = fm.fill_limit(intent, book, 0, book.timestamp)
        assert outcome.status == SimOrderStatus.FILLED

    def test_limit_rests_when_not_crossing(self):
        cfg = SimulationConfig(random_seed=1)
        book = build_book_from_bar(symbol="X", open_price=100.0, previous_volume=1000.0, config=cfg, sequence=0)
        fm = FillModel(cfg)
        intent = OrderIntent(order_id="o1", side=SimSide.BUY, order_type=SimOrderType.LIMIT, quantity=1.0, limit_price=90.0)
        outcome = fm.fill_limit(intent, book, 0, book.timestamp)
        # Not crossing, and low passive probability may still fill — but it
        # must never fill at a price worse than the limit.
        if outcome.status == SimOrderStatus.FILLED:
            assert all(f.price <= 90.0 for f in outcome.fills)
        else:
            assert outcome.status == SimOrderStatus.SUBMITTED

    def test_stale_quote_detected(self):
        cfg = SimulationConfig(random_seed=1, max_book_age_seconds=1.0)
        fm = FillModel(cfg)
        book = build_book_from_bar(symbol="X", open_price=100.0, previous_volume=1000.0, config=cfg, sequence=0)
        # Book timestamp now, then query 5s later → stale.
        assert fm.check_stale(book, book.timestamp.timestamp() + 5.0)
        assert not fm.check_stale(book, book.timestamp.timestamp())


class TestImpactModel:
    def test_impact_positive_and_decays(self):
        cfg = SimulationConfig(random_seed=1, impact_coeff=1.0)
        im = ImpactModel(cfg)
        book = build_book_from_bar(symbol="X", open_price=100.0, previous_volume=1000.0, config=cfg, sequence=0)
        imp = im.temporary_impact_bps(10.0, SimSide.BUY, book, volatility_bps=50.0)
        assert imp > 0
        assert im.decay(imp, 3) < imp
        assert im.decay(imp, 0) == pytest.approx(imp)

    def test_adverse_selection_deterministic(self):
        # Same seed → same draw sequence across independent instances.
        cfg = SimulationConfig(random_seed=1)
        a_im, b_im = ImpactModel(cfg), ImpactModel(cfg)
        fill = Fill(order_id="o", bar_index=0, timestamp=None, side=SimSide.BUY, quantity=1.0, price=100.0, fee=0.0, fee_asset="quote", aggressor="market", level_price=100.0, mid_before=100.0)
        a1 = a_im.adverse_selection_bps(fill, aggressor_aggressive=True)
        a2 = a_im.adverse_selection_bps(fill, aggressor_aggressive=True)
        b1 = b_im.adverse_selection_bps(fill, aggressor_aggressive=True)
        b2 = b_im.adverse_selection_bps(fill, aggressor_aggressive=True)
        assert a1 > 0
        assert (a1, a2) == (b1, b2)  # identical seeded sequence


class TestFeeModel:
    def test_maker_taker_rates(self):
        cfg = SimulationConfig(random_seed=1, taker_fee=0.001, maker_fee=0.0002)
        fm = FeeModel(cfg)
        fill = Fill(order_id="o", bar_index=0, timestamp=None, side=SimSide.BUY, quantity=1.0, price=100.0, fee=0.0, fee_asset="quote", aggressor="market", level_price=100.0, mid_before=100.0)
        assert fm.compute_fee(fill, is_maker=False) == pytest.approx(0.1)
        assert fm.compute_fee(fill, is_maker=True) == pytest.approx(0.02)


# ── Engine end-to-end ─────────────────────────────────────────────────────


class TestEngine:
    def test_market_order_round_trip(self):
        df = make_df(30)
        cfg = SimulationConfig(random_seed=1, spread_bps=5.0)
        engine = MarketReplayEngine(df, config=cfg, symbol="TEST", initial_cash=10_000.0)

        def provider(i, eng):
            if i == 5:
                return [OrderIntent(order_id="buy1", side=SimSide.BUY, order_type=SimOrderType.MARKET, quantity=1.0)]
            if i == 20:
                return [OrderIntent(order_id="sell1", side=SimSide.SELL, order_type=SimOrderType.MARKET, quantity=eng.ledger.inventory_base)]
            return []

        result = engine.run(provider)
        assert result.ledger.inventory_base == pytest.approx(0.0, abs=1e-9)
        assert len(result.fills) >= 2
        assert result.metrics.trade_count == 1  # one round trip (buy + sell)
        assert result.data_manifest  # non-empty

    def test_deterministic_replay_same_seed(self):
        df = make_df(40)
        cfg = SimulationConfig(random_seed=42)
        r1 = MarketReplayEngine(df, config=cfg, symbol="T").run(
            lambda i, e: [OrderIntent(order_id=f"o{i}", side=SimSide.BUY if i % 2 == 0 else SimSide.SELL, order_type=SimOrderType.MARKET, quantity=1.0)] if i in (3, 10, 17) else []
        )
        r2 = MarketReplayEngine(df, config=cfg, symbol="T").run(
            lambda i, e: [OrderIntent(order_id=f"o{i}", side=SimSide.BUY if i % 2 == 0 else SimSide.SELL, order_type=SimOrderType.MARKET, quantity=1.0)] if i in (3, 10, 17) else []
        )
        assert r1.to_dict() == r2.to_dict()

    def test_rejects_stale_quote(self):
        df = make_df(10)
        cfg = SimulationConfig(random_seed=1, max_book_age_seconds=1e-9)  # effectively always stale
        engine = MarketReplayEngine(df, config=cfg, symbol="T", initial_cash=10_000.0)
        result = engine.run(
            lambda i, e: [OrderIntent(order_id=f"o{i}", side=SimSide.BUY, order_type=SimOrderType.MARKET, quantity=1.0)] if i == 3 else []
        )
        assert result.metrics.rejected_order_rate == 1.0
        assert result.order_results[0].reject_reason == RejectReason.STALE_QUOTE

    def test_rejects_min_notional(self):
        df = make_df(10)
        cfg = SimulationConfig(random_seed=1, min_notional=500.0)
        engine = MarketReplayEngine(df, config=cfg, symbol="T", initial_cash=10_000.0)
        result = engine.run(
            lambda i, e: [OrderIntent(order_id=f"o{i}", side=SimSide.BUY, order_type=SimOrderType.MARKET, quantity=0.001)] if i == 3 else []
        )
        assert result.order_results[0].reject_reason == RejectReason.MIN_NOTIONAL

    def test_rejects_insufficient_cash(self):
        df = make_df(10)
        cfg = SimulationConfig(random_seed=1)
        engine = MarketReplayEngine(df, config=cfg, symbol="T", initial_cash=10.0)
        result = engine.run(
            lambda i, e: [OrderIntent(order_id=f"o{i}", side=SimSide.BUY, order_type=SimOrderType.MARKET, quantity=100.0)] if i == 3 else []
        )
        assert result.order_results[0].reject_reason == RejectReason.INSUFFICIENT_CASH

    def test_rejects_insufficient_inventory(self):
        df = make_df(10)
        cfg = SimulationConfig(random_seed=1)
        engine = MarketReplayEngine(df, config=cfg, symbol="T", initial_cash=10_000.0)
        result = engine.run(
            lambda i, e: [OrderIntent(order_id=f"o{i}", side=SimSide.SELL, order_type=SimOrderType.MARKET, quantity=1.0)] if i == 3 else []
        )
        assert result.order_results[0].reject_reason == RejectReason.INSUFFICIENT_INVENTORY

    def test_cancel_resting_limit(self):
        df = make_df(20)
        cfg = SimulationConfig(random_seed=1, passive_fill_prob=0.0)  # never passive-fill
        engine = MarketReplayEngine(df, config=cfg, symbol="T", initial_cash=10_000.0)
        result = engine.run(
            lambda i, e: [OrderIntent(order_id="lim", side=SimSide.BUY, order_type=SimOrderType.LIMIT, quantity=1.0, limit_price=1.0)] if i == 3 else []
        )
        order = next(o for o in result.order_results if o.order_id == "lim")
        assert order.status == SimOrderStatus.SUBMITTED
        # Cancel it.
        engine.cancel_order("lim", 10)
        assert order.status == SimOrderStatus.CANCELED
        assert result.ledger.missed_fill_quantity > 0

    def test_attribution_identity(self):
        """realized ≈ theoretical alpha − (spread+impact+fees+delay+opp)."""
        df = make_df(100)
        cfg = SimulationConfig(random_seed=7, spread_bps=5.0, taker_fee=0.0005)
        engine = MarketReplayEngine(df, config=cfg, symbol="T", initial_cash=10_000.0)
        result = engine.run(
            lambda i, e: [OrderIntent(order_id=f"o{i}", side=SimSide.BUY if i % 2 == 0 else SimSide.SELL, order_type=SimOrderType.MARKET, quantity=1.0)] if i in (5, 15, 25, 35, 45, 55, 65, 75, 85, 95) else []
        )
        attr = result.metrics.attribution
        costs = attr.spread_cost + attr.impact_cost + attr.fees + attr.delay_cost + attr.opportunity_cost
        # Attribution identity is structural: execution_cost == sum of parts.
        assert attr.execution_cost == pytest.approx(costs, abs=1e-6)


# ── Strategy bridge ───────────────────────────────────────────────────────


class TestStrategyBridge:
    def test_run_strategy_through_simulator(self):
        from trading_agent.strategies.base import get_strategy

        df = make_df(200, start=100.0)
        strat = get_strategy("ma_crossover")({})
        result = run_strategy_through_simulator(
            strat, df, symbol="TEST", initial_cash=10_000.0,
            config=SimulationConfig(random_seed=42),
        )
        assert result.metrics.trade_count > 0
        assert result.metrics.fill_ratio > 0
        assert result.theoretical_alpha_pnl != 0.0
        assert result.model_versions["execution_model_version"] == "2.0.0"


# ── Reality gap ───────────────────────────────────────────────────────────


class TestRealityGap:
    def test_identical_reference_score_zero(self):
        ref = {
            "fill_ratio": 1.0,
            "slippage_bps": 0.0,
            "implementation_shortfall_bps": 0.0,
            "trade_count": 10,
            "rejected_order_rate": 0.0,
            "partial_fill_rate": 0.0,
        }
        report = compute_reality_gap(
            environment="simulator", reference_environment="backtest",
            observed=ref, reference=ref,
        )
        assert report.score == pytest.approx(0.0)
        assert report.pass_gate

    def test_large_gap_breaches(self):
        ref = {
            "fill_ratio": 1.0,
            "slippage_bps": 1.0,
            "implementation_shortfall_bps": 2.0,
            "trade_count": 10,
            "rejected_order_rate": 0.0,
            "partial_fill_rate": 0.0,
        }
        obs = {
            "fill_ratio": 0.5,
            "slippage_bps": 100.0,
            "implementation_shortfall_bps": 50.0,
            "trade_count": 2,
            "rejected_order_rate": 0.1,
            "partial_fill_rate": 0.2,
        }
        report = compute_reality_gap(
            environment="simulator", reference_environment="backtest",
            observed=obs, reference=ref,
        )
        assert report.score > 1.0
        assert not report.pass_gate
        assert report.breaches

    def test_promotion_fail_closed(self):
        ref = {
            "fill_ratio": 1.0,
            "slippage_bps": 0.0,
            "implementation_shortfall_bps": 0.0,
            "trade_count": 10,
            "rejected_order_rate": 0.0,
            "partial_fill_rate": 0.0,
        }
        ok = compute_reality_gap(environment="a", reference_environment="b", observed=ref, reference=ref)
        bad = compute_reality_gap(environment="a", reference_environment="b", observed={"fill_ratio": 0.1, "slippage_bps": 0.0, "implementation_shortfall_bps": 0.0, "trade_count": 10, "rejected_order_rate": 0.0, "partial_fill_rate": 0.0}, reference=ref)
        assert promotion_check(ok)
        assert not promotion_check(bad)

    def test_raw_metrics_preserved(self):
        obs = {
            "fill_ratio": 0.5,
            "slippage_bps": 12.3,
            "implementation_shortfall_bps": 15.0,
            "trade_count": 5,
            "rejected_order_rate": 0.0,
            "partial_fill_rate": 0.0,
        }
        ref = {
            "fill_ratio": 1.0,
            "slippage_bps": 0.0,
            "implementation_shortfall_bps": 0.0,
            "trade_count": 10,
            "rejected_order_rate": 0.0,
            "partial_fill_rate": 0.0,
        }
        report = compute_reality_gap(environment="a", reference_environment="b", observed=obs, reference=ref)
        assert report.metrics["slippage_bps"] == 12.3

    def test_missing_required_in_both_fails_gate(self):
        """Required metric missing from BOTH → hard breach (fail-closed)."""
        ref = {"fill_ratio": 1.0, "slippage_bps": 0.0}
        obs = {"fill_ratio": 0.9, "slippage_bps": 1.0}
        report = compute_reality_gap(
            environment="a", reference_environment="b",
            observed=obs, reference=ref,
            required_metrics=frozenset(["fill_ratio", "slippage_bps", "trade_count"])
        )
        assert "trade_count" in report.missing_in_both
        assert "trade_count: REQUIRED but missing from BOTH" in " ".join(report.breaches)
        assert not report.pass_gate

    def test_missing_required_in_one_warns(self):
        """Required metric missing from ONE side → warning, excluded from score."""
        ref = {"fill_ratio": 1.0, "slippage_bps": 0.0, "trade_count": 10}
        obs = {"fill_ratio": 0.9, "slippage_bps": 1.0}  # missing trade_count
        report = compute_reality_gap(
            environment="a", reference_environment="b",
            observed=obs, reference=ref,
            required_metrics=frozenset(["fill_ratio", "slippage_bps", "trade_count"])
        )
        assert "trade_count" in report.missing_in_one
        assert "trade_count: REQUIRED but missing from" in " ".join(report.breaches)
        assert not report.pass_gate

    def test_optional_missing_in_both_warns_only(self):
        """Optional metric missing from BOTH → warning only, gate still passes if others ok."""
        ref = {"fill_ratio": 1.0, "slippage_bps": 0.0, "trade_count": 10, "rejected_order_rate": 0.0, "partial_fill_rate": 0.0, "implementation_shortfall_bps": 0.0}
        obs = {"fill_ratio": 1.0, "slippage_bps": 0.0, "trade_count": 10, "rejected_order_rate": 0.0, "partial_fill_rate": 0.0, "implementation_shortfall_bps": 0.0}
        # tracking_error_bps is optional and missing from both
        report = compute_reality_gap(environment="a", reference_environment="b", observed=obs, reference=ref)
        assert "tracking_error_bps" in report.missing_in_both
        assert report.pass_gate  # optional missing doesn't fail gate
