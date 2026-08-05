"""
Phase 6 P3 - Chaos experiments dry-run (no Kubernetes cluster required).

Simulates a chaos experiment suite locally so the report generation and
experiment lifecycle can be validated before running against a real cluster.

Run:  python scripts/chaos_dryrun.py
"""

import asyncio
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from trading_agent.infrastructure.chaos.chaos_experiments import ChaosExperimentSuite


async def main():
    suite = ChaosExperimentSuite(namespace="trading-agent", dry_run=True)
    suite.add_pod_kill("pod-kill-1", {"app": "trading-agent"}, duration=5)
    suite.add_network_latency("latency-1", {"app": "trading-agent"}, duration=5, latency_ms=100)
    suite.add_exchange_api_failure("api-failure-1", {"app": "trading-agent"}, duration=5)
    suite.add_database_failure("db-failure-1", {"app": "trading-agent"}, duration=5)
    suite.add_cpu_stress("cpu-stress-1", {"app": "trading-agent"}, duration=5, cpu_percent=80)

    results = await suite.run_all()
    print(f"\nRan {len(results)} experiments in dry-run mode")
    print(suite.generate_report())


if __name__ == "__main__":
    asyncio.run(main())
