from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta

import pytest

from trading_agent.execution.simulator.calibration_provenance import (
    BookLevel,
    BookSnapshot,
    CalibrationDataset,
    CalibrationDatasetStore,
    CalibrationObservation,
    CalibrationProfile,
    CalibrationSource,
    CalibrationStatus,
    collect_exchange_observations,
)
from trading_agent.execution.simulator.reality_gap import (
    DEFAULT_REQUIRED_DISTRIBUTIONS,
    ExecutionDistributionEvidence,
    compute_distributional_reality_gap,
    compute_reality_gap,
    promotion_check,
)


def _observation(
    index: int,
    *,
    source: CalibrationSource,
    latency_ms: float = 50.0,
    slippage_bps: float = 1.0,
    fill_ratio: float = 1.0,
    partial_fills: int = 0,
    adverse_scale: float = 1.0,
) -> CalibrationObservation:
    timestamp = datetime(2025, 1, 1, tzinfo=UTC) + timedelta(seconds=index)
    snapshot = BookSnapshot(
        observed_at=timestamp,
        sequence=index,
        bids=(BookLevel(99.99, 10.0), BookLevel(99.98, 20.0)),
        asks=(BookLevel(100.01, 10.0), BookLevel(100.02, 20.0)),
    )
    return CalibrationObservation(
        timestamp=timestamp,
        symbol="BTC/USDT",
        exchange="binance",
        book_snapshot=snapshot,
        best_bid=99.99,
        best_ask=100.01,
        bid_depth=30.0,
        ask_depth=30.0,
        spread_bps=2.0,
        trade_flow=0.1,
        order_side="buy",
        order_type="limit",
        requested_qty=1.0,
        filled_qty=fill_ratio,
        fill_latency_ms=latency_ms,
        partial_fills=partial_fills,
        slippage_bps=slippage_bps,
        adverse_selection_100ms_bps=0.1 * adverse_scale,
        adverse_selection_1s_bps=0.2 * adverse_scale,
        adverse_selection_5s_bps=0.3 * adverse_scale,
        adverse_selection_30s_bps=0.4 * adverse_scale,
        client_order_id=f"client-{index}",
        broker_order_id=f"broker-{index}",
        source=source,
    )


def _profile(dataset: CalibrationDataset) -> CalibrationProfile:
    return CalibrationProfile.build(
        dataset,
        spread_model_version="spread-v1",
        depth_model_version="depth-v1",
        fill_model_version="fill-v1",
        latency_model_version="latency-v1",
        impact_model_version="impact-v1",
        adverse_selection_model_version="adverse-v1",
    )


def test_calibration_dataset_and_profile_keep_source_immutable(tmp_path) -> None:
    synthetic = CalibrationDataset.build(
        _observation(index, source=CalibrationSource.SYNTHETIC) for index in range(20)
    )
    testnet = CalibrationDataset.build(
        _observation(index, source=CalibrationSource.TESTNET) for index in range(20)
    )
    assert synthetic.dataset_id != testnet.dataset_id
    assert _profile(synthetic).status == CalibrationStatus.HEURISTIC
    assert _profile(testnet).status == CalibrationStatus.EMPIRICAL
    assert _profile(synthetic).source == CalibrationSource.SYNTHETIC
    with pytest.raises(FrozenInstanceError):
        synthetic.source = CalibrationSource.LIVE  # type: ignore[misc]

    store = CalibrationDatasetStore(tmp_path / "datasets")
    first_path = store.put(synthetic)
    assert first_path == store.put(synthetic)
    assert first_path.exists()


def test_dataset_rejects_mixed_sources() -> None:
    records = [
        _observation(0, source=CalibrationSource.SYNTHETIC),
        _observation(1, source=CalibrationSource.TESTNET),
    ]
    with pytest.raises(ValueError, match="mix sources"):
        CalibrationDataset.build(records)


def test_exchange_collection_interface_rejects_synthetic() -> None:
    class Provider:
        def __init__(self, source):
            self.source = source

        def observations(self, **kwargs):
            return [_observation(0, source=self.source)]

    start = datetime(2025, 1, 1, tzinfo=UTC)
    end = start + timedelta(hours=1)
    dataset = collect_exchange_observations(
        Provider(CalibrationSource.TESTNET),
        exchange="binance",
        symbols=["BTC/USDT"],
        start=start,
        end=end,
    )
    assert dataset.source == CalibrationSource.TESTNET
    with pytest.raises(ValueError, match="SYNTHETIC"):
        collect_exchange_observations(
            Provider(CalibrationSource.SYNTHETIC),
            exchange="binance",
            symbols=["BTC/USDT"],
            start=start,
            end=end,
        )


def test_identical_distribution_evidence_passes_with_real_observations() -> None:
    synthetic_records = tuple(
        _observation(index, source=CalibrationSource.SYNTHETIC, latency_ms=40 + index % 5)
        for index in range(100)
    )
    exchange_records = tuple(
        _observation(index, source=CalibrationSource.TESTNET, latency_ms=40 + index % 5)
        for index in range(100)
    )
    simulator = ExecutionDistributionEvidence.from_observations(
        _profile(CalibrationDataset.build(synthetic_records)), synthetic_records
    )
    observed = ExecutionDistributionEvidence.from_observations(
        _profile(CalibrationDataset.build(exchange_records)), exchange_records
    )
    report = compute_distributional_reality_gap(
        stage="TESTNET", simulator=simulator, observed_exchange=observed
    )
    assert report.pass_gate
    latency = next(result for result in report.results if result.metric == "latency_ms")
    assert latency.simulator.p50 == latency.observed_exchange.p50
    assert latency.simulator.p95 == latency.observed_exchange.p95
    assert latency.simulator.p99 == latency.observed_exchange.p99


def test_tail_distribution_gap_fails_even_when_latency_mean_matches() -> None:
    observed_latencies = [10.0] * 100
    simulator_latencies = [0.0] * 99 + [1_000.0]
    assert sum(observed_latencies) / 100 == sum(simulator_latencies) / 100
    synthetic_records = tuple(
        _observation(index, source=CalibrationSource.SYNTHETIC, latency_ms=value)
        for index, value in enumerate(simulator_latencies)
    )
    exchange_records = tuple(
        _observation(index, source=CalibrationSource.TESTNET, latency_ms=value)
        for index, value in enumerate(observed_latencies)
    )
    simulator = ExecutionDistributionEvidence.from_observations(
        _profile(CalibrationDataset.build(synthetic_records)), synthetic_records
    )
    observed = ExecutionDistributionEvidence.from_observations(
        _profile(CalibrationDataset.build(exchange_records)), exchange_records
    )
    report = compute_distributional_reality_gap(
        stage="TESTNET", simulator=simulator, observed_exchange=observed
    )
    assert not report.pass_gate
    assert any("latency_ms" in breach for breach in report.breaches)


def test_missing_or_synthetic_distribution_evidence_fails_promotion() -> None:
    missing = compute_distributional_reality_gap(
        stage="SHADOW", simulator=None, observed_exchange=None
    )
    assert not missing.pass_gate
    assert set(missing.missing_required) == set(DEFAULT_REQUIRED_DISTRIBUTIONS)

    records = tuple(
        _observation(index, source=CalibrationSource.SYNTHETIC) for index in range(20)
    )
    profile = _profile(CalibrationDataset.build(records))
    evidence = ExecutionDistributionEvidence.from_observations(profile, records)
    synthetic_only = compute_distributional_reality_gap(
        stage="SHADOW", simulator=evidence, observed_exchange=evidence
    )
    assert not synthetic_only.pass_gate
    assert any("SYNTHETIC" in breach for breach in synthetic_only.breaches)

    scalar_metrics = {
        "fill_ratio": 1.0,
        "slippage_bps": 0.0,
        "implementation_shortfall_bps": 0.0,
        "trade_count": 10.0,
        "rejected_order_rate": 0.0,
        "partial_fill_rate": 0.0,
    }
    scalar = compute_reality_gap(
        environment="simulator",
        reference_environment="testnet",
        observed=scalar_metrics,
        reference=scalar_metrics,
    )
    assert scalar.pass_gate
    assert not promotion_check(scalar, missing)
