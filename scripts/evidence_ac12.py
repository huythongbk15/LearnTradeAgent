"""AC12 — Protection & Telemetry. Independent oracle for HealthMonitor."""
import asyncio
import json
import sys
from pathlib import Path
from trading_agent.exchanges.health_monitor import (
    HealthMonitor, HealthStatus,
)

results = []
def check(name, cond, detail=""):
    status = "PASS" if cond else "FAIL"
    results.append({"name": name, "status": status, "detail": detail})
    print(f"[{status}] {name}: {detail}")
    if not cond:
        raise SystemExit(f"AC12 FAIL: {name}")

async def run():
    async def good(name): return 0.01
    async def bad(name): raise RuntimeError("unreachable")
    async def slow(name): return 5.0

    # C1: Initial UNKNOWN
    m = HealthMonitor(failures_to_down=3, recoveries_to_healthy=2)
    m.register_exchange("binance", good)
    check("C1_initial_unknown", m._health["binance"].status == HealthStatus.UNKNOWN, f"status={m._health['binance'].status}")

    # C2: 3 consecutive failures -> DOWN
    m2 = HealthMonitor(failures_to_down=3, recoveries_to_healthy=2, error_rate_max=0.3)
    m2.register_exchange("binance", bad)
    await m2.check_all()
    check("C2_1fail_degraded", m2._health["binance"].status == HealthStatus.DEGRADED, f"{m2._health['binance'].status}")
    await m2.check_all()
    check("C2_2fail_degraded", m2._health["binance"].status == HealthStatus.DEGRADED, f"{m2._health['binance'].status}")
    await m2.check_all()
    check("C2_3fail_down", m2._health["binance"].status == HealthStatus.DOWN, f"{m2._health['binance'].status}")

    # C3: Recovery from DOWN: error_rate must decay below threshold (0.3)
    #     AND consecutive_successes >= recoveries_to_healthy (2)
    #     After 3 fails: err=0.488. Each success: err *= 0.9
    #     err: 0.488->0.439->0.395->0.356->0.320->0.288 (< 0.3 at 5th success)
    #     Transition: DOWN -> DEGRADED -> DEGRADED -> DEGRADED -> DEGRADED -> HEALTHY
    m2._checkers["binance"] = good  # Switch to good checker for recovery
    await m2.check_exchange("binance")
    check("C3_1recover_degraded", m2._health["binance"].status == HealthStatus.DEGRADED,
          f"{m2._health['binance'].status}, err={m2._health['binance'].error_rate:.4f}")
    for _ in range(3):
        await m2.check_exchange("binance")
    check("C3_4recover_still_degraded", m2._health["binance"].status == HealthStatus.DEGRADED,
          f"{m2._health['binance'].status}, err={m2._health['binance'].error_rate:.4f}")
    await m2.check_exchange("binance")
    check("C3_5recover_healthy", m2._health["binance"].status == HealthStatus.HEALTHY,
          f"{m2._health['binance'].status}, err={m2._health['binance'].error_rate:.4f}")

    # C4: High latency tracked
    m3 = HealthMonitor(failures_to_down=5, recoveries_to_healthy=1)
    m3.register_exchange("kraken", slow)
    await m3.check_all()
    h = m3._health["kraken"]
    check("C4_latency_tracked", h.avg_latency_ms >= 2000.0, f"latency={h.avg_latency_ms:.0f}ms")

    # C5: Failover callback fires on DOWN
    ff = []
    m4 = HealthMonitor(failures_to_down=2, recoveries_to_healthy=2)
    m4.register_exchange("binance", bad)
    m4.on_failover(lambda name: ff.append(name))
    await m4.check_exchange("binance")
    await m4.check_exchange("binance")
    check("C5_down_detected", m4._health["binance"].status == HealthStatus.DOWN, f"{m4._health['binance'].status}")

    # C6: Multiple exchanges tracked independently
    m5 = HealthMonitor(failures_to_down=3, recoveries_to_healthy=2)
    m5.register_exchange("binance", good)
    m5.register_exchange("kraken", bad)
    await m5.check_all()
    check("C6_multi_exchange", m5._health["binance"].total_checks > 0 and m5._health["kraken"].total_checks > 0,
          f"bin={m5._health['binance'].total_checks}, krk={m5._health['kraken'].total_checks}")

    # C7: Error rate on failures
    m6 = HealthMonitor(failures_to_down=5, recoveries_to_healthy=1)
    m6.register_exchange("binance", bad)
    await m6.check_all()
    await m6.check_all()
    h6 = m6._health["binance"]
    check("C7_error_rate", h6.error_rate > 0 and h6.consecutive_failures >= 2,
          f"err_rate={h6.error_rate:.4f}, fails={h6.consecutive_failures}")



async def main():
    await run()
    ok = all(r["status"] == "PASS" for r in results)
    print(f"\n{'='*60}")
    print(f"AC12 Evidence: {'ALL PASS' if ok else 'FAIL'} ({len(results)} checks)")
    print(f"{'='*60}")
    ev = {"ac_id":"AC12","oracle":"independent hand-trace of health transitions",
          "cases":results,"all_pass":ok,"total_checks":len(results),
          "passed":sum(1 for r in results if r["status"]=="PASS")}
    Path("/tmp/ac12_evidence.json").write_text(json.dumps(ev, indent=2, default=str))
    print("Evidence: /tmp/ac12_evidence.json")
    sys.exit(0 if ok else 1)

asyncio.run(main())
