"""Workspace page (docs/operations/WORKSPACE_UI.md): read model and local server.

Local fixtures only: a fake checkout in tmp_path, no network, no exchange.
"""

from datetime import UTC, datetime, timedelta
import http.client
import json
import os
import socket
import sys
import threading

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts", "ops"))
import workspace_server
import workspace_status as ws

SECRETS = {
    "LIVE_SAFETY_HMAC_KEY": "sentinel-hmac-key-5f1c",
    "LIVE_ACCOUNT_ID": "sentinel-account-id-77a2",
    "BINANCE_TESTNET_API_KEY": "sentinel-api-key-9d03",
    "BINANCE_TESTNET_API_SECRET": "sentinel-api-secret-41be",
}


def _z(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


@pytest.fixture
def checkout(tmp_path, monkeypatch):
    """A checkout with one clean scheduled cycle, a health log and research packets."""
    now = datetime.now(UTC).replace(microsecond=0)
    start = now - timedelta(minutes=20)
    (tmp_path / ".env").write_text("".join(f"{k}={v}\n" for k, v in SECRETS.items()))
    for key, value in SECRETS.items():  # restored by monkeypatch after the test
        monkeypatch.setenv(key, value)
    audit = tmp_path / "data" / "execution" / "binance_live_audit.jsonl"
    audit.parent.mkdir(parents=True)
    events = [
        ("run_started", 0, {"execute": True, "testnet": True}),
        ("run_stage", 2, {"stage": "account_read"}),
        ("run_stage", 7, {"stage": "signals_computed"}),
        ("entries_locked_no_promoted_runtime", 7, {"reason": "operational track not authorized"}),
        ("run_completed", 8, {"exit_code": 0}),
    ]
    audit.write_text("".join(
        json.dumps({"timestamp": (start + timedelta(seconds=s)).isoformat(), "event": e,
                    "pid": 4242, "correlation_id": "run-1", "details": d}) + "\n"
        for e, s, d in events
    ))
    (tmp_path / "logs").mkdir()
    (tmp_path / "logs" / "binance_testnet.log").write_text(
        f"=== {_z(start)} cycle start pid=4242\nBINANCE SPOT\n"
        f"=== {_z(start + timedelta(seconds=9))} cycle end rc=0\n"
    )
    health = tmp_path / "health.log"
    health.write_text(f"{_z(now - timedelta(minutes=5))} exit=0 LIVE_AUDIT_OK: run_completed\n")
    monkeypatch.setenv("WORKSPACE_HEALTH_LOG", str(health))
    for name in ("thinning_pending_20261029_01", "thinning_pending_20261029_02"):
        packet = tmp_path / "data" / name
        (packet / "prepared").mkdir(parents=True)
        (packet / "protocol.json").write_text(json.dumps(
            {"approval": {"approval_state": "PENDING_OPERATOR_APPROVAL", "approved_by": None}}))
        (packet / "prepared" / "protocol.json").write_text(json.dumps({"cells": list(range(36))}))
        (packet / "verification_manifest.json").write_text(json.dumps({"market_cells_executed": 0}))
    os.utime(tmp_path / "data" / "thinning_pending_20261029_01" / "protocol.json", (1, 1))
    closed = tmp_path / "data" / "matrix_20261029_01"
    closed.mkdir()
    (closed / "CLOSEOUT.json").write_text(json.dumps({"verdict": "CLOSED_NO_SUPPORTED_INCREMENT"}))
    docs = tmp_path / "docs" / "operations"
    docs.mkdir(parents=True)
    (tmp_path / "docs" / "CAMPAIGN_STATUS.md").write_text(
        "# Tracker\n\n### 2026-10-29 — binding bypass resolved\n\nP1 is fixed. The pending "
        "packet stays PENDING_OPERATOR_APPROVAL.\n\n"
        "### 2026-10-27 — H-LIQ-REVERSAL-01 closed RESEARCH_ABSTAIN\n\nNo edge.\n\n"
        "### Research procedure, 2026-10-18\n\nSee RESEARCH_RUN_PROTOCOL for the steps.\n"
    )
    (docs / "OPERABLE_SYSTEM_PLAN.md").write_text(
        "# Plan\n\n## Incident log\n\n| Date (UTC) | Incident | Root cause | Fix |\n"
        "| --- | --- | --- | --- |\n| 2026-10-30 (rehearsal) | Second BUY blocked | No evidence | Acknowledge |\n"
    )
    (docs / "RSI_EVIDENCE_CARD.md").write_text("# Card\n\nStatus: TERMINATED_NOT_QUALIFIED — fails cost stress.\n")
    for package, files in (("execution", 3), ("backtest", 2), ("features", 1), ("llm", 1)):
        directory = tmp_path / "src" / "trading_agent" / package
        directory.mkdir(parents=True)
        for index in range(files):
            (directory / f"m{index}.py").write_text("")
    candidates = tmp_path / "src" / "trading_agent" / "strategies" / "canonical"
    candidates.mkdir(parents=True)
    (candidates / "candidates.py").write_text(
        "from typing import Any\n_PARAM_SCHEMAS: dict[str, dict[str, Any]] = {'rsi': {}, 'enhanced_ma': {}}\n"
    )
    monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.setattr(ws, "runner_packages", lambda: {"execution", "data"})
    monkeypatch.setattr(ws, "_git", lambda *args: None)
    return tmp_path


def _snapshot(base):
    return sorted((str(p.relative_to(base)), p.stat().st_mtime_ns) for p in base.rglob("*"))


def test_doing_shows_waiting_work_the_cycle_timeline_and_milestones(checkout):
    doing = ws.doing()
    (packet,) = [i for i in doing["waiting"] if i["title"].startswith("Duyệt packet")]
    assert packet["title"].endswith("thinning_pending_20261029_02")  # newest of its family
    assert "1 packet cũ hơn cùng scope" in packet["detail"]
    assert packet["progress"] == {"kind": "ratio", "done": 0, "total": 36,
                                  "why": "cell kế hoạch trong prepared/protocol.json"}
    (m3_sign,) = [i for i in doing["waiting"] if i["title"] == "Ký giấy phép M3"]
    assert m3_sign["state"] == "not_yet" and "M1 PASSED" in m3_sign["detail"]
    cycle = doing["automatic"][0]
    assert cycle["state"] == "ok" and cycle["last_result"] == "run_completed"
    assert [t["label"] for t in cycle["timeline"]] == [
        "Runner bắt đầu", "Đọc tài khoản và vị thế", "Tính tín hiệu", "Khóa mua", "Hoàn tất"]
    m1 = next(i for i in doing["automatic"] if i["title"].startswith("M1"))
    assert m1["progress"]["kind"] == "ratio" and m1["progress"]["total"] == 24
    health = next(i for i in doing["automatic"] if i["title"].startswith("Health"))
    assert health["state"] == "ok"
    assert doing["entries_locked"] == "operational track not authorized"


def test_a_newer_approved_packet_supersedes_its_pending_proposals(checkout):
    for name in ("thinning_pending_20261029_01", "thinning_pending_20261029_02"):
        protocol = checkout / "data" / name / "protocol.json"
        document = json.loads(protocol.read_text())
        protocol.write_text(json.dumps({**document, "scope": "thinning-v1"}))
    approved = checkout / "data" / "thinning_approved_20261030_01"
    (approved / "prepared").mkdir(parents=True)
    (approved / "prepared" / "protocol.json").write_text(json.dumps({"cells": list(range(36))}))
    (approved / "protocol.json").write_text(json.dumps({
        "scope": "thinning-v1", "command": [".venv/bin/python", "-m", "x", "--run", "p"],
        "approval": {"approval_state": "APPROVED", "approved_by": "operator",
                     "approved_at": "2026-10-30T03:53:54+00:00", "approved_scope": "s"}}))
    (item,) = [i for i in ws.pending_packets()]
    assert item["title"] == "Chạy packet nghiên cứu đã duyệt thinning_approved_20261030_01"
    assert "2 packet cũ hơn cùng scope" in item["detail"] and "chưa chạy" in item["detail"]
    assert item["next"] == ".venv/bin/python -m x --run p"
    assert item["progress"]["done"] == 0 and item["progress"]["total"] == 36
    # Once it starts, it is not waiting for the operator any more.
    (approved / "execution_started.json").write_text("{}")
    assert ws.pending_packets() == []


def test_processes_are_named_by_entrypoint_and_runs_grouped_per_packet(checkout):
    packet = checkout / "data" / "thinning_pending_20261029_02"
    protocol = str(packet / "protocol.json")
    wrapper = [".venv/bin/python", "scripts/qwenpaw_control/controlled_exec.py", "--result-file",
               str(packet / "execution.json"), "--", ".venv/bin/python", "-m",
               "trading_agent.backtest.thinning_execution", "--run", protocol]
    # The wrapper only mentions the run in its arguments; it is not the run.
    assert ws.describe_process(wrapper) is None
    run = ws.describe_process([".venv/bin/python", "-m", "trading_agent.backtest.thinning_execution",
                               "--run", protocol])
    worker = ws.describe_process(["/x/.venv/bin/python", "-m", "trading_agent.backtest.thinning_execution",
                                  "--worker", protocol, "ETH_USDT__1x__42"])
    assert run["script"] == "thinning_execution" and run["packet"] == packet.name and run["cell"] is None
    assert worker["cell"] == "ETH_USDT__1x__42"
    (packet / "prepared" / "protocol.json").write_text(json.dumps(
        {"cells": list(range(36)), "budget": {"hard_deadline_seconds": 23400}}))
    (packet / "cells" / "ETH_USDT__1x__PLUS").mkdir(parents=True)
    (packet / "cells" / "ETH_USDT__1x__PLUS" / "execution.json").write_text('{"status": "COMPLETED"}')
    (packet / "cells" / "ETH_USDT__1x__42").mkdir()
    now = datetime.now(UTC)
    started = (now - timedelta(minutes=4)).isoformat()
    processes = [{**run, "pid": 10, "started_at": started}, {**worker, "pid": 11, "started_at": started}]
    (item,) = ws.research_runs(processes, now)
    assert item["state"] == "running" and item["pids"] == [10, 11]
    assert item["progress"] == {"kind": "ratio", "done": 1, "total": 36,
                                "why": "cell kế hoạch trong prepared/protocol.json"}
    assert "ETH_USDT__1x__42" in item["detail"] and "hạn cuối theo budget" in item["next"]
    (packet / "cells" / "ETH_USDT__1x__42" / "execution.json").write_text('{"status": "TIMEOUT"}')
    (item,) = ws.research_runs(processes, now)
    assert item["state"] == "error" and "1 cell không hoàn tất" in item["detail"]


def test_a_cycle_never_shows_a_percentage_and_no_denominator_means_none(checkout):
    cycle = ws.doing()["automatic"][0]
    assert cycle["progress"]["kind"] == "step"  # step k of N, not a percentage
    (checkout / "data" / "thinning_pending_20261029_02" / "prepared" / "protocol.json").write_text("{}")
    (packet,) = [i for i in ws.doing()["waiting"] if i["title"].startswith("Duyệt packet")]
    assert packet["progress"]["kind"] == "none"


def test_stale_and_failed_health_checks_are_not_shown_as_success(checkout):
    log = checkout / "health.log"
    now = datetime.now(UTC)
    log.write_text(f"{_z(now - timedelta(hours=2))} exit=0 LIVE_AUDIT_OK\n")
    assert ws.health_item(now)["state"] == "stale"
    log.write_text(f"{_z(now)} exit=2 LIVE_AUDIT_CRITICAL: silence\n")
    assert ws.health_item(now)["state"] == "error"
    log.unlink()
    assert ws.health_item(now)["state"] == "unknown"


def test_outcome_labels_come_from_headings_and_status_fields_only(checkout):
    research = {e["title"]: e["outcome"] for e in ws.done()["entries"] if e["flow"] == "research"}
    # The body mentions a pending packet; the entry itself states no verdict.
    assert research["binding bypass resolved"] is None
    assert research["H-LIQ-REVERSAL-01 closed RESEARCH_ABSTAIN"] == "RESEARCH_ABSTAIN"
    assert research["Rsi Evidence Card"] == "TERMINATED_NOT_QUALIFIED"
    assert ws._outcome("See RESEARCH_RUN_PROTOCOL for the steps.") is None
    newest = {p["name"]: p["outcome"] for p in ws.have()["research"]["newest"]}
    assert newest["matrix_20261029_01"] == "CLOSED_NO_SUPPORTED_INCREMENT"
    (incident,) = [e for e in ws.done()["entries"] if e["flow"] == "operations"]
    assert incident["outcome"] == "FIXED" and incident["date"] == "2026-10-30"


def test_have_counts_code_by_measured_role_and_lists_strategies(checkout):
    have = ws.have()
    roles = {p["name"]: p["role"] for p in have["packages"]}
    assert roles == {"execution": "core", "backtest": "research", "features": "unused",
                     "llm": "outside", "strategies": "strategies"}
    assert have["strategies"]["ids"] == ["enhanced_ma", "rsi"]
    assert have["strategies"]["promotion_store"] is False


def test_nothing_returned_holds_a_secret_and_nothing_is_written(checkout):
    before = _snapshot(checkout)
    text = "".join(ws.to_json(section()) for section in ws.SECTIONS.values())
    for value in SECRETS.values():
        assert value not in text
    assert _snapshot(checkout) == before


# ── Server ─────────────────────────────────────────────────────────────────


@pytest.fixture
def server(monkeypatch):
    monkeypatch.setattr(ws, "SECTIONS", {
        "doing": lambda: {"as_of": "now", "note": "<script>alert(1)</script>"},
        "done": lambda: {"entries": []},
        "have": lambda: {"packages": []},
    })
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    httpd = workspace_server.serve(port)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield port, httpd
    httpd.shutdown()
    httpd.server_close()


def _request(port, method, path, host=None):
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    connection.putrequest(method, path, skip_host=True)
    connection.putheader("Host", host or f"127.0.0.1:{port}")
    connection.endheaders()
    response = connection.getresponse()
    return response.status, dict(response.getheaders()), response.read()


def test_server_binds_loopback_serves_the_page_and_json(server):
    port, httpd = server
    assert httpd.server_address[0] == "127.0.0.1"
    status, headers, body = _request(port, "GET", "/")
    assert status == 200 and "Toàn cảnh hệ thống" in body.decode()
    assert "default-src 'none'" in headers["Content-Security-Policy"]
    assert "connect-src 'self'" in headers["Content-Security-Policy"]
    status, headers, body = _request(port, "GET", "/api/doing")
    assert status == 200 and headers["Content-Type"].startswith("application/json")
    assert json.loads(body)["as_of"] == "now"


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE", "OPTIONS"])
def test_server_refuses_every_method_but_get(server, method):
    status, _, _ = _request(server[0], method, "/api/doing")
    assert status == 405


@pytest.mark.parametrize(
    "path", ["/../../etc/passwd", "/workspace_status.py", "/api/../.env", "/api/doing/x", "/.env"]
)
def test_server_serves_no_other_path(server, path):
    status, _, body = _request(server[0], "GET", path)
    assert status == 404
    assert b"sentinel" not in body


def test_server_refuses_a_foreign_host_header(server):
    status, _, _ = _request(server[0], "GET", "/api/doing", host="attacker.example:80")
    assert status == 403


def test_page_escapes_everything_it_renders():
    page = workspace_server.PAGE.read_text(encoding="utf-8")
    assert "innerHTML" in page and "const esc" in page
    # No external resource can load: every URL in the page is relative.
    assert "http://" not in page and "https://" not in page


def test_books_are_unknown_without_the_hmac_key(checkout, monkeypatch):
    state = checkout / ws.STATE_FILE
    state.write_text("{}")
    monkeypatch.delenv("LIVE_SAFETY_HMAC_KEY")
    item = ws.books_item(checkout)
    assert item["state"] == "unknown" and "HMAC" in item["detail"]


def test_entries_are_known_only_once_a_run_has_said(checkout):
    audit = checkout / ws.AUDIT_LOG
    assert ws.doing()["entries_known"] is True
    audit.write_text("".join(
        line + "\n" for line in audit.read_text().splitlines()
        if "entries_locked_no_promoted_runtime" not in line
    ))
    doing = ws.doing()
    assert doing["entries_locked"] is None and doing["entries_known"] is False


def test_an_unreadable_audit_log_does_not_read_as_entries_open(checkout):
    (checkout / ws.AUDIT_LOG).write_text("not json\n")
    doing = ws.doing()
    assert doing["audit"]["error"]
    assert doing["entries_locked"] is None and doing["entries_known"] is False


def test_a_critical_event_since_the_m1_start_shows_on_the_milestone(checkout, monkeypatch):
    audit = checkout / ws.AUDIT_LOG
    lines = audit.read_text().splitlines()
    first = json.loads(lines[0])
    monkeypatch.setattr(ws.daily_check, "DEFAULT_M1_START",
                        datetime.fromisoformat(first["timestamp"]).isoformat())
    audit.write_text("\n".join([*lines, json.dumps(
        {**first, "event": "position_protection_failed", "details": {}})]) + "\n")
    m1 = next(i for i in ws.doing()["automatic"] if i["title"].startswith("M1"))
    assert m1["state"] == "error" and "1 vi phạm" in m1["detail"]


def test_the_page_shows_nothing_green_when_it_cannot_read():
    page = workspace_server.PAGE.read_text(encoding="utf-8")
    assert 'doingData.entries_known ? chip("ok", "Mua được phép") : chip("unknown", "Mua: không rõ")' in page
    assert 'chip("unknown", "Không đọc được")' in page
    assert 'esc(d.error)}</div>`; renderTop(); return;' in page
