"""Pytest fixtures — ensure src/ is importable regardless of CWD.

This replaces the per-file ``sys.path`` hacks. ``trading_agent`` is also
editable-installed, but keeping the explicit path here makes tests robust
even when the package is not installed (e.g. fresh CI checkout).
"""

from __future__ import annotations

import logging
import random
import sys
from pathlib import Path

import numpy as np
import pytest

SRC = Path(__file__).resolve().parent.parent / "src"
PROJECT_ROOT = SRC.parent
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


# One auditable definition of the edit-time P0 regression gate.  Keeping this
# mapping here avoids scattering module-level markers across safety tests and
# makes newly-added critical modules obvious during review.
_P0_MODULES = frozenset(
    {
        "tests/test_alpaca_safety.py",
        "tests/test_binance_live_runner.py",
        "tests/test_ccxt_contract.py",
        "tests/test_ccxt_order_filters.py",
        "tests/test_chaos_invariants.py",
        "tests/test_cli_execution_safety.py",
        "tests/test_decision_trace.py",
        "tests/test_direct_broker_write_guard.py",
        "tests/test_e2e_authority_chain.py",
        "tests/test_execution_hardening.py",
        "tests/test_execution_lifecycle.py",
        "tests/test_execution_simulator_property.py",
        "tests/test_golden_execute_promoted.py",
        "tests/test_live_broker_balances.py",
        "tests/test_live_risk_guard.py",
        "tests/test_live_safety.py",
        "tests/test_live_safety_property.py",
        "tests/test_multi_pair_batch_adversarial.py",
        "tests/test_multi_pair_runtime.py",
        "tests/test_order_permission.py",
        "tests/test_p0_convergence.py",
        "tests/test_paper_exchange_accounting.py",
        "tests/test_promotion_bridge.py",
        "tests/test_risk_decision.py",
        "tests/test_shadow_mainnet.py",
        "tests/test_verify_account_hardening.py",
        "tests/test_verify_provenance.py",
        "tests/test_web_security.py",
        "tests/backtest/test_tournament_faults.py",
    }
)
_P0_PREFIXES = ("tests/authority/", "tests/execution/")


def pytest_configure(config) -> None:
    """Deterministic global seed for the whole test session.

    Audit Phase 4: a global seed fixture pins ``random``/``numpy`` so
    stochastic code paths (bootstrap, ensemble, sampling, weight init)
    are reproducible across runs and CI.
    """
    seed = getattr(config.option, "seed", None) or 42
    random.seed(seed)
    np.random.seed(seed)


def pytest_addoption(parser) -> None:
    parser.addoption(
        "--seed",
        action="store",
        default=42,
        help="Global random seed for deterministic tests",
    )


def pytest_collection_modifyitems(items) -> None:
    """Attach the centralized P0 marker without changing test semantics."""
    for item in items:
        try:
            relative_path = (
                Path(item.path).resolve().relative_to(PROJECT_ROOT).as_posix()
            )
        except ValueError:
            continue
        if relative_path in _P0_MODULES or relative_path.startswith(_P0_PREFIXES):
            item.add_marker(pytest.mark.p0)


def pytest_runtest_setup(item) -> None:
    """Re-seed before every test so ordering cannot leak randomness."""
    seed = getattr(item.config.option, "seed", None) or 42
    random.seed(seed)
    np.random.seed(seed)
    # The simulation engine logs every HOLD bar at INFO.  Capturing tens of
    # thousands of those records makes WFO tests CPU/I/O bound.  Individual
    # logging assertions can still opt back into INFO with caplog.set_level().
    logging.getLogger().setLevel(logging.WARNING)
