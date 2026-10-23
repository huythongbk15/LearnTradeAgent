"""Invariant tests for the evaluation ledger and the preregistration binding.

These are the fast, synthetic checks the review asked for: the schema and
ledger defects are caught by unit tests over the **production functions**, not
by re-running the 220-290 s launcher integration.

Nothing here reads market data or starts a campaign.
"""
from __future__ import annotations

import json
import copy
import os
import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, ".")

from trading_agent.backtest import attempt_ledger as ledger
from trading_agent.backtest import preregistration_binding as binding
from trading_agent.backtest.preregistration_binding import (
    load_approved_payload,
    normalise_singleton_grid,
    preregistration_fingerprint,
    scoped_token_map_is_consistent,
    verify_registered_payload_binding,
)

ROOT = Path(".").resolve()
SCOPE_TOKEN = "bnb_di_adx_stateedge_prereg_20261020_01"
PREREG_REL = "data/bnb_di_adx_stateedge_prereg_20261020_01"
PAYLOAD_REL = "data/bnb_di_adx_stateedge_20261020_01"


# ── helpers ────────────────────────────────────────────────────────────────
def _payload() -> dict:
    return json.loads((ROOT / PREREG_REL / "preregistration_payload.json").read_text())


def _protocol(payload: dict) -> dict:
    """A protocol that agrees with the approved payload on every checked field."""
    candidate = payload["candidate"]
    grid = {axis: [value] for axis, value in candidate["params"].items()}
    costs = [
        {
            "name": name,
            "fee_multiplier": value["fee_multiplier"],
            "slippage_multiplier": value["slippage_multiplier"],
        }
        for name, value in payload["costs"].items()
    ]
    windows = payload["geometry"]["inner_validation_windows"]
    budget = payload["budget"]
    from trading_agent.backtest.campaign_evidence import content_hash

    bodies = payload["execution_contract"]["native_contract_bodies_by_cost"]
    return {
        "native_execution_contracts": {
            name: {"contract_id": content_hash(body), "body": copy.deepcopy(body)}
            for name, body in bodies.items()
        },
        "input_binding": {"sha256": payload["data"]["sha256"]},
        "spec": {
            "param_grid": grid,
            "cost_scenarios": costs,
            # the SUBJECT travels in the protocol spec; without it the protocol
            # is not a scoped study and the binding does not apply
            "strategy_id": candidate["strategy_id"],
            "symbol": candidate.get("symbol"),
            "timeframe": candidate.get("timeframe"),
            "evidence_class": "SYNTHETIC_TEST_ONLY",
        },
        "folds": [
            {"inner_val_start": start, "inner_val_end": end} for start, end in windows
        ],
        "execution": {
            "scope_token": payload["scope_token_binding"]["token"],
            "cell_timeout": budget["cell_timeout_seconds"],
            "max_retries": budget["max_retries"],
            "runtime_config": copy.deepcopy(payload["execution_contract"]["runtime_config"]),
        },
        "validation_budget": {
            "total_timeout_seconds": budget["total_timeout_seconds"],
            "max_rss_mb": budget["max_rss_mb"],
        },
    }


def _approved_payload_copy(tmp_path: Path) -> tuple[Path, dict]:
    """A copy of the preregistration packet that the gate can read and mutate."""
    destination = tmp_path / PREREG_REL
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(ROOT / PREREG_REL, destination)
    return tmp_path, json.loads((destination / "preregistration_payload.json").read_text())


def _write_manifest(root: Path, payload: dict) -> None:
    manifest = json.loads((root / PREREG_REL / "manifest.json").read_text())
    manifest["identity_B_preregistration_fingerprint"] = preregistration_fingerprint(payload)
    manifest["identity_A_source_fingerprint"] = payload["identity_A_source_fingerprint"]
    (root / PREREG_REL / "manifest.json").write_text(json.dumps(manifest, indent=2))


def _approve(root: Path, payload: dict) -> None:
    request = payload["data"]["scope_change_request"]
    request["approval_state"] = "APPROVED"
    request["approved_scope"] = request["expected_approved_scope"]
    request["approved_by"] = "synthetic-approver"
    request["approved_at"] = "2026-10-21T00:00:00+00:00"
    (root / PREREG_REL / "preregistration_payload.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True)
    )
    _write_manifest(root, payload)


# ── ledger invariants ──────────────────────────────────────────────────────
def _attempt(tmp_path: Path, name: str, epoch: float, status: str, pid: int = -1) -> Path:
    directory = tmp_path / "cell_execution" / name
    directory.mkdir(parents=True)
    (directory / "started.json").write_text(json.dumps({
        "started_at_epoch": epoch, "worker_pid": pid, "cell_id": "cell-x",
        "cost_scenario": "1x",
    }))
    (directory / "result.json").write_text(json.dumps({"artifact": {"status": status}}))
    return directory


def test_ledger_publish_is_idempotent_and_registry_stays_unknown(tmp_path):
    """A repeated publication (fresh and cache hit) must not duplicate."""
    attempt = _attempt(tmp_path, "a", 1, "COMPLETED")
    target = tmp_path / "cell_ledger"
    for cache_hit in (False, True, True):
        path = ledger.write_ledger_row(
            target, "key", {}, [attempt, attempt], extra={"cache_hit": cache_hit}
        )
        row = json.loads(path.read_text())
        assert row["attempts"] == 1
        assert row["attempt_outcomes"] == ["COMPLETED"]
        assert row["registry_counted"] is None
        assert row["registry_accounting_status"] == "UNKNOWN_NOT_RECONCILED"


def test_ledger_new_attempt_updates_snapshot_and_picks_newest_outcome(tmp_path):
    old = _attempt(tmp_path, "z-old", 1, "FAILED")
    new = _attempt(tmp_path, "a-new", 2, "COMPLETED")
    target = tmp_path / "cell_ledger"
    ledger.write_ledger_row(target, "key", {}, [old])
    row = json.loads(ledger.write_ledger_row(target, "key", {}, [old, new]).read_text())
    assert row["attempts"] == 2
    assert sorted(row["attempt_outcomes"]) == ["COMPLETED", "FAILED"]
    assert row["outcome"] == "COMPLETED", "the newest attempt must decide the outcome"


def test_ledger_does_not_join_two_evaluations_with_different_cache_keys(tmp_path):
    """Two evaluations must never be merged into one row."""
    from trading_agent.backtest.campaign_evidence import content_hash

    root = tmp_path / "packet"
    attempts = root / "cell_attempts"
    for index, identity in enumerate((
        {"version": 2, "cell_id": "cell-a", "cost": "1x"},
        {"version": 2, "cell_id": "cell-b", "cost": "1x"},
    )):
        attempt_root = attempts / f"uuid{index}"
        (attempt_root / "cell_execution" / "u").mkdir(parents=True)
        (attempt_root / "execution_started.json").write_text(
            json.dumps({"identity": identity})
        )
    key_a = content_hash({"version": 2, "cell_id": "cell-a", "cost": "1x"})
    key_b = content_hash({"version": 2, "cell_id": "cell-b", "cost": "1x"})
    assert key_a != key_b
    matched_a = ledger.attempt_roots_for_evaluation(root, key_a)
    matched_b = ledger.attempt_roots_for_evaluation(root, key_b)
    assert len(matched_a) == 1 and len(matched_b) == 1
    assert matched_a[0] != matched_b[0]
    assert ledger.evaluation_attempt_dirs(root, key_a) != \
        ledger.evaluation_attempt_dirs(root, key_b)


def test_running_and_interrupted_are_distinguished(tmp_path):
    """A missing result is not proof that the process died."""
    alive = tmp_path / "alive"
    alive.mkdir()
    (alive / "started.json").write_text(json.dumps({
        "started_at_epoch": 1, "worker_pid": os.getpid(),
    }))
    dead = tmp_path / "dead"
    dead.mkdir()
    (dead / "started.json").write_text(json.dumps({
        "started_at_epoch": 1, "worker_pid": 999999,
    }))
    assert ledger.classify_attempt(alive) == ledger.RUNNING
    assert ledger.classify_attempt(dead) == ledger.INTERRUPTED
    # and a live attempt is never reported as interrupted
    assert ledger.classify_attempt(alive) != ledger.INTERRUPTED


def test_ledger_write_failure_keeps_previous_row(tmp_path, monkeypatch):
    """A failed atomic replace must preserve the previous row."""
    attempt = _attempt(tmp_path, "a", 1, "COMPLETED")
    target = tmp_path / "cell_ledger"
    path = ledger.write_ledger_row(target, "key", {}, [attempt])
    before = path.read_bytes()

    def fail_replace(*args):
        raise OSError("synthetic publication failure")

    monkeypatch.setattr(ledger.os, "replace", fail_replace)
    with pytest.raises(OSError):
        ledger.write_ledger_row(target, "key", {}, [attempt], extra={"broken": True})
    assert path.read_bytes() == before, "a failed publication must keep the old row"
    assert not list(target.glob("*.pending")), "the temporary file must be cleaned up"


def test_ledger_failure_is_recorded_durably_and_by_the_runner(tmp_path):
    """A ledger failure must not disappear: it is appended to an append-only log.

    ``write_ledger_row`` raises; the RUNNER is responsible for catching it and
    recording it, so both halves are checked: the log writer itself, and the
    runner's call site.
    """
    target = tmp_path / "cell_ledger"
    path = ledger.record_ledger_error(target, "key", OSError("synthetic"))
    assert path.exists()
    entry = json.loads(path.read_text().strip().splitlines()[-1])
    assert entry["evaluation_key"] == "key"
    assert "synthetic" in entry["error"]

    runner_source = (ROOT / "src" / "trading_agent" / "backtest" / "campaign_execution.py").read_text()
    assert "record_ledger_error" in runner_source, (
        "the runner must record a ledger failure instead of only printing it"
    )


def test_ledger_is_not_consulted_to_certify_completion():
    """The ledger is an index; the durable receipts and the coverage decide."""
    launcher = (ROOT / "scripts" / "run_wfo_parallel.py").read_text()
    assert "cell_ledger" not in launcher, (
        "the launcher must not read the evaluation ledger to decide completion"
    )


# ── preregistration binding invariants ─────────────────────────────────────
def test_singleton_grid_is_normalised_and_non_singletons_rejected():
    assert normalise_singleton_grid({"a": [1], "b": [2.0]}) == {"a": 1, "b": 2.0}
    assert normalise_singleton_grid({"a": [1, 2]}) is None
    assert normalise_singleton_grid({"a": []}) is None
    assert normalise_singleton_grid({}) is None
    assert normalise_singleton_grid(None) is None


def test_binding_accepts_the_registered_grid_and_rejects_a_changed_one(tmp_path):
    root, payload = _approved_payload_copy(tmp_path)
    _approve(root, payload)
    protocol = _protocol(payload)
    source = payload["identity_A_source_fingerprint"]
    assert verify_registered_payload_binding(
        protocol, SCOPE_TOKEN, root=root, source_fingerprint=source
    ) == []
    mutated = _protocol(payload)
    mutated["spec"]["param_grid"]["adx_threshold"] = [20.0]
    failed = verify_registered_payload_binding(
        mutated, SCOPE_TOKEN, root=root, source_fingerprint=source
    )
    assert failed == ["params"]


def test_binding_rejects_a_contract_body_swapped_between_costs(tmp_path):
    """The native contract of a cell must be the approved body of ITS cost.

    Membership in the set of approved bodies is not enough: a protocol that
    hands the 1x cells a 2x body (or the other way round) would pass such a
    check, and the cost gate would no longer describe the run.
    """
    root, payload = _approved_payload_copy(tmp_path)
    _approve(root, payload)
    source = payload["identity_A_source_fingerprint"]
    protocol = _protocol(payload)
    contracts = protocol["native_execution_contracts"]
    names = sorted(contracts)
    assert len(names) >= 2, "the fixture must have at least two cost scenarios"
    first, second = names[0], names[1]
    contracts[first]["body"], contracts[second]["body"] = (
        copy.deepcopy(contracts[second]["body"]),
        copy.deepcopy(contracts[first]["body"]),
    )
    for key in (first, second):
        from trading_agent.backtest.campaign_evidence import content_hash
        contracts[key]["contract_id"] = content_hash(contracts[key]["body"])
    failed = verify_registered_payload_binding(
        protocol, SCOPE_TOKEN, root=root, source_fingerprint=source
    )
    assert failed == ["native_execution_contracts"], failed


def test_scope_token_map_must_agree_with_the_packet_it_names(tmp_path, monkeypatch):
    """The token map and the preregistration map must agree about the world.

    Comparing the token map with itself proves nothing. The check has to
    compare the candidate the token unlocks with the candidate the named
    preregistration packet actually registers.
    """
    assert scoped_token_map_is_consistent() is True

    # a packet that registers a different candidate is a disagreement
    packet = tmp_path / "other_packet"
    packet.mkdir(parents=True)
    payload = _payload()
    payload["candidate"]["strategy_id"] = "some_other_candidate"
    (packet / "preregistration_payload.json").write_text(json.dumps(payload))
    monkeypatch.setitem(
        binding.SCOPED_CANDIDATE_PREREGISTRATION_PATHS, SCOPE_TOKEN, str(packet)
    )
    assert scoped_token_map_is_consistent() is False

    # an unreadable packet is a disagreement too (fail closed)
    monkeypatch.setitem(
        binding.SCOPED_CANDIDATE_PREREGISTRATION_PATHS, SCOPE_TOKEN,
        str(tmp_path / "does_not_exist"),
    )
    assert scoped_token_map_is_consistent() is False


def test_binding_rejects_every_mutated_field(tmp_path):
    root, payload = _approved_payload_copy(tmp_path)
    _approve(root, payload)
    source = payload["identity_A_source_fingerprint"]
    base = _protocol(payload)

    mutations = {
        "input_sha256": lambda p: p["input_binding"].update({"sha256": "0" * 64}),
        "windows": lambda p: p["folds"].__setitem__(
            0, {"inner_val_start": 1, "inner_val_end": 2}),
        "costs": lambda p: p["spec"]["cost_scenarios"][0].update({"fee_multiplier": 9.0}),
        "cell_timeout": lambda p: p["execution"].update({"cell_timeout": 1}),
        "total_timeout": lambda p: p["validation_budget"].update({"total_timeout_seconds": 1}),
        "max_rss_mb": lambda p: p["validation_budget"].update({"max_rss_mb": 1}),
        "max_retries": lambda p: p["execution"].update({"max_retries": 3}),
        "scope_token": lambda p: p["execution"].update({"scope_token": "other"}),
    }
    for field, mutate in mutations.items():
        protocol = _protocol(payload)
        mutate(protocol)
        failed = verify_registered_payload_binding(
            protocol, SCOPE_TOKEN, root=root, source_fingerprint=source
        )
        assert field in failed, f"{field} was not detected (failed={failed})"

    # the source identity is checked too
    failed = verify_registered_payload_binding(
        base, SCOPE_TOKEN, root=root, source_fingerprint="f" * 64
    )
    assert "identity_A_source" in failed


def test_pending_approval_cannot_start_a_run(tmp_path):
    root, payload = _approved_payload_copy(tmp_path)
    _write_manifest(root, payload)
    protocol = _protocol(payload)
    failed = verify_registered_payload_binding(
        protocol, SCOPE_TOKEN, root=root,
        source_fingerprint=payload["identity_A_source_fingerprint"],
    )
    assert "approval_state" in failed
    assert "approved_by" in failed
    assert "approved_at" in failed
    assert "approved_scope" in failed


def test_approval_requires_scope_approver_and_timestamp(tmp_path):
    root, payload = _approved_payload_copy(tmp_path)
    _approve(root, payload)
    source = payload["identity_A_source_fingerprint"]
    for field, bad in (
        ("approved_scope", "something else"),
        ("approved_by", ""),
        ("approved_at", ""),
    ):
        broken = json.loads(json.dumps(payload))
        broken["data"]["scope_change_request"][field] = bad
        (root / PREREG_REL / "preregistration_payload.json").write_text(
            json.dumps(broken, indent=2, sort_keys=True)
        )
        _write_manifest(root, broken)
        failed = verify_registered_payload_binding(
            _protocol(broken), SCOPE_TOKEN, root=root, source_fingerprint=source
        )
        assert field in failed, f"{field} was not checked (failed={failed})"


def test_there_is_only_one_authoritative_approval_state():
    request = _payload()["data"]["scope_change_request"]
    assert "status" not in request, (
        "a second status field would make the approval ambiguous"
    )
    assert request["approval_state"] in {"PENDING_OPERATOR_APPROVAL", "APPROVED"}


def test_payload_edited_after_approval_is_refused(tmp_path):
    root, payload = _approved_payload_copy(tmp_path)
    _approve(root, payload)
    # edit the payload without refreshing the manifest's identity B
    payload["data"]["scope_change_request"]["approved_by"] = "someone-else"
    (root / PREREG_REL / "preregistration_payload.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True)
    )
    with pytest.raises(ValueError, match="identity B"):
        load_approved_payload(SCOPE_TOKEN, root)


def test_binding_is_a_no_op_for_a_non_scoped_study():
    """The frozen control and every ordinary study must run unchanged.

    The binding applies only to a scoped candidate; for anything else it is a
    no-op, so the shared validation launcher is not blocked by it.
    """
    payload = _payload()
    protocol = _protocol(payload)
    protocol["spec"]["strategy_id"] = "ma_adx"
    protocol["spec"]["symbol"] = "BNB/USDT"
    protocol["spec"]["timeframe"] = "4h"
    protocol["input_binding"]["sha256"] = "0" * 64
    protocol["validation_budget"]["total_timeout_seconds"] = 1
    assert verify_registered_payload_binding(
        protocol, None, root=ROOT,
        source_fingerprint="f" * 64,
    ) == []


def test_binding_gate_is_reached_from_launcher_and_worker():
    launcher = (ROOT / "scripts" / "run_wfo_parallel.py").read_text()
    assert launcher.count("_verify_approved_payload_binding(") >= 2, (
        "the gate must be reached from the launcher AND the worker"
    )


def test_real_market_cannot_use_an_approved_copy_of_pending_canonical_packet(tmp_path):
    root, payload = _approved_payload_copy(tmp_path)
    _approve(root, payload)
    protocol = _protocol(payload)
    protocol["spec"]["evidence_class"] = "REAL_MARKET"
    assert verify_registered_payload_binding(
        protocol, SCOPE_TOKEN, root=root,
        source_fingerprint=payload["identity_A_source_fingerprint"],
    ) == ["preregistration_root"]
    assert _payload()["data"]["scope_change_request"]["approval_state"] == "PENDING_OPERATOR_APPROVAL"


@pytest.mark.parametrize("setting,value", [
    ("STOP_LOSS_PCT", 0.10),
    ("MAX_POS_SIZE_PCT", 0.30),
    ("MAX_POSITION_PCT", 0.60),
    ("MAX_DRAWDOWN_PCT", 0.20),
    ("DAILY_LOSS_LIMIT_PCT", 0.10),
    ("COOLDOWN_HOURS", 12),
    ("INITIAL_CAPITAL", 20000),
])
def test_runtime_refrozen_after_environment_change_is_rejected(tmp_path, monkeypatch, setting, value):
    """Exercise the production runtime capture on changed loaded env constants."""
    from scripts import full_system_backtest as simulator
    from trading_agent.backtest.campaign_execution import simulator_runtime_config

    root, payload = _approved_payload_copy(tmp_path)
    _approve(root, payload)
    protocol = _protocol(payload)
    monkeypatch.setattr(simulator, setting, value)
    protocol["execution"]["runtime_config"] = simulator_runtime_config()
    failed = verify_registered_payload_binding(
        protocol, SCOPE_TOKEN, root=root,
        source_fingerprint=payload["identity_A_source_fingerprint"],
    )
    assert "runtime_config" in failed


def test_validly_rehashed_native_contract_cannot_change_authority(tmp_path):
    from trading_agent.backtest.campaign_evidence import content_hash

    root, payload = _approved_payload_copy(tmp_path)
    _approve(root, payload)
    protocol = _protocol(payload)
    contract = next(iter(protocol["native_execution_contracts"].values()))
    contract["body"]["authority"]["exposure"]["max_single_symbol_exposure"] = 0.50
    contract["contract_id"] = content_hash(contract["body"])
    assert "native_execution_contracts" in verify_registered_payload_binding(
        protocol, SCOPE_TOKEN, root=root,
        source_fingerprint=payload["identity_A_source_fingerprint"],
    )


def test_launcher_and_worker_reject_real_market_root_override(tmp_path, monkeypatch):
    from scripts import run_wfo_parallel as worker

    root, payload = _approved_payload_copy(tmp_path)
    _approve(root, payload)
    protocol = _protocol(payload)
    protocol["spec"]["evidence_class"] = "REAL_MARKET"
    protocol["execution"]["preregistration_root"] = str(root)
    protocol["execution"]["preregistration_root_canonical"] = False
    monkeypatch.setenv("TRADING_PREREGISTRATION_ROOT", str(root))
    with pytest.raises(ValueError, match="preregistration_root"):
        worker._verify_approved_payload_binding(protocol, SCOPE_TOKEN)
    monkeypatch.setattr(sys, "argv", [
        "run_wfo_parallel.py", "--scope-token", SCOPE_TOKEN,
        "--strategy", "di_adx_state_edge", "--out", str(tmp_path / "forbidden"),
    ])
    with pytest.raises(SystemExit):
        worker.main()
    assert not (tmp_path / "forbidden").exists()


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
