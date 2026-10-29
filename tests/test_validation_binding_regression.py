"""Directed regression on the newly added caller-controlled validation flag.
No market data/worker: reuse existing hard-check fixture and fake runner.
"""
import importlib.util
import pytest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location("existing_hard_check",ROOT/"tests/test_btc_plus_hard_checks.py")
hard=importlib.util.module_from_spec(spec)
spec.loader.exec_module(hard)


def test_validation_flag_cannot_skip_scoped_binding(monkeypatch,tmp_path):
    original=hard.CampaignCellRunner
    def caller_flag(root,context,**kwargs):
        return original(root,{**context,"validation_only":True},**kwargs)
    monkeypatch.setattr(hard,"CampaignCellRunner",caller_flag)
    hard.test_launcher_calls_binding_gate_and_passes_plus_timeout(monkeypatch,tmp_path)


def test_pending_approval_never_reaches_backend_even_with_validation_flag(monkeypatch,tmp_path):
    from types import SimpleNamespace
    hard._install_scope_token()
    try:
        payload,manifest,source=hard._bind_packet_dynamically()
        approval=payload["data"]["scope_change_request"]
        approval.update(approval_state="PENDING_OPERATOR_APPROVAL",approved_by=None,approved_at=None,approved_scope=None)
        manifest["identity_B_preregistration_fingerprint"]=hard.preregistration_fingerprint(payload)
        monkeypatch.setattr("trading_agent.backtest.preregistration_binding.load_approved_payload",lambda *a,**k:(payload,manifest))
        failed=hard.verify_registered_payload_binding(hard._base_protocol(payload),"tok_plus",root=hard.REPOSITORY_ROOT,source_fingerprint=source)
        assert "approval_state" in failed, failed
        calls=[]
        def backend(spec,**kwargs):
            calls.append(True)
            return SimpleNamespace(status="COMPLETED",report_path=None)
        runner=hard.CampaignCellRunner(tmp_path,{"scope_token":"tok_plus","validation_only":True,
            "source":{"tree_fingerprint":source}},runner=backend)
        spec=hard.EvaluationCellSpec(strategy_id="cand_plus",symbol="ETH/USDT",timeframe="1h",
            params={"arm":"A0"},cost_scenario=hard.SCENARIO_BASE,fault=hard.FAULT_NONE)
        try:
            runner(spec,out_root=tmp_path)
        except ValueError as exc:
            assert not calls
            assert "preregistration" in str(exc)
        else:
            assert not calls, "BACKEND_REACHED_WITH_PENDING_APPROVAL"
    finally:
        hard._uninstall_scope_token()


def qualification_fixture():
    from dataclasses import replace
    from scripts import run_wfo_parallel as worker
    spec=hard.EvaluationCellSpec(strategy_id="cand_plus",symbol="ETH/USDT",timeframe="1h",
        params={"arm":"A0"},cost_scenario=hard.SCENARIO_BASE,fault=hard.FAULT_NONE)
    key=replace(spec,expected_execution_contract_id=None).cell_id
    protocol={"spec":{"strategy_id":"cand_plus","symbol":"ETH/USDT","timeframe":"1h"},
        "execution":{"scope_token":"tok_plus"},"native_execution_contracts":{key:{}}}
    runner=object.__new__(worker.QualificationCellRunner)
    runner.protocol=protocol
    return worker,runner,spec,key


def test_qualification_admission_uses_actual_protocol(monkeypatch):
    worker,runner,spec,_=qualification_fixture()
    calls=[]
    monkeypatch.setattr(worker,"_verify_approved_payload_binding",lambda p,t:calls.append((p,t)))
    runner._verify_scoped_admission(spec,"tok_plus")
    assert calls==[(runner.protocol,"tok_plus")]


@pytest.mark.parametrize("mismatch",["token","cell"])
def test_qualification_subject_or_cell_cannot_reach_verifier(monkeypatch,mismatch):
    worker,runner,spec,key=qualification_fixture()
    monkeypatch.setattr(worker,"_verify_approved_payload_binding",lambda *a:pytest.fail("bad scope reached verifier"))
    token="wrong" if mismatch=="token" else "tok_plus"
    if mismatch=="cell":
        runner.protocol["native_execution_contracts"]={}
    with pytest.raises(ValueError,match="preregistration"):
        runner._verify_scoped_admission(spec,token)


def test_qualification_actual_pending_payload_is_rejected(monkeypatch):
    worker,runner,spec,key=qualification_fixture()
    hard._install_scope_token()
    try:
        payload,manifest,_=hard._bind_packet_dynamically()
        payload["data"]["scope_change_request"]["approval_state"]="PENDING_OPERATOR_APPROVAL"
        manifest["identity_B_preregistration_fingerprint"]=hard.preregistration_fingerprint(payload)
        protocol=hard._base_protocol(payload)
        contract=protocol["native_execution_contracts"]["c1"]
        protocol["native_execution_contracts"]={key:contract}
        protocol["expected_inner_cells"]=[{"cell_id":key,"cost":{"name":"1x"}}]
        runner.protocol=protocol
        monkeypatch.setattr("trading_agent.backtest.preregistration_binding.load_approved_payload",lambda *a,**k:(payload,manifest))
        with pytest.raises(ValueError,match="approval_state"):
            runner._verify_scoped_admission(spec,"tok_plus")
    finally:
        hard._uninstall_scope_token()
