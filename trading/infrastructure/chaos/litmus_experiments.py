"""
Litmus Chaos Experiments for Trading Agent

These are Litmus Chaos workflows that can be applied to Kubernetes cluster.
"""

import yaml

# Litmus Chaos Experiment: Pod Kill
POD_KILL_EXPERIMENT = {
    "apiVersion": "litmuschaos.io/v1alpha1",
    "kind": "ChaosExperiment",
    "metadata": {
        "name": "pod-kill-trading-agent",
        "namespace": "litmus",
    },
    "spec": {
        "definition": {
            "scope": "Namespaced",
            "permissions": [
                {
                    "apiGroups": [""],
                    "resources": ["pods"],
                    "verbs": ["get", "list", "delete"],
                },
                {
                    "apiGroups": ["apps"],
                    "resources": ["deployments"],
                    "verbs": ["get", "list"],
                },
            ],
            "image": "litmuschaos/go-runner:latest",
            "imagePullPolicy": "Always",
            "args": [
                "-c", "pod-delete",
                "-ns", "trading-agent",
                "-l", "app=trading-agent,component=core",
                "-c", "30",
                "-k", "1",
            ],
        },
    },
}

# Litmus Chaos Experiment: Network Latency
NETWORK_LATENCY_EXPERIMENT = {
    "apiVersion": "litmuschaos.io/v1alpha1",
    "kind": "ChaosExperiment",
    "metadata": {
        "name": "network-latency-trading-agent",
        "namespace": "litmus",
    },
    "spec": {
        "definition": {
            "scope": "Namespaced",
            "permissions": [
                {
                    "apiGroups": [""],
                    "resources": ["pods"],
                    "verbs": ["get", "list"],
                },
                {
                    "apiGroups": [""],
                    "resources": ["pods/exec"],
                    "verbs": ["create"],
                },
            ],
            "image": "litmuschaos/go-runner:latest",
            "imagePullPolicy": "Always",
            "args": [
                "-c", "network-latency",
                "-ns", "trading-agent",
                "-l", "app=trading-agent,component=core",
                "-d", "200",
                "-j", "50",
                "-t", "60",
            ],
        },
    },
}

# Litmus Chaos Experiment: CPU Hog
CPU_HOG_EXPERIMENT = {
    "apiVersion": "litmuschaos.io/v1alpha1",
    "kind": "ChaosExperiment",
    "metadata": {
        "name": "cpu-hog-trading-agent",
        "namespace": "litmus",
    },
    "spec": {
        "definition": {
            "scope": "Namespaced",
            "permissions": [
                {
                    "apiGroups": [""],
                    "resources": ["pods"],
                    "verbs": ["get", "list"],
                },
                {
                    "apiGroups": [""],
                    "resources": ["pods/exec"],
                    "verbs": ["create"],
                },
            ],
            "image": "litmuschaos/go-runner:latest",
            "imagePullPolicy": "Always",
            "args": [
                "-c", "cpu-hog",
                "-ns", "trading-agent",
                "-l", "app=trading-agent,component=core",
                "-c", "80",
                "-t", "60",
            ],
        },
    },
}

# Litmus Chaos Experiment: Memory Hog
MEMORY_HOG_EXPERIMENT = {
    "apiVersion": "litmuschaos.io/v1alpha1",
    "kind": "ChaosExperiment",
    "metadata": {
        "name": "memory-hog-trading-agent",
        "namespace": "litmus",
    },
    "spec": {
        "definition": {
            "scope": "Namespaced",
            "permissions": [
                {
                    "apiGroups": [""],
                    "resources": ["pods"],
                    "verbs": ["get", "list"],
                },
                {
                    "apiGroups": [""],
                    "resources": ["pods/exec"],
                    "verbs": ["create"],
                },
            ],
            "image": "litmuschaos/go-runner:latest",
            "imagePullPolicy": "Always",
            "args": [
                "-c", "memory-hog",
                "-ns", "trading-agent",
                "-l", "app=trading-agent,component=core",
                "-c", "80",
                "-t", "60",
            ],
        },
    },
}

# Litmus Chaos Experiment: IO Stress
IO_STRESS_EXPERIMENT = {
    "apiVersion": "litmuschaos.io/v1alpha1",
    "kind": "ChaosExperiment",
    "metadata": {
        "name": "io-stress-trading-agent",
        "namespace": "litmus",
    },
    "spec": {
        "definition": {
            "scope": "Namespaced",
            "permissions": [
                {
                    "apiGroups": [""],
                    "resources": ["pods"],
                    "verbs": ["get", "list"],
                },
                {
                    "apiGroups": [""],
                    "resources": ["pods/exec"],
                    "verbs": ["create"],
                },
            ],
            "image": "litmuschaos/go-runner:latest",
            "imagePullPolicy": "Always",
            "args": [
                "-c", "io-stress",
                "-ns", "trading-agent",
                "-l", "app=trading-agent,component=core",
                "-t", "60",
            ],
        },
    },
}

# Litmus Chaos Experiment: DNS Chaos
DNS_CHAOS_EXPERIMENT = {
    "apiVersion": "litmuschaos.io/v1alpha1",
    "kind": "ChaosExperiment",
    "metadata": {
        "name": "dns-chaos-trading-agent",
        "namespace": "litmus",
    },
    "spec": {
        "definition": {
            "scope": "Namespaced",
            "permissions": [
                {
                    "apiGroups": [""],
                    "resources": ["pods"],
                    "verbs": ["get", "list"],
                },
                {
                    "apiGroups": [""],
                    "resources": ["pods/exec"],
                    "verbs": ["create"],
                },
            ],
            "image": "litmuschaos/go-runner:latest",
            "imagePullPolicy": "Always",
            "args": [
                "-c", "dns-chaos",
                "-ns", "trading-agent",
                "-l", "app=trading-agent,component=core",
                "-d", "timescaledb,redis",
                "-t", "60",
            ],
        },
    },
}

# Litmus Chaos Experiment: Time Skew
TIME_SKEW_EXPERIMENT = {
    "apiVersion": "litmuschaos.io/v1alpha1",
    "kind": "ChaosExperiment",
    "metadata": {
        "name": "time-skew-trading-agent",
        "namespace": "litmus",
    },
    "spec": {
        "definition": {
            "scope": "Namespaced",
            "permissions": [
                {
                    "apiGroups": [""],
                    "resources": ["pods"],
                    "verbs": ["get", "list"],
                },
                {
                    "apiGroups": [""],
                    "resources": ["pods/exec"],
                    "verbs": ["create"],
                },
            ],
            "image": "litmuschaos/go-runner:latest",
            "imagePullPolicy": "Always",
            "args": [
                "-c", "time-skew",
                "-ns", "trading-agent",
                "-l", "app=trading-agent,component=core",
                "-o", "300",  # offset in seconds
                "-t", "60",
            ],
        },
    },
}

# ChaosEngine to run experiments
CHAOS_ENGINE_TEMPLATE = {
    "apiVersion": "litmuschaos.io/v1alpha1",
    "kind": "ChaosEngine",
    "metadata": {
        "name": "trading-agent-chaos-engine",
        "namespace": "trading-agent",
    },
    "spec": {
        "appinfo": {
            "appns": "trading-agent",
            "applabel": "app=trading-agent",
            "appkind": "deployment",
        },
        "chaosServiceAccount": "litmus-admin",
        "experiments": [
            {"name": "pod-kill-trading-agent"},
            {"name": "network-latency-trading-agent"},
            {"name": "cpu-hog-trading-agent"},
            {"name": "memory-hog-trading-agent"},
            {"name": "io-stress-trading-agent"},
            {"name": "dns-chaos-trading-agent"},
            {"name": "time-skew-trading-agent"},
        ],
        "monitoring": True,
        "jobCleanUpPolicy": "delete",
    },
}

# Custom experiment for Exchange API Failure (requires custom chaos library)
EXCHANGE_API_FAILURE_EXPERIMENT = {
    "apiVersion": "litmuschaos.io/v1alpha1",
    "kind": "ChaosExperiment",
    "metadata": {
        "name": "exchange-api-failure-trading-agent",
        "namespace": "litmus",
    },
    "spec": {
        "definition": {
            "scope": "Namespaced",
            "permissions": [
                {
                    "apiGroups": [""],
                    "resources": ["pods"],
                    "verbs": ["get", "list"],
                },
                {
                    "apiGroups": [""],
                    "resources": ["pods/exec"],
                    "verbs": ["create"],
                },
                {
                    "apiGroups": [""],
                    "resources": ["configmaps"],
                    "verbs": ["get", "patch"],
                },
            ],
            "image": "trading-agent/chaos-runner:latest",
            "imagePullPolicy": "IfNotPresent",
            "args": [
                "-c", "exchange-api-failure",
                "-ns", "trading-agent",
                "-l", "app=trading-agent,component=core",
                "-t", "60",
            ],
        },
    },
}


def generate_litmus_experiments(output_dir: str = "./infrastructure/chaos/litmus"):
    """Generate Litmus Chaos experiment YAML files."""
    import os
    os.makedirs(output_dir, exist_ok=True)
    
    experiments = {
        "pod-kill.yaml": POD_KILL_EXPERIMENT,
        "network-latency.yaml": NETWORK_LATENCY_EXPERIMENT,
        "cpu-hog.yaml": CPU_HOG_EXPERIMENT,
        "memory-hog.yaml": MEMORY_HOG_EXPERIMENT,
        "io-stress.yaml": IO_STRESS_EXPERIMENT,
        "dns-chaos.yaml": DNS_CHAOS_EXPERIMENT,
        "time-skew.yaml": TIME_SKEW_EXPERIMENT,
        "exchange-api-failure.yaml": EXCHANGE_API_FAILURE_EXPERIMENT,
        "chaos-engine.yaml": CHAOS_ENGINE_TEMPLATE,
    }
    
    for filename, experiment in experiments.items():
        filepath = os.path.join(output_dir, filename)
        with open(filepath, "w") as f:
            yaml.dump(experiment, f, default_flow_style=False, sort_keys=False)
        print(f"Generated: {filepath}")


if __name__ == "__main__":
    generate_litmus_experiments()