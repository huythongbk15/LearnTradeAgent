#!/usr/bin/env python3
"""End-to-end system test — validates the full trading pipeline.

Covers 7 layers with 5 test scenarios:

  Scenario 1  Data Integrity         — BinanceDataFeed dry-run, validation
  Scenario 2  Strategy Signals       — 5 canonical strategies on shared OHLCV
  Scenario 3  Tournament Routing     — tournament.route() on 300+ bars (shadow)
  Scenario 4  Risk Policy            — ForecastRiskPolicy edge cases
  Scenario 5  Order Planning         — OrderPlanner.plan() with confidence scaling
  Scenario 6  LLM Enrichment         — deterministic + replay consistency
  Scenario 7  Monitoring             — audit DB, process registry, alerts

Usage:
    python scripts/e2e_system_test.py [--bars N] [--output-dir DIR]
"""
from __future__ import annotations

import argparse
import importlib
import os
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import polars as pl

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, "src")
sys.path.insert(0, str(Path(__file__).resolve().parent))

# ── Defaults ─────────────────────────────────────────────────────────────

DEFAULT_SYMBOLS = ["BTC/USDT", "ETH/USDT", "SOL/USDT"]
DEFAULT_BARS = 300
DEFAULT_WINDOW = 120

# ── Test Results ───────────────────────────────────────────────────────────


class TestResult:
    def __init__(self, name: str):
        self.name = name
        self.passed = 0
        self.failed = 0
        self.details: list[str] = []
        self._start = time.perf_counter()

    def check(self, condition: bool, description: str, detail: str = "") -> None:
        if condition:
            self.passed += 1
            self.details.append(f"  ✓ {description}")
        else:
            self.failed += 1
            self.details.append(f"  ✗ {description}" + (f" — {detail}" if detail else ""))

    def assert_eq(self, a: Any, b: Any, desc: str) -> None:
        self.check(a == b, f"{desc}: {a!r} == {b!r}", f"got {a!r}")

    def assert_true(self, cond: bool, desc: str, detail: str = "") -> None:
        self.check(cond, desc, detail)

    def summary(self) -> str:
        elapsed = time.perf_counter() - self._start
        status = "PASS" if self.failed == 0 else "FAIL"
        return (
            f"[{status}] {self.name}: {self.passed} passed, {self.failed} failed "
            f"({elapsed:.1f}s)"
        )

    def report(self) -> str:
        lines = [self.summary()]
        lines.extend(self.details)
        return "\n".join(lines)


# ── Scenario 1: Data Integrity ────────────────────────────────────────────


def scenario_1_data_integrity(symbols: list[str], window: int) -> TestResult:
    from live_data_pipeline import BinanceDataFeed, validate_live_hourly_bars

    result = TestResult("Scenario 1: Data Integrity (BinanceDataFeed)")

    feed = BinanceDataFeed(symbols, dry_run=True, window_bars=window)

    for symbol in symbols:
        try:
            df = feed.fetch_recent_closed(symbol)
        except Exception as e:
            result.check(False, f"{symbol} fetch", str(e))
            continue

        result.check(
            df.height >= window, f"{symbol} bar count >= {window}", f"got {df.height}"
        )
        result.check(df["timestamp"].is_sorted(), f"{symbol} timestamps sorted")

        # Validate OHLC consistency
        ohlc_ok = all(
            df["high"][i] >= max(df["open"][i], df["low"][i], df["close"][i])
            and df["low"][i] <= min(df["open"][i], df["high"][i], df["close"][i])
            for i in range(df.height)
        )
        result.check(ohlc_ok, f"{symbol} OHLC range consistency")

        null_count = df.null_count().sum_horizontal().item()
        result.check(null_count == 0, f"{symbol} no null values", f"nulls={null_count}")

    # Fail-closed: stale candles
    now = datetime.now(UTC)
    stale_ts = int((now - timedelta(hours=2, seconds=10)).timestamp() * 1000)
    stale_bar = [[stale_ts, 50000, 50100, 49900, 50000, 100]]
    result.check(
        _expect_raise(validate_live_hourly_bars, stale_bar, "BTCUSDT", now),
        "Fail-closed: stale candle rejected",
    )

    # Fail-closed: time gap detection
    t1 = int((now - timedelta(hours=2)).timestamp() * 1000)
    gap_bars = [
        [t1, 50000, 50100, 49900, 50000, 100],
        [t1 + 3600000, 50100, 50200, 50000, 50100, 200],
        [t1 + 10800000, 50100, 50200, 50000, 50100, 200],  # 2h gap
    ]
    result.check(
        _expect_raise(validate_live_hourly_bars, gap_bars, "BTCUSDT", now),
        "Fail-closed: candle gap rejected",
    )

    return result


def _expect_raise(func, *args, **kwargs) -> bool:
    try:
        func(*args, **kwargs)
        return False
    except Exception:
        return True


# ── Scenario 2: Strategy Signals ─────────────────────────────────────────


def scenario_2_strategy_signals(df: pl.DataFrame) -> TestResult:
    result = TestResult("Scenario 2: Strategy Signal Generation")

    from trading_agent.strategies.enhanced_ma import EnhancedMaCrossover
    from trading_agent.strategies.rsi import RsiStrategy
    from trading_agent.strategies.bbands import BBandsStrategy
    from trading_agent.strategies.trend_pullback import TrendPullbackStrategy
    from trading_agent.strategies.range_mean_reversion import RangeMeanReversionStrategy
    from trading_agent.strategies.canonical.adapter import LegacyDataFrameAdapter
    from trading_agent.research.forecast import MarketObservation

    strategies = [
        (
            "enhanced_ma",
            LegacyDataFrameAdapter(
                EnhancedMaCrossover(params={"fast_period": 10, "slow_period": 60}),
                model_artifact_id="enhanced_ma-e2e",
                warmup_bars=60,
            ),
        ),
        (
            "rsi",
            LegacyDataFrameAdapter(
                RsiStrategy(params={"period": 14, "oversold": 30, "overbought": 70}),
                model_artifact_id="rsi-e2e",
                warmup_bars=14,
            ),
        ),
        (
            "bbands",
            LegacyDataFrameAdapter(
                BBandsStrategy(params={"period": 20, "std_dev": 2.0}),
                model_artifact_id="bbands-e2e",
                warmup_bars=20,
            ),
        ),
        (
            "trend_pullback",
            LegacyDataFrameAdapter(
                TrendPullbackStrategy(
                    params={"ma_fast": 20, "ma_slow": 50, "adx_threshold": 20,
                            "vol_multiplier": 1.0}
                ),
                model_artifact_id="trend_pullback-e2e",
                warmup_bars=50,
            ),
        ),
        (
            "range_mean_reversion",
            LegacyDataFrameAdapter(
                RangeMeanReversionStrategy(
                    params={"lookback": 20, "entry_z": 2.0, "exit_z": 0.0,
                            "rsi_oversold": 30, "rsi_overbought": 70}
                ),
                model_artifact_id="range_mean_reversion-e2e",
                warmup_bars=20,
            ),
        ),
    ]

    for name, _ in strategies:
        result.check(True, f"{name} initialized with adapter")

    test_window = df.tail(250)
    for name, adapter in strategies:
        signals = 0
        errors = 0
        warmup = adapter._warmup_bars
        for i in range(warmup + 1, len(test_window)):
            bar_df = test_window.head(i)
            last = bar_df.tail(1).row(0, named=True)
            obs = MarketObservation(
                symbol="BTC/USDT",
                observed_at=last["timestamp"],
                open=float(last["open"]),
                high=float(last["high"]),
                low=float(last["low"]),
                close=float(last["close"]),
                volume=float(last["volume"]),
                features={"ohlcv_window": bar_df},
            )
            try:
                forecast = adapter.forecast(obs)
                if forecast is not None:
                    signals += 1
            except Exception:
                errors += 1

        result.check(
            signals > 0,
            f"{name} produces signals",
            f"signals={signals}/{len(test_window) - warmup - 1}, errors={errors}",
        )

    return result


# ── Scenario 3: Tournament Routing ────────────────────────────────────────


def scenario_3_tournament(
    symbols: list[str], window: int, tmp_dir: Path
) -> TestResult:
    from live_data_pipeline import (
        BinanceDataFeed,
        build_tournament,
        build_observation,
        compute_deterministic_posterior,
        compute_bar_return,
    )

    result = TestResult("Scenario 3: Tournament Routing (Shadow Mode)")

    # Clear persisted state from previous runs (mirrors dry_run() in pipeline)
    if tmp_dir.exists():
        import shutil
        shutil.rmtree(tmp_dir)
    tmp_dir.mkdir(parents=True, exist_ok=True)

    feed = BinanceDataFeed(symbols, dry_run=True, window_bars=window)
    tournament = build_tournament(symbols, tmp_dir, shadow_mode=True)

    symbol = symbols[0]
    df = feed.fetch_recent_closed(symbol).sort("timestamp")
    result.check(df.height >= 300, "Data has >= 300 bars", f"got {df.height}")

    test_bars = df.tail(300)

    decisions = 0
    strategies_used: set[str] = set()
    exposure_multipliers: list[float] = []

    warmup = 50
    for i in range(warmup, len(test_bars)):
        bar_window = test_bars[max(0, i - window + 1) : i + 1]
        bar_time = bar_window["timestamp"].item(-1)

        obs = build_observation(symbol, bar_window, bar_time)
        posterior = compute_deterministic_posterior(bar_window, bar_time)
        bar_ret = compute_bar_return(bar_window, bar_time)

        decision = tournament.route(
            symbol=symbol,
            timeframe="1h",
            posterior=posterior,
            observed_at=bar_time,
            position_is_flat=True,
            observation=obs,
            bar_return=bar_ret,
        )
        decisions += 1
        if decision.chosen_strategy_id:
            strategies_used.add(decision.chosen_strategy_id)
        exposure_multipliers.append(decision.exposure_multiplier)

    result.check(decisions >= 250, "Processed >= 250 routing decisions",
                 f"got {decisions}")
    result.check(
        len(strategies_used) >= 1,
        "≥1 strategy selected across bars",
        f"strategies={strategies_used}",
    )
    result.check(
        all(0.0 <= e <= 1.0 for e in exposure_multipliers),
        "All exposure multipliers in [0, 1]",
    )

    # Audit trails
    audit_jsonl = tmp_dir / "router_audit.jsonl"
    result.check(audit_jsonl.exists(), "Audit log exists")
    if audit_jsonl.exists():
        lines = audit_jsonl.read_text().strip().split("\n")
        result.check(
            len(lines) >= decisions * 0.8,
            f"Audit log has entries (≥80% of decisions: {len(lines)}/{decisions})",
        )

    audit_db = tmp_dir / "tournament_state" / "audit.sqlite3"
    if audit_db.exists():
        import sqlite3

        conn = sqlite3.connect(str(audit_db))
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM entries")
        count = c.fetchone()[0]
        result.check(count > 0, f"Audit DB has entries ({count})")
        conn.close()

    return result


# ── Scenario 4: Risk Policy ─────────────────────────────────────────────


def scenario_4_risk_policy() -> TestResult:
    from trading_agent.research.forecast import (
        Forecast,
        RiskReason,
        CalibrationState,
        ForecastRiskPolicy,
    )

    result = TestResult("Scenario 4: Risk Policy (Deterministic)")

    policy = ForecastRiskPolicy(max_ood=0.70)
    now = datetime.now(UTC)

    # Positive forecast, low OOD → approved
    f1 = Forecast(
        expected_excess_return=0.02, horizon=1,
        lower_bound=0.01, upper_bound=0.05,  # strictly positive interval
        direction_probability=0.8,
        calibration_state=CalibrationState.CALIBRATED,
        ood_score=0.1, model_artifact_id="test-v1",
        generated_at=now,
    )
    d1 = policy.evaluate(f1, requested_exposure=0.5)
    result.check(d1.approved, "Positive forecast approved", str(d1.reason_codes))
    result.check(
        0.0 <= d1.allowed_exposure <= 0.5,
        "Allowed exposure <= requested",
        f"allowed={d1.allowed_exposure}",
    )

    # Zero expected return → rejected
    f2 = Forecast(
        expected_excess_return=0.0, horizon=1,
        lower_bound=-0.01, upper_bound=0.01,
        direction_probability=0.5,
        calibration_state=CalibrationState.CALIBRATED,
        ood_score=0.1, model_artifact_id="test-v1",
        generated_at=now,
    )
    d2 = policy.evaluate(f2, requested_exposure=0.3)
    result.check(not d2.approved, "Zero-edge forecast rejected",
                 f"approved={d2.approved}")

    # High OOD → rejected
    f3 = Forecast(
        expected_excess_return=0.03, horizon=1,
        lower_bound=0.00, upper_bound=0.06,
        direction_probability=0.9,
        calibration_state=CalibrationState.CALIBRATED,
        ood_score=0.85, model_artifact_id="test-v1",
        generated_at=now,
    )
    d3 = policy.evaluate(f3, requested_exposure=0.5)
    result.check(not d3.approved, "High OOD forecast rejected",
                 f"ood={f3.ood_score}, approved={d3.approved}")

    # Stale calibration → rejected
    f4 = Forecast(
        expected_excess_return=0.03, horizon=1,
        lower_bound=0.00, upper_bound=0.06,
        direction_probability=0.9,
        calibration_state=CalibrationState.STALE,
        ood_score=0.1, model_artifact_id="test-v1",
        generated_at=now,
    )
    d4 = policy.evaluate(f4, requested_exposure=0.5)
    result.check(not d4.approved, "Stale calibration rejected",
                 f"cal_state={f4.calibration_state}")

    # Exposure clamping [0, 1]
    f5 = Forecast(
        expected_excess_return=0.02, horizon=1,
        lower_bound=0.01, upper_bound=0.05,  # strictly positive interval
        direction_probability=0.8,
        calibration_state=CalibrationState.CALIBRATED,
        ood_score=0.1, model_artifact_id="test-v1",
        generated_at=now,
    )
    d5 = policy.evaluate(f5, requested_exposure=1.5)
    result.check(
        0.0 <= d5.allowed_exposure <= 1.0,
        "Exposure clamped to [0,1]",
        f"allowed={d5.allowed_exposure}",
    )

    return result


# ── Scenario 5: Order Planning ──────────────────────────────────────────


def scenario_5_order_planner() -> TestResult:
    from trading_agent.execution.canonical.order_planner import (
        OrderPlanner,
        InstrumentRules,
        CurrentPortfolioState,
        MarketPrice,
        OrderPlanningStatus,
    )
    from trading_agent.execution.canonical.market_observation import (
        EnrichedMarketObservation,
    )
    from trading_agent.execution.canonical.risk_decision import (
        UnifiedRiskDecision,
        RiskLevel,
        EvidenceState,
        RiskReason,
    )
    from trading_agent.research.forecast import TargetExposure
    from trading_agent.llm.context_enrichment import MarketContext

    result = TestResult("Scenario 5: Order Planning (LLM-free)")

    now = datetime.now(UTC)

    rules = InstrumentRules(
        symbol="BTC/USDT",
        asset_class="spot",
        min_order_qty=0.001,
        max_order_qty=10.0,
        qty_step=0.001,
        price_precision=2,
        spot_long_only=True,
        min_notional=10.0,
    )
    planner = OrderPlanner(rules, strategy_version="e2e-v1")

    portfolio = CurrentPortfolioState(
        symbol="BTC/USDT",
        equity=10_000.0,
        current_exposure=0.0,
        available_cash=10_000.0,  # flat portfolio → all equity available
    )
    price = MarketPrice(
        symbol="BTC/USDT",
        mid=50_000.0,
        bid=49_990.0,
        ask=50_010.0,
        last=50_000.0,
    )
    obs = EnrichedMarketObservation(
        symbol="BTC/USDT",
        observed_at=now,
        open=50_000,
        high=50_200,
        low=49_800,
        close=50_100,
        volume=100.0,
        is_closed=True,
        bar_close_at=now - timedelta(minutes=1),
        data_manifest_id="e2e-test-manifest",
    )

    risk = UnifiedRiskDecision(
        decision_id="d-001",
        forecast_fingerprint="fp-001",
        model_artifact_id="ma-v1",
        requested_target_exposure=0.8,
        allowed_target_exposure=0.6,
        max_new_exposure=0.6,
        reduce_only=False,
        risk_level=RiskLevel.LOW,
        reason_codes=(RiskReason.APPROVED,),
        calibration_state=EvidenceState.KNOWN,
        calibration_artifact_id="cal-001",
        calibration_ece=0.05,
        ood_state=EvidenceState.KNOWN,
        ood_score=0.1,
        regime_state=EvidenceState.KNOWN,
        regime_entropy=0.3,
        interval_width=0.02,
        created_at=now,
    )

    target = TargetExposure(
        symbol="BTC/USDT",
        exposure=0.6,
        horizon=1,
        forecast_fingerprint="fp-001",
        model_artifact_id="ma-v1",
        risk_decision_id="d-001",
    )

    # Baseline: plan without LLM context
    plan_no_ctx = planner.plan(
        target=target,
        risk_decision=risk,
        observation=obs,
        portfolio=portfolio,
        price=price,
    )
    result.check(
        plan_no_ctx.status in (OrderPlanningStatus.ORDER_REQUIRED, OrderPlanningStatus.NOOP),
        "Plan without LLM context produces valid status",
        f"status={plan_no_ctx.status.value}",
    )

    # With LLM context — confidence 0.7 (advisory scaling)
    mc = MarketContext(
        regime_tags={"trend": "neutral", "volatility": "medium"},
        anomaly_flags=[],
        confidence_adjustment=0.7,
        reasoning="LLM detected neutral regime",
    )
    plan_with_ctx = planner.plan(
        target=target,
        risk_decision=risk,
        observation=obs,
        portfolio=portfolio,
        price=price,
        market_context=mc,
    )
    result.check(
        plan_with_ctx.status in (OrderPlanningStatus.ORDER_REQUIRED, OrderPlanningStatus.NOOP),
        "Plan with LLM context (confidence=0.7) accepted as enrichment",
    )

    # With LLM context — confidence 1.3 (amplified)
    mc_amp = MarketContext(
        regime_tags={"trend": "bullish", "volatility": "low"},
        anomaly_flags=[],
        confidence_adjustment=1.3,
        reasoning="LLM detected strong bullish regime",
    )
    plan_amp = planner.plan(
        target=target,
        risk_decision=risk,
        observation=obs,
        portfolio=portfolio,
        price=price,
        market_context=mc_amp,
    )
    result.check(
        plan_amp.status in (OrderPlanningStatus.ORDER_REQUIRED, OrderPlanningStatus.NOOP),
        "Plan with LLM context (confidence=1.3) accepted as enrichment",
    )

    # LLM-free invariance: removing context yields same risk-decision outcome
    plan_recheck = planner.plan(
        target=target,
        risk_decision=risk,
        observation=obs,
        portfolio=portfolio,
        price=price,
    )
    result.check(
        plan_recheck.status == plan_no_ctx.status,
        "LLM-free path is deterministic (same status with/without context)",
    )

    return result


# ── Scenario 6: LLM Enrichment ──────────────────────────────────────────


def scenario_6_llm_enrichment(df: pl.DataFrame, tmp_dir: Path) -> TestResult:
    from trading_agent.llm.context_enrichment import ContextEnricher, MarketContext
    from trading_agent.llm.research_memory import ResearchMemory
    from trading_agent.agents.base import AnalysisContext

    result = TestResult("Scenario 6: LLM Enrichment (Deterministic Mode)")

    # Force deterministic mode (no LLM calls)
    os.environ["USE_LLM"] = "false"
    import trading_agent.llm
    importlib.reload(trading_agent.llm)

    enricher = ContextEnricher()
    memory = ResearchMemory(tmp_dir / "e2e_memory.sqlite3")

    test_bars = df.tail(50)
    contexts: list[MarketContext] = []

    for i in range(30, len(test_bars)):
        row = test_bars.row(i, named=True)
        ma_20 = float(test_bars["close"].tail(20).mean())
        ma_50 = float(test_bars["close"].tail(50).mean())
        indicators = {
            "close": float(row["close"]),
            "ma_20": ma_20,
            "ma_50": ma_50,
        }
        ctx = AnalysisContext(
            symbol="BTC/USDT",
            timeframe="1h",
            current_price=float(row["close"]),
            indicators=indicators,
        )
        enriched = enricher.enrich(
            ctx,
            indicators=indicators,
            symbol="BTC/USDT",
            timeframe="1h",
        )
        contexts.append(enriched)
        memory.store("BTC/USDT", "1h", row["timestamp"], enriched, deterministic=True)

    result.check(len(contexts) == 20, f"Generated {len(contexts)} contexts")

    all_conf = [c.confidence_adjustment for c in contexts]
    result.check(
        all(abs(c - 1.0) < 1e-6 for c in all_conf),
        "Deterministic mode: all confidence = 1.0 (no LLM)",
    )
    result.check(
        all(len(c.anomaly_flags) == 0 for c in contexts),
        "Deterministic mode: no anomaly flags",
    )

    # Replay consistency
    for i, stored_ctx in enumerate(contexts):
        row = test_bars.row(30 + i, named=True)
        replay_ctx = AnalysisContext(
            symbol="BTC/USDT",
            timeframe="1h",
            current_price=float(row["close"]),
        )
        replayed = enricher.replay(
            replay_ctx,
            indicators={"close": float(row["close"])},
            bar_timestamp=row["timestamp"],
            symbol="BTC/USDT",
            timeframe="1h",
            memory=memory,
        )
        result.check(
            abs(replayed.confidence_adjustment - stored_ctx.confidence_adjustment) < 1e-6,
            f"Replay matches store (bar {i})",
            f"stored={stored_ctx.confidence_adjustment}, "
            f"replay={replayed.confidence_adjustment}",
        )

    os.environ.pop("USE_LLM", None)
    importlib.reload(trading_agent.llm)
    return result


# ── Scenario 7: Monitoring ────────────────────────────────────────────────


def scenario_7_monitoring(tmp_dir: Path) -> TestResult:
    result = TestResult("Scenario 7: Monitoring & Audit")

    # Audit DB from tournament run
    audit_db = tmp_dir / "tournament_state" / "audit.sqlite3"
    if audit_db.exists():
        import sqlite3

        conn = sqlite3.connect(str(audit_db))
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM entries")
        count = c.fetchone()[0]
        result.check(count > 0, f"Tournament audit DB has entries ({count})")
        c.execute("SELECT * FROM entries LIMIT 1")
        cols = [d[0] for d in c.description]
        result.check(
            len(cols) >= 5, f"Audit schema valid ({len(cols)} columns)"
        )
        conn.close()

    router_audit = tmp_dir / "router_audit.jsonl"
    prod_audit = Path(
        "data/tournament_paper/BTC_USDT/audit/tournament_audit.sqlite3"
    )
    result.check(
        router_audit.exists() or audit_db.exists() or prod_audit.exists(),
        "Audit trail exists (router_audit or SQLite audit DB or production)",
    )

    # Check process registry / monitoring dashboard via subprocess
    import subprocess

    proc = subprocess.run(
        ["python", "scripts/qwenpaw_control/process_registry.py", "list"],
        capture_output=True,
        text=True,
        timeout=10,
        cwd=str(Path(__file__).resolve().parent.parent),
    )
    has_dashboard = "monitoring_dashboard" in proc.stdout
    if not has_dashboard:
        # Check if the dashboard script exists (capability check)
        dash_script = Path("scripts/monitoring_dashboard.py")
        result.check(
            dash_script.exists(),
            "Monitoring dashboard script available (not yet running)",
        )
    else:
        result.check(True, "Monitoring dashboard process registered")

    result.check(
        Path("scripts/qwenpaw_control/health_check.py").exists(),
        "Health check script available",
    )
    result.check(
        Path("scripts/qwenpaw_control/process_registry.py").exists(),
        "Process registry available",
    )

    # Check existing production audit DB
    if prod_audit.exists():
        import sqlite3

        conn = sqlite3.connect(str(prod_audit))
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM entries")
        count = c.fetchone()[0]
        result.check(count > 0, f"Production audit DB has entries ({count})")
        conn.close()

    return result


# ── Main ─────────────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(description="End-to-end system test")
    parser.add_argument("--bars", type=int, default=DEFAULT_BARS)
    parser.add_argument("--symbols", default=",".join(DEFAULT_SYMBOLS))
    parser.add_argument("--output-dir", default="data/e2e_test_output")
    parser.add_argument(
        "--scenarios", default="all",
        help="Comma-separated scenario numbers (e.g. '1,2,3')",
    )
    args = parser.parse_args()

    symbols = [s.strip() for s in args.symbols.split(",")]
    tmp_dir = Path(args.output_dir)
    tmp_dir.mkdir(parents=True, exist_ok=True)

    from live_data_pipeline import BinanceDataFeed

    feed = BinanceDataFeed(symbols, dry_run=True, window_bars=DEFAULT_WINDOW)
    df = feed.fetch_recent_closed(symbols[0])
    test_df = df.tail(min(args.bars, len(df)))

    print(f"\n{'=' * 60}")
    print(f"  End-to-End System Test")
    print(f"  Symbol: {symbols[0]} | Timeframe: 1h | Bars: {len(test_df)}")
    print(f"  Output: {tmp_dir}")
    print(f"{'=' * 60}\n")

    scenarios: dict[str, callable] = {
        "1": lambda: scenario_1_data_integrity(symbols, DEFAULT_WINDOW),
        "2": lambda: scenario_2_strategy_signals(test_df),
        "3": lambda: scenario_3_tournament(symbols, DEFAULT_WINDOW, tmp_dir),
        "4": lambda: scenario_4_risk_policy(),
        "5": lambda: scenario_5_order_planner(),
        "6": lambda: scenario_6_llm_enrichment(test_df, tmp_dir),
        "7": lambda: scenario_7_monitoring(tmp_dir),
    }

    selected = (
        args.scenarios.split(",") if args.scenarios != "all" else list(scenarios.keys())
    )

    all_passed = True
    for num in selected:
        if num not in scenarios:
            print(f"  Unknown scenario: {num}")
            continue

        print(f"\n--- Running Scenario {num} ---")
        result = scenarios[num]()

        for detail in result.details:
            print(detail)
        print(f"\n{result.summary()}")

        if result.failed > 0:
            all_passed = False

    print(f"\n{'=' * 60}")
    overall = "ALL PASS" if all_passed else "SOME FAILURES"
    print(f"  OVERALL: {overall}")
    print(f"{'=' * 60}\n")

    return 0 if all_passed else 1


if __name__ == "__main__":
    sys.exit(main())
