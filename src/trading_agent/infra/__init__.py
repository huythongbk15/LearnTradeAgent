# Trading Agent Infrastructure Package
"""
Infrastructure components for multi-region deployment, monitoring, and operations.
"""

from trading_agent.infra.multi_region import (
    RegionConfig,
    RegionHealth,
    RegionStatus,
    MultiRegionManager,
    StateSynchronizer,
    SyncState,
    LatencyOptimizer,
    DisasterRecoveryTester,
    DEFAULT_REGIONS,
    create_multi_region_setup,
)

__all__ = [
    "RegionConfig",
    "RegionHealth",
    "RegionStatus",
    "MultiRegionManager",
    "StateSynchronizer",
    "SyncState",
    "LatencyOptimizer",
    "DisasterRecoveryTester",
    "DEFAULT_REGIONS",
    "create_multi_region_setup",
]