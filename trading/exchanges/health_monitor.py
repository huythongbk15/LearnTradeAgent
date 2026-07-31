"""
Exchange Health Monitor — health checks, latency, auto-failover.

Tracks per-exchange health state:
- Periodic async health checks (latency measurement, connectivity)
- Rolling latency average and error rate
- Health status transitions: UNKNOWN -> HEALTHY -> DEGRADED -> DOWN
- Consecutive-failure detection with backoff between checks
- Failover callback hook (e.g. to switch the order router to a healthy venue)

The monitor is checker-agnostic: register an async checker function per
exchange that returns latency seconds or raises on failure.

Run a quick demo:
    python -m trading.exchanges.health_monitor
"""

from __future__ import annotations

import asyncio
import logging
import statistics
import time
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Awaitable, Callable, Optional

logger = logging.getLogger(__name__)


class HealthStatus(str, Enum):
    UNKNOWN = "unknown"
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    DOWN = "down"


# thresholds: latency seconds
DEFAULT_LATENCY_GOOD = 0.5      # below -> healthy
DEFAULT_LATENCY_DEGRADED = 2.0  # below -> degraded, above -> down-ish
DEFAULT_ERROR_RATE_MAX = 0.2    # error ratio above -> degraded
DEFAULT_FAILURES_TO_DOWN = 3    # consecutive failures -> down
DEFAULT_RECOVERIES_TO_HEALTHY = 2


@dataclass(slots=True)
class ExchangeHealth:
    """Health state for a single exchange."""
    name: str
    status: HealthStatus = HealthStatus.UNKNOWN
    latency_ms: float = 0.0
    avg_latency_ms: float = 0.0
    error_rate: float = 0.0
    consecutive_failures: int = 0
    consecutive_successes: int = 0
    total_checks: int = 0
    last_check: Optional[datetime] = None
    last_success: Optional[datetime] = None
    last_error: Optional[str] = None
    healthy_since: Optional[datetime] = None
    down_since: Optional[datetime] = None
    recent_latencies: list[float] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "status": self.status.value,
            "latency_ms": round(self.latency_ms, 2),
            "avg_latency_ms": round(self.avg_latency_ms, 2),
            "error_rate": round(self.error_rate, 4),
            "consecutive_failures": self.consecutive_failures,
            "consecutive_successes": self.consecutive_successes,
            "total_checks": self.total_checks,
            "last_check": self.last_check.isoformat() if self.last_check else None,
            "last_success": self.last_success.isoformat() if self.last_success else None,
            "last_error": self.last_error,
            "down_since": self.down_since.isoformat() if self.down_since else None,
        }


Checker = Callable[[str], Awaitable[float]]  # returns latency seconds


class HealthMonitor:
    """Periodically checks exchange health and tracks state."""

    def __init__(
        self,
        interval_seconds: float = 30.0,
        latency_good: float = DEFAULT_LATENCY_GOOD,
        latency_degraded: float = DEFAULT_LATENCY_DEGRADED,
        error_rate_max: float = DEFAULT_ERROR_RATE_MAX,
        failures_to_down: int = DEFAULT_FAILURES_TO_DOWN,
        recoveries_to_healthy: int = DEFAULT_RECOVERIES_TO_HEALTHY,
        latency_window: int = 20,
        check_timeout: float = 10.0,
    ):
        self.interval_seconds = interval_seconds
        self.latency_good = latency_good
        self.latency_degraded = latency_degraded
        self.error_rate_max = error_rate_max
        self.failures_to_down = failures_to_down
        self.recoveries_to_healthy = recoveries_to_healthy
        self.latency_window = latency_window
        self.check_timeout = check_timeout

        self._checkers: dict[str, Checker] = {}
        self._health: dict[str, ExchangeHealth] = {}
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._failover_callback: Optional[Callable[[str], Awaitable[None] | None]] = None
        self._status_callback: Optional[Callable[[ExchangeHealth], Awaitable[None] | None]] = None

    # --- registration ----------------------------------------------------
    def register_exchange(self, name: str, checker: Checker) -> None:
        """Register an async checker for an exchange.

        The checker receives the exchange name and returns latency in seconds,
        or raises an exception on failure.
        """
        self._checkers[name] = checker
        self._health[name] = ExchangeHealth(name=name)
        logger.info(f"Registered health checker for {name}")

    @property
    def exchanges(self) -> list[str]:
        return sorted(self._checkers.keys())

    # --- callbacks -------------------------------------------------------
    def on_failover(self, callback: Callable[[str], Awaitable[None] | None]) -> None:
        """Called when an exchange transitions to DOWN (auto-failover hook)."""
        self._failover_callback = callback

    def on_status_change(self, callback: Callable[[ExchangeHealth], Awaitable[None] | None]) -> None:
        """Called whenever an exchange's health status changes."""
        self._status_callback = callback

    # --- lifecycle -------------------------------------------------------
    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        await self.check_all()
        self._task = asyncio.create_task(self._loop())
        logger.info(f"Health monitor started (interval={self.interval_seconds}s)")

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            self._task = None
        logger.info("Health monitor stopped")

    async def _loop(self) -> None:
        while self._running:
            await asyncio.sleep(self.interval_seconds)
            if not self._running:
                break
            await self.check_all()

    # --- checking --------------------------------------------------------
    async def check_all(self) -> dict[str, ExchangeHealth]:
        """Run one round of checks for all registered exchanges."""
        results = await asyncio.gather(
            *(self._check_exchange(name) for name in self.exchanges),
            return_exceptions=True,
        )
        return self._health

    async def check_exchange(self, name: str) -> ExchangeHealth:
        """Run a single health check for one exchange."""
        await self._check_exchange(name)
        return self._health[name]

    async def _check_exchange(self, name: str) -> None:
        health = self._health.get(name)
        if health is None:
            return
        checker = self._checkers.get(name)
        if checker is None:
            return

        health.total_checks += 1
        health.last_check = datetime.utcnow()
        start = time.monotonic()
        try:
            latency = await asyncio.wait_for(checker(name), timeout=self.check_timeout)
            elapsed = (time.monotonic() - start) * 1000.0
            measured = latency * 1000.0 if latency > 0 else elapsed

            health.latency_ms = measured
            health.recent_latencies.append(measured)
            if len(health.recent_latencies) > self.latency_window:
                health.recent_latencies.pop(0)
            health.avg_latency_ms = statistics.mean(health.recent_latencies)
            health.last_success = datetime.utcnow()
            health.last_error = None
            health.consecutive_failures = 0
            health.consecutive_successes += 1

            # error rate decays towards 0 with successes
            health.error_rate = max(0.0, health.error_rate * 0.9)

            self._update_status_from_latency(health)
        except Exception as e:
            health.consecutive_failures += 1
            health.consecutive_successes = 0
            health.last_error = str(e)[:300]
            # accumulate error rate (1 recent sample)
            health.error_rate = min(1.0, health.error_rate * 0.8 + 0.2)
            self._set_status(health, HealthStatus.DOWN if health.consecutive_failures >= self.failures_to_down else HealthStatus.DEGRADED)

    def _update_status_from_latency(self, health: ExchangeHealth) -> None:
        if health.avg_latency_ms / 1000.0 > self.latency_degraded:
            new_status = HealthStatus.DEGRADED
        elif health.error_rate > self.error_rate_max:
            new_status = HealthStatus.DEGRADED
        else:
            if health.consecutive_successes >= self.recoveries_to_healthy:
                new_status = HealthStatus.HEALTHY
            else:
                new_status = HealthStatus.DEGRADED if health.status == HealthStatus.UNKNOWN else health.status
        self._set_status(health, new_status)

    def _set_status(self, health: ExchangeHealth, new_status: HealthStatus) -> None:
        if health.status == new_status:
            return
        old = health.status
        health.status = new_status
        now = datetime.utcnow()
        if new_status == HealthStatus.HEALTHY:
            health.healthy_since = now
            health.down_since = None
        elif new_status == HealthStatus.DOWN:
            health.down_since = now
        logger.warning(
            f"Exchange {health.name} health: {old.value} -> {new_status.value} "
            f"(failures={health.consecutive_failures}, latency={health.avg_latency_ms:.0f}ms)"
        )
        if self._status_callback:
            try:
                res = self._status_callback(health)
                if asyncio.iscoroutine(res):
                    asyncio.create_task(res)
            except Exception as e:
                logger.error(f"Status callback error for {health.name}: {e}")
        if new_status == HealthStatus.DOWN and self._failover_callback:
            try:
                res = self._failover_callback(health.name)
                if asyncio.iscoroutine(res):
                    asyncio.create_task(res)
            except Exception as e:
                logger.error(f"Failover callback error for {health.name}: {e}")

    # --- queries ---------------------------------------------------------
    def get_status(self) -> dict[str, dict]:
        return {name: h.to_dict() for name, h in sorted(self._health.items())}

    def get_exchange_status(self, name: str) -> Optional[dict]:
        h = self._health.get(name)
        return h.to_dict() if h else None

    def is_healthy(self, name: str) -> bool:
        h = self._health.get(name)
        return bool(h and h.status == HealthStatus.HEALTHY)

    def get_healthy_exchanges(self) -> list[str]:
        return [n for n in self.exchanges if self.is_healthy(n)]

    def get_unhealthy(self) -> list[str]:
        return [n for n, h in self._health.items() if h.status in (HealthStatus.DEGRADED, HealthStatus.DOWN)]

    def get_primary_candidates(self) -> list[str]:
        """Exchanges suitable as primary venue (healthy first, then degraded)."""
        healthy = self.get_healthy_exchanges()
        if healthy:
            return healthy
        degraded = [n for n, h in self._health.items() if h.status == HealthStatus.DEGRADED]
        return degraded


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    async def demo():
        monitor = HealthMonitor(interval_seconds=1.0)

        async def good_checker(name: str) -> float:
            await asyncio.sleep(0.05)
            return 0.05

        async def bad_checker(name: str) -> float:
            raise ConnectionError("exchange unreachable")

        monitor.register_exchange("binance", good_checker)
        monitor.register_exchange("bybit", bad_checker)
        monitor.on_failover(lambda name: print(f"  [FAILOVER] switching away from {name}"))

        await monitor.start()
        await asyncio.sleep(4)
        print("status:", monitor.get_status())
        print("healthy:", monitor.get_healthy_exchanges())
        print("unhealthy:", monitor.get_unhealthy())
        await monitor.stop()

    asyncio.run(demo())
