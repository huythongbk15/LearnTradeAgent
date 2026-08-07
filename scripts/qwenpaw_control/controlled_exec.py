#!/usr/bin/env python3
"""
QwenPaw Agent: Controlled subprocess execution with timeout, heartbeat, structured result.
Replaces raw execute_shell_command for long-running commands.
"""
import subprocess
import json
import time
import sys
import os
import threading
from pathlib import Path
from dataclasses import dataclass, asdict
from typing import Optional, List

# Import registry
sys.path.insert(0, str(Path(__file__).parent))
from process_registry import register, heartbeat, complete

@dataclass
class ExecResult:
    status: str          # ok, failed, timeout, killed, interrupted
    rc: int
    elapsed_sec: float
    stdout_tail: List[str]
    stderr_tail: List[str]
    result_file: str = ""
    error: str = ""

class ControlledExec:
    def __init__(self, workspace: Path, timeout_sec: int = 3600, heartbeat_sec: int = 30):
        self.workspace = workspace
        self.timeout_sec = timeout_sec
        self.heartbeat_sec = heartbeat_sec
        self._proc: Optional[subprocess.Popen] = None
        self._start_time = 0.0
        self._last_heartbeat = 0.0
        self._output_lines: List[str] = []
        self._error_lines: List[str] = []
        self._lock = threading.Lock()
    
    def run(self, cmd: List[str], env: dict = None, cwd: Path = None, 
            result_file: str = None) -> ExecResult:
        """Run command with control."""
        cwd = cwd or self.workspace
        env = {**os.environ, "PYTHONUNBUFFERED": "1", **(env or {})}
        
        self._proc = subprocess.Popen(
            cmd, cwd=cwd, env=env,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, bufsize=1
        )
        self._start_time = time.time()
        self._last_heartbeat = self._start_time
        self._output_lines.clear()
        self._error_lines.clear()
        
        # Register in process registry
        meta = {"type": "shell", "cmd": " ".join(cmd[:5])}
        registry_pid = self._proc.pid
        register(registry_pid, cmd, meta)
        
        # Start reader threads
        stdout_thread = threading.Thread(target=self._read_stream, args=(self._proc.stdout, "stdout"))
        stderr_thread = threading.Thread(target=self._read_stream, args=(self._proc.stderr, "stderr"))
        stdout_thread.start()
        stderr_thread.start()
        
        try:
            while True:
                if time.time() - self._start_time > self.timeout_sec:
                    self._terminate("timeout")
                    complete(registry_pid, "timeout")
                    return ExecResult("timeout", -1, time.time()-self._start_time,
                        self._output_lines[-50:], self._error_lines[-50:],
                        error=f"Timeout after {self.timeout_sec}s")
                
                # Heartbeat
                if time.time() - self._last_heartbeat > self.heartbeat_sec:
                    elapsed = int(time.time() - self._start_time)
                    print(f"[QWENPAW HEARTBEAT] PID={self._proc.pid} elapsed={elapsed}s cmd={' '.join(cmd[:3])}...", flush=True)
                    heartbeat(registry_pid)
                    self._last_heartbeat = time.time()
                
                if self._proc.poll() is not None:
                    break
                time.sleep(0.1)
            
            stdout_thread.join(timeout=2)
            stderr_thread.join(timeout=2)
            rc = self._proc.wait()
            
            # Write result file if requested
            result_path = ""
            if result_file:
                result_path = str(cwd / result_file)
                with open(result_path, "w") as f:
                    json.dump({
                        "cmd": cmd, "rc": rc, "elapsed_sec": time.time()-self._start_time,
                        "stdout": self._output_lines, "stderr": self._error_lines
                    }, f, indent=2)
            
            complete(registry_pid, "completed" if rc == 0 else "failed", result_path)
            return ExecResult("ok" if rc == 0 else "failed", rc, time.time()-self._start_time,
                self._output_lines[-100:], self._error_lines[-100:], result_path)
                
        except KeyboardInterrupt:
            self._terminate("interrupted")
            complete(registry_pid, "interrupted")
            return ExecResult("interrupted", -1, time.time()-self._start_time,
                self._output_lines[-50:], self._error_lines[-50:], error="Interrupted")
        except Exception as e:
            complete(registry_pid, "failed")
            return ExecResult("failed", -1, time.time()-self._start_time,
                self._output_lines[-50:], self._error_lines[-50:], error=str(e))
    
    def _read_stream(self, stream, name: str):
        target = self._output_lines if name == "stdout" else self._error_lines
        for line in stream:
            with self._lock:
                target.append(line.rstrip())
    
    def _terminate(self, reason: str):
        if self._proc and self._proc.poll() is None:
            print(f"[QWENPAW] Terminating PID={self._proc.pid} reason={reason}", flush=True)
            self._proc.terminate()
            try:
                self._proc.wait(timeout=10)
            except Exception:
                self._proc.kill()
                self._proc.wait()

# CLI for direct use
if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--timeout", type=int, default=3600)
    parser.add_argument("--heartbeat", type=int, default=30)
    parser.add_argument("--result-file", type=str)
    parser.add_argument("--cwd", type=str)
    parser.add_argument("cmd", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    
    # Handle -- separator
    if args.cmd and args.cmd[0] == "--":
        args.cmd = args.cmd[1:]
    
    if not args.cmd:
        parser.error("No command provided")
    
    ws = Path(args.cwd) if args.cwd else Path.cwd()
    exec = ControlledExec(ws, args.timeout, args.heartbeat)
    result = exec.run(args.cmd, result_file=args.result_file)
    print(f"\n=== QWENPAW_EXEC_RESULT ==={json.dumps(asdict(result))}", flush=True)
    sys.exit(0 if result.status == "ok" else 1)