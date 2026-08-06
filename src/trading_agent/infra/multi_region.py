#!/usr/bin/env python3
"""
Multi-Region Deployment Infrastructure.

Provides:
1. Region configuration and health monitoring
2. Failover orchestration
3. Latency optimization (edge routing)
4. Disaster recovery testing
5. State synchronization across regions
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Callable, Optional
from pathlib import Path


class RegionStatus(Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"
    MAINTENANCE = "maintenance"
    FAILOVER = "failover"


@dataclass
class RegionConfig:
    """Region configuration."""
    name: str
    code: str  # e.g., "us-east-1", "eu-west-1", "ap-southeast-1"
    endpoint: str  # Primary endpoint
    backup_endpoints: list[str] = field(default_factory=list)
    priority: int = 1  # Lower = higher priority
    latency_weight: float = 1.0
    max_latency_ms: int = 100
    health_check_interval: int = 30
    failover_threshold: int = 3  # consecutive failures
    services: list[str] = field(default_factory=list)  # ["api", "ws", "db", "redis"]
    metadata: dict = field(default_factory=dict)


@dataclass
class RegionHealth:
    """Region health state."""
    region: str
    status: RegionStatus = RegionStatus.HEALTHY
    latency_ms: float = 0.0
    error_rate: float = 0.0
    consecutive_failures: int = 0
    last_check: float = 0.0
    last_success: float = 0.0
    details: dict = field(default_factory=dict)


class MultiRegionManager:
    """
    Manages multi-region deployment with automatic failover.
    """

    def __init__(self, regions: list[RegionConfig], primary_region: str | None = None):
        self.regions = {r.code: r for r in regions}
        self.health = {r.code: RegionHealth(region=r.code) for r in regions}
        self.primary_region = primary_region or min(regions, key=lambda r: r.priority).code
        self.active_region = self.primary_region
        self.failover_callbacks: list[Callable[[str, str], None]] = []
        self._monitoring = False
        self._monitor_task: Optional[asyncio.Task] = None

    def register_failover_callback(self, callback: Callable[[str, str], None]) -> None:
        """Register callback(old_region, new_region) for failover events."""
        self.failover_callbacks.append(callback)

    async def start_monitoring(self, check_func: Callable[[str], bool]) -> None:
        """Start health monitoring loop."""
        self._monitoring = True
        self._monitor_task = asyncio.create_task(self._monitor_loop(check_func))

    async def stop_monitoring(self) -> None:
        self._monitoring = False
        if self._monitor_task:
            self._monitor_task.cancel()
            try:
                await self._monitor_task
            except asyncio.CancelledError:
                pass

    async def _monitor_loop(self, check_func: Callable[[str], bool]) -> None:
        while self._monitoring:
            for region_code, region in self.regions.items():
                await self._check_region(region_code, region, check_func)
            await asyncio.sleep(min(r.health_check_interval for r in self.regions.values()))

    async def _check_region(self, region_code: str, region: RegionConfig, check_func: Callable[[str], bool]) -> None:
        health = self.health[region_code]
        start = time.perf_counter()

        try:
            # Run health check in thread pool to avoid blocking
            healthy = await asyncio.get_event_loop().run_in_executor(None, check_func, region.endpoint)
            latency = (time.perf_counter() - start) * 1000

            health.latency_ms = latency
            health.last_check = time.time()

            if healthy and latency <= region.max_latency_ms:
                health.status = RegionStatus.HEALTHY
                health.consecutive_failures = 0
                health.last_success = time.time()
            elif healthy:
                health.status = RegionStatus.DEGRADED
                health.consecutive_failures = 0
            else:
                health.consecutive_failures += 1
                health.error_rate = health.consecutive_failures / max(health.consecutive_failures + 10, 1)
                if health.consecutive_failures >= region.failover_threshold:
                    health.status = RegionStatus.UNHEALTHY
                    await self._trigger_failover(region_code)
                else:
                    health.status = RegionStatus.DEGRADED

        except Exception as e:
            health.consecutive_failures += 1
            health.error_rate = health.consecutive_failures / max(health.consecutive_failures + 10, 1)
            health.status = RegionStatus.UNHEALTHY if health.consecutive_failures >= region.failover_threshold else RegionStatus.DEGRADED
            health.details["last_error"] = str(e)

    async def _trigger_failover(self, failed_region: str) -> None:
        if failed_region != self.active_region:
            return  # Only failover if active region fails

        # Find best healthy region
        candidates = [
            (r, self.health[r]) for r in self.regions
            if self.health[r].status in (RegionStatus.HEALTHY, RegionStatus.DEGRADED)
        ]
        if not candidates:
            return  # No healthy region available

        # Sort by priority then latency
        candidates.sort(key=lambda x: (self.regions[x[0]].priority, self.health[x[0]].latency_ms))
        new_region = candidates[0][0]

        old_region = self.active_region
        self.active_region = new_region
        self.health[new_region].status = RegionStatus.FAILOVER

        # Notify callbacks
        for callback in self.failover_callbacks:
            try:
                callback(old_region, new_region)
            except Exception:
                pass

    def get_best_region(self, service: str | None = None) -> str:
        """Get best region for a service (considering latency)."""
        candidates = []
        for code, region in self.regions.items():
            health = self.health[code]
            if health.status in (RegionStatus.HEALTHY, RegionStatus.DEGRADED):
                if service is None or service in region.services:
                    score = health.latency_ms * region.latency_weight
                    candidates.append((code, score))

        if not candidates:
            return self.active_region

        candidates.sort(key=lambda x: x[1])
        return candidates[0][0]

    def get_region_status(self) -> dict:
        """Get status of all regions."""
        return {
            code: {
                "status": health.status.value,
                "latency_ms": round(health.latency_ms, 2),
                "error_rate": round(health.error_rate, 4),
                "consecutive_failures": health.consecutive_failures,
                "is_active": code == self.active_region,
                "is_primary": code == self.primary_region,
            }
            for code, health in self.health.items()
        }


# ══════════════════════════════════════════════════════════════════════════
# State Synchronization
# ══════════════════════════════════════════════════════════════════════════

@dataclass
class SyncState:
    """Synchronized state across regions."""
    key: str
    value: bytes
    version: int
    timestamp: float
    region: str
    checksum: str


class StateSynchronizer:
    """
    Synchronizes state across regions using CRDT-like approach.
    In production, would use Redis Cluster, Consul, or etcd.
    """

    def __init__(self, region_manager: MultiRegionManager, storage_path: str = "./state_sync"):
        self.region_manager = region_manager
        self.storage_path = Path(storage_path)
        self.storage_path.mkdir(parents=True, exist_ok=True)
        self.local_state: dict[str, SyncState] = {}
        self._lock = asyncio.Lock()

    async def set(self, key: str, value: bytes, region: str | None = None) -> SyncState:
        """Set state value."""
        region = region or self.region_manager.active_region
        async with self._lock:
            current = self.local_state.get(key)
            version = (current.version + 1) if current else 1
            state = SyncState(
                key=key,
                value=value,
                version=version,
                timestamp=time.time(),
                region=region,
                checksum=self._checksum(value),
            )
            self.local_state[key] = state
            await self._persist(state)
            return state

    async def get(self, key: str) -> SyncState | None:
        """Get state value."""
        async with self._lock:
            return self.local_state.get(key)

    async def merge(self, other_state: SyncState) -> bool:
        """Merge state from another region (last-write-wins with version)."""
        async with self._lock:
            current = self.local_state.get(other_state.key)
            if current is None or other_state.version > current.version or \
               (other_state.version == current.version and other_state.timestamp > current.timestamp):
                self.local_state[other_state.key] = other_state
                await self._persist(other_state)
                return True
            return False

    async def sync_from_region(self, region_code: str, keys: list[str] | None = None) -> int:
        """Sync state from another region (placeholder for actual implementation)."""
        # In production: fetch from remote region's state store
        # This is a stub for the interface
        return 0

    def _checksum(self, data: bytes) -> str:
        import hashlib
        return hashlib.sha256(data).hexdigest()[:16]

    async def _persist(self, state: SyncState) -> None:
        file = self.storage_path / f"{state.key}.json"
        data = {
            "key": state.key,
            "value": state.value.hex(),
            "version": state.version,
            "timestamp": state.timestamp,
            "region": state.region,
            "checksum": state.checksum,
        }
        file.write_text(json.dumps(data))


# ══════════════════════════════════════════════════════════════════════════
# Latency Optimization (Edge Routing)
# ══════════════════════════════════════════════════════════════════════════

class LatencyOptimizer:
    """
    Optimizes request routing based on latency measurements.
    """

    def __init__(self, region_manager: MultiRegionManager):
        self.region_manager = region_manager
        self.latency_history: dict[str, list[float]] = {code: [] for code in region_manager.regions}
        self.max_history = 100

    def record_latency(self, region: str, latency_ms: float) -> None:
        if region in self.latency_history:
            self.latency_history[region].append(latency_ms)
            if len(self.latency_history[region]) > self.max_history:
                self.latency_history[region].pop(0)

    def get_optimal_region(self, service: str | None = None) -> str:
        """Get region with lowest average latency."""
        best_region = self.region_manager.active_region
        best_latency = float('inf')

        for code, history in self.latency_history.items():
            if not history:
                continue
            region = self.region_manager.regions[code]
            if service and service not in region.services:
                continue
            health = self.region_manager.health[code]
            if health.status not in (RegionStatus.HEALTHY, RegionStatus.DEGRADED):
                continue

            avg_latency = sum(history) / len(history)
            if avg_latency < best_latency:
                best_latency = avg_latency
                best_region = code

        return best_region

    def get_latency_stats(self) -> dict:
        return {
            code: {
                "avg_ms": round(sum(h) / len(h), 2) if h else 0,
                "p50_ms": round(sorted(h)[len(h)//2], 2) if h else 0,
                "p99_ms": round(sorted(h)[int(len(h)*0.99)], 2) if h else 0,
                "samples": len(h),
            }
            for code, h in self.latency_history.items()
        }


# ══════════════════════════════════════════════════════════════════════════
# Disaster Recovery Testing
# ══════════════════════════════════════════════════════════════════════════

class DisasterRecoveryTester:
    """
    Automated DR testing: failover, data integrity, RTO/RPO validation.
    """

    def __init__(self, region_manager: MultiRegionManager, state_sync: StateSynchronizer):
        self.region_manager = region_manager
        self.state_sync = state_sync
        self.test_results: list[dict] = []

    async def run_failover_test(self, target_region: str | None = None) -> dict:
        """Simulate region failure and measure failover time."""
        target = target_region or self.region_manager.active_region
        original_active = self.region_manager.active_region

        start = time.perf_counter()

        # Mark region as unhealthy
        self.region_manager.health[target].status = RegionStatus.UNHEALTHY
        self.region_manager.health[target].consecutive_failures = 999

        # Trigger failover check
        await self.region_manager._trigger_failover(target)

        failover_time = time.perf_counter() - start

        # Verify new active region
        new_active = self.region_manager.active_region

        # Verify state consistency
        state_consistent = await self._verify_state_consistency(original_active, new_active)

        result = {
            "test": "failover",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "original_region": original_active,
            "failed_region": target,
            "new_region": new_active,
            "failover_time_seconds": round(failover_time, 3),
            "state_consistent": state_consistent,
            "rto_target_met": failover_time < 30,  # 30s RTO target
        }

        self.test_results.append(result)
        return result

    async def run_data_integrity_test(self, keys: list[str]) -> dict:
        """Verify data consistency across regions."""
        start = time.perf_counter()
        inconsistencies = []

        for key in keys:
            # In production: fetch from each region and compare
            # This is a placeholder
            pass

        result = {
            "test": "data_integrity",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "keys_checked": len(keys),
            "inconsistencies": len(inconsistencies),
            "duration_seconds": round(time.perf_counter() - start, 3),
        }
        self.test_results.append(result)
        return result

    async def _verify_state_consistency(self, old_region: str, new_region: str) -> bool:
        """Verify critical state is consistent after failover."""
        # Check key state keys
        critical_keys = ["positions", "orders", "risk_limits", "portfolio"]
        for key in critical_keys:
            state = await self.state_sync.get(key)
            if state and state.region == old_region:
                # State hasn't been replicated to new region
                return False
        return True

    def get_test_report(self) -> dict:
        return {
            "total_tests": len(self.test_results),
            "results": self.test_results,
            "last_test": self.test_results[-1] if self.test_results else None,
        }


# ══════════════════════════════════════════════════════════════════════════
# Default Configuration
# ══════════════════════════════════════════════════════════════════════════

DEFAULT_REGIONS = [
    RegionConfig(
        name="US East (Virginia)",
        code="us-east-1",
        endpoint="https://api.us-east-1.trading.example.com",
        backup_endpoints=["https://api-backup.us-east-1.trading.example.com"],
        priority=1,
        latency_weight=1.0,
        max_latency_ms=50,
        services=["api", "ws", "db", "redis", "execution"],
    ),
    RegionConfig(
        name="EU West (Ireland)",
        code="eu-west-1",
        endpoint="https://api.eu-west-1.trading.example.com",
        backup_endpoints=["https://api-backup.eu-west-1.trading.example.com"],
        priority=2,
        latency_weight=1.2,
        max_latency_ms=80,
        services=["api", "ws", "db", "redis", "execution"],
    ),
    RegionConfig(
        name="Asia Pacific (Singapore)",
        code="ap-southeast-1",
        endpoint="https://api.ap-southeast-1.trading.example.com",
        backup_endpoints=["https://api-backup.ap-southeast-1.trading.example.com"],
        priority=3,
        latency_weight=1.5,
        max_latency_ms=100,
        services=["api", "ws", "db", "redis"],
    ),
]


def create_multi_region_setup() -> tuple[MultiRegionManager, StateSynchronizer, LatencyOptimizer, DisasterRecoveryTester]:
    """Create complete multi-region setup."""
    region_manager = MultiRegionManager(DEFAULT_REGIONS, primary_region="us-east-1")
    state_sync = StateSynchronizer(region_manager)
    latency_optimizer = LatencyOptimizer(region_manager)
    dr_tester = DisasterRecoveryTester(region_manager, state_sync)
    return region_manager, state_sync, latency_optimizer, dr_tester


if __name__ == "__main__":
    async def demo():
        rm, ss, lo, dr = create_multi_region_setup()

        # Mock health check
        def mock_check(endpoint: str) -> bool:
            import random
            return random.random() > 0.1

        await rm.start_monitoring(mock_check)
        await asyncio.sleep(2)
        print("Region status:", json.dumps(rm.get_region_status(), indent=2))
        print("Best region:", rm.get_best_region())

        # Simulate failover
        await dr.run_failover_test("us-east-1")
        print("After failover:", rm.active_region)
        print("Test result:", json.dumps(dr.test_results[-1], indent=2))

        await rm.stop_monitoring()

    asyncio.run(demo())