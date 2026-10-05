"""The native producer must derive everything and bind itself to its plan.

A producer that accepted a caller's aggregate, gate outcomes or thresholds
would let a plan be rewritten once the numbers arrived. These tests pin that
boundary: derive, don't accept; and refuse when the two stories disagree.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import polars as pl
import pytest

from trading_agent.backtest.campaign_evidence import content_hash, validate_v2
from trading_agent.backtest.campaign_plan import (
    build_campaign_bundle,
    build_data_manifest,
    freeze_campaign_plan,
    verified_bundle,
    verify_frozen_plan,
)

DATA_ROOT = Path("data")


def write_frame(directory: Path, rows: int = 60) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    start = datetime(2024, 1, 1, tzinfo=UTC)
    path = directory / "bars.parquet"
    frame = pl.DataFrame(
        {
            "timestamp": [start + timedelta(hours=i) for i in range(rows)],
            "open": [100.0 + i * 0.1 for i in range(rows)],
            "high": [101.0 + i * 0.1 for i in range(rows)],
            "low": [99.0 + i * 0.1 for i in range(rows)],
            "close": [100.5 + i * 0.1 for i in range(rows)],
            "volume": [10.0] * rows,
        }
    )
    frame.write_parquet(path)
    return path


SUBJECT = {
    "strategy_id": "enhanced_ma",
    "symbol": "BTC/USDT",
    "market_type": "spot",
    "timeframe": "1h",
}


def data_manifest(directory: Path, rows: int = 60, **subject_overrides) -> dict:
    """Build the manifest the same way a real campaign would: from the file."""
    path = write_frame(directory, rows)
    subject = {**SUBJECT, "params_hash": content_hash({"fast": 10, "slow": 60}), **subject_overrides}
    return build_data_manifest(
        path,
        data_root=directory,
        subject=subject,
        window={
            "start": datetime(2024, 1, 1, tzinfo=UTC).isoformat(),
            "end": (datetime(2024, 1, 1, tzinfo=UTC) + timedelta(hours=rows - 1)).isoformat(),
            "timeframe": subject["timeframe"],
            "market": subject["market_type"],
        },
    )


def folds(count: int = 3) -> list[dict]:
    records = []
    base = datetime(2024, 1, 1, tzinfo=UTC)
    for i in range(count):
        start = base + timedelta(days=i * 10)
        train_end = start + timedelta(days=3)
        val_end = start + timedelta(days=6)
        test_end = start + timedelta(days=9)
        records.append(
            {
                "fold_id": f"f{i}",
                "train_span": {"start": start.isoformat(), "end": train_end.isoformat()},
                "val_span": {"start": train_end.isoformat(), "end": val_end.isoformat()},
                "test_span": {"start": val_end.isoformat(), "end": test_end.isoformat()},
                "purge_bars": 1,
                "embargo_bars": 2,
            }
        )
    return records


def plan_for(directory: Path, *, gates=None, fold_count: int = 3) -> dict:
    return freeze_campaign_plan(
        subject={
            "strategy_id": "enhanced_ma",
            "symbol": "BTC/USDT",
            "market_type": "spot",
            "timeframe": "1h",
            "params_hash": content_hash({"fast": 10, "slow": 60}),
        },
        folds=folds(fold_count),
        data_manifest=data_manifest(directory),
        cost_schedule={
            "commission_bps": 5.0,
            "slippage_bps": 2.0,
            "spread_bps": 1.0,
            "round_trip_bps": 15.0,
        },
        gate_set=gates
        or [
            {
                "gate_id": "median_sharpe_positive",
                "metric": "aggregate.median_sharpe",
                "comparison": ">",
                "threshold": 0.5,
                "unit": "ratio",
            },
            {
                "gate_id": "min_trades",
                "metric": "aggregate.total_trades",
                "comparison": ">=",
                "threshold": 30,
                "unit": "trades",
            },
        ],
        param_grid={"fast": [10], "slow": [60]},
        frozen_by="test",
        frozen_at=datetime(2024, 1, 1, tzinfo=UTC),
    )


def row(fold_id: str, *, sharpe: float, trades: int, net: float) -> dict:
    fees, slip, spread = 5.0, 3.0, 2.0
    return {
        "fold_id": fold_id,
        "report_sha256": "a" * 64,
        "gross_pnl": net + fees + slip + spread,
        "net_pnl": net,
        "fees": fees,
        "slippage": slip,
        "spread_cost": spread,
        "return_pct": 1.0,
        "sharpe": sharpe,
        "max_dd_pct": 5.0,
        "trades": trades,
    }


def source_binding() -> dict:
    return {
        "code_commit": "b" * 40,
        "tree_fingerprint": "c" * 64,
        "worktree_dirty": False,
        "dirty_diff_sha256": None,
    }


def test_frozen_plan_binds_its_own_content(tmp_path: Path) -> None:
    plan = plan_for(tmp_path)
    verify_frozen_plan(plan)
    assert len(plan["plan_fingerprint"]) == 64
    assert plan["gate_set_fingerprint"] == content_hash(plan["gate_set"])


def test_edited_plan_is_refused(tmp_path: Path) -> None:
    plan = plan_for(tmp_path)
    plan["gate_set"][0]["threshold"] = -99.0
    with pytest.raises(ValueError, match="edited after freezing"):
        verify_frozen_plan(plan)


def test_plan_must_be_frozen_before_it_can_produce(tmp_path: Path) -> None:
    plan = plan_for(tmp_path)
    del plan["plan_fingerprint"]
    with pytest.raises(ValueError, match="plan_fingerprint"):
        build_campaign_bundle(
            plan=plan, rows=[], source=source_binding(), data_root=DATA_ROOT
        )


def test_producer_recomputes_aggregate_instead_of_accepting_it(tmp_path: Path) -> None:
    rows = [row("f0", sharpe=1.0, trades=10, net=1.0), row("f1", sharpe=3.0, trades=10, net=1.0)]
    bundle = build_campaign_bundle(
        plan=plan_for(tmp_path, fold_count=2),
        rows=rows,
        source=source_binding(),
        data_root=DATA_ROOT,
    )
    assert bundle["aggregate"]["median_sharpe"] == 2.0
    assert bundle["aggregate"]["total_trades"] == 20


def test_gate_outcome_and_verdict_recompute_from_frozen_thresholds(
    tmp_path: Path,
) -> None:
    rows = [row("f0", sharpe=0.1, trades=10, net=1.0), row("f1", sharpe=0.2, trades=10, net=1.0)]
    bundle = build_campaign_bundle(
        plan=plan_for(tmp_path, fold_count=2),
        rows=rows,
        source=source_binding(),
        data_root=DATA_ROOT,
    )
    assert bundle["verdict"] == "FAIL"
    assert [g["gate_id"] for g in bundle["failed_gates"]] == [
        "median_sharpe_positive",
        "min_trades",
    ]


def test_single_fold_never_passes_a_median_gate(tmp_path: Path) -> None:
    # One fold has no dispersion, so a median from it is just that fold. It
    # must not clear a threshold for reasons unrelated to the strategy.
    plan = plan_for(tmp_path, fold_count=1)
    rows = [row("f0", sharpe=99.0, trades=100, net=100.0)]
    bundle = build_campaign_bundle(
        plan=plan, rows=rows, source=source_binding(), data_root=DATA_ROOT
    )
    assert bundle["verdict"] == "FAIL"
    assert bundle["failed_gates"]


def test_rows_must_match_the_frozen_folds(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="do not match the frozen plan"):
        build_campaign_bundle(
            plan=plan_for(tmp_path),
            rows=[row("f9", sharpe=1.0, trades=10, net=1.0)],
            source=source_binding(),
            data_root=DATA_ROOT,
        )


def test_subject_cannot_drift_from_the_plan(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="differs from the frozen plan"):
        build_campaign_bundle(
            plan=plan_for(tmp_path),
            rows=[row("f0", sharpe=1.0, trades=10, net=1.0)],
            source=source_binding(),
            data_root=DATA_ROOT,
            subject={"strategy_id": "something_else"},
        )


def test_produced_bundle_passes_schema_v2_validation(tmp_path: Path) -> None:
    rows = [
        row("f0", sharpe=1.0, trades=10, net=1.0),
        row("f1", sharpe=1.2, trades=10, net=1.0),
        row("f2", sharpe=0.9, trades=12, net=1.0),
    ]
    plan = plan_for(tmp_path)
    bundle = verified_bundle(
        plan=plan, rows=rows, source=source_binding(), data_root=tmp_path
    )
    errors = validate_v2(bundle, data_root=tmp_path)
    assert errors == []


def test_campaign_id_binds_bundle_content(tmp_path: Path) -> None:
    rows = [row("f0", sharpe=1.0, trades=10, net=1.0)]
    bundle = build_campaign_bundle(
        plan=plan_for(tmp_path, fold_count=1), rows=rows, source=source_binding(), data_root=DATA_ROOT
    )
    tampered = {**bundle, "net_pnl_note": "changed"}
    assert content_hash({k: v for k, v in tampered.items() if k != "campaign_id"}) != (
        bundle["campaign_id"]
    )


def test_cost_schedule_must_recompute(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="round_trip_bps does not recompute"):
        freeze_campaign_plan(
            subject={
                "strategy_id": "s",
                "symbol": "BTC/USDT",
                "market_type": "spot",
                "timeframe": "1h",
                "params_hash": content_hash({}),
            },
            folds=folds(1),
            data_manifest=data_manifest(tmp_path),
            cost_schedule={
                "commission_bps": 5.0,
                "slippage_bps": 2.0,
                "spread_bps": 1.0,
                "round_trip_bps": 999.0,
            },
            gate_set=[
                {
                    "gate_id": "g",
                    "metric": "aggregate.median_sharpe",
                    "comparison": ">",
                    "threshold": 0.0,
                    "unit": "ratio",
                }
            ],
            frozen_by="test",
        )


def test_gate_must_measure_a_recorded_aggregate(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="aggregate"):
        freeze_campaign_plan(
            subject={
                "strategy_id": "s",
                "symbol": "BTC/USDT",
                "market_type": "spot",
                "timeframe": "1h",
                "params_hash": content_hash({}),
            },
            folds=folds(1),
            data_manifest=data_manifest(tmp_path),
            cost_schedule={
                "commission_bps": 5.0,
                "slippage_bps": 2.0,
                "spread_bps": 1.0,
                "round_trip_bps": 15.0,
            },
            gate_set=[
                {
                    "gate_id": "g",
                    "metric": "max_drawdown",  # not an aggregate field
                    "comparison": "<",
                    "threshold": 10.0,
                    "unit": "pct",
                }
            ],
            frozen_by="test",
        )


def test_frozen_plan_survives_a_round_trip_through_json(tmp_path: Path) -> None:
    # A plan written to disk and reloaded must still verify; otherwise the
    # fingerprint proves nothing about the plan that actually ran.
    plan = plan_for(tmp_path, fold_count=1)
    reloaded = json.loads(json.dumps(plan))
    verify_frozen_plan(reloaded)
    bundle = build_campaign_bundle(
        plan=reloaded,
        rows=[row("f0", sharpe=1.0, trades=10, net=1.0)],
        source=source_binding(),
        data_root=DATA_ROOT,
    )
    assert bundle["plan_fingerprint"] == plan["plan_fingerprint"]