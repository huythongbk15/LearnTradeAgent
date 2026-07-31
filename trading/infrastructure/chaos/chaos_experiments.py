"""
Chaos Engineering for Trading Agent System

Implements chaos experiments using Chaos Mesh / Litmus concepts.
Tests resilience of trading system under various failure scenarios.
"""

import asyncio
import logging
import random
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Optional
import kubernetes
from kubernetes import client, config

logger = logging.getLogger(__name__)


class ChaosExperimentType(str, Enum):
    POD_KILL = "pod_kill"
    NETWORK_PARTITION = "network_partition"
    NETWORK_LATENCY = "network_latency"
    NETWORK_LOSS = "network_loss"
    CPU_STRESS = "cpu_stress"
    MEMORY_STRESS = "memory_stress"
    DISK_FILL = "disk_fill"
    DNS_FAILURE = "dns_failure"
    TIME_DRIFT = "time_drift"
    CLOCK_SKEW = "clock_skew"
    EXCHANGE_API_FAILURE = "exchange_api_failure"
    DATABASE_CONNECTION_FAILURE = "database_connection_failure"
    REDIS_CONNECTION_FAILURE = "redis_connection_failure"
    LLM_API_FAILURE = "llm_api_failure"
    MARKET_DATA_DELAY = "market_data_delay"
    ORDER_REJECTION = "order_rejection"


class ExperimentStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    ABORTED = "aborted"


@dataclass
class ChaosExperiment:
    name: str
    experiment_type: ChaosExperimentType
    namespace: str = "trading-agent"
    target_labels: dict[str, str] = field(default_factory=dict)
    duration_seconds: int = 60
    parameters: dict[str, Any] = field(default_factory=dict)
    status: ExperimentStatus = ExperimentStatus.PENDING
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    result: Optional[dict] = None
    error: Optional[str] = None


@dataclass
class ExperimentResult:
    experiment_name: str
    success: bool
    duration_seconds: float
    metrics_before: dict[str, float]
    metrics_after: dict[str, float]
    observations: list[str]
    recommendations: list[str]


class ChaosExperimentRunner(ABC):
    """Base class for chaos experiment runners."""
    
    def __init__(self, experiment: ChaosExperiment):
        self.experiment = experiment
        self.k8s_core = client.CoreV1Api()
        self.k8s_apps = client.AppsV1Api()
        self._original_state: dict = {}
    
    @abstractmethod
    async def inject_fault(self) -> bool:
        """Inject the fault. Returns True if successful."""
        pass
    
    @abstractmethod
    async def recover(self) -> bool:
        """Recover from the fault. Returns True if successful."""
        pass
    
    @abstractmethod
    async def verify_impact(self) -> dict[str, Any]:
        """Verify the impact of the fault. Returns metrics/observations."""
        pass
    
    async def run(self, metrics_collector=None) -> ExperimentResult:
        """Run the full experiment."""
        self.experiment.status = ExperimentStatus.RUNNING
        self.experiment.start_time = datetime.utcnow()
        
        logger.info(f"Starting chaos experiment: {self.experiment.name}")
        
        # Collect baseline metrics
        metrics_before = {}
        if metrics_collector:
            metrics_before = await metrics_collector.collect()
        
        try:
            # Inject fault
            success = await self.inject_fault()
            if not success:
                raise RuntimeError("Fault injection failed")
            
            # Wait for duration
            await asyncio.sleep(self.experiment.duration_seconds)
            
            # Verify impact
            impact = await self.verify_impact()
            
            # Collect post-experiment metrics
            metrics_after = {}
            if metrics_collector:
                metrics_after = await metrics_collector.collect()
            
            self.experiment.status = ExperimentStatus.COMPLETED
            self.experiment.result = impact
            
        except Exception as e:
            logger.error(f"Experiment failed: {e}")
            self.experiment.status = ExperimentStatus.FAILED
            self.experiment.error = str(e)
            metrics_after = {}
            impact = {"error": str(e)}
        
        finally:
            # Always attempt recovery
            await self.recover()
            self.experiment.end_time = datetime.utcnow()
        
        duration = (self.experiment.end_time - self.experiment.start_time).total_seconds()
        
        return ExperimentResult(
            experiment_name=self.experiment.name,
            success=self.experiment.status == ExperimentStatus.COMPLETED,
            duration_seconds=duration,
            metrics_before=metrics_before,
            metrics_after=metrics_after,
            observations=impact.get("observations", []),
            recommendations=impact.get("recommendations", []),
        )


class PodKillExperiment(ChaosExperimentRunner):
    """Kill random pods to test resilience."""
    
    async def inject_fault(self) -> bool:
        label_selector = ",".join(f"{k}={v}" for k, v in self.experiment.target_labels.items())
        
        pods = self.k8s_core.list_namespaced_pod(
            namespace=self.experiment.namespace,
            label_selector=label_selector,
        )
        
        if not pods.items:
            logger.warning(f"No pods found for selector: {label_selector}")
            return False
        
        # Kill random pod(s)
        kill_count = self.experiment.parameters.get("kill_count", 1)
        kill_count = min(kill_count, len(pods.items))
        
        targets = random.sample(pods.items, kill_count)
        
        for pod in targets:
            logger.info(f"Killing pod: {pod.metadata.name}")
            self._original_state[pod.metadata.name] = {
                "deletion_timestamp": pod.metadata.deletion_timestamp,
            }
            self.k8s_core.delete_namespaced_pod(
                name=pod.metadata.name,
                namespace=self.experiment.namespace,
                grace_period_seconds=0,
            )
        
        return True
    
    async def recover(self) -> bool:
        # Pods should be recreated by Deployment/ReplicaSet
        # Wait for recovery
        label_selector = ",".join(f"{k}={v}" for k, v in self.experiment.target_labels.items())
        
        for _ in range(30):  # Wait up to 5 minutes
            pods = self.k8s_core.list_namespaced_pod(
                namespace=self.experiment.namespace,
                label_selector=label_selector,
            )
            
            ready = all(
                p.status.phase == "Running" and 
                all(c.ready for c in p.status.container_statuses or [])
                for p in pods.items
            )
            
            if ready and len(pods.items) >= self.experiment.parameters.get("min_replicas", 1):
                return True
            
            await asyncio.sleep(10)
        
        return False
    
    async def verify_impact(self) -> dict[str, Any]:
        # Check if system continued operating
        label_selector = ",".join(f"{k}={v}" for k, v in self.experiment.target_labels.items())
        
        pods = self.k8s_core.list_namespaced_pod(
            namespace=self.experiment.namespace,
            label_selector=label_selector,
        )
        
        observations = [
            f"Killed {len(self._original_state)} pod(s)",
            f"Remaining pods: {len(pods.items)}",
        ]
        
        recommendations = []
        if len(pods.items) < self.experiment.parameters.get("min_replicas", 1):
            observations.append("Insufficient replicas after pod kill")
            recommendations.append("Increase replica count or add pod disruption budget")
        
        return {
            "observations": observations,
            "recommendations": recommendations,
        }


class NetworkLatencyExperiment(ChaosExperimentRunner):
    """Inject network latency using tc (traffic control)."""
    
    async def inject_fault(self) -> bool:
        # This would typically use a sidecar or init container with NET_ADMIN capability
        # For now, we simulate by patching pod annotations for a network chaos controller
        label_selector = ",".join(f"{k}={v}" for k, v in self.experiment.target_labels.items())
        
        pods = self.k8s_core.list_namespaced_pod(
            namespace=self.experiment.namespace,
            label_selector=label_selector,
        )
        
        latency_ms = self.experiment.parameters.get("latency_ms", 100)
        jitter_ms = self.experiment.parameters.get("jitter_ms", 10)
        
        for pod in pods.items:
            # Add network chaos annotation (requires Chaos Mesh or similar)
            annotations = pod.metadata.annotations or {}
            annotations["chaos-mesh.org/network-latency"] = f"{latency_ms}ms"
            annotations["chaos-mesh.org/network-jitter"] = f"{jitter_ms}ms"
            
            self._original_state[pod.metadata.name] = {
                "annotations": pod.metadata.annotations or {},
            }
            
            self.k8s_core.patch_namespaced_pod(
                name=pod.metadata.name,
                namespace=self.experiment.namespace,
                body={"metadata": {"annotations": annotations}},
            )
        
        return True
    
    async def recover(self) -> bool:
        for pod_name, original in self._original_state.items():
            try:
                self.k8s_core.patch_namespaced_pod(
                    name=pod_name,
                    namespace=self.experiment.namespace,
                    body={"metadata": {"annotations": original.get("annotations", {})}},
                )
            except Exception as e:
                logger.warning(f"Failed to restore annotations for {pod_name}: {e}")
        
        return True
    
    async def verify_impact(self) -> dict[str, Any]:
        # Would check actual latency metrics from application
        return {
            "observations": [
                f"Injected {self.experiment.parameters.get('latency_ms', 100)}ms latency",
                f"Jitter: {self.experiment.parameters.get('jitter_ms', 10)}ms",
            ],
            "recommendations": [
                "Monitor API timeout settings",
                "Verify circuit breaker behavior under latency",
            ],
        }


class ExchangeAPIFailureExperiment(ChaosExperimentRunner):
    """Simulate exchange API failures."""
    
    async def inject_fault(self) -> bool:
        # Block exchange API endpoints using network policies or egress filtering
        # For simulation, we patch the trading agent config to use a failing endpoint
        
        try:
            cm = self.k8s_core.read_namespaced_config_map(
                name="trading-agent-config",
                namespace=self.experiment.namespace,
            )
            
            # Store original config
            self._original_state["config"] = cm.data.get("config.yaml", "")
            
            # Modify to use invalid endpoint
            config_yaml = cm.data.get("config.yaml", "")
            config_yaml = config_yaml.replace(
                "default_exchange: binance",
                "default_exchange: binance\n    test_mode: true\n    force_api_failure: true"
            )
            
            cm.data["config.yaml"] = config_yaml
            self.k8s_core.patch_namespaced_config_map(
                name="trading-agent-config",
                namespace=self.experiment.namespace,
                body=cm,
            )
            
            # Restart pods to pick up new config
            label_selector = ",".join(f"{k}={v}" for k, v in self.experiment.target_labels.items())
            pods = self.k8s_core.list_namespaced_pod(
                namespace=self.experiment.namespace,
                label_selector=label_selector,
            )
            
            for pod in pods.items:
                self.k8s_core.delete_namespaced_pod(
                    name=pod.metadata.name,
                    namespace=self.experiment.namespace,
                    grace_period_seconds=0,
                )
            
            return True
            
        except Exception as e:
            logger.error(f"Failed to inject exchange API failure: {e}")
            return False
    
    async def recover(self) -> bool:
        try:
            if "config" in self._original_state:
                cm = self.k8s_core.read_namespaced_config_map(
                    name="trading-agent-config",
                    namespace=self.experiment.namespace,
                )
                cm.data["config.yaml"] = self._original_state["config"]
                self.k8s_core.patch_namespaced_config_map(
                    name="trading-agent-config",
                    namespace=self.experiment.namespace,
                    body=cm,
                )
                
                # Restart pods
                label_selector = ",".join(f"{k}={v}" for k, v in self.experiment.target_labels.items())
                pods = self.k8s_core.list_namespaced_pod(
                    namespace=self.experiment.namespace,
                    label_selector=label_selector,
                )
                
                for pod in pods.items:
                    self.k8s_core.delete_namespaced_pod(
                        name=pod.metadata.name,
                        namespace=self.experiment.namespace,
                        grace_period_seconds=0,
                    )
            
            return True
        except Exception as e:
            logger.error(f"Failed to recover from exchange API failure: {e}")
            return False
    
    async def verify_impact(self) -> dict[str, Any]:
        return {
            "observations": [
                "Exchange API failure simulated",
                "Trading agent should handle gracefully (circuit breaker, fallback)",
            ],
            "recommendations": [
                "Verify circuit breaker activates",
                "Check fallback to paper trading mode",
                "Ensure alerts fire on API failure",
            ],
        }


class DatabaseConnectionFailureExperiment(ChaosExperimentRunner):
    """Simulate database connection failures."""
    
    async def inject_fault(self) -> bool:
        # Block database connections using network policy
        label_selector = ",".join(f"{k}={v}" for k, v in self.experiment.target_labels.items())
        
        pods = self.k8s_core.list_namespaced_pod(
            namespace=self.experiment.namespace,
            label_selector=label_selector,
        )
        
        for pod in pods.items:
            annotations = pod.metadata.annotations or {}
            annotations["chaos-mesh.org/network-partition"] = "timescaledb:5432"
            self._original_state[pod.metadata.name] = {"annotations": pod.metadata.annotations or {}}
            
            self.k8s_core.patch_namespaced_pod(
                name=pod.metadata.name,
                namespace=self.experiment.namespace,
                body={"metadata": {"annotations": annotations}},
            )
        
        return True
    
    async def recover(self) -> bool:
        for pod_name, original in self._original_state.items():
            try:
                self.k8s_core.patch_namespaced_pod(
                    name=pod_name,
                    namespace=self.experiment.namespace,
                    body={"metadata": {"annotations": original.get("annotations", {})}},
                )
            except Exception:
                pass
        return True
    
    async def verify_impact(self) -> dict[str, Any]:
        return {
            "observations": ["Database connection blocked"],
            "recommendations": [
                "Verify connection pooling handles failures",
                "Check read-from-replica fallback",
                "Ensure write-ahead logging survives disconnect",
            ],
        }


class CPUStressExperiment(ChaosExperimentRunner):
    """Stress CPU to test performance under load."""
    
    async def inject_fault(self) -> bool:
        # Use a stress container or chaos mesh CPU stress
        label_selector = ",".join(f"{k}={v}" for k, v in self.experiment.target_labels.items())
        
        pods = self.k8s_core.list_namespaced_pod(
            namespace=self.experiment.namespace,
            label_selector=label_selector,
        )
        
        cpu_percent = self.experiment.parameters.get("cpu_percent", 80)
        
        for pod in pods.items:
            annotations = pod.metadata.annotations or {}
            annotations["chaos-mesh.org/cpu-stress"] = str(cpu_percent)
            self._original_state[pod.metadata.name] = {"annotations": pod.metadata.annotations or {}}
            
            self.k8s_core.patch_namespaced_pod(
                name=pod.metadata.name,
                namespace=self.experiment.namespace,
                body={"metadata": {"annotations": annotations}},
            )
        
        return True
    
    async def recover(self) -> bool:
        for pod_name, original in self._original_state.items():
            try:
                self.k8s_core.patch_namespaced_pod(
                    name=pod_name,
                    namespace=self.experiment.namespace,
                    body={"metadata": {"annotations": original.get("annotations", {})}},
                )
            except Exception:
                pass
        return True
    
    async def verify_impact(self) -> dict[str, Any]:
        return {
            "observations": [f"CPU stressed to {self.experiment.parameters.get('cpu_percent', 80)}%"],
            "recommendations": [
                "Verify HPA triggers scale-up",
                "Check latency degradation is acceptable",
                "Ensure critical paths have CPU limits",
            ],
        }


class ChaosExperimentSuite:
    """Manages a suite of chaos experiments."""
    
    def __init__(self, namespace: str = "trading-agent", dry_run: bool = False):
        self.namespace = namespace
        self.dry_run = dry_run
        self.experiments: list[ChaosExperiment] = []
        self.results: list[ExperimentResult] = []
    
    def add_experiment(self, experiment: ChaosExperiment):
        self.experiments.append(experiment)
    
    def add_pod_kill(self, name: str, target_labels: dict, duration: int = 60, kill_count: int = 1):
        exp = ChaosExperiment(
            name=name,
            experiment_type=ChaosExperimentType.POD_KILL,
            namespace=self.namespace,
            target_labels=target_labels,
            duration_seconds=duration,
            parameters={"kill_count": kill_count, "min_replicas": 1},
        )
        self.add_experiment(exp)
    
    def add_network_latency(self, name: str, target_labels: dict, duration: int = 60, latency_ms: int = 100):
        exp = ChaosExperiment(
            name=name,
            experiment_type=ChaosExperimentType.NETWORK_LATENCY,
            namespace=self.namespace,
            target_labels=target_labels,
            duration_seconds=duration,
            parameters={"latency_ms": latency_ms, "jitter_ms": 10},
        )
        self.add_experiment(exp)
    
    def add_exchange_api_failure(self, name: str, target_labels: dict, duration: int = 60):
        exp = ChaosExperiment(
            name=name,
            experiment_type=ChaosExperimentType.EXCHANGE_API_FAILURE,
            namespace=self.namespace,
            target_labels=target_labels,
            duration_seconds=duration,
            parameters={},
        )
        self.add_experiment(exp)
    
    def add_database_failure(self, name: str, target_labels: dict, duration: int = 60):
        exp = ChaosExperiment(
            name=name,
            experiment_type=ChaosExperimentType.DATABASE_CONNECTION_FAILURE,
            namespace=self.namespace,
            target_labels=target_labels,
            duration_seconds=duration,
            parameters={},
        )
        self.add_experiment(exp)
    
    def add_cpu_stress(self, name: str, target_labels: dict, duration: int = 60, cpu_percent: int = 80):
        exp = ChaosExperiment(
            name=name,
            experiment_type=ChaosExperimentType.CPU_STRESS,
            namespace=self.namespace,
            target_labels=target_labels,
            duration_seconds=duration,
            parameters={"cpu_percent": cpu_percent},
        )
        self.add_experiment(exp)
    
    async def run_all(self, metrics_collector=None) -> list[ExperimentResult]:
        """Run all experiments sequentially.

        In dry-run mode (``dry_run=True``), experiments are simulated locally
        without requiring a Kubernetes cluster.
        """
        for exp in self.experiments:
            if self.dry_run:
                result = await self._dry_run_experiment(exp)
            else:
                runner = self._create_runner(exp)
                if runner:
                    result = await runner.run(metrics_collector)
                else:
                    logger.warning(f"No runner for experiment type: {exp.experiment_type}")
                    continue
            self.results.append(result)
            
            # Wait between experiments
            await asyncio.sleep(0.1 if self.dry_run else 30)
        
        return self.results
    
    async def _dry_run_experiment(self, exp: ChaosExperiment) -> ExperimentResult:
        """Simulate an experiment locally (no cluster)."""
        exp.status = ExperimentStatus.RUNNING
        exp.start_time = datetime.utcnow()
        await asyncio.sleep(0.05)
        exp.status = ExperimentStatus.COMPLETED
        exp.end_time = datetime.utcnow()
        exp.result = {"simulated": True}
        logger.info(f"[dry-run] Simulated {exp.experiment_type.value} on {exp.target_labels}")
        return ExperimentResult(
            experiment_name=exp.name,
            success=True,
            duration_seconds=exp.duration_seconds,
            metrics_before={"simulated": 1.0},
            metrics_after={"simulated": 1.0},
            observations=[
                f"[dry-run] Simulated {exp.experiment_type.value} "
                f"on labels {exp.target_labels}"
            ],
            recommendations=[
                "[dry-run] No real recommendations - run against a cluster "
                "to observe actual impact"
            ],
        )
    
    def _create_runner(self, exp: ChaosExperiment) -> Optional[ChaosExperimentRunner]:
        runners = {
            ChaosExperimentType.POD_KILL: PodKillExperiment,
            ChaosExperimentType.NETWORK_LATENCY: NetworkLatencyExperiment,
            ChaosExperimentType.EXCHANGE_API_FAILURE: ExchangeAPIFailureExperiment,
            ChaosExperimentType.DATABASE_CONNECTION_FAILURE: DatabaseConnectionFailureExperiment,
            ChaosExperimentType.CPU_STRESS: CPUStressExperiment,
        }
        
        runner_class = runners.get(exp.experiment_type)
        if runner_class:
            return runner_class(exp)
        return None
    
    def generate_report(self) -> str:
        """Generate chaos engineering report."""
        lines = [
            "# Chaos Engineering Report",
            f"Generated: {datetime.utcnow().isoformat()}",
            f"Namespace: {self.namespace}",
            "",
            "## Summary",
        ]
        
        passed = sum(1 for r in self.results if r.success)
        failed = sum(1 for r in self.results if not r.success)
        lines.append(f"- Total Experiments: {len(self.results)}")
        lines.append(f"- Passed: {passed}")
        lines.append(f"- Failed: {failed}")
        lines.append("")
        
        for result in self.results:
            status = "✅ PASS" if result.success else "❌ FAIL"
            lines.append(f"### {result.experiment_name} - {status}")
            lines.append(f"- Duration: {result.duration_seconds:.1f}s")
            lines.append(f"- Observations:")
            for obs in result.observations:
                lines.append(f"  - {obs}")
            lines.append(f"- Recommendations:")
            for rec in result.recommendations:
                lines.append(f"  - {rec}")
            lines.append("")
        
        return "\n".join(lines)


# Example usage
async def run_chaos_suite():
    """Run a standard chaos engineering suite."""
    # Load kube config
    try:
        config.load_incluster_config()
    except config.ConfigException:
        config.load_kube_config()
    
    suite = ChaosExperimentSuite(namespace="trading-agent")
    
    # Add experiments
    suite.add_pod_kill(
        "pod-kill-trading-agent",
        {"app": "trading-agent", "component": "core"},
        duration=60,
        kill_count=1,
    )
    
    suite.add_network_latency(
        "network-latency-trading-agent",
        {"app": "trading-agent", "component": "core"},
        duration=60,
        latency_ms=200,
    )
    
    suite.add_exchange_api_failure(
        "exchange-api-failure",
        {"app": "trading-agent", "component": "core"},
        duration=60,
    )
    
    suite.add_database_failure(
        "database-failure",
        {"app": "trading-agent", "component": "core"},
        duration=60,
    )
    
    suite.add_cpu_stress(
        "cpu-stress-trading-agent",
        {"app": "trading-agent", "component": "core"},
        duration=60,
        cpu_percent=90,
    )
    
    # Run suite
    results = await suite.run_all()
    
    # Generate report
    report = suite.generate_report()
    print(report)
    
    # Save report
    with open("chaos_report.md", "w") as f:
        f.write(report)
    
    return results


if __name__ == "__main__":
    asyncio.run(run_chaos_suite())