"""Pytest fixtures — ensure src/ is importable regardless of CWD.

This replaces the per-file ``sys.path`` hacks. ``trading_agent`` is also
editable-installed, but keeping the explicit path here makes tests robust
even when the package is not installed (e.g. fresh CI checkout).
"""

from __future__ import annotations

import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
