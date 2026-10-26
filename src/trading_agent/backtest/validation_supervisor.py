"""Linux validation-worker resource supervision; no data/strategy authority.

Launcher adapters supply process-group collaborators at call time.
"""

import os
from pathlib import Path
import signal
import subprocess
import sys
import time


def _validation_group_rss_mb(group):
    """Linux RSS sum of the owned process session, including spawned cells."""
    rss_kb = 0
    for path in Path("/proc").iterdir():
        if not path.name.isdigit():
            continue
        try:
            stat = (path / "stat").read_text().rsplit(")", 1)[1].split()
            if int(stat[2]) == group:  # state, ppid, pgrp
                status = (path / "status").read_text().splitlines()
                rss_kb += next((int(line.split()[1]) for line in status if line.startswith("VmRSS:")), 0)
        except (FileNotFoundError, ProcessLookupError):
            continue
    return rss_kb / 1024


def _validation_group_pids(group):
    """Return all PIDs in the process group, including zombies."""
    pids = []
    for path in Path("/proc").iterdir():
        if not path.name.isdigit():
            continue
        try:
            stat = (path / "stat").read_text().rsplit(")", 1)[1].split()
            if int(stat[2]) == group:
                pids.append(int(path.name))
        except (FileNotFoundError, ProcessLookupError):
            continue
    return pids


def _wait_for_all_reaped(group, deadline, *, group_pids):
    """Wait until all processes in group are reaped (no PIDs exist)."""
    while group_pids(group):
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.01)
    return True


def _supervise_validation(
    command, out_root, *, total_timeout_seconds, max_rss_mb, cwd,
    group_rss_mb, wait_for_all_reaped, group_pids,
):
    if not sys.platform.startswith("linux"):
        raise RuntimeError("validation budget supervision requires Linux")
    started = time.monotonic()
    peak = 0.0
    status = None
    with (out_root / "worker_stdout.log").open("x") as stdout, (out_root / "worker_stderr.log").open("x") as stderr:
        process = subprocess.Popen(command, cwd=cwd, stdout=stdout, stderr=stderr, start_new_session=True)
        try:
            while process.poll() is None:
                peak = max(peak, group_rss_mb(process.pid))
                if time.monotonic() - started >= total_timeout_seconds:
                    status = "TIMEOUT"
                    break
                if peak > max_rss_mb:
                    status = "MEMORY_LIMIT"
                    break
                time.sleep(0.05)
        finally:
            # Always clean our session, even if the leader exited first.
            cleanup_deadline = time.monotonic() + 5
            for sig in (signal.SIGTERM, signal.SIGKILL):
                try:
                    os.killpg(process.pid, sig)
                except ProcessLookupError:
                    break
                if sig == signal.SIGTERM:
                    time.sleep(0.2)
            process.wait(timeout=max(0, cleanup_deadline - time.monotonic()))
            # Wait for ALL processes in group to be reaped (including zombies)
            if not wait_for_all_reaped(process.pid, cleanup_deadline):
                remaining = group_pids(process.pid)
                raise RuntimeError(f"owned validation session did not stop within cleanup grace; remaining PIDs: {remaining}")
    return {"status": status or ("COMPLETED" if process.returncode == 0 else "WORKER_ERROR"),
            "worker_exit_code": process.returncode, "elapsed_seconds": time.monotonic() - started,
            "sampled_group_peak_rss_mb": peak, "rss_sample_interval_seconds": 0.05,
            "budget_enforcement": "owned_session_wall_deadline_and_sampled_RSS_not_hard_allocator_cap"}
