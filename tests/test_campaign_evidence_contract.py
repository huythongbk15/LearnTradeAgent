"""Admissibility rules for campaign evidence.

docs/operations/CAMPAIGN_EVIDENCE_CONTRACT.md specifies what a
walk-forward campaign must record before its numbers can be used as L2
evidence. A contract with no test is a document, and the last three
contracts in this repository were documents until something read them.

Each test removes one field from an otherwise complete bundle and asserts
the check refuses it. A contract that only passes on the happy path is not
a contract — the failure mode it exists to catch is a bundle that looks
complete and is not.

Run: .venv/bin/python -m pytest tests/test_campaign_evidence_contract.py
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import polars as pl
from trading_agent.backtest.campaign_evidence import content_hash, window_hash

from trading_agent.backtest.campaign_integrity import (
    verify_campaign_coverage,
    verify_evidence_bundle as _verify_evidence_bundle,
)

ROUND_TRIP_BPS = 22.0  # 2*5 commission + 2*5 slippage + 2 spread


def verify_evidence_bundle(root, **kwargs):
    kwargs.setdefault("data_root", root.parent)
    return _verify_evidence_bundle(root, **kwargs)


def _hash(*parts: object) -> str:
    return hashlib.sha256("|".join(str(p) for p in parts).encode()).hexdigest()


def complete_bundle() -> dict:
    """A campaign bundle satisfying every rule in the contract."""
    folds = [
        {
            "fold_id": "fold_000",
            "train_span": ["2022-01-01", "2023-01-01"],
            "val_span": ["2023-01-01", "2023-03-01"],
            "test_span": ["2023-03-01", "2023-05-01"],
            "purge_bars": 25,
            "embargo_bars": 25,
            "overlap_group": None,
        }
    ]
    return {
        "schema_version": 2,
        "campaign_id": "placeholder",
        "subject": {
            "strategy_id": "stat_arbitrage_lo",
            "symbol": "BTC/USDT",
            "market_type": "spot",
            "timeframe": "1d",
            "params_hash": _hash("fast", 20, "slow", 80),
        },
        "code_commit": "a" * 40,
        "tree_fingerprint": _hash("src"),
        "dirty_diff_sha256": None,
        "worktree_dirty": False,
        "gate_version": "gates-2026-10",
        "data_manifest": {
            "data_manifest": _hash("ohlcv", 2456),
            "input_path": "data/raw/binance/BTC_USDT/1d.parquet",
            "input_rows": 2456,
            "window": {
                "start": "2020-01-01",
                "end": "2026-09-21",
                "bars": 2456,
                "timeframe": "1d",
                "market": "spot",
            },
            "cutoff_policy": "as_of",
            "gaps": [],
        },
        "cost_schedule": {
            "commission_bps": 5.0,
            "slippage_bps": 5.0,
            "spread_bps": 2.0,
            "cost_source": "constants",
            "round_trip_bps": ROUND_TRIP_BPS,
        },
        "folds": folds,
        "fold_count": 1,
        "overlap_policy": "independent",
        "results": [
            {
                "fold_id": "fold_000",
                "trades": 12,
                "gross_pnl": 120.0,
                "net_pnl": 40.0,
                "fees": 60.0,
                "slippage": 20.0,
                "spread_cost": 0.0,
                "return_pct": 0.4,
                "sharpe": 0.6,
                "max_dd_pct": -2.0,
                "cost_cleared": True,
                "entry_price": 100.0,
                "exit_price": 104.0,
            }
        ],
        "aggregate": {
            "median_return_pct": 0.4,
            "median_sharpe": 0.6,
            "total_trades": 12,
        },
        "trades": [
            {
                "fold_id": "fold_000",
                "entry_price": 100.0,
                "exit_price": 104.0,
                "pnl": 40.0,
                "bars_held": 30,
            }
        ],
        "trades_absent_reason": None,
        "verdict": "FAIL",
        "failed_gates": [
            {
                "gate_id": "outer_oos_sharpe_ge_080",
                "observed": 0.6,
                "unit": "sharpe",
                "threshold": 0.8,
                "comparison": ">=",
            }
        ],
        "gate_set_fingerprint": _hash("p<=0.20", "n>=14"),
        "gate_set": [
            {
                "gate_id": "outer_oos_sharpe_ge_080",
                "metric": "aggregate.median_sharpe",
                "unit": "sharpe",
                "threshold": 0.8,
                "comparison": ">=",
            }
        ],
    }


def write_bundle(tmp_path: Path, bundle: dict) -> Path:
    d = tmp_path / "campaign"
    d.mkdir()
    data_path = d / "input.parquet"
    frame = pl.DataFrame(
        {
            "timestamp": [
                datetime(2020, 1, 1, tzinfo=UTC) + timedelta(days=i)
                for i in range(2456)
            ],
            "open": [100.0] * 2456,
            "high": [101.0] * 2456,
            "low": [99.0] * 2456,
            "close": [100.0] * 2456,
            "volume": [10.0] * 2456,
        }
    )
    frame.write_parquet(data_path)
    if (
        bundle.get("data_manifest", {}).get("input_path")
        == "data/raw/binance/BTC_USDT/1d.parquet"
    ):
        bundle["data_manifest"]["input_path"] = str(data_path)
        bundle["data_manifest"]["file_sha256"] = hashlib.sha256(
            data_path.read_bytes()
        ).hexdigest()
        bundle["data_manifest"]["data_manifest"] = window_hash(frame)
    if "gate_set_fingerprint" in bundle:
        bundle["gate_set_fingerprint"] = content_hash(bundle.get("gate_set"))
    try:
        bundle["campaign_id"] = content_hash(
            {key: value for key, value in bundle.items() if key != "campaign_id"}
        )
    except ValueError:
        pass  # Intentionally corrupt NaN/inf fixtures must reach the validator.
    (d / "campaign_evidence.json").write_text(json.dumps(bundle, indent=2))
    return d


# ── the happy path ────────────────────────────────────────────────────────


def test_complete_bundle_is_admissible(tmp_path):
    report = verify_evidence_bundle(
        write_bundle(tmp_path, complete_bundle()),
        expected_commit="a" * 40,
        data_root=tmp_path,
    )
    assert report.admissible, report.problems
    assert list(report.problems) == []


# ── rule 1: code binding ──────────────────────────────────────────────────


def test_missing_commit_is_refused(tmp_path):
    b = complete_bundle()
    del b["code_commit"]
    r = verify_evidence_bundle(write_bundle(tmp_path, b))
    assert not r.admissible
    assert any("code_commit" in p for p in r.problems)


def test_commit_mismatch_is_refused(tmp_path):
    b = complete_bundle()
    b["code_commit"] = "b" * 40
    r = verify_evidence_bundle(write_bundle(tmp_path, b), expected_commit="a" * 40)
    assert not r.admissible
    assert any("does not match" in p for p in r.problems)


def test_dirty_diff_without_hash_is_refused(tmp_path):
    b = complete_bundle()
    b["dirty_diff_sha256"] = None
    b["tree_fingerprint"] = "dirty"
    # clean tree with a recorded hash is fine; a clean tree with a fingerprint
    # that differs from the recorded one is not.
    r = verify_evidence_bundle(write_bundle(tmp_path, b), expected_commit="a" * 40)
    assert not r.admissible
    assert any("tree_fingerprint" in problem for problem in r.problems)


# ── rule 3: input file identity ───────────────────────────────────────────


def test_missing_input_path_is_refused(tmp_path):
    b = complete_bundle()
    del b["data_manifest"]["input_path"]
    r = verify_evidence_bundle(write_bundle(tmp_path, b))
    assert not r.admissible
    assert any("input_path" in p for p in r.problems)


def test_row_count_mismatch_is_refused(tmp_path):
    b = complete_bundle()
    b["data_manifest"]["input_rows"] = 31783  # the hourly file, not daily
    r = verify_evidence_bundle(write_bundle(tmp_path, b))
    assert not r.admissible
    assert any("input_rows" in p for p in r.problems)


# ── rule 4: cost schedule ─────────────────────────────────────────────────


def test_missing_round_trip_is_refused(tmp_path):
    b = complete_bundle()
    del b["cost_schedule"]["round_trip_bps"]
    r = verify_evidence_bundle(write_bundle(tmp_path, b))
    assert not r.admissible
    assert any("round_trip_bps" in p for p in r.problems)


def test_round_trip_must_match_its_components(tmp_path):
    b = complete_bundle()
    b["cost_schedule"]["round_trip_bps"] = 999.0
    r = verify_evidence_bundle(write_bundle(tmp_path, b))
    assert not r.admissible
    assert any("round_trip_bps" in p for p in r.problems)


# ── rule 5 and 6: folds and overlap ───────────────────────────────────────


def test_duplicate_fold_ids_are_refused(tmp_path):
    b = complete_bundle()
    b["folds"] = b["folds"] * 2
    b["fold_count"] = 2
    r = verify_evidence_bundle(write_bundle(tmp_path, b))
    assert not r.admissible
    assert any("duplicate" in p.lower() or "fold_id" in p for p in r.problems)


def test_result_referencing_unknown_fold_is_refused(tmp_path):
    b = complete_bundle()
    b["results"][0]["fold_id"] = "fold_999"
    r = verify_evidence_bundle(write_bundle(tmp_path, b))
    assert not r.admissible
    assert any("fold_999" in p or "unknown fold" in p.lower() for p in r.problems)


def test_overlap_without_group_is_refused(tmp_path):
    b = complete_bundle()
    b["folds"][0]["overlap_group"] = None
    b["overlap_policy"] = "overlapping"
    r = verify_evidence_bundle(write_bundle(tmp_path, b))
    assert not r.admissible
    assert any("overlap" in p.lower() for p in r.problems)


# ── rule 7: per-trade records ─────────────────────────────────────────────


def test_missing_trades_without_reason_is_refused(tmp_path):
    b = complete_bundle()
    b["trades"] = None
    b["trades_absent_reason"] = None
    r = verify_evidence_bundle(write_bundle(tmp_path, b))
    assert not r.admissible
    assert any("trades" in p for p in r.problems)


def test_missing_trades_with_reason_is_admissible(tmp_path):
    b = complete_bundle()
    b["trades"] = None
    b["trades_absent_reason"] = "engine did not persist per-trade rows"
    r = verify_evidence_bundle(write_bundle(tmp_path, b), expected_commit="a" * 40)
    assert r.admissible, r.problems


# ── rule 8: verdict derivability ──────────────────────────────────────────


def test_failed_gates_must_be_named(tmp_path):
    b = complete_bundle()
    b["failed_gates"] = []
    r = verify_evidence_bundle(write_bundle(tmp_path, b))
    assert not r.admissible
    assert any("failed_gates" in p for p in r.problems)


def test_pass_verdict_requires_no_failed_gates(tmp_path):
    b = complete_bundle()
    b["verdict"] = "PASS"
    r = verify_evidence_bundle(write_bundle(tmp_path, b))
    assert not r.admissible
    assert any("PASS" in p for p in r.problems)


def test_missing_gate_fingerprint_is_refused(tmp_path):
    b = complete_bundle()
    del b["gate_set_fingerprint"]
    r = verify_evidence_bundle(write_bundle(tmp_path, b))
    assert not r.admissible
    assert any("gate_set_fingerprint" in p for p in r.problems)


# ── the check composes with the coverage check ────────────────────────────


@pytest.mark.parametrize(
    "mutation",
    [
        "missing_hash",
        "missing_file",
        "empty_metrics",
        "false_pass",
        "nan_cost",
        "duplicate_results",
        "wrong_aggregate",
        "broken_cost_bridge",
    ],
)
def test_semantic_counterexamples_fail_even_with_rehashed_identity(tmp_path, mutation):
    directory = write_bundle(tmp_path, complete_bundle())
    path = directory / "campaign_evidence.json"
    bundle = json.loads(path.read_text())
    if mutation == "missing_hash":
        bundle["data_manifest"].pop("file_sha256")
    elif mutation == "missing_file":
        bundle["data_manifest"]["input_path"] = str(tmp_path / "absent.parquet")
    elif mutation == "empty_metrics":
        bundle["results"] = [{"fold_id": "fold_000"}]
    elif mutation == "false_pass":
        bundle["verdict"] = "PASS"
        bundle["failed_gates"] = []
    elif mutation == "nan_cost":
        bundle["cost_schedule"]["round_trip_bps"] = float("nan")
    elif mutation == "duplicate_results":
        bundle["results"].append(dict(bundle["results"][0]))
    elif mutation == "wrong_aggregate":
        bundle["aggregate"]["total_trades"] += 1
    elif mutation == "broken_cost_bridge":
        bundle["results"][0]["net_pnl"] += 1
    if mutation != "nan_cost":
        bundle["campaign_id"] = content_hash(
            {k: v for k, v in bundle.items() if k != "campaign_id"}
        )
    path.write_text(json.dumps(bundle))
    assert not verify_evidence_bundle(directory).admissible


def test_bundle_check_and_coverage_check_are_separable(tmp_path):
    d = write_bundle(tmp_path, complete_bundle())
    bundle = verify_evidence_bundle(d)
    coverage = verify_campaign_coverage(
        requested_strategies=["stat_arbitrage_lo"], out_root=d, results=None
    )
    assert isinstance(bundle.admissible, bool)
    assert coverage.requested == 1


def test_wfo_consumer_refuses_missing_evidence_before_using_result(tmp_path):
    from trading_agent.backtest.campaign_integrity import require_wfo_campaign_evidence

    with pytest.raises(ValueError):
        require_wfo_campaign_evidence(
            None,
            tmp_path,
            data_root=tmp_path,
            source_binding={"code_commit": "a" * 40, "tree_fingerprint": "b" * 64},
        )


@pytest.mark.parametrize(
    "mutation",
    ["duplicate_time", "reverse_time", "nan_close", "negative_volume", "bad_high"],
)
def test_rehashed_invalid_ohlcv_is_rejected(tmp_path, mutation):
    directory = write_bundle(tmp_path, complete_bundle())
    path = directory / "campaign_evidence.json"
    bundle = json.loads(path.read_text())
    data_path = Path(bundle["data_manifest"]["input_path"])
    frame = pl.read_parquet(data_path)
    if mutation == "duplicate_time":
        frame = pl.concat([frame.head(1), frame.slice(0, frame.height - 1)])
    elif mutation == "reverse_time":
        frame = frame.reverse()
    elif mutation == "nan_close":
        frame = frame.with_columns(pl.lit(float("nan")).alias("close"))
    elif mutation == "negative_volume":
        frame = frame.with_columns(pl.lit(-1.0).alias("volume"))
    else:
        frame = frame.with_columns(pl.lit(98.0).alias("high"))
    frame.write_parquet(data_path)
    bundle["data_manifest"]["file_sha256"] = hashlib.sha256(
        data_path.read_bytes()
    ).hexdigest()
    if mutation != "nan_close":
        bundle["data_manifest"]["data_manifest"] = window_hash(frame)
    bundle["campaign_id"] = content_hash(
        {k: v for k, v in bundle.items() if k != "campaign_id"}
    )
    path.write_text(json.dumps(bundle))
    report = verify_evidence_bundle(directory)
    assert not report.admissible
    assert any("OHLCV" in problem for problem in report.problems)
