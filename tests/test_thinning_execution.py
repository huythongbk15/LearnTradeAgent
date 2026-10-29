"""Actual development worker path on hash-bound synthetic tmp_path inputs."""
from dataclasses import replace
from datetime import UTC, datetime, timedelta
import hashlib
import json

import polars as pl
import pytest

from trading_agent.backtest import thinning_execution as lane
from trading_agent.execution.lifecycle.events import make_event


def reseal(path, plan):
    plan["fingerprint"] = lane.digest({k:v for k,v in plan.items() if k != "fingerprint"})
    path.write_text(json.dumps(plan))
    (path.parent / "binding.json").write_text(json.dumps({"protocol_sha256": hashlib.sha256(path.read_bytes()).hexdigest()}))


@pytest.fixture
def pending(tmp_path, monkeypatch):
    prep = lane.prep
    stamps = [datetime(2023,3,24,14,tzinfo=UTC) + timedelta(hours=k) for k in range(25198)]
    inputs = {}
    for pair in ("ETH/USDT", "BNB/USDT", "BTC/USDT"):
        step = 235 if pair == "BTC/USDT" else 230
        prices = [1000.0]*step + [1100.0]*(25198-step)
        path = tmp_path / (pair.replace("/", "_")+".parquet")
        pl.DataFrame({"timestamp":stamps,"open":prices,"high":prices,"low":prices,
                      "close":prices,"volume":[1.0]*25198}).write_parquet(path)
        inputs[pair] = {"path":path.name,"sha256":hashlib.sha256(path.read_bytes()).hexdigest()}
    (tmp_path / "design.md").write_bytes(b"synthetic-fixture-design")
    monkeypatch.setattr(prep,"ROOT",tmp_path)
    monkeypatch.setattr(prep,"INPUTS",inputs)
    monkeypatch.setattr(prep,"DESIGN","design.md")
    monkeypatch.setattr(prep,"DESIGN_SHA",hashlib.sha256(b"synthetic-fixture-design").hexdigest())
    monkeypatch.setattr(prep,"CALIBRATION",(216,240))
    monkeypatch.setattr(prep,"EVALUATION",(240,280))
    monkeypatch.setattr(prep,"capture_campaign_source",lambda root:{"tree_fingerprint":"synthetic-fixture-source"})
    return lane.freeze(tmp_path / "packet")


def approve_fixture(path):
    # Only synthetic tmp_path inputs; never called on a repository market packet.
    plan=json.loads(path.read_bytes())
    plan["approval"]={"approval_state":"APPROVED","approved_by":"synthetic-fixture-only",
        "approved_at":"2024-01-01T00:00:00+00:00","approved_scope":plan["expected_approved_scope"]}
    plan["market_run_authorized"]=True
    reseal(path,plan)
    return path


def test_pending_run_and_worker_refused_before_decode(pending,monkeypatch):
    monkeypatch.setattr(lane.prep,"read_calibration",lambda:pytest.fail("must reject before input decode"))
    with pytest.raises(PermissionError,match="NOT_AUTHORIZED"):
        lane.run(pending)
    with pytest.raises(PermissionError,match="NOT_AUTHORIZED"):
        lane.worker(pending,"ETH_USDT__1x__42")
    assert not (pending.parent/"cells").exists()
    assert not (pending.parent/"execution_started.json").exists()


@pytest.mark.parametrize("field,value",[("approved_scope","wrong"),("approved_by",""),("approved_at","2024-01-01")])
def test_bad_approval_field_cannot_admit(pending,field,value):
    approve_fixture(pending)
    plan=json.loads(pending.read_bytes())
    plan["approval"][field]=value
    reseal(pending,plan)
    with pytest.raises(ValueError):
        lane.verify(pending)


def test_payload_edit_without_binding_reseal_rejected(pending):
    plan=json.loads(pending.read_bytes())
    plan["live_authorized"]=True
    plan["fingerprint"]=lane.digest({k:v for k,v in plan.items() if k!="fingerprint"})
    pending.write_text(json.dumps(plan))
    with pytest.raises(ValueError,match="binding hash"):
        lane.verify(pending,approved=False)


def test_request_cannot_forge_strategy_or_frame(pending):
    approve_fixture(pending)
    frame,request=lane.load_frame(pending,"ETH_USDT__1x__42")
    _,base=lane.verify(pending)
    cell=lane.cell_for(base,request.cell_id)
    out=pending.parent/"cells"/request.cell_id
    kwargs=dict(symbol=cell["pair"],timeframe="1h",strategy=lane.policy(cell,base["calibration"][cell["pair"]]),
        manifest="development-thinning:"+request.protocol_fingerprint,
        contract_id=base["native_contracts"][cell["id"]]["contract_id"],state_dir=out/"execution",
        report_path=out/"report.json",fresh=True)
    changed=frame.with_columns((pl.col("close")+1).alias("close"))
    forged=replace(request,frame_sha256=lane.frame_digest(changed))
    with pytest.raises(ValueError,match="registered input"):
        lane.verify_simulator_input(forged,changed,**kwargs)
    kwargs["strategy"]=lane.policy({**cell,"seed":123},base["calibration"][cell["pair"]])
    with pytest.raises(ValueError,match="binding mismatch"):
        lane.verify_simulator_input(request,frame,**kwargs)
    assert not out.exists()


@pytest.mark.parametrize("cell_id",["ETH_USDT__1x__42","BNB_USDT__2x__PLUS"])
def test_actual_bound_worker_fills_and_expiry(pending,cell_id):
    approve_fixture(pending)
    lane.worker(pending,cell_id)
    out=pending.parent/"cells"/cell_id
    report=json.loads((out/"report.json").read_bytes())
    metrics=json.loads((out/"metrics.json").read_bytes())
    assert report["total_trades"]>=1
    assert report["open_positions"]==report["open_orders"]==0
    for trade in report["trades"]:
        assert datetime.fromisoformat(trade["exit_time"])-datetime.fromisoformat(trade["entry_time"])==timedelta(hours=24)
    assert metrics["buy_fill_notional"]>0
    assert metrics["marked_net_pnl"]==pytest.approx(report["final_equity"]-10000)
    assert metrics["primary_net_return_on_entry_notional"]==pytest.approx(metrics["marked_net_pnl"]/metrics["buy_fill_notional"])
    with pytest.raises(ValueError,match="no worker retry"):
        lane.worker(pending,cell_id)


def test_bound_worker_terminal_carry_is_marked_not_closed_only(pending,monkeypatch):
    # Rebuild a fresh fixture protocol for a shorter evaluation; no repository packet changed.
    monkeypatch.setattr(lane.prep,"EVALUATION",(240,250))
    path=lane.freeze(pending.parent.parent/"carry-packet")
    approve_fixture(path)
    lane.worker(path,"ETH_USDT__1x__PLUS")
    out=path.parent/"cells"/"ETH_USDT__1x__PLUS"
    report=json.loads((out/"report.json").read_bytes())
    metrics=json.loads((out/"metrics.json").read_bytes())
    assert report["open_positions"]==1
    assert not report["trades"]
    assert metrics["buy_fill_notional"]>0
    assert metrics["marked_net_pnl"]<0
    assert metrics["marked_net_pnl"]==pytest.approx(report["final_equity"]-10000)


def test_timeout_never_becomes_complete_coverage(pending,monkeypatch):
    approve_fixture(pending)
    monkeypatch.setattr(lane,"_supervise_validation",lambda *a,**k:{"status":"TIMEOUT","worker_exit_code":-9})
    assert lane.run(pending)==2
    summary=json.loads((pending.parent/"summary.json").read_bytes())
    assert summary["status"]=="INCOMPLETE" and len(summary["coverage"])==36
    assert not any(r["verified"] for r in summary["coverage"])
    with pytest.raises(ValueError,match="no resume"):
        lane.run(pending)


def event(kind,intent,seq,payload):
    return make_event(kind,intent,seq,payload=payload,occurred_at=datetime(2024,1,1,tzinfo=UTC))


def metric_report(pnl=-10):
    return {"symbol":"ETH/USDT","initial_capital":10000,"final_equity":10000+pnl,
        "measured_accounting":{"equity_delta":pnl},"execution_funnel":{"records":[{"position_after":True}]},
        "simulation_window":{"bar_count":1},"max_drawdown_pct":1,"open_positions":1,"open_orders":0}


def test_partial_fill_denominator_counts_increment_once_and_includes_carry():
    created=event("exec.order_intent_created","entry",0,{"symbol":"ETH/USDT","side":"buy","size":3})
    partial=event("exec.partial_fill_received","entry",1,{"order_id":"entry","size":1,"price":100})
    full=event("exec.fill_received","entry",2,{"order_id":"entry","size":2,"price":110})
    result=lane.fill_metrics([created,partial,full,full],metric_report())
    assert result["buy_fill_notional"]==320
    assert result["primary_net_return_on_entry_notional"]==pytest.approx(-10/320)
    assert len(result["fills"])==2
    with pytest.raises(ValueError,match="conflicting duplicate"):
        lane.fill_metrics([created,partial,replace(partial,payload={**partial.payload,"price":999})],metric_report())


def test_zero_fill_denominator_stays_undefined():
    result=lane.fill_metrics([],metric_report(0))
    assert result["buy_fill_notional"]==0
    assert result["primary_net_return_on_entry_notional"] is None


def test_complete_summary_keeps_all_six_groups_and_all_seeds(pending):
    approve_fixture(pending)
    plan,base=lane.verify(pending)
    for cell in base["cells"]:
        out=pending.parent/"cells"/cell["id"]
        out.mkdir(parents=True)
        report={"symbol":cell["pair"],"timeframe":"1h","initial_capital":10000,"final_equity":9990,
            "active_config":{"execution_contract":base["native_contracts"][cell["id"]]},
            "simulation_window":{"bar_count":40}}
        metrics={"marked_net_pnl":-10,"primary_net_return_on_entry_notional":None if cell["seed"]==42 else -0.01,"fills":[]}
        (out/"report.json").write_text(json.dumps(report))
        (out/"metrics.json").write_text(json.dumps(metrics))
        (out/"execution.json").write_text(json.dumps({"status":"COMPLETED"}))
        (out/"result.json").write_text(json.dumps({"status":"COMPLETED","cell":cell,
            "protocol_fingerprint":plan["fingerprint"],
            "report_sha256":hashlib.sha256((out/"report.json").read_bytes()).hexdigest(),
            "metrics_sha256":hashlib.sha256((out/"metrics.json").read_bytes()).hexdigest()}))
    summary=lane.coverage(pending)
    assert summary["status"]=="DEVELOPMENT_COMPLETE"
    assert len(summary["comparisons"])==6
    for group in summary["comparisons"]:
        assert [c["seed"] for c in group["controls"]]==[42,123,2026,777,31415]
        assert group["controls"][0]["plus_minus_control_primary"] is None
        assert group["statistical_conclusion"].startswith("DESCRIPTIVE_ONLY")
    # A missing cell must invalidate exact coverage and suppress comparisons.
    (pending.parent/"cells"/base["cells"][-1]["id"]/"result.json").unlink()
    assert lane.coverage(pending)["status"]=="INCOMPLETE"
    assert not lane.coverage(pending)["comparisons"]


@pytest.mark.integration
def test_real_cli_supervisor_worker_matrix_on_synthetic_inputs(pending,monkeypatch):
    """Only fixture location/source context injected; admission/engine/supervisor real."""
    import sys
    from pathlib import Path
    approve_fixture(pending)
    repo=Path(__file__).resolve().parents[1]
    settings={"ROOT":str(lane.prep.ROOT),"INPUTS":lane.prep.INPUTS,"DESIGN":lane.prep.DESIGN,
              "DESIGN_SHA":lane.prep.DESIGN_SHA,"CALIBRATION":list(lane.prep.CALIBRATION),
              "EVALUATION":list(lane.prep.EVALUATION)}
    bootstrap=pending.parent.parent/"synthetic_worker_bootstrap.py"
    bootstrap.write_text(
        "import sys,json\nfrom pathlib import Path\nsys.path.insert(0,"+repr(str(repo))+")\n"
        "from trading_agent.backtest import thinning_execution as lane\n"
        "settings=json.loads("+repr(json.dumps(settings))+")\n"
        "for key,value in settings.items():\n"
        "    setattr(lane.prep,key,Path(value) if key=='ROOT' else tuple(value) if key in ('CALIBRATION','EVALUATION') else value)\n"
        "lane.prep.capture_campaign_source=lambda root:{'tree_fingerprint':'synthetic-fixture-source'}\n"
        "raise SystemExit(lane.main())\n")
    supervise=lane._supervise_validation
    launches=[]
    def fixture_transport(cmd,out,**kwargs):
        assert cmd[:3]==[sys.executable,"-m","trading_agent.backtest.thinning_execution"]
        assert cmd[3]=="--worker"
        launches.append(cmd[-1])
        kwargs["cwd"]=repo
        return supervise([sys.executable,str(bootstrap),*cmd[3:]],out,**kwargs)
    monkeypatch.setattr(lane,"_supervise_validation",fixture_transport)
    monkeypatch.setattr(sys,"argv",["thinning_execution","--run",str(pending)])
    assert lane.main()==0
    summary=json.loads((pending.parent/"summary.json").read_bytes())
    assert len(launches)==len(set(launches))==36
    assert summary["status"]=="DEVELOPMENT_COMPLETE"
    assert all(c["verified"] for c in summary["coverage"])
    assert len(summary["comparisons"])==6
    assert not summary["qualified"] and not summary["live_authorized"]
