"""v2 calibration regression tests. Synthetic evidence is never alpha proof."""

import importlib
import json
import os
import shutil
import sqlite3
import sys
import tempfile

import pytest

from trading_agent.calibration import execution, packet


@pytest.fixture(scope="module")
def measured(tmp_path_factory):
    from trading_agent.backtest.synthetic_data import generate_synthetic_ohlcv

    root = tmp_path_factory.mktemp("calibration_v2")
    frame = generate_synthetic_ohlcv(n_bars=800, seed=7)
    data = root / "input.parquet"
    frame.write_parquet(data)
    out = root / "packet"
    execution.freeze_plan(out, data, synthetic=True)
    result = execution.execute(out)
    assert result["status"] == "ACCEPTED_CALIBRATION", result
    return out


@pytest.fixture
def clone(measured, tmp_path):
    # Paths are frozen absolute identities: test tampering in place, then restore
    # all bytes with an owned backup. Each test is serial in this module.
    backup = tmp_path / "backup"
    shutil.copytree(measured, backup)
    yield measured
    shutil.rmtree(measured)
    shutil.copytree(backup, measured)


def change(path, fn):
    value = packet.read(path)
    fn(value)
    path.write_text(json.dumps(value), encoding="utf-8")


def rejected(root):
    result = packet.verify_packet(root)
    assert result["status"] == "REJECTED", result
    assert not result["live_authorized"] and not result["qualified"]
    return result


def test_roundtrip_real_engine_is_validation_only(measured):
    result = packet.verify_packet(measured)
    assert all(result["checks"].values())
    assert result["status"] == "ACCEPTED_CALIBRATION"
    plan = packet.read(measured / "calibration_manifest.json")["plan"]
    assert plan["evidence_class"] == "SYNTHETIC_TEST_ONLY"
    assert len(plan["cells"]) == 2
    rows = packet.registry_snapshot(measured / "trials.sqlite3")["evaluation_records"]
    assert len(rows) == 4  # Intent + outcome; intent is not a second measurement.
    assert {r["trial_phase"] for r in rows} == {"INNER_VALIDATION", "UNKNOWN"}
    assert not list((measured / "inner_selection_freezes").glob("*.json"))


@pytest.mark.parametrize("action", ["edit", "add", "remove", "missing_terminal"])
def test_inventory_mutations_fail(clone, action):
    path = clone / "execution_summary.json"
    if action == "edit":
        path.write_bytes(path.read_bytes() + b" ")
    elif action == "add":
        (clone / "extra.bak").write_text("unexpected")
    elif action == "remove":
        path.unlink()
    else:
        (clone / packet.TERMINAL).unlink()
    rejected(clone)


def test_mtime_only_does_not_invalidate_evidence(clone):
    path = clone / "execution_summary.json"
    os.utime(path, (1, 1))
    assert packet.verify_packet(clone)["status"] == "ACCEPTED_CALIBRATION"


@pytest.mark.parametrize(
    "field,value",
    [
        ("cpu_seconds", None),
        ("rss_peak_bytes", 0),
        ("cpu_seconds", float("nan")),
        ("cpu_seconds", 121),
        ("rss_peak_bytes", 3_000_000_000),
    ],
)
def test_resource_receipt_requires_both_finite_measurements(clone, field, value):
    path = sorted((clone / "receipts").glob("*.json"))[0]
    change(path, lambda x: x["resources"].update({field: value}))
    result = rejected(clone)
    assert result["checks"]["C6_enforced_resources"] is False


def test_failed_measurement_is_not_completed_abstain(clone):
    path = sorted((clone / "receipts").glob("*.json"))[0]
    change(path, lambda x: x.update(status="FAILED"))
    assert rejected(clone)["checks"]["C1_exact_completed_coverage"] is False


def test_nonfirst_registry_row_tampering_fails_logical_seal(clone):
    db = clone / "trials.sqlite3"
    with sqlite3.connect(db) as conn:
        conn.execute("DROP TRIGGER evaluation_records_no_update")
        row = conn.execute(
            "SELECT evaluation_id, metadata_json FROM evaluation_records ORDER BY evaluation_id DESC LIMIT 1"
        ).fetchone()
        meta = json.loads(row[1])
        meta["evaluation_identity"]["execution_context"]["source"][
            "tree_fingerprint"
        ] = "f" * 64
        conn.execute(
            "UPDATE evaluation_records SET metadata_json=? WHERE evaluation_id=?",
            (json.dumps(meta), row[0]),
        )
    result = rejected(clone)
    assert result["checks"]["C3_all_versioned_trials"] is False
    assert result["checks"]["C9_logical_registry_seal"] is False


def test_registry_metric_edit_not_hidden_by_excluded_sqlite_bytes(clone):
    with sqlite3.connect(clone / "trials.sqlite3") as conn:
        conn.execute("DROP TRIGGER evaluation_records_no_update")
        conn.execute(
            "UPDATE evaluation_records SET metric_value=99 WHERE metric_name='inner_validation_sharpe'"
        )
    assert rejected(clone)["checks"]["C9_logical_registry_seal"] is False


def test_wrong_report_path_sha_and_scope_are_rejected(clone):
    path = sorted((clone / "receipts").glob("*.json"))[0]
    change(
        path,
        lambda x: x["candidate"]["validation_source"].update(
            report_path="../outside.json", report_sha256="f" * 64
        ),
    )
    assert rejected(clone)["checks"]["C5_attributed_raw_reports"] is False


def test_fake_freeze_for_abstain_rejected(clone):
    (clone / "inner_selection_freezes").mkdir(exist_ok=True)
    (clone / "inner_selection_freezes" / "rsi_cal_early.json").write_text("{}")
    assert rejected(clone)["checks"]["C4_canonical_decisions"] is False


def test_post_run_source_drift_rejected(clone):
    change(
        clone / "identity_after.json",
        lambda x: x["source"].update(tree_fingerprint="f" * 64),
    )
    assert rejected(clone)["checks"]["C10_source_input_config_stable"] is False


def test_shared_script_verifier_and_readonly(measured):
    from scripts.verify_calibration import verify_calibration

    files_before = packet.inventory(measured)
    registry_before = packet.registry_snapshot(measured / "trials.sqlite3")
    assert verify_calibration(measured) == packet.verify_packet(measured)["checks"]
    assert files_before == packet.inventory(measured)
    assert registry_before == packet.registry_snapshot(measured / "trials.sqlite3")


@pytest.mark.parametrize("kind", ["strategy", "cost", "risk"])
def test_raw_effective_config_cannot_be_relabelled_by_artifact(clone, kind):
    import hashlib
    from trading_agent.backtest.reporting import fingerprint_payload

    receipt_path = sorted((clone / "receipts").glob("*.json"))[0]
    receipt = packet.read(receipt_path)
    source = receipt["candidate"]["validation_source"]
    path = clone / source["report_path"]
    report = packet.read(path)
    config = report["active_config"]
    if kind == "strategy":
        config["strategy"]["parameters"]["period"] = 21
    elif kind == "cost":
        config["cost_model"]["commission_rate"] = 0.0
    else:
        config["risk"]["max_drawdown_pct"] = 99.0
    config["config_id"] = fingerprint_payload(
        {k: v for k, v in config.items() if k != "config_id"}
    )
    path.write_text(json.dumps(report))
    source["report_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    receipt_path.write_text(json.dumps(receipt))
    assert rejected(clone)["checks"]["C5_attributed_raw_reports"] is False


def test_unrelated_future_registry_append_does_not_invalidate_packet(clone):
    from trading_agent.research.trials import ExperimentRegistry, ExperimentSpec

    registry = ExperimentRegistry(str(clone / "trials.sqlite3"))
    spec = ExperimentSpec.build(
        strategy_name="unrelated",
        strategy_code_sha="other",
        data_manifest_sha="other",
        feature_schema_hash="other",
        params_hash="other",
        search_family="other",
        search_space_hash="other",
        target_horizon="other",
        evaluator_version="v1",
        seed=42,
    )
    registry.register_experiment(spec)
    registry.append_evaluation(
        experiment_id=spec.experiment_id,
        fold_id="other",
        metric_name="legacy",
        metric_value=1.0,
        environment_hash="legacy",
        metadata={},
    )
    assert packet.verify_packet(clone)["status"] == "ACCEPTED_CALIBRATION"


def test_import_does_not_load_prices(monkeypatch):
    from trading_agent.data import storage

    def forbidden(*args, **kwargs):
        raise AssertionError("import tried to load prices")

    monkeypatch.setattr(storage, "load_ohlcv", forbidden)
    monkeypatch.setattr(storage, "load_research_ohlcv", forbidden)
    import scripts.run_calibration as runner

    importlib.reload(runner)
    import scripts.verify_calibration as verifier

    importlib.reload(verifier)


def test_legacy_packet_never_upgraded(tmp_path):
    (tmp_path / "calibration_manifest.json").write_text('{"protocol_version":"1.0"}')
    assert packet.verify_packet(tmp_path)["status"] == "REJECTED"
    assert not (tmp_path / packet.TERMINAL).exists()


@pytest.mark.parametrize("kind", ["wall", "cpu", "rss"])
def test_watchdog_interrupts_real_process(tmp_path, kind):
    budget = packet.default_budget()
    budget["cell_wall_seconds"] = 0.2 if kind == "wall" else 4
    budget["cell_cpu_seconds"] = 0.1 if kind == "cpu" else 120
    budget["rss_limit_bytes"] = 1 if kind == "rss" else budget["rss_limit_bytes"]
    code = "while True: pass" if kind == "cpu" else "import time; time.sleep(30)"
    receipt = execution.supervise(
        [sys.executable, "-c", code],
        budget=budget,
        log_path=tmp_path / "child.log",
        remaining_wall=4,
        remaining_cpu=120,
    )
    assert receipt["limit_exceeded"] == kind
    assert receipt["returncode"] != 0
    assert receipt["active_descendants"] == []


def test_owned_descendants_are_stopped(tmp_path):
    code = "import subprocess,time,sys; subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)']); time.sleep(30)"
    budget = packet.default_budget() | {"cell_wall_seconds": 0.3}
    receipt = execution.supervise(
        [sys.executable, "-c", code],
        budget=budget,
        log_path=tmp_path / "child.log",
        remaining_wall=4,
        remaining_cpu=120,
    )
    assert receipt["limit_exceeded"] == "wall"
    assert not receipt["active_descendants"]


def test_successful_leader_cannot_leave_running_child(tmp_path):
    code = "import subprocess,sys; subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)'])"
    receipt = execution.supervise(
        [sys.executable, "-c", code],
        budget=packet.default_budget(),
        log_path=tmp_path / "child.log",
        remaining_wall=4,
        remaining_cpu=120,
    )
    assert receipt["limit_exceeded"] == "active_descendants_after_exit"
    assert not receipt["active_descendants"]


def test_holdout_rejected_before_full_ohlcv_read(tmp_path, monkeypatch):
    from trading_agent.backtest.synthetic_data import generate_synthetic_ohlcv
    import polars as pl

    frame = generate_synthetic_ohlcv(n_bars=800, start_ts=execution.utc("2026-01-01"))
    path = tmp_path / "input.parquet"
    frame.write_parquet(path)
    original = pl.read_parquet
    calls = []

    def timestamps_only(*args, **kwargs):
        calls.append(kwargs)
        assert kwargs.get("columns") == ["timestamp"]
        return original(*args, **kwargs)

    monkeypatch.setattr(pl, "read_parquet", timestamps_only)
    with pytest.raises(ValueError, match="holdout"):
        execution.freeze_plan(tmp_path / "out", path, synthetic=True)
    assert len(calls) == 1


def test_completed_packet_cannot_resume_or_reseal(measured):
    with pytest.raises(FileExistsError):
        execution.execute(measured)


@pytest.mark.parametrize(
    "kind", ["threshold", "budget", "identity", "matrix", "candidate_id"]
)
def test_invalid_plan_rejected_before_worker_launch(measured, monkeypatch, kind):
    import copy

    plan = copy.deepcopy(packet.read(measured / "calibration_manifest.json")["plan"])
    if kind == "threshold":
        plan["selection_contract"]["min_validation_trades"] = 1
    elif kind == "budget":
        plan["budget"]["retries"] = 2
    elif kind == "identity":
        plan["execution_context"]["source"]["tree_fingerprint"] = "f" * 64
    elif kind == "matrix":
        plan["cells"].append(plan["cells"][0])
    else:
        plan["cells"][0]["experiment"]["experiment_id"] = "exp_fake"
    with pytest.raises(ValueError):
        packet.validate_plan(plan, measured)


def test_actual_timestamp_binding_not_arithmetic_index(measured):
    import copy

    plan = copy.deepcopy(packet.read(measured / "calibration_manifest.json")["plan"])
    plan["cells"][0]["measurement_window"][0] += 1
    with pytest.raises(ValueError, match="timestamp"):
        packet.validate_plan(plan, measured)


def test_worker_resource_identity_cannot_be_cumulative_receipt(clone):
    paths = sorted((clone / "receipts").glob("*.json"))
    first = packet.read(paths[0])
    change(paths[1], lambda x: x.update(resources=first["resources"]))
    assert rejected(clone)["checks"]["C6_enforced_resources"] is False


def test_select_requires_exact_freeze_without_bypassing_report_guard(clone):
    from trading_agent.backtest.selection_eligibility import (
        assess_validation_candidates,
    )

    manifest = packet.read(clone / "calibration_manifest.json")
    key = manifest["plan"]["cells"][0]["key"]
    path = clone / f"receipts/{key}.json"
    receipt = packet.read(path)
    receipt["candidate"]["val_metrics"].update(total_trades=30, sharpe=1.0)
    path.write_text(json.dumps(receipt))
    decision = assess_validation_candidates(
        manifest["plan"]["selection_contract"], [receipt["candidate"]]
    )
    (clone / f"decisions/{key}.json").write_text(json.dumps(decision))
    (clone / "inner_selection_freezes").mkdir(exist_ok=True)
    (clone / f"inner_selection_freezes/{key}.json").write_text(
        json.dumps(
            {
                "protocol_fingerprint": manifest["protocol_fingerprint"],
                "key": key,
                "selected": decision["selected"],
                "validation_only": True,
            }
        )
    )
    result = rejected(clone)
    assert result["checks"]["C4_canonical_decisions"] is True
    assert result["checks"]["C5_attributed_raw_reports"] is False


def test_direct_worker_cannot_reach_engine_without_supervisor(measured, monkeypatch):
    from trading_agent.backtest import tournament

    monkeypatch.delenv(execution.AUTHORITY_FD_ENV, raising=False)

    def forbidden(*args, **kwargs):
        pytest.fail("standalone worker reached the engine")

    monkeypatch.setattr(tournament, "run_cell", forbidden)
    before = packet.inventory(measured)
    with pytest.raises(ValueError, match="supervised parent authority"):
        execution.worker(measured, "rsi_cal_early")
    assert packet.inventory(measured) == before


def test_worker_cli_without_authority_fails_closed(measured, tmp_path, monkeypatch):
    monkeypatch.delenv(execution.AUTHORITY_FD_ENV, raising=False)
    path = tmp_path / "worker.log"
    before = packet.inventory(measured)
    receipt = execution.supervise(
        [
            sys.executable,
            str(execution.ROOT / "scripts/run_calibration.py"),
            "--worker",
            "--out",
            str(measured),
            "--key",
            "rsi_cal_early",
        ],
        budget=packet.default_budget(),
        log_path=path,
        remaining_wall=10,
        remaining_cpu=10,
    )
    assert receipt["returncode"] != 0
    assert "requires supervised parent authority" in path.read_text()
    assert packet.inventory(measured) == before


def test_worker_rejects_wrong_intent_and_completed_replay(measured):
    manifest = packet.read(measured / "calibration_manifest.json")
    key = manifest["plan"]["cells"][0]["key"]
    intent = packet.read(measured / f"attempts/{key}.json")
    authority = {
        "protocol_fingerprint": manifest["protocol_fingerprint"],
        "intent_evaluation_id": "fake",
    }
    with pytest.raises(ValueError, match="intent differs"):
        execution._validate_worker_intent(measured, key, authority)
    authority["intent_evaluation_id"] = intent["evaluation_id"]
    with pytest.raises(ValueError, match="outcome already recorded"):
        execution._validate_worker_intent(measured, key, authority)


def test_worker_authority_is_bound_in_receipts(clone):
    path = sorted((clone / "worker_results").glob("*.json"))[0]
    change(
        path,
        lambda value: value["worker_authority"].update(protocol_fingerprint="f" * 64),
    )
    assert rejected(clone)["checks"]["C6_enforced_resources"] is False


@pytest.mark.parametrize(
    "field,value",
    [("qualified", True), ("live_authorized", True), ("error", "unhandled failure")],
)
def test_terminal_cannot_claim_authority_outside_validation(clone, field, value):
    change(clone / packet.TERMINAL, lambda data: data.update({field: value}))
    assert rejected(clone)["checks"]["terminal_integrity_seal"] is False


@pytest.mark.parametrize(
    "by,at,valid",
    [
        ("operator", "2026-10-13T12:00:00Z", True),
        ("operator", "2026-10-15T12:00:00Z", False),
        ("operator", "2026-10-13T12:00:00", False),
        ("  ", "2026-10-13T12:00:00Z", False),
        (1, "2026-10-13T12:00:00Z", False),
        ("operator", 12, False),
    ],
)
def test_shared_approval_scope_and_time_check(by, at, valid):
    body = {"approved_by": by, "approved_at": at, "protocol_fingerprint": "a" * 64}
    approval = body | {"approval_sha256": packet.digest(body)}
    if valid:
        assert packet.validate_approval(
            approval, "a" * 64, not_after=execution.utc("2026-10-14")
        )
    else:
        with pytest.raises(ValueError):
            packet.validate_approval(
                approval, "a" * 64, not_after=execution.utc("2026-10-14")
            )


def test_cpu_watchdog_retains_reaped_child_usage(tmp_path):
    burn = "import time; t=time.process_time();\nwhile time.process_time()-t<0.12: pass"
    code = f"import subprocess,sys,time\nfor _ in range(5): subprocess.run([sys.executable,'-c',{burn!r}],check=True)\ntime.sleep(30)"
    receipt = execution.supervise(
        [sys.executable, "-c", code],
        budget=packet.default_budget()
        | {"cell_wall_seconds": 3, "cell_cpu_seconds": 0.35},
        log_path=tmp_path / "reaped.log",
        remaining_wall=3,
        remaining_cpu=0.35,
    )
    assert receipt["limit_exceeded"] == "cpu"
    assert receipt["sampled_cpu_seconds"] > 0.35
    assert not receipt["active_descendants"]


def test_waited_children_cpu_not_double_counted(tmp_path):
    burn = "import time; t=time.process_time();\nwhile time.process_time()-t<0.12: pass"
    code = f"import subprocess,sys,time; subprocess.run([sys.executable,'-c',{burn!r}],check=True); time.sleep(0.2)"
    receipt = execution.supervise(
        [sys.executable, "-c", code],
        budget=packet.default_budget()
        | {"cell_wall_seconds": 3, "cell_cpu_seconds": 0.35},
        log_path=tmp_path / "single.log",
        remaining_wall=3,
        remaining_cpu=0.35,
    )
    assert receipt["returncode"] == 0 and receipt["limit_exceeded"] is None
    assert 0.1 <= receipt["sampled_cpu_seconds"] < 0.35


def test_nullable_unrelated_legacy_metadata_does_not_crash_scope_filter():
    snapshot = {
        "evaluation_records": [
            {
                "experiment_id": "legacy",
                "metadata_json": {
                    "evaluation_execution_identity": None,
                    "evaluation_identity": None,
                },
            }
        ],
        "experiment_specs": [],
        "experiment_aliases": [],
    }
    assert packet.scoped_snapshot(snapshot, "a" * 64)["evaluation_records"] == []


def test_older_worker_contract_requires_refreeze(measured):
    import copy

    plan = copy.deepcopy(packet.read(measured / "calibration_manifest.json")["plan"])
    del plan["worker_authority_contract"]
    with pytest.raises(ValueError, match="refreeze"):
        packet.validate_plan(plan, measured)


# --- Strategy validation regression tests (F001/F002) ---

def test_expected_strategies_none_returns_canonical_defaults():
    """None expected_strategies falls back to VALID_STRATEGIES."""
    result = packet._validate_expected_strategies(None)
    assert result == packet.VALID_STRATEGIES
    assert len(result) == 5


def test_expected_strategies_rejects_empty_list():
    """Empty list must raise; cannot silently fall back to all strategies."""
    with pytest.raises(ValueError, match="non-empty"):
        packet._validate_expected_strategies([])


def test_expected_strategies_rejects_duplicates():
    """Duplicate strategy names must be rejected."""
    with pytest.raises(ValueError, match="duplicate"):
        packet._validate_expected_strategies(["ma_adx", "ma_adx"])


def test_expected_strategies_rejects_invalid_name():
    """Invalid strategy names must be rejected."""
    with pytest.raises(ValueError, match="invalid strategy"):
        packet._validate_expected_strategies(["bogus"])


def test_expected_strategies_accepts_single_strategy():
    """Pilot single-strategy must work."""
    result = packet._validate_expected_strategies(["ma_vol_target"])
    assert result == ("ma_vol_target",)


def test_expected_strategies_accepts_and_sorts_subset():
    """Subset accepted, sorted for deterministic comparison."""
    result = packet._validate_expected_strategies(["ma_adx", "rsi"])
    assert result == ("ma_adx", "rsi")


def test_verify_packet_accepts_valid_synthetic(measured):
    """_validate_expected_strategies is the single source of truth for strategy
    name/dedup checks; verifier uses it for both REAL_MARKET and synthetic.

    The measured synthetic fixture must be ACCEPTED — verifier rejecting a valid
    packet would be a critical regression."""
    result = packet.verify_packet(measured)
    assert result["status"] == "ACCEPTED_CALIBRATION", result
    assert result["qualified"] is False  # calibration != qualification
    assert result["live_authorized"] is False


def test_freeze_plan_synthetic_default_produces_two_cells():
    """Producer + verifier must agree: synthetic mode -> 1 strategy (rsi) -> 2 cells (early/late).

    Synthetic mode always uses rsi (active_strategies = (\"rsi\",)); the strategies
    parameter is for REAL_MARKET only. This test locks the producer's
    expected_strategies and cell count so the verifier cannot disagree.
    """
    from trading_agent.backtest.synthetic_data import generate_synthetic_ohlcv
    frame = generate_synthetic_ohlcv(n_bars=800, seed=7)
    with tempfile.NamedTemporaryFile(suffix=".parquet") as f:
        frame.write_parquet(f.name)
        out = f.name + "_out"
        result = execution.freeze_plan(out, f.name, synthetic=True)
        assert len(result["cells"]) == 2
        assert result["expected_strategies"] == ["rsi"]
        result_exec = execution.execute(out)
        assert result_exec["status"] == "ACCEPTED_CALIBRATION"


def test_freeze_plan_synthetic_strategy_param_ignored():
    """Synthetic mode ignores user-supplied strategies: active_strategies is
    always (\"rsi\",) regardless of the strategies kwarg. This prevents
    accidental scope expansion when a caller passes strategies to a
    synthetic test."""
    from trading_agent.backtest.synthetic_data import generate_synthetic_ohlcv
    frame = generate_synthetic_ohlcv(n_bars=800, seed=7)
    with tempfile.NamedTemporaryFile(suffix=".parquet") as f:
        frame.write_parquet(f.name)
        result = execution.freeze_plan(
            f.name + "_out", f.name, synthetic=True, strategies=["ma_adx"]
        )
        assert result["expected_strategies"] == ["rsi"]  # synthetic ignores strategies


@pytest.mark.parametrize("strategies, message", [
    ([], "non-empty"),
    (["ma_adx", "ma_adx"], "duplicate"),
    (["bogus"], "invalid strategy"),
    ("ma_adx", "non-empty"),
    (42, "non-empty"),
])
def test_freeze_plan_empty_strategies_rejected(tmp_path, monkeypatch, strategies, message):
    """API: freeze_plan(strategies=[]) in REAL_MARKET mode must raise
    ValueError, not silently fall back to all 5 strategies (scope expansion bug).

    Calls freeze_plan() with REAL_MARKET (synthetic=False) + strategies=[] and
    verifies ValueError is raised before any data loading or output directory creation.
    """
    import polars as pl

    def forbidden(*args, **kwargs):
        pytest.fail("invalid strategy request reached data or registry access")

    monkeypatch.setattr(pl, "read_parquet", forbidden)
    monkeypatch.setattr(execution, "registry_snapshot", forbidden)
    out = tmp_path / "not-created"
    with pytest.raises(ValueError, match=message):
        execution.freeze_plan(out, tmp_path / "missing.parquet", synthetic=False, strategies=strategies)
    assert not out.exists()
