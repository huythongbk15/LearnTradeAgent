#!/usr/bin/env python3
"""
QwenPaw Agent: Health check & metrics endpoint.
Can be called via HTTP (if server) or CLI for monitoring.
"""
import sys
import json
import time
import psutil
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from process_registry import list_active, list_recent

def health_check() -> dict:
    """Comprehensive health check for QwenPaw agent."""
    checks = {}
    overall = "healthy"
    
    # 1. Process registry health
    active = list_active()
    stale_count = sum(1 for p in active if time.time() - p.heartbeat_at > 300)  # 5min no heartbeat
    checks["process_registry"] = {
        "status": "degraded" if stale_count > 0 else "healthy",
        "active_processes": len(active),
        "stale_processes": stale_count,
        "details": [{"pid": p.pid, "age_sec": int(time.time()-p.started_at), 
                     "hb_age_sec": int(time.time()-p.heartbeat_at), "cmd": p.cmd[:60]} 
                    for p in active[:10]]
    }
    if stale_count > 0:
        overall = "degraded"
    
    # 2. Disk space
    data_dir = Path(__file__).parent.parent.parent / "data"
    disk = psutil.disk_usage(str(data_dir))
    disk_pct = disk.used / disk.total * 100
    checks["disk"] = {
        "status": "critical" if disk_pct > 90 else "warning" if disk_pct > 80 else "healthy",
        "used_gb": round(disk.used / 1e9, 2),
        "free_gb": round(disk.free / 1e9, 2),
        "pct_used": round(disk_pct, 1)
    }
    if disk_pct > 90:
        overall = "critical"
    elif disk_pct > 80:
        overall = "degraded"
    
    # 3. Memory (current process)
    mem = psutil.Process().memory_info()
    checks["memory"] = {
        "status": "healthy",
        "rss_mb": round(mem.rss / 1e6, 1),
        "vms_mb": round(mem.vms / 1e6, 1)
    }
    
    # 4. QwenPaw main process
    main_proc = None
    for proc in psutil.process_iter(['pid', 'name', 'cmdline', 'create_time']):
        try:
            if 'qwenpaw' in ' '.join(proc.info['cmdline'] or []).lower() and 'app' in ' '.join(proc.info['cmdline'] or []):
                main_proc = proc.info
                break
        except Exception:
            pass
    
    checks["qwenpaw_main"] = {
        "status": "healthy" if main_proc else "warning",
        "pid": main_proc['pid'] if main_proc else None,
        "uptime_sec": int(time.time() - main_proc['create_time']) if main_proc else None
    }
    if not main_proc:
        overall = "degraded"
    
    # 5. Recent task success rate
    recent = list_recent(50)
    completed = sum(1 for r in recent if r.status == 'completed')
    failed = sum(1 for r in recent if r.status in ('failed', 'timeout', 'killed'))
    total = len(recent)
    success_rate = completed / total * 100 if total > 0 else 100
    checks["task_success_rate"] = {
        "status": "critical" if success_rate < 50 else "warning" if success_rate < 80 else "healthy",
        "recent_total": total,
        "completed": completed,
        "failed": failed,
        "success_rate_pct": round(success_rate, 1)
    }
    if success_rate < 50:
        overall = "critical"
    elif success_rate < 80:
        overall = "degraded"
    
    return {
        "overall": overall,
        "timestamp": time.time(),
        "checks": checks
    }

def format_nagios(result: dict) -> str:
    """Format for Nagios/Icinga/Prometheus alerting."""
    status_map = {"healthy": 0, "degraded": 1, "critical": 2}
    code = status_map.get(result["overall"], 3)
    msg = f"QwenPaw Agent {result['overall'].upper()}: "
    for name, check in result["checks"].items():
        msg += f"{name}={check['status']} "
    return f"{code} {msg.strip()}"

# CLI
if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--format", choices=["json", "nagios", "prometheus"], default="json")
    parser.add_argument("--output", type=str)
    args = parser.parse_args()
    
    result = health_check()
    
    if args.format == "json":
        out = json.dumps(result, indent=2)
    elif args.format == "nagios":
        out = format_nagios(result)
    else:  # prometheus
        lines = [f'qwenpaw_health_overall{{status="{result["overall"]}"}} 1']
        for name, check in result["checks"].items():
            for k, v in check.items():
                if isinstance(v, (int, float)):
                    lines.append(f'qwenpaw_{name}_{k} {v}')
        out = "\n".join(lines)
    
    if args.output:
        with open(args.output, "w") as f:
            f.write(out)
    else:
        print(out)
    
    sys.exit({"healthy": 0, "degraded": 1, "critical": 2}.get(result["overall"], 3))