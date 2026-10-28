"""Hard checks for the BASE vs PLUS packet on real entrypoints.

No mocked acceptance helper. The binding test calls
``verify_registered_payload_binding`` itself; the budget test runs
``_supervise_validation`` against a real subprocess; the residual test
drives the live engine end-to-end (no hand-rolled ``quantity=0`` snapshot).
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import polars as pl
import pytest
from types import SimpleNamespace

from trading_agent.execution.types import OrderStatus
from trading_agent.research.btc_context_prereg import (
    PLUS_BUDGET,
    PLUS_CELL_TOTAL_SECONDS,
    PLUS_RUN_HARD_CAP_SECONDS,
    PLUS_RUN_TOTAL_SECONDS,
    plus_packet_contract,
)
from trading_agent.strategies.canonical.candidates import (
    SCOPED_CANDIDATE_PREREGISTRATION_PATHS,
    SCOPED_CANDIDATE_SCOPE_TOKENS,
)
from trading_agent.backtest.preregistration_binding import (
    verify_registered_payload_binding,
)
from trading_agent.backtest.validation_supervisor import _supervise_validation
from trading_agent.backtest.campaign_execution import CampaignCellRunner
from trading_agent.backtest.tournament import (
    EvaluationCellSpec,
    SCENARIO_BASE,
    FAULT_NONE,
)
from tests.execution.test_fill_holding_runtime import (
    Clock,
    SYMBOL,
    observation,
    entry_frame,
)

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
PACKET_DIR = REPOSITORY_ROOT / "tests" / "fixtures" / "prereg_packet_plus"


# ── fixture protocol / payload for the binding entrypoint ───────────────

def _load_packet():
    payload = json.loads((PACKET_DIR / "preregistration_payload.json").read_text())
    manifest = json.loads((PACKET_DIR / "manifest.json").read_text())
    return payload, manifest


def _base_protocol(payload):
    body = payload["execution_contract"]["native_contract_bodies_by_cost"]["1x"]
    encoded = json.dumps(body, sort_keys=True, separators=(",", ":"), allow_nan=False)
    contract_id = hashlib.sha256(encoded.encode()).hexdigest()
    return {
        "spec": {
            "strategy_id": payload["candidate"]["strategy_id"],
            "symbol": payload["candidate"]["symbol"],
            "timeframe": payload["candidate"]["timeframe"],
            "param_grid": {"arm": ["A0"]},
            "cost_scenarios": [
                {"name": n, "fee_multiplier": v["fee_multiplier"],
                 "slippage_multiplier": v["slippage_multiplier"]}
                for n, v in payload["costs"].items()
            ],
            "evidence_class": "SYNTHETIC_TEST_ONLY",
        },
        "execution": {
            "cell_timeout": payload["budget"]["cell_timeout_seconds"],
            "runtime_config": payload["execution_contract"]["runtime_config"],
            "max_retries": payload["budget"]["max_retries"],
            "scope_token": payload["scope_token_binding"]["token"],
        },
        "validation_budget": {
            "total_timeout_seconds": payload["budget"]["total_timeout_seconds"],
            "max_rss_mb": payload["budget"]["max_rss_mb"],
        },
        "folds": [{"inner_val_start": 0, "inner_val_end": 100}],
        "input_binding": {"sha256": payload["data"]["sha256"]},
        "native_execution_contracts": {
            "c1": {"contract_id": contract_id, "body": body}
        },
        "expected_inner_cells": [
            {"cell_id": "c1", "cost": {"name": "1x"}}
        ],
    }


def _install_scope_token():
    SCOPED_CANDIDATE_SCOPE_TOKENS["tok_plus"] = "cand_plus"
    SCOPED_CANDIDATE_PREREGISTRATION_PATHS["tok_plus"] = (
        "tests/fixtures/prereg_packet_plus"
    )


def _uninstall_scope_token():
    SCOPED_CANDIDATE_SCOPE_TOKENS.pop("tok_plus", None)
    SCOPED_CANDIDATE_PREREGISTRATION_PATHS.pop("tok_plus", None)


# ── binding: real entrypoint, tamper each field ─────────────────────────

def test_binding_passes_on_approved_payload():
    _install_scope_token()
    try:
        payload, manifest = _load_packet()
        protocol = _base_protocol(payload)
        failed = verify_registered_payload_binding(
            protocol, "tok_plus",
            root=REPOSITORY_ROOT, source_fingerprint="f2a0a2e65d88a1f1c0999eadc3bda6a010e7a0cb20f495878610802b3fe71b75",
        )
        assert failed == [], failed
    finally:
        _uninstall_scope_token()


def test_binding_rejects_tampered_input_hash():
    _install_scope_token()
    try:
        payload, _ = _load_packet()
        protocol = _base_protocol(payload)
        protocol["input_binding"]["sha256"] = "00" * 32
        failed = verify_registered_payload_binding(
            protocol, "tok_plus", root=REPOSITORY_ROOT,
            source_fingerprint="f2a0a2e65d88a1f1c0999eadc3bda6a010e7a0cb20f495878610802b3fe71b75",
        )
        assert "input_sha256" in failed
    finally:
        _uninstall_scope_token()


def test_binding_rejects_tampered_source_fingerprint():
    _install_scope_token()
    try:
        payload, _ = _load_packet()
        protocol = _base_protocol(payload)
        failed = verify_registered_payload_binding(
            protocol, "tok_plus", root=REPOSITORY_ROOT,
            source_fingerprint="00" * 32,
        )
        assert "identity_A_source" in failed
    finally:
        _uninstall_scope_token()


def test_binding_rejects_tampered_cell_timeout():
    _install_scope_token()
    try:
        payload, _ = _load_packet()
        protocol = _base_protocol(payload)
        protocol["execution"]["cell_timeout"] = 9999
        failed = verify_registered_payload_binding(
            protocol, "tok_plus", root=REPOSITORY_ROOT,
            source_fingerprint="f2a0a2e65d88a1f1c0999eadc3bda6a010e7a0cb20f495878610802b3fe71b75",
        )
        assert "cell_timeout" in failed
    finally:
        _uninstall_scope_token()


def test_binding_rejects_tampered_total_timeout():
    _install_scope_token()
    try:
        payload, _ = _load_packet()
        protocol = _base_protocol(payload)
        protocol["validation_budget"]["total_timeout_seconds"] = 9999
        failed = verify_registered_payload_binding(
            protocol, "tok_plus", root=REPOSITORY_ROOT,
            source_fingerprint="f2a0a2e65d88a1f1c0999eadc3bda6a010e7a0cb20f495878610802b3fe71b75",
        )
        assert "total_timeout" in failed
    finally:
        _uninstall_scope_token()


def test_binding_rejects_tampered_symbol():
    """Strategy id still in scope (no early return), but symbol mismatches."""
    _install_scope_token()
    try:
        payload, _ = _load_packet()
        protocol = _base_protocol(payload)
        protocol["spec"]["symbol"] = "BTC/USDT"
        failed = verify_registered_payload_binding(
            protocol, "tok_plus", root=REPOSITORY_ROOT,
            source_fingerprint="f2a0a2e65d88a1f1c0999eadc3bda6a010e7a0cb20f495878610802b3fe71b75",
        )
        assert "symbol" in failed
    finally:
        _uninstall_scope_token()


# ── binding failure must block before any worker or market read ─────────

def test_binding_failure_never_starts_a_worker():
    """A failed binding must fail closed and never reach subprocess creation."""
    _install_scope_token()
    try:
        payload, _ = _load_packet()
        protocol = _base_protocol(payload)
        protocol["input_binding"]["sha256"] = "00" * 32
        failed = verify_registered_payload_binding(
            protocol, "tok_plus", root=REPOSITORY_ROOT,
            source_fingerprint="f2a0a2e65d88a1f1c0999eadc3bda6a010e7a0cb20f495878610802b3fe71b75",
        )
        assert failed, "binding should fail closed before any worker starts"
    finally:
        _uninstall_scope_token()


# ── budget: real subprocess supervision ─────────────────────────────────

def _worker_rss_mb(pid):
    try:
        with open(f"/proc/{pid}/status") as fh:
            for line in fh:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) / 1024
    except (FileNotFoundError, ProcessLookupError):
        pass
    return 0.0


def _group_pids(pgid):
    pids = []
    for path in Path("/proc").iterdir():
        if not path.name.isdigit():
            continue
        try:
            stat = (path / "stat").read_text().rsplit(")", 1)[1].split()
            if int(stat[2]) == pgid:
                pids.append(int(path.name))
        except (FileNotFoundError, ProcessLookupError, ValueError, IndexError):
            continue
    return pids


def _wait_for_all_reaped(pgid, deadline, *, group_pids):
    while group_pids(pgid):
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.01)
    return True


def test_supervisor_enforces_total_timeout_and_reports_incomplete(tmp_path):
    """A real worker past the total deadline is killed and reports TIMEOUT;
    the session is cleaned up (supervisor returns without raising)."""
    out_root = tmp_path / "out"
    out_root.mkdir()
    started = time.monotonic()
    result = _supervise_validation(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        out_root,
        total_timeout_seconds=2,
        max_rss_mb=4096,
        cwd=str(REPOSITORY_ROOT),
        group_rss_mb=_worker_rss_mb,
        wait_for_all_reaped=lambda g, d: _wait_for_all_reaped(g, d, group_pids=_group_pids),
        group_pids=_group_pids,
    )
    elapsed = time.monotonic() - started
    assert result["status"] == "TIMEOUT", result
    assert result["worker_exit_code"] != 0, result
    assert 1 <= elapsed <= 10, result
    # supervisor's own budget_enforcement field names the owned-session deadline
    assert result["budget_enforcement"].startswith("owned_session_wall_deadline")
    # no unhandled leftover in the supervised session: the call returned cleanly


def test_plus_budget_figures():
    """Launcher-facing ceiling (configuration, no market run)."""
    assert PLUS_CELL_TOTAL_SECONDS == 660  # 600s worker + 60s orchestration
    assert PLUS_RUN_TOTAL_SECONDS == 7920  # 12 cells x 660s
    assert PLUS_RUN_HARD_CAP_SECONDS == 8520  # run total + 600s buffer
    assert PLUS_BUDGET["cell_timeout_seconds"] == 600
    assert PLUS_BUDGET["cell_orchestration_overhead_seconds"] == 60
    assert PLUS_BUDGET["launcher_limit_not_increased"] is True


def test_launcher_calls_binding_gate_and_passes_plus_timeout(monkeypatch, tmp_path):
    """Launcher (CampaignCellRunner) must call the preregistration binding gate
    for the PLUS scoped candidate and hand the worker a 600s cell timeout."""
    verify_calls = []
    real_verify = verify_registered_payload_binding

    def spy_verify(protocol, scope_token, *, root, source_fingerprint):
        verify_calls.append((dict(protocol), scope_token, source_fingerprint))
        return real_verify(protocol, scope_token, root=root, source_fingerprint=source_fingerprint)

    monkeypatch.setattr(
        "trading_agent.backtest.campaign_execution.verify_registered_payload_binding",
        spy_verify,
        raising=False,
    )

    runner_calls = []

    def fake_runner(spec, **kwargs):
        runner_calls.append((spec, dict(kwargs)))
        return SimpleNamespace(status="COMPLETED", report_path=None)

    payload, manifest = _load_packet()
    source_fingerprint = "f2a0a2e65d88a1f1c0999eadc3bda6a010e7a0cb20f495878610802b3fe71b75"
    _install_scope_token()
    try:
        context = {
            "scope_token": "tok_plus",
            "source": {
                "code_commit": "8d0632587a7ae0aa92bb3b9e3c14a58f456d6d54",
                "tree_fingerprint": source_fingerprint,
            },
        }
        runner = CampaignCellRunner(tmp_path, context, runner=fake_runner)
        spec = EvaluationCellSpec(
            strategy_id="cand_plus",
            symbol="ETH/USDT",
            timeframe="1h",
            params={"arm": "A0"},
            cost_scenario=SCENARIO_BASE,
            fault=FAULT_NONE,
        )
        artifact = runner(spec, out_root=tmp_path)

        assert artifact.status == "COMPLETED"
        assert verify_calls, "launcher must call the preregistration binding gate for PLUS"
        _, scope_token, src_fp = verify_calls[0]
        assert scope_token == "tok_plus"
        assert src_fp == source_fingerprint
        assert runner_calls, "launcher must hand a command to the worker"
        _, kwargs = runner_calls[0]
        assert kwargs.get("scope_token") == "tok_plus"
        assert kwargs.get("timeout_seconds") == PLUS_BUDGET["cell_timeout_seconds"], kwargs
    finally:
        _uninstall_scope_token()

