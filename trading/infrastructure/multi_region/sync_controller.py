"""
Multi-Region Sync Controller

Manages data synchronization and failover across trading agent regions.
Regions: Primary (ap-southeast-1), Secondary (us-east-1), Tertiary (eu-west-1)
"""

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Optional

import kubernetes
from kubernetes import client, config

logger = logging.getLogger(__name__)


class RegionRole(str, Enum):
    PRIMARY = "primary"
    SECONDARY = "secondary"
    TERTIARY = "tertiary"


class RegionStatus(str, Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"
    FAILOVER = "failover"


@dataclass
class RegionInfo:
    name: str
    role: RegionRole
    priority: int  # Lower = higher priority
    kube_context: str
    status: RegionStatus = RegionStatus.HEALTHY
    last_heartbeat: Optional[datetime] = None
    last_sync: Optional[datetime] = None
    sync_lag_seconds: float = 0.0
    error_count: int = 0
    metadata: dict = field(default_factory=dict)


@dataclass
class SyncPolicy:
    interval_seconds: int = 60
    max_lag_seconds: int = 300  # 5 minutes max lag before alert
    batch_size: int = 1000
    retry_attempts: int = 3
    retry_delay_seconds: int = 10


class RegionSyncController:
    """
    Controls data synchronization between regions.
    Primary region writes, secondaries read and replicate.
    """
    
    def __init__(
        self,
        regions: list[RegionInfo],
        sync_policy: Optional[SyncPolicy] = None,
        dry_run: bool = False,
    ):
        self.regions = {r.name: r for r in regions}
        self.sync_policy = sync_policy or SyncPolicy()
        self.primary_region = next((r for r in regions if r.role == RegionRole.PRIMARY), None)
        self.dry_run = dry_run
        self._running = False
        self._tasks: list[asyncio.Task] = []
        self._k8s_ready = False
    
    async def start(self):
        """Start the sync controller."""
        if self._running:
            return
        
        self._running = True
        
        # Load kube config (skipped in dry-run mode)
        if not self.dry_run:
            try:
                config.load_incluster_config()
            except config.ConfigException:
                config.load_kube_config()
            
            self.k8s_apps = client.AppsV1Api()
            self.k8s_core = client.CoreV1Api()
            self.k8s_custom = client.CustomObjectsApi()
            self._k8s_ready = True
        
        # Start sync tasks for secondary/tertiary regions
        for region in self.regions.values():
            if region.role != RegionRole.PRIMARY:
                task = asyncio.create_task(self._sync_region_loop(region))
                self._tasks.append(task)
        
        # Start health monitoring
        health_task = asyncio.create_task(self._health_monitor_loop())
        self._tasks.append(health_task)
        
        logger.info(
            f"Region sync controller started (dry_run={self.dry_run}). "
            f"Primary: {self.primary_region.name if self.primary_region else 'none'}"
        )
    
    async def stop(self):
        """Stop the sync controller."""
        self._running = False
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        logger.info("Region sync controller stopped")
    
    async def _sync_region_loop(self, region: RegionInfo):
        """Sync loop for a secondary region."""
        logger.info(f"Starting sync loop for region: {region.name}")
        
        while self._running:
            try:
                await self._sync_region(region)
                region.last_sync = datetime.utcnow()
                region.error_count = 0
                region.status = RegionStatus.HEALTHY
            except Exception as e:
                logger.error(f"Sync failed for region {region.name}: {e}")
                region.error_count += 1
                region.status = RegionStatus.DEGRADED if region.error_count < 3 else RegionStatus.UNHEALTHY
                
                if region.error_count >= 3:
                    logger.warning(f"Region {region.name} marked unhealthy, initiating failover check")
                    await self._check_failover(region)
            
            # Wait for next interval
            await asyncio.sleep(self.sync_policy.interval_seconds)
    
    async def _sync_region(self, region: RegionInfo):
        """Sync data from primary to secondary region."""
        if not self.primary_region:
            raise RuntimeError("No primary region configured")
        
        if self.dry_run:
            # Simulate a successful sync without a live cluster
            region.last_sync = datetime.utcnow()
            region.sync_lag_seconds = 0.0
            region.error_count = 0
            region.status = RegionStatus.HEALTHY
            return
        
        # Sync ConfigMaps
        await self._sync_configmaps(region)
        
        # Sync PVC data (using volume snapshots or rsync)
        await self._sync_persistent_data(region)
        
        # Sync secrets (excluding sensitive keys)
        await self._sync_secrets(region)
        
        # Update sync lag metric
        if region.last_sync:
            region.sync_lag_seconds = (datetime.utcnow() - region.last_sync).total_seconds()
    
    async def _sync_configmaps(self, region: RegionInfo):
        """Sync ConfigMaps from primary to secondary."""
        # Get primary ConfigMaps
        primary_cms = self.k8s_core.list_namespaced_config_map(
            namespace="trading-agent",
            label_selector="app=trading-agent"
        )
        
        for cm in primary_cms.items:
            # Skip region-specific ConfigMaps
            if cm.metadata.name in ["trading-agent-region-config"]:
                continue
            
            # Create/update in secondary region
            try:
                self.k8s_core.patch_namespaced_config_map(
                    name=cm.metadata.name,
                    namespace="trading-agent",
                    body=cm,
                )
            except kubernetes.client.exceptions.ApiException as e:
                if e.status == 404:
                    # Create if not exists
                    cm.metadata.resource_version = None
                    cm.metadata.uid = None
                    self.k8s_core.create_namespaced_config_map(
                        namespace="trading-agent",
                        body=cm,
                    )
                else:
                    raise
    
    async def _sync_persistent_data(self, region: RegionInfo):
        """
        Sync persistent volume data.
        Uses volume snapshots for efficient replication.
        """
        # In production, this would use:
        # - CSI volume snapshots
        # - Velero for backup/restore
        # - Or rsync sidecar containers
        
        # For now, log the sync action
        logger.info(f"Data sync triggered for region {region.name}")
        
        # Check PVC status
        pvcs = self.k8s_core.list_namespaced_persistent_volume_claim(
            namespace="trading-agent",
            label_selector="app=trading-agent"
        )
        
        for pvc in pvcs.items:
            if pvc.status.phase == "Bound":
                logger.debug(f"PVC {pvc.metadata.name} is bound in {region.name}")
    
    async def _sync_secrets(self, region: RegionInfo):
        """Sync non-sensitive secrets."""
        # Only sync non-sensitive secrets (like TLS certs, not API keys)
        safe_secrets = ["trading-agent-tls", "trading-agent-ca"]
        
        for secret_name in safe_secrets:
            try:
                secret = self.k8s_core.read_namespaced_secret(
                    name=secret_name,
                    namespace="trading-agent"
                )
                secret.metadata.resource_version = None
                secret.metadata.uid = None
                self.k8s_core.patch_namespaced_secret(
                    name=secret_name,
                    namespace="trading-agent",
                    body=secret,
                )
            except kubernetes.client.exceptions.ApiException as e:
                if e.status == 404:
                    logger.debug(f"Secret {secret_name} not found in primary, skipping")
                else:
                    raise
    
    async def _health_monitor_loop(self):
        """Monitor health of all regions."""
        while self._running:
            for region in self.regions.values():
                await self._check_region_health(region)
            
            await asyncio.sleep(30)  # Check every 30 seconds
    
    async def _check_region_health(self, region: RegionInfo):
        """Check health of a specific region."""
        if self.dry_run:
            # Simulate healthy region with small lag for non-primary regions
            region.status = RegionStatus.HEALTHY
            region.last_heartbeat = datetime.utcnow()
            if region.role != RegionRole.PRIMARY and region.last_sync:
                lag = (datetime.utcnow() - region.last_sync).total_seconds()
                if lag > self.sync_policy.max_lag_seconds:
                    region.status = RegionStatus.DEGRADED
            return
        
        try:
            # Check deployment status
            deployment = self.k8s_apps.read_namespaced_deployment(
                name="trading-agent",
                namespace="trading-agent",
            )
            
            ready_replicas = deployment.status.ready_replicas or 0
            desired_replicas = deployment.spec.replicas or 1
            
            if ready_replicas == desired_replicas:
                region.status = RegionStatus.HEALTHY
            elif ready_replicas > 0:
                region.status = RegionStatus.DEGRADED
            else:
                region.status = RegionStatus.UNHEALTHY
            
            region.last_heartbeat = datetime.utcnow()
            
            # Check sync lag
            if region.role != RegionRole.PRIMARY and region.last_sync:
                lag = (datetime.utcnow() - region.last_sync).total_seconds()
                if lag > self.sync_policy.max_lag_seconds:
                    logger.warning(f"Region {region.name} sync lag: {lag:.0f}s (max: {self.sync_policy.max_lag_seconds}s)")
                    region.status = RegionStatus.DEGRADED
        
        except Exception as e:
            logger.error(f"Health check failed for {region.name}: {e}")
            region.error_count += 1
            if region.error_count >= 3:
                region.status = RegionStatus.UNHEALTHY
    
    async def _check_failover(self, failed_region: RegionInfo):
        """Check if failover is needed."""
        if failed_region.role == RegionRole.PRIMARY:
            # Primary failed - promote secondary
            await self._promote_secondary()
        elif failed_region.role == RegionRole.SECONDARY:
            # Secondary failed - promote tertiary to secondary
            await self._promote_tertiary()
    
    async def _promote_secondary(self):
        """Promote secondary region to primary."""
        secondary = next(
            (r for r in self.regions.values() if r.role == RegionRole.SECONDARY),
            None
        )
        
        if secondary and secondary.status == RegionStatus.HEALTHY:
            logger.warning(f"FAILOVER: Promoting {secondary.name} to PRIMARY")
            
            # Update roles
            if self.primary_region:
                self.primary_region.role = RegionRole.SECONDARY
                self.primary_region.status = RegionStatus.FAILOVER
            
            secondary.role = RegionRole.PRIMARY
            self.primary_region = secondary
            
            # Update ConfigMaps in new primary
            await self._update_region_config(secondary, is_primary=True)
            
            # Trigger alert
            await self._send_failover_alert("primary", secondary.name)
    
    async def _promote_tertiary(self):
        """Promote tertiary region to secondary."""
        tertiary = next(
            (r for r in self.regions.values() if r.role == RegionRole.TERTIARY),
            None
        )
        
        if tertiary and tertiary.status == RegionStatus.HEALTHY:
            logger.warning(f"Promoting {tertiary.name} to SECONDARY")
            tertiary.role = RegionRole.SECONDARY
            await self._update_region_config(tertiary, is_primary=False)
    
    async def _update_region_config(self, region: RegionInfo, is_primary: bool):
        """Update region ConfigMap with new role."""
        if self.dry_run:
            logger.info(
                f"[dry-run] Would update region config for {region.name}: "
                f"IS_PRIMARY={is_primary}, REGION_ROLE={'primary' if is_primary else 'secondary'}"
            )
            return
        
        try:
            cm = self.k8s_core.read_namespaced_config_map(
                name="trading-agent-region-config",
                namespace="trading-agent",
            )
            cm.data["IS_PRIMARY"] = "true" if is_primary else "false"
            cm.data["REGION_ROLE"] = "primary" if is_primary else "secondary"
            self.k8s_core.patch_namespaced_config_map(
                name="trading-agent-region-config",
                namespace="trading-agent",
                body=cm,
            )
        except Exception as e:
            logger.error(f"Failed to update region config: {e}")
    
    async def _send_failover_alert(self, from_role: str, to_region: str):
        """Send failover alert via Telegram/webhook."""
        # Integrate with existing alerting system
        logger.critical(f"FAILOVER ALERT: {from_role} -> {to_region}")
    
    def get_status(self) -> dict[str, Any]:
        """Get status of all regions."""
        return {
            "primary": self.primary_region.name if self.primary_region else None,
            "regions": {
                name: {
                    "role": region.role.value,
                    "status": region.status.value,
                    "priority": region.priority,
                    "last_heartbeat": region.last_heartbeat.isoformat() if region.last_heartbeat else None,
                    "last_sync": region.last_sync.isoformat() if region.last_sync else None,
                    "sync_lag_seconds": region.sync_lag_seconds,
                    "error_count": region.error_count,
                }
                for name, region in self.regions.items()
            }
        }


# Global controller instance
_sync_controller: Optional[RegionSyncController] = None


async def get_sync_controller(dry_run: bool = False) -> RegionSyncController:
    """Get or create global sync controller."""
    global _sync_controller
    
    if _sync_controller is None:
        regions = [
            RegionInfo(
                name="ap-southeast-1",
                role=RegionRole.PRIMARY,
                priority=1,
                kube_context="ap-southeast-1",
            ),
            RegionInfo(
                name="us-east-1",
                role=RegionRole.SECONDARY,
                priority=2,
                kube_context="us-east-1",
            ),
            RegionInfo(
                name="eu-west-1",
                role=RegionRole.TERTIARY,
                priority=3,
                kube_context="eu-west-1",
            ),
        ]
        _sync_controller = RegionSyncController(regions, dry_run=dry_run)
        await _sync_controller.start()
    
    return _sync_controller


async def shutdown_sync_controller():
    """Shutdown global sync controller."""
    global _sync_controller
    if _sync_controller:
        await _sync_controller.stop()
        _sync_controller = None


# CLI for manual failover
async def manual_failover(target_region: str):
    """Manually trigger failover to target region."""
    controller = await get_sync_controller()
    
    target = controller.regions.get(target_region)
    if not target:
        raise ValueError(f"Unknown region: {target_region}")
    
    if target.role == RegionRole.PRIMARY:
        print(f"Region {target_region} is already primary")
        return
    
    # Demote current primary
    if controller.primary_region:
        controller.primary_region.role = RegionRole.SECONDARY
        await controller._update_region_config(controller.primary_region, is_primary=False)
    
    # Promote target
    target.role = RegionRole.PRIMARY
    controller.primary_region = target
    await controller._update_region_config(target, is_primary=True)
    
    print(f"Failover complete: {target_region} is now PRIMARY")


if __name__ == "__main__":
    import sys
    
    if len(sys.argv) > 1 and sys.argv[1] == "failover":
        if len(sys.argv) < 3:
            print("Usage: python region_sync.py failover <region>")
            sys.exit(1)
        asyncio.run(manual_failover(sys.argv[2]))
    elif len(sys.argv) > 1 and sys.argv[1] == "dryrun":
        # Start controller in dry-run mode (no cluster required) for a few seconds
        async def _dryrun():
            controller = await get_sync_controller(dry_run=True)
            await asyncio.sleep(3)
            print(json.dumps(controller.get_status(), indent=2))
            await shutdown_sync_controller()
        asyncio.run(_dryrun())
    else:
        print("Multi-Region Sync Controller")
        print("Usage:")
        print("  python region_sync.py failover <region>   # manual failover")
        print("  python region_sync.py dryrun              # dry-run (no cluster)")