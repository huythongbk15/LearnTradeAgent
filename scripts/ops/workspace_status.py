#!/usr/bin/env python3
"""Read model of the workspace page: what is being done, what was done, what exists.

Reads files only (docs/operations/WORKSPACE_UI.md): no exchange call, no network,
no write; SQLite opens read-only and git runs read-only commands. Nothing it
returns holds a secret, an API key, an account ID or `.env` content.

    .venv/bin/python scripts/ops/workspace_status.py doing|done|have

WORKSPACE_ROOT points at the checkout whose data to read (default: this one).
"""

from __future__ import annotations

import ast
import json
import os
import re
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from glob import glob
from pathlib import Path

CODE = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(CODE / "src"), str(CODE / "scripts"), str(CODE / "scripts" / "ops")]

from check_live_audit import (  # noqa: E402
    AuditHealthError,
    _runs,
    load_events,
    validate_audit_health,
)
import daily_check  # noqa: E402

AUDIT_LOG = "data/execution/binance_live_audit.jsonl"
CYCLE_LOG = "logs/binance_testnet.log"
STATE_FILE = "data/binance_testnet_risk_state_v2.json"
HEALTH_LOG_GLOB = "/mnt/c/Users/*/AppData/Local/TradingAgent/health.log"
HEALTH_STALE = timedelta(minutes=45)
AUTHORIZE_COMMAND = (
    '.venv/bin/python scripts/ops/authorize_operational_track.py '
    '--approver NAME --reason "M3 E2E"'
)
RECENT_BRANCH = timedelta(days=14)
OUTCOME_TOKENS = (
    "TERMINATED_NOT_QUALIFIED", "CLOSED_NO_SUPPORTED_INCREMENT", "NOT_QUALIFIED",
    "NO_INCREMENT", "PENDING_OPERATOR_APPROVAL", "PREPARED_NOT_AUTHORIZED",
    "INCOMPLETE", "ABSTAIN", "QUALIFIED", "PASSED", "PASS", "CURRENT",
)
VERDICT_WORDS = (
    "QUALIFIED", "ABSTAIN", "INCREMENT", "PASS", "FAIL", "AUTHORIZED", "APPROVAL",
    "COMPLETE", "CLOSED", "SELECT", "REJECT", "TERMINATED", "CURRENT",
)
# Package roles. "core" is decided per package by what the testnet runner loads;
# these two sets come from docs/operations/CORE_RUNTIME_BOUNDARY.md.
RESEARCH_PACKAGES = {"backtest", "research", "calibration", "alpha_research"}
UNUSED_PACKAGES = {"features", "enterprise", "messaging", "infrastructure"}
EVENT_LABELS = {
    "run_started": "Runner bắt đầu",
    "entries_locked_no_promoted_runtime": "Khóa mua",
    "operational_track_active": "Luồng vận hành",
    "order_acknowledged": "Sàn nhận lệnh",
    "order_filled": "Lệnh khớp",
    "protective_stop_placed": "Đặt stop",
    "protective_stop_replaced": "Dời stop",
    "protective_stop_handed_to_exit": "Hủy stop để bán",
    "protective_stop_filled": "Stop khớp trên sàn",
    "exit_superseded_by_protective_stop": "Stop khớp trước lệnh bán",
    "run_completed": "Hoàn tất",
    "run_failed": "Lỗi",
    "run_interrupted": "Bị ngắt",
}
STAGE_LABELS = {
    "account_read": "Đọc tài khoản và vị thế",
    "signals_computed": "Tính tín hiệu",
    "orders_planned": "Lập lệnh",
}
# The cycle's steps the runner records, in order (it ends early when it has
# nothing to trade). Protection, submission and reconciliation are not recorded.
CYCLE_STEPS = ("run_started", "account_read", "signals_computed", "orders_planned", "run_completed")
KNOWN_PROCESSES = (
    ("live_enhanced_ma_binance.py", "Chu kỳ Binance testnet", "operations"),
    ("live_enhanced_ma.py", "Alpaca paper", "operations"),
    ("run_wfo_parallel.py", "Walk-forward (WFO)", "research"),
    ("thinning_execution", "Thinning", "research"),
    ("thinning_synthetic", "Thinning tổng hợp", "research"),
    ("run_calibration.py", "Calibration", "research"),
    ("run_campaign.py", "Research campaign", "research"),
    ("fill_cycle_development", "BASE/PLUS fill cycle", "research"),
    ("run_test_suite.py", "Bộ test", "tests"),
)


def root() -> Path:
    return Path(os.getenv("WORKSPACE_ROOT") or CODE)


def _iso(value: datetime | None) -> str | None:
    return value.astimezone(UTC).isoformat() if value else None


def _short(value: datetime | None) -> str:
    """A time inside a sentence: ``31/10 17:41Z``."""
    return value.astimezone(UTC).strftime("%d/%m %H:%MZ") if value else ""


def _mtime(path: Path) -> datetime:
    return datetime.fromtimestamp(path.stat().st_mtime, UTC)


def _read_json(path: Path) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _git(*args: str) -> str | None:
    """A read-only git command in the data checkout; None when git cannot answer."""
    try:
        done = subprocess.run(
            ["git", "-C", str(root()), *args],
            capture_output=True, text=True, timeout=20, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return done.stdout if done.returncode == 0 else None


def _ratio(done: int, total: int, why: str) -> dict:
    return {"kind": "ratio", "done": done, "total": total, "why": why}


def _none(why: str) -> dict:
    return {"kind": "none", "why": why}


def _item(title: str, state: str, *, detail: str = "", progress: dict | None = None,
          next_step: str = "", since: datetime | None = None, source: str = "",
          **extra) -> dict:
    return {"title": title, "state": state, "detail": detail,
            "progress": progress or _none(""), "next": next_step,
            "since": _iso(since), "source": source, **extra}


# ── Doing ───────────────────────────────────────────────────────────────────


def _boot_time() -> float:
    for line in Path("/proc/stat").read_text().splitlines():
        if line.startswith("btime "):
            return float(line.split()[1])
    raise OSError("btime missing from /proc/stat")


def live_processes() -> list[dict]:
    """Known entrypoints alive in /proc now. Only the script and packet are shown."""
    found = []
    try:
        btime, ticks = _boot_time(), os.sysconf("SC_CLK_TCK")
    except (OSError, ValueError):
        return found
    data = root() / "data"
    for proc in Path("/proc").iterdir():
        if not proc.name.isdigit() or int(proc.name) == os.getpid():
            continue
        try:
            args = (proc / "cmdline").read_bytes().split(b"\0")
            stat = (proc / "stat").read_text()
        except OSError:
            continue
        args = [a.decode(errors="replace") for a in args if a]
        if not args or "python" not in Path(args[0]).name:
            continue
        match = next(
            ((token, name, flow) for token, name, flow in KNOWN_PROCESSES
             if any(token in arg for arg in args[1:])),
            None,
        )
        if match is None:
            continue
        # Field 22 of /proc/<pid>/stat (start time in clock ticks after boot).
        started = datetime.fromtimestamp(btime + int(stat.rsplit(")", 1)[1].split()[19]) / ticks, UTC)
        packet = None
        for arg in args[1:]:
            candidate = Path(arg) if Path(arg).is_absolute() else root() / arg
            if _under(candidate, data):
                packet = candidate.resolve().relative_to(data.resolve()).parts[0]
                break
        found.append({"pid": int(proc.name), "name": match[1], "flow": match[2],
                      "script": Path(next(a for a in args[1:] if match[0] in a)).name,
                      "packet": packet, "started_at": _iso(started)})
    return sorted(found, key=lambda p: p["started_at"] or "")


def _under(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
    except (ValueError, OSError):
        return False
    return path.resolve() != parent.resolve()


def _run_of(events: list[dict], start_index: int) -> list[dict]:
    """The events of one runner invocation, from its run_started to its end."""
    start = events[start_index]
    run = [start]
    for event in events[start_index + 1:]:
        if event["event"] == "run_started" and event.get("pid") == start.get("pid"):
            break
        if event.get("correlation_id") and start.get("correlation_id") \
                and event["correlation_id"] != start["correlation_id"]:
            continue
        run.append(event)
        if event["event"] in ("run_completed", "run_failed", "run_interrupted"):
            break
    return run


def _timeline_entry(event: dict) -> dict:
    details = event.get("details") or {}
    name = event["event"]
    if name == "run_stage":
        label = STAGE_LABELS.get(details.get("stage"), str(details.get("stage")))
        detail = ""
    else:
        label = EVENT_LABELS.get(name, name)
        detail = ""
        if name == "entries_locked_no_promoted_runtime":
            detail = str(details.get("reason", ""))[:300]
        elif name == "operational_track_active":
            blocked = details.get("entries_blocked")
            detail = (f"được mở {details.get('may_open')}, đang giữ {details.get('held')}"
                      + (f"; chặn mua: {blocked}" if blocked else ""))[:300]
        elif name in ("order_filled", "order_acknowledged"):
            detail = " ".join(str(details.get(k, "")) for k in ("side", "symbol", "filled_qty"))
        elif name.startswith("protective_stop") or name.startswith("exit_superseded"):
            detail = str(details.get("symbol", ""))
        elif name == "run_failed":
            detail = f"{details.get('error_type', '')}: {details.get('error', '')}"[:300]
        elif name == "run_interrupted":
            detail = str(details.get("signal", ""))
    return {"at": event.get("timestamp"), "event": name, "label": label, "detail": detail}


def _cycle_steps(run: list[dict]) -> dict:
    seen = [e["details"].get("stage") if e["event"] == "run_stage" else e["event"] for e in run]
    reached = [s for s in CYCLE_STEPS if s in seen]
    last = reached[-1] if reached else None
    index = CYCLE_STEPS.index(last) + 1 if last else 0
    return {"kind": "step", "done": index, "total": len(CYCLE_STEPS),
            "why": "các bước dài ngắn khác nhau, không quy ra %; "
                   "runner chưa ghi mốc bảo vệ stop, gửi lệnh, đối soát"}


def cycle_job(events: list[dict], cycles: list[dict], now: datetime) -> dict:
    """The scheduled testnet cycle: last runs, last timeline, next projection."""
    starts = [i for i, e in enumerate(events)
              if e["event"] == "run_started" and (e.get("details") or {}).get("execute")]
    timeline, steps, result = [], _none("chưa có run nào trong audit log"), None
    if starts:
        run = _run_of(events, starts[-1])
        timeline = [_timeline_entry(e) for e in run][:40]
        steps = _cycle_steps(run)
        result = run[-1]["event"] if run[-1]["event"] != "run_started" else None
    last = cycles[-1] if cycles else None
    recent = [c for c in cycles if c["start"] >= now - timedelta(hours=24)]
    bad = [c for c in recent if c["result"] not in ("rc=0", "running")]
    if last is None:
        state, detail = "unknown", "không thấy chu kỳ nào trong 24 h"
    elif last["result"] == "running":
        state, detail = "running", "đang chạy"
    elif now - last["start"] > daily_check.GAP_LIMIT:
        state, detail = "late", f"quá {daily_check.GAP_LIMIT} không có chu kỳ mới"
    elif last["result"] == "rc=0":
        state, detail = "ok", "lần cuối rc=0"
    else:
        state, detail = "error", f"lần cuối {last['result']}"
    took = (last["end"] - last["start"]).total_seconds() if last and last["end"] else None
    next_at = last["start"] + daily_check.CADENCE if last else None
    return _item(
        "Chu kỳ Binance testnet", state, detail=detail, progress=steps,
        next_step=f"chu kỳ kế theo lịch: {_short(next_at)}" if next_at else "",
        next_at=_iso(next_at),
        since=last["start"] if last else None,
        source=f"{CYCLE_LOG} · {AUDIT_LOG}",
        last_result=result, last_seconds=took,
        cycles_24h=len(recent), failed_24h=len(bad),
        timeline=timeline, projection=True,
    )


def health_item(now: datetime) -> dict:
    configured = os.getenv("WORKSPACE_HEALTH_LOG")
    paths = [Path(configured)] if configured else [Path(p) for p in glob(HEALTH_LOG_GLOB)]
    path = next((p for p in paths if p.is_file()), None)
    if path is None:
        return _item("Health check (Windows, 30 phút)", "unknown",
                     detail="không thấy health.log", source=HEALTH_LOG_GLOB)
    lines = [line for line in path.read_text(errors="replace").splitlines() if line.strip()]
    match = re.match(r"(\S+Z) exit=(\d+) ?(.*)", lines[-1]) if lines else None
    if match is None:
        return _item("Health check (Windows, 30 phút)", "unknown",
                     detail="dòng cuối không đọc được", source=str(path))
    at = datetime.fromisoformat(match[1].replace("Z", "+00:00"))
    state = ("error" if match[2] != "0" else "stale" if now - at > HEALTH_STALE else "ok")
    return _item("Health check (Windows, 30 phút)", state,
                 detail=f"exit={match[2]} {match[3]}"[:240], since=at, source=str(path))


def books_item(base: Path) -> dict:
    """Ledger (HMAC-verified) and lifecycle consistency, as daily_check reads them."""
    from trading_agent.execution.live_safety import LiveRiskStateStore, LiveSafetyError

    state_file = base / STATE_FILE
    if not state_file.is_file():
        return _item("Sổ sách: ledger và lifecycle", "unknown",
                     detail="không có ledger", source=STATE_FILE)
    try:
        store = LiveRiskStateStore(state_file, integrity_key=os.environ.get("LIVE_SAFETY_HMAC_KEY"))
        unfinished = len(store.unfinished_orders())
        locked = store.state.locked_reason
        intents = daily_check.open_lifecycle_intents(base / "data" / "execution" / "accounts")
    except (LiveSafetyError, OSError, ValueError) as exc:
        return _item("Sổ sách: ledger và lifecycle", "error",
                     detail=f"không đọc được: {type(exc).__name__}", source=STATE_FILE)
    ok = not unfinished and not locked and not intents
    return _item(
        "Sổ sách: ledger và lifecycle", "ok" if ok else "error",
        detail=f"lệnh dở {unfinished} · khóa {locked or 'không'} · intent mở {len(intents)}",
        source=f"{STATE_FILE} · data/execution/accounts/*/events.db",
    )


def pending_packets() -> list[dict]:
    """Research packets waiting for the operator: to approve, or approved but not run.

    Packets are grouped by their protocol ``scope`` (by name otherwise) and only
    the newest counts: a proposal stays PENDING forever once a newer packet of the
    same scope supersedes it, because packets are never edited.
    """
    scopes: dict[str, list[tuple[Path, dict]]] = {}
    for protocol in (root() / "data").glob("*/protocol.json"):
        document = _read_json(protocol)
        approval = document.get("approval") if isinstance(document, dict) else None
        if isinstance(approval, dict) and approval.get("approval_state"):
            key = str(document.get("scope") or re.sub(r"_\d{2}$", "", protocol.parent.name))
            scopes.setdefault(key, []).append((protocol.parent, document))
    items = []
    for packets in scopes.values():
        packets.sort(key=lambda p: _mtime(p[0] / "protocol.json"))
        newest, document = packets[-1]
        approval = document["approval"]
        if (newest / "execution_started.json").exists():
            continue  # running or run: no longer waiting for the operator
        prepared = _read_json(newest / "prepared" / "protocol.json")
        cells = prepared.get("cells") if isinstance(prepared, dict) else None
        progress = (
            _ratio(len(list((newest / "cells").glob("*/execution.json"))), len(cells),
                   "cell kế hoạch trong prepared/protocol.json")
            if isinstance(cells, list) else _none("protocol không liệt kê cell")
        )
        older = f"; {len(packets) - 1} packet cũ hơn cùng scope" if len(packets) > 1 else ""
        if approval["approval_state"] == "APPROVED":
            command = document.get("command")
            items.append(_item(
                f"Chạy packet nghiên cứu đã duyệt {newest.name}", "waiting",
                detail=f"duyệt bởi {approval.get('approved_by')} lúc "
                       f"{_short(datetime.fromisoformat(approval['approved_at']))}; chưa chạy{older}",
                progress=progress,
                next_step=" ".join(command) if isinstance(command, list) else "",
                since=_mtime(newest / "protocol.json"),
                source=f"data/{newest.name}/APPROVAL.md",
            ))
        elif approval["approval_state"] == "PENDING_OPERATOR_APPROVAL":
            review = newest / "REVIEW.md"
            items.append(_item(
                f"Duyệt packet nghiên cứu {newest.name}", "waiting",
                detail=f"packet đã chuẩn bị, chờ phê duyệt trước khi chạy{older}",
                progress=progress,
                next_step=f"xem {review.relative_to(root())}" if review.is_file() else "",
                since=_mtime(newest / "protocol.json"),
                source=f"data/{newest.name}/protocol.json",
            ))
    return sorted(items, key=lambda i: i["since"] or "", reverse=True)


def code_items(now: datetime) -> list[dict]:
    """Commits not on origin/master and recent local branches not merged."""
    items = []
    fetch_head = root() / ".git" / "FETCH_HEAD"
    fetched = _mtime(fetch_head) if fetch_head.is_file() else None
    ahead = _git("rev-list", "--count", "origin/master..master")
    if ahead is None:
        items.append(_item("Trạng thái git", "unknown", detail="git không trả lời"))
    elif int(ahead.strip() or 0) > 0:
        items.append(_item(
            f"Đẩy {int(ahead)} commit lên origin", "waiting",
            detail="code trên master ở máy này chưa có trên origin"
                   + (f" (so với lần fetch {_short(fetched)})" if fetched else ""),
            source="git log origin/master..master",
        ))
    refs = _git("for-each-ref", "--format=%(refname:short)\t%(committerdate:iso-strict)",
                "refs/heads/") or ""
    for line in refs.splitlines():
        name, _, date = line.partition("\t")
        if name == "master" or not date:
            continue
        when = datetime.fromisoformat(date)
        if now - when > RECENT_BRANCH:
            continue
        count = _git("rev-list", "--count", f"master..{name}")
        if count and int(count) > 0:
            items.append(_item(
                f"Nhánh {name}: {int(count)} commit chưa vào master", "waiting",
                detail="cập nhật gần đây; cần quyết định merge hay bỏ",
                since=when, source=f"git log master..{name}",
            ))
    return items


def m3_waiting(m1: dict, m3: dict) -> list[dict]:
    if m3["state"] not in ("not authorized", "inactive"):
        return []
    if m1["state"] == "PASSED":
        return [_item("Ký giấy phép M3", "waiting", detail="M1 đã đạt; luồng vận hành chờ giấy phép",
                      next_step=AUTHORIZE_COMMAND, source="scripts/ops/authorize_operational_track.py")]
    earliest = m1.get("earliest_pass")
    return [_item(
        "Ký giấy phép M3", "not_yet",
        detail="điều kiện: M1 PASSED" + (f" · sớm nhất {_short(earliest)} (dự kiến theo lịch)" if earliest else ""),
        next_step=AUTHORIZE_COMMAND, source="docs/operations/OPERABLE_SYSTEM_PLAN.md",
    )]


def _m3_reason(m3: dict) -> str:
    if m3["state"] == "not authorized":
        return "chưa ký giấy phép vận hành; mua vẫn khóa"
    return f"giấy phép không dùng được: {m3['reason']}"


def milestone_items(m1: dict, m3: dict) -> list[dict]:
    m1_state = {"PASSED": "ok", "on track": "progress", "not started": "not_yet"}.get(m1["state"], "error")
    items = [_item(
        "M1 · lịch chạy ổn định", m1_state,
        detail=f"{m1['clean']}/{m1['required']} chu kỳ sạch · {m1['hours']:.1f}/{m1['required_hours']} h · {m1['state']}",
        progress=_ratio(m1["clean"], m1["required"], "≥24 chu kỳ sạch trong ≥48 h"),
        next_step=(f"sớm nhất {_short(m1['earliest_pass'])} (dự kiến theo lịch)"
                   if m1.get("earliest_pass") and m1["state"] != "PASSED" else ""),
        since=m1["start"], source="scripts/ops/daily_check.py",
    )]
    if m3["state"] in ("not authorized", "inactive"):
        items.append(_item("M3 · vòng đời lệnh đầu-cuối", "not_yet", detail=_m3_reason(m3),
                           progress=_ratio(0, daily_check.M3_LIFECYCLES, "lifecycle đóng của luồng vận hành"),
                           source="scripts/ops/daily_check.py"))
    else:
        items.append(_item(
            "M3 · vòng đời lệnh đầu-cuối",
            "ok" if m3["state"] == "PASSED" else "error" if m3["failed"] else "progress",
            detail=(f"bán {m3['sold']} · stop sàn {m3['stopped']} · khác {m3['other']} · "
                    f"mở {m3['opened']} · giấy phép {m3['authorization_id']} đến {_short(m3['expires_at'])}"),
            progress=_ratio(m3["closed"], m3["required"], f"cần ≥{m3['each_exit']} bán và ≥{m3['each_exit']} stop sàn"),
            since=m3["since"], source="scripts/ops/daily_check.py",
        ))
    return items


def doing(now: datetime | None = None) -> dict:
    now = now or datetime.now(UTC)
    base = root()
    from dotenv import load_dotenv

    # Only the HMAC key and the account binding are used; neither is returned.
    load_dotenv(base / ".env")
    try:
        events = load_events(base / AUDIT_LOG)
        audit_error = None
    except AuditHealthError as exc:
        events, audit_error = [], str(exc)
    events.sort(key=lambda e: e["_timestamp"])
    runs = [r for r in _runs(events) if r["execute"]]
    m1_start = datetime.fromisoformat(daily_check.DEFAULT_M1_START)
    cycles = daily_check.wrapper_cycles(base / CYCLE_LOG, min(now - timedelta(hours=24), m1_start))
    m1 = daily_check.m1_status(runs, cycles, m1_start=m1_start, now=now)
    m3 = daily_check.m3_status(events, runs, now=now) if events else {
        "state": "inactive", "reason": "không đọc được audit log", "failed": []}
    try:
        validate_audit_health(events, now=now, max_age_seconds=9_900,
                              lookback_seconds=9_900, max_run_seconds=2_700)
        audit_health = None
    except AuditHealthError as exc:
        audit_health = str(exc)
    processes = live_processes()
    return {
        "as_of": _iso(now),
        "processes": processes,
        "waiting": m3_waiting(m1, m3) + pending_packets() + code_items(now),
        "automatic": [cycle_job(events, cycles, now), *milestone_items(m1, m3),
                      health_item(now), books_item(base)],
        "research_running": [p for p in processes if p["flow"] == "research"],
        "audit": {"error": audit_error, "health": audit_health or "OK"},
        "entries_locked": _entries_lock(events),
    }


def _entries_lock(events: list[dict]) -> str | None:
    for event in reversed(events):
        if event["event"] == "entries_locked_no_promoted_runtime":
            return str((event.get("details") or {}).get("reason", ""))[:300]
        if event["event"] == "operational_track_active":
            return (event.get("details") or {}).get("entries_blocked")
    return None


# ── Done ────────────────────────────────────────────────────────────────────


def _first_date(text: str) -> str | None:
    match = re.search(r"\d{4}-\d{2}-\d{2}", text)
    return match[0] if match else None


def _outcome(text: str) -> str | None:
    """The first status label ``text`` states, as written (``RESEARCH_ABSTAIN``).

    Callers pass only a heading or a status line, never free prose.
    """
    for match in re.finditer(
        r"\b(?:[A-Z]{2,}(?:_[A-Z0-9]+)+|" + "|".join(OUTCOME_TOKENS) + r")\b", text
    ):
        # A file or protocol name in capitals is not a verdict.
        if any(word in match[0] for word in VERDICT_WORDS):
            return match[0]
    return None


def _first_sentence(text: str) -> str:
    return re.split(r"(?<=[.!?])\s|\n\n", text.strip(), maxsplit=1)[0]


def merged_pull_requests(limit: int = 30) -> list[dict]:
    log = _git("log", "origin/master", "--first-parent", "--merges",
               f"-n{limit}", "--format=%cI%x1f%s%x1f%b%x1e")
    entries = []
    for record in (log or "").split("\x1e"):
        parts = record.strip().split("\x1f")
        if len(parts) < 2:
            continue
        match = re.match(r"Merge pull request #(\d+)", parts[1])
        if not match:
            continue
        title = next((line.strip() for line in (parts[2] if len(parts) > 2 else "").splitlines()
                      if line.strip()), parts[1])
        entries.append({"date": parts[0][:10], "flow": "code", "title": f"PR #{match[1]}: {title}",
                        "outcome": "MERGED", "detail": "", "source": f"origin/master · PR #{match[1]}"})
    return entries


def incidents() -> list[dict]:
    plan = root() / "docs" / "operations" / "OPERABLE_SYSTEM_PLAN.md"
    if not plan.is_file():
        return []
    text = plan.read_text(encoding="utf-8")
    table = text.split("## Incident log", 1)[-1] if "## Incident log" in text else ""
    entries = []
    for line in table.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) != 4 or not _first_date(cells[0]):
            continue
        entries.append({"date": _first_date(cells[0]), "flow": "operations", "title": cells[1],
                        "outcome": "FIXED", "detail": f"Nguyên nhân: {cells[2]} · Sửa: {cells[3]}",
                        "source": "docs/operations/OPERABLE_SYSTEM_PLAN.md"})
    return entries


def campaign_entries(limit: int = 30) -> list[dict]:
    tracker = root() / "docs" / "CAMPAIGN_STATUS.md"
    if not tracker.is_file():
        return []
    entries = []
    blocks = re.split(r"^### ", tracker.read_text(encoding="utf-8"), flags=re.M)[1:]
    for block in blocks:
        heading, _, body = block.partition("\n")
        date = _first_date(heading)
        if not date:
            continue
        title = re.sub(r"^\d{4}-\d{2}-\d{2}\s*[—-]\s*", "", heading).strip()
        # The label is the one the heading or the entry's first sentence states,
        # not any token the body happens to mention.
        entries.append({"date": date, "flow": "research", "title": title,
                        "outcome": _outcome(heading) or _outcome(_first_sentence(body)),
                        "detail": " ".join(body.split())[:280], "source": "docs/CAMPAIGN_STATUS.md"})
        if len(entries) >= limit:
            break
    return entries


def evidence_cards() -> list[dict]:
    entries = []
    for card in sorted((root() / "docs" / "operations").glob("*_EVIDENCE_CARD.md")):
        text = card.read_text(encoding="utf-8")
        status = next((line for line in text.splitlines() if line.startswith("Status:")), "")
        date = (_git("log", "-1", "--format=%cs", "--", str(card)) or "").strip() or _mtime(card).date().isoformat()
        entries.append({"date": date, "flow": "research", "title": card.stem.replace("_", " ").title(),
                        "outcome": _outcome(status), "detail": status.removeprefix("Status:").strip()[:280],
                        "source": str(card.relative_to(root()))})
    return entries


def done(now: datetime | None = None) -> dict:
    now = now or datetime.now(UTC)
    entries = merged_pull_requests() + incidents() + campaign_entries() + evidence_cards()
    entries.sort(key=lambda e: e["date"], reverse=True)
    return {"as_of": _iso(now), "entries": entries}


# ── Have ────────────────────────────────────────────────────────────────────


def runner_packages() -> set[str] | None:
    """trading_agent packages the testnet runner loads (measured, not declared)."""
    probe = (
        "import importlib.util,json,sys;"
        "s=importlib.util.spec_from_file_location('r','scripts/live_enhanced_ma_binance.py');"
        "m=importlib.util.module_from_spec(s);s.loader.exec_module(m);"
        "print(json.dumps(sorted({n.split('.')[1] for n in sys.modules"
        " if n.startswith('trading_agent.') and n.count('.')>=1})))"
    )
    base = root()
    try:
        done = subprocess.run(
            [sys.executable, "-c", probe], cwd=base, capture_output=True, text=True, timeout=120,
            env={**os.environ, "PYTHONPATH": f"{base / 'src'}:{base / 'scripts'}"}, check=False,
        )
        return set(json.loads(done.stdout.strip().splitlines()[-1])) if done.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired, ValueError, IndexError):
        return None


def packages() -> list[dict]:
    loaded = runner_packages()
    rows = []
    for directory in sorted((root() / "src" / "trading_agent").iterdir()):
        if not directory.is_dir() or directory.name.startswith("__"):
            continue
        files = sum(1 for _ in directory.rglob("*.py"))
        if directory.name == "strategies":
            role = "strategies"
        elif directory.name in UNUSED_PACKAGES:
            role = "unused"
        elif directory.name in RESEARCH_PACKAGES:
            role = "research"
        elif loaded is None:
            role = "unknown"
        else:
            role = "core" if directory.name in loaded else "outside"
        rows.append({"name": directory.name, "files": files, "role": role,
                     "runner_loads": None if loaded is None else directory.name in loaded})
    return rows


def strategy_ids() -> list[str]:
    source = root() / "src" / "trading_agent" / "strategies" / "canonical" / "candidates.py"
    if not source.is_file():
        return []
    for node in ast.walk(ast.parse(source.read_text(encoding="utf-8"))):
        target = getattr(node, "target", None) or (getattr(node, "targets", [None]) or [None])[0]
        if isinstance(node, (ast.Assign, ast.AnnAssign)) and getattr(target, "id", "") == "_PARAM_SCHEMAS" \
                and isinstance(node.value, ast.Dict):
            return sorted(k.value for k in node.value.keys if isinstance(k, ast.Constant))
    return []


def _packet_date(directory: Path) -> datetime:
    """The date in the packet's name (``..._20261029_01``); its mtime otherwise."""
    match = re.search(r"_(20\d{6})(?:_\d{2})?$", directory.name)
    if match:
        return datetime.strptime(match[1], "%Y%m%d").replace(tzinfo=UTC)
    return _mtime(directory)


def _packet_outcome(directory: Path) -> str | None:
    """A verdict the packet states in a structured field, never a word in its prose."""
    for name in ("CLOSEOUT.json", "summary.json", "protocol.json"):
        document = _read_json(directory / name)
        if not isinstance(document, dict):
            continue
        approval = document.get("approval")
        if isinstance(approval, dict) and approval.get("approval_state"):
            return str(approval["approval_state"])
        for key in ("verdict", "economic_verdict", "decision", "outcome", "status"):
            value = document.get(key)
            if isinstance(value, str) and value.isupper():
                return value
    return None


def research_packets(now: datetime) -> dict:
    markers = ("protocol.json", "RESULTS.md", "summary.json", "CLOSEOUT.json")
    packets, with_results = [], 0
    for directory in (root() / "data").iterdir() if (root() / "data").is_dir() else []:
        if not directory.is_dir() or not any((directory / m).is_file() for m in markers):
            continue
        has_results = any((directory / m).is_file() for m in ("RESULTS.md", "CLOSEOUT.json", "summary.json"))
        with_results += has_results
        packets.append((_packet_date(directory), directory.name, directory, has_results))
    packets.sort(key=lambda p: (p[0], p[1]), reverse=True)
    newest = [{"name": directory.name, "date": _iso(when), "has_results": has_results,
               "outcome": _packet_outcome(directory)}
              for when, _, directory, has_results in packets[:8]]
    return {"total": len(packets), "with_results": with_results,
            "last_7_days": sum(1 for p in packets if now - p[0] <= timedelta(days=7)),
            "newest": newest, "source": "data/*/"}


def environments(now: datetime) -> list[dict]:
    cycles = daily_check.wrapper_cycles(root() / CYCLE_LOG, now - timedelta(hours=6))
    cron_alive = any((p / "comm").read_text().strip() == "cron"
                     for p in Path("/proc").iterdir() if p.name.isdigit() and (p / "comm").exists())
    testnet = ("scheduled" if cycles and now - cycles[-1]["start"] <= daily_check.GAP_LIMIT
               else "late")
    cron_state = "scheduled" if cron_alive else "code_only"
    cron_note = "cron đang chạy" if cron_alive else "cần cron, mà cron daemon không chạy"
    return [
        {"name": "Binance testnet", "state": testnet,
         "detail": "Windows Task Scheduler, 2 h/lần", "source": CYCLE_LOG},
        {"name": "Health check", "state": health_item(now)["state"],
         "detail": "Windows, 30 phút/lần", "source": HEALTH_LOG_GLOB},
        {"name": "Alpaca paper", "state": cron_state, "detail": cron_note,
         "source": "docs/operations/CORE_RUNTIME_BOUNDARY.md"},
        {"name": "Soak tracker, báo cáo ngày", "state": cron_state, "detail": cron_note,
         "source": "docs/operations/CORE_RUNTIME_BOUNDARY.md"},
        {"name": "Mainnet", "state": "locked", "detail": "NO-GO",
         "source": "docs/operations/OPERABLE_SYSTEM_PLAN.md"},
    ]


def have(now: datetime | None = None) -> dict:
    now = now or datetime.now(UTC)
    promotions = root() / "data" / "promotion_store" / "promotions.db"
    rows = packages()
    return {
        "as_of": _iso(now),
        "packages": rows,
        "code_files": sum(r["files"] for r in rows),
        "strategies": {"ids": strategy_ids(), "promotion_store": promotions.is_file(),
                       "source": "src/trading_agent/strategies/canonical/candidates.py"},
        "research": research_packets(now),
        "environments": environments(now),
    }


SECTIONS = {"doing": doing, "done": done, "have": have}


def to_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, default=str, indent=1)


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    if len(args) != 1 or args[0] not in SECTIONS:
        print(f"usage: workspace_status.py {'|'.join(SECTIONS)}", file=sys.stderr)
        return 2
    print(to_json(SECTIONS[args[0]]()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
