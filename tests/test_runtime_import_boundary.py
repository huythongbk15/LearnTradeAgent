"""Runtime import boundary for the live runners.

The cron runners must not drag research or LLM code into the process just by
importing it. See docs/operations/CORE_RUNTIME_BOUNDARY.md. Each entrypoint is
loaded (not run) in a fresh interpreter, and the loaded package roots are
compared with the forbidden set.

Research-time fitting (sklearn/scipy) and the strategies that need ml or the
LLM agents are imported where they are used, not at module import.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

ENTRYPOINTS = (
    "scripts/live_enhanced_ma_binance.py",  # Binance testnet, cron every 2h
    "scripts/live_enhanced_ma.py",  # Alpaca paper, via live_cron_runner.py
)

_PROBE = """
import importlib, importlib.util, json, sys
target = sys.argv[1]
if target.endswith(".py"):  # a script: load it without running main()
    spec = importlib.util.spec_from_file_location("runtime_entrypoint", target)
    spec.loader.exec_module(importlib.util.module_from_spec(spec))
else:  # a package module: import it by name
    importlib.import_module(target)
print(json.dumps(sorted(sys.modules)))
"""

FORBIDDEN = (
    "trading_agent.llm",
    "trading_agent.agents",
    "trading_agent.ml",
    "trading_agent.backtest",
    "trading_agent.alpha_research",
    "hmmlearn",
    "sklearn",
    "scipy",
)


def _load(target: str) -> set[str]:
    """Module names loaded by ``target`` (script path or module name) in a fresh interpreter."""
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(
        [str(ROOT / "src"), str(ROOT / "scripts"), env.get("PYTHONPATH", "")]
    ).rstrip(os.pathsep)
    result = subprocess.run(
        [sys.executable, "-c", _PROBE, target],
        cwd=ROOT, env=env, capture_output=True, text=True, timeout=180,
    )
    assert result.returncode == 0, result.stderr[-4000:]
    return set(json.loads(result.stdout.strip().splitlines()[-1]))


def _roots(modules: set[str]) -> set[str]:
    return {
        ".".join(name.split(".")[:2]) if name.startswith("trading_agent.") else name.split(".")[0]
        for name in modules
    }


@pytest.fixture(scope="module", params=ENTRYPOINTS)
def loaded_modules(request) -> set[str]:
    return _load(str(ROOT / request.param))


@pytest.mark.parametrize("forbidden", FORBIDDEN)
def test_runner_does_not_load(loaded_modules, forbidden):
    assert forbidden not in _roots(loaded_modules)


# Direct checks for individual cuts, independent of the package-level list.

def test_runner_does_not_load_the_adaptive_router(loaded_modules):
    assert "trading_agent.authority.adaptive_router" not in loaded_modules


def test_order_planner_does_not_load_the_llm_layer():
    loaded = _load("trading_agent.execution.canonical.order_planner")
    assert not {m for m in loaded if m.startswith("trading_agent.llm")}


def test_admission_registry_build_does_not_load_ml_or_agents():
    # live_admission builds the full canonical allowlist on every run.
    env = dict(os.environ, PYTHONPATH=str(ROOT / "src"))
    code = (
        "import json, sys\n"
        "from trading_agent.strategies.canonical import build_default_registry\n"
        "build_default_registry().describe('regime_switching')\n"
        "print(json.dumps(sorted(sys.modules)))\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], cwd=ROOT, env=env,
        capture_output=True, text=True, timeout=180,
    )
    assert result.returncode == 0, result.stderr[-4000:]
    roots = _roots(set(json.loads(result.stdout.strip().splitlines()[-1])))
    assert not roots & {"trading_agent.ml", "trading_agent.agents", "trading_agent.llm", "hmmlearn"}
