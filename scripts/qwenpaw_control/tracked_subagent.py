#!/usr/bin/env python3
"""
QwenPaw Agent: Tracked subagent spawning with registry integration.
Wraps spawn_subagent to auto-register, heartbeat, and capture results.
"""

import json
import time
import sys
from pathlib import Path
from typing import Optional, Dict, Any

# Add parent to path for process_registry
sys.path.insert(0, str(Path(__file__).parent))
from process_registry import register, heartbeat, complete


class TrackedSubagent:
    """Wrapper for QwenPaw spawn_subagent with full lifecycle tracking."""

    def __init__(self, agent_id: str = "trading"):
        self.agent_id = agent_id
        self.task_id: Optional[str] = None
        self.registry_pid: Optional[int] = None  # We track via task_id, not PID

    def spawn(
        self,
        task: str,
        timeout: int = 600,
        allowed_tools: list = None,
        skills: list = None,
        background: bool = False,
        fork: bool = False,
    ) -> Dict[str, Any]:
        """
        Spawn a tracked subagent.
        Returns: {"task_id": str, "status": "submitted|running|completed|failed", "result": ...}
        """
        # Import here to avoid circular
        from qwenpaw.agents.api import spawn_subagent as qwenpaw_spawn

        # Build meta for registry
        meta = {
            "type": "subagent",
            "agent_id": self.agent_id,
            "task": task[:200],
            "timeout": timeout,
            "background": background,
            "fork": fork,
        }

        # Register in our registry (use negative PID to avoid collision)
        registry_pid = -int(time.time() * 1000) % 1000000
        self.registry_pid = registry_pid
        register(registry_pid, ["subagent", task[:50]], meta)

        try:
            # Call QwenPaw spawn_subagent
            result = qwenpaw_spawn(
                task=task,
                timeout=timeout,
                allowed_tools=allowed_tools,
                skills=skills,
                background=background,
                fork=fork,
            )

            # Extract task_id from result
            if isinstance(result, dict):
                self.task_id = result.get("task_id") or result.get("id")
            else:
                self.task_id = str(result)[:50]

            # Update registry
            complete(
                registry_pid, "submitted", result_file=f"subagent_{self.task_id}.json"
            )

            # Save submission result
            self._save_result(
                {"submitted": True, "task_id": self.task_id, "meta": meta}
            )

            return {
                "status": "submitted",
                "task_id": self.task_id,
                "registry_pid": registry_pid,
            }

        except Exception as e:
            complete(registry_pid, "failed", error=str(e))
            return {"status": "failed", "error": str(e), "registry_pid": registry_pid}

    def wait(
        self, task_id: str = None, poll_interval: int = 10, max_wait: int = 3600
    ) -> Dict[str, Any]:
        """Wait for background subagent to complete."""
        from qwenpaw.agents.api import check_agent_task

        task_id = task_id or self.task_id
        if not task_id:
            return {"status": "failed", "error": "No task_id"}

        start = time.time()
        while time.time() - start < max_wait:
            # Heartbeat
            if self.registry_pid:
                heartbeat(self.registry_pid)

            try:
                result = check_agent_task(task_id)
                if result.get("status") in ("completed", "failed", "error"):
                    # Save final result
                    self._save_result(result)
                    if self.registry_pid:
                        complete(
                            self.registry_pid,
                            result.get("status", "completed"),
                            result_file=f"subagent_{task_id}.json",
                        )
                    return result
            except Exception as e:
                pass

            time.sleep(poll_interval)

        return {"status": "timeout", "task_id": task_id}

    def _save_result(self, result: Dict):
        """Save result to file for later inspection."""
        out_dir = Path(__file__).parent.parent.parent / "data" / "subagent_results"
        out_dir.mkdir(exist_ok=True)
        fname = (
            out_dir / f"subagent_{self.task_id or 'unknown'}_{int(time.time())}.json"
        )
        with open(fname, "w") as f:
            json.dump(result, f, indent=2, default=str)


# Convenience function for direct use
def spawn_tracked_subagent(task: str, **kwargs) -> Dict:
    """One-liner to spawn and optionally wait."""
    tracker = TrackedSubagent()
    submit_result = tracker.spawn(task, **kwargs)
    if kwargs.get("background", False):
        return submit_result
    if submit_result.get("status") == "submitted":
        return tracker.wait(submit_result["task_id"])
    return submit_result


# CLI
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("task")
    parser.add_argument("--timeout", type=int, default=600)
    parser.add_argument("--background", action="store_true")
    parser.add_argument("--wait", action="store_true")
    parser.add_argument("--allowed-tools", type=str)
    parser.add_argument("--skills", type=str)
    args = parser.parse_args()

    tracker = TrackedSubagent()
    result = tracker.spawn(
        args.task,
        timeout=args.timeout,
        background=args.background,
        allowed_tools=args.allowed_tools.split(",") if args.allowed_tools else None,
        skills=args.skills.split(",") if args.skills else None,
    )

    if args.wait and result.get("status") == "submitted":
        result = tracker.wait(result["task_id"])

    print(json.dumps(result, indent=2, default=str))
