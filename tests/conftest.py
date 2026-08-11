"""Pytest fixtures — ensure src/ is importable regardless of CWD.

This replaces the per-file ``sys.path`` hacks. ``trading_agent`` is also
editable-installed, but keeping the explicit path here makes tests robust
even when the package is not installed (e.g. fresh CI checkout).
"""

from __future__ import annotations

import random
import sys
from pathlib import Path

import numpy as np

SRC = Path(__file__).resolve().parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


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


def pytest_runtest_setup(item) -> None:
    """Re-seed before every test so ordering cannot leak randomness."""
    seed = getattr(item.config.option, "seed", None) or 42
    random.seed(seed)
    np.random.seed(seed)
