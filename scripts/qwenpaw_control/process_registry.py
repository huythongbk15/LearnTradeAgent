#!/usr/bin/env python3
"""
QwenPaw Agent: Process Registry - track all spawned subprocesses and subagents.
SQLite-backed, survives restarts, CLI for inspection.
"""
import sqlite3
import json
import time
import os
import signal
from pathlib import Path
from dataclasses import dataclass, asdict
from typing import Optional, List, Dict
from contextlib import contextmanager

DB_PATH = Path(__file__).parent.parent.parent / "data" / "qwenpaw_process_registry.db"
DB_PATH.parent.mkdir(exist_ok=True)

@dataclass
class ProcessRecord:
    pid: int
    cmd: str
    started_at: float
    status: str          # running, completed, failed, timeout, killed, stale_killed
    result_file: str = ""
    heartbeat_at: float = 0
    meta: str = ""       # JSON: {"type": "shell|subagent|cron|browser", "agent_id": "", "session_id": ""}

def _init_db():
    with sqlite3.connect(DB_PATH) as con:
        con.execute("""CREATE TABLE IF NOT EXISTS qwenpaw_processes (
            pid INTEGER PRIMARY KEY,
            cmd TEXT NOT NULL,
            started_at REAL NOT NULL,
            status TEXT NOT NULL,
            result_file TEXT DEFAULT '',
            heartbeat_at REAL NOT NULL,
            meta TEXT DEFAULT '{}'
        )""")
        con.execute("CREATE INDEX IF NOT EXISTS idx_status ON qwenpaw_processes(status)")
        con.execute("CREATE INDEX IF NOT EXISTS idx_heartbeat ON qwenpaw_processes(heartbeat_at)")

@contextmanager
def _db():
    _init_db()
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    try:
        yield con
    finally:
        con.close()

def register(pid: int, cmd: List[str], meta: Dict = None) -> ProcessRecord:
    """Register a new spawned process."""
    record = ProcessRecord(
        pid=pid, cmd=" ".join(cmd), started_at=time.time(),
        status="running", heartbeat_at=time.time(), meta=json.dumps(meta or {})
    )
    with _db() as con:
        con.execute("""INSERT OR REPLACE INTO qwenpaw_processes 
            (pid, cmd, started_at, status, result_file, heartbeat_at, meta)
            VALUES (?,?,?,?,?,?,?)""",
            (record.pid, record.cmd, record.started_at, record.status,
             record.result_file, record.heartbeat_at, record.meta))
        con.commit()
    print(f"[REGISTRY] Registered PID={pid} cmd={record.cmd[:80]}", flush=True)
    return record

def heartbeat(pid: int):
    """Update heartbeat for a running process."""
    with _db() as con:
        con.execute("UPDATE qwenpaw_processes SET heartbeat_at=? WHERE pid=?", (time.time(), pid))
        con.commit()

def complete(pid: int, status: str, result_file: str = ""):
    """Mark process as completed."""
    with _db() as con:
        con.execute("UPDATE qwenpaw_processes SET status=?, result_file=? WHERE pid=?",
            (status, result_file, pid))
        con.commit()
    print(f"[REGISTRY] PID={pid} status={status} result={result_file}", flush=True)

def get(pid: int) -> Optional[ProcessRecord]:
    with _db() as con:
        row = con.execute("SELECT * FROM qwenpaw_processes WHERE pid=?", (pid,)).fetchone()
        if row:
            return ProcessRecord(**dict(row))
    return None

def list_active() -> List[ProcessRecord]:
    with _db() as con:
        rows = con.execute("SELECT * FROM qwenpaw_processes WHERE status='running' ORDER BY started_at").fetchall()
        return [ProcessRecord(**dict(r)) for r in rows]

def list_recent(limit: int = 20) -> List[ProcessRecord]:
    with _db() as con:
        rows = con.execute("SELECT * FROM qwenpaw_processes ORDER BY started_at DESC LIMIT ?", (limit,)).fetchall()
        return [ProcessRecord(**dict(r)) for r in rows]

def cleanup_stale(max_age_sec: int = 14400, kill: bool = True) -> int:
    """Find and optionally kill processes with no heartbeat > max_age_sec."""
    now = time.time()
    killed = 0
    with _db() as con:
        rows = con.execute("SELECT pid, cmd FROM qwenpaw_processes WHERE status='running'").fetchall()
        for row in rows:
            pid, cmd = row
            hb_row = con.execute("SELECT heartbeat_at FROM qwenpaw_processes WHERE pid=?", (pid,)).fetchone()
            if hb_row and now - hb_row[0] > max_age_sec:
                if kill:
                    try:
                        os.kill(pid, signal.SIGTERM)
                        time.sleep(2)
                        if os.path.exists(f"/proc/{pid}"):
                            os.kill(pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                complete(pid, "stale_killed")
                killed += 1
                print(f"[REGISTRY] Stale killed PID={pid} cmd={cmd[:60]}", flush=True)
    return killed

# CLI
if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: python process_registry.py [list|recent|cleanup|get <pid>]")
        sys.exit(1)
    
    cmd = sys.argv[1]
    if cmd == "list":
        for r in list_active():
            age = int(time.time() - r.started_at)
            hb_age = int(time.time() - r.heartbeat_at)
            meta = json.loads(r.meta) if r.meta else {}
            print(f"PID={r.pid:6d} | age={age:5d}s | hb_age={hb_age:4d}s | type={meta.get('type','?')} | {r.cmd[:100]}")
    elif cmd == "recent":
        for r in list_recent(20):
            age = int(time.time() - r.started_at)
            print(f"PID={r.pid:6d} | {time.ctime(r.started_at)} | {r.status:12s} | {r.cmd[:80]}")
    elif cmd == "cleanup":
        n = cleanup_stale()
        print(f"Cleaned up {n} stale processes")
    elif cmd == "get" and len(sys.argv) > 2:
        pid = int(sys.argv[2])
        r = get(pid)
        if r:
            print(json.dumps(asdict(r), indent=2, default=str))
        else:
            print(f"PID {pid} not found")
    else:
        print("Unknown command")