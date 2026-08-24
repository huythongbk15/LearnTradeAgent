"""Static CI guard: ensure no direct broker/exchange calls outside canonical boundaries.

This test scans the source tree for direct calls to exchange/broker methods
that should only be made through BrokerGateway or adapter boundaries.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

import pytest

# ── Direct broker call patterns ──────────────────────────────────────

# Methods that MUST only be called through BrokerGateway or adapter boundaries
FORBIDDEN_METHODS = {
    "place_order",
    "create_order",
    "cancel_order",
    "close_position",
    "replace_order",
    "close_all_positions",
    "submit_order",
}

# Exact implementation boundaries allowed to perform broker writes.
# Keep this list path-specific so a same-named runtime script cannot bypass CI.
ALLOWED_BOUNDARIES = {
    Path("src/trading_agent/execution/canonical/broker_gateway.py"),
    Path("src/trading_agent/execution/canonical/adapters.py"),
    Path("src/trading_agent/execution/paper_exchange.py"),
    Path("src/trading_agent/exchanges/live_broker.py"),
    Path("src/trading_agent/exchanges/ccxt_adapter.py"),
    Path("src/trading_agent/exchanges/alpaca_adapter.py"),
    Path("src/trading_agent/exchanges/futures/binance_futures.py"),
    Path("src/trading_agent/exchanges/futures/bybit_futures.py"),
    Path("src/trading_agent/exchanges/futures/deribit_options.py"),
}

# Directories to scan
SCAN_DIRS = [
    Path("src"),
    Path("scripts"),
    Path("webui/backend"),
]


def _is_forbidden_call(node: ast.Call, file_path: Path) -> tuple[bool, str | None]:
    """Check if an AST call node is a forbidden direct broker call."""
    if file_path in ALLOWED_BOUNDARIES:
        return False, None
    # Get the method name being called
    if isinstance(node.func, ast.Attribute):
        method_name = node.func.attr
        if method_name not in FORBIDDEN_METHODS:
            return False, None

        # Get the full attribute chain: self.exchange.place_order -> ["self", "exchange", "place_order"]
        attrs = []
        obj = node.func
        while isinstance(obj, ast.Attribute):
            attrs.append(obj.attr)
            obj = obj.value
        if isinstance(obj, ast.Name):
            attrs.append(obj.id)
        attrs.reverse()

        if len(attrs) >= 2:
            root = attrs[0]
            child = attrs[1]

            # Canonical lifecycle/gateway/storage calls are safe
            if root in ("lifecycle", "store", "gateway", "lc", "engine", "simulator"):
                return False, None

            # Canonical self.* calls are safe (lifecycle, gateway, store, engine)
            if root == "self" and child in (
                "lifecycle",
                "gateway",
                "store",
                "engine",
                "planner",
                "simulator",
            ):
                return False, None

            # ExecutionLifecycle may call its own event-recording method. This
            # is not a venue write and remains inside the lifecycle package.
            if (
                file_path == Path("src/trading_agent/execution/lifecycle/lifecycle.py")
                and root == "self"
                and method_name == "submit_order"
            ):
                return False, None

            # Direct exchange/broker calls on self.exchange or self.adapter
            if root == "self" and child in ("exchange", "adapter"):
                return True, method_name

        # Any other object calling a forbidden method is a violation
        # (e.g., client.submit_order, adapter.place_order outside adapter files)
        return True, method_name
    return False, None


def _scan_file_for_forbidden_calls(file_path: Path) -> list[dict[str, Any]]:
    """Scan a Python file for forbidden direct broker calls."""
    violations = []
    try:
        with open(file_path, "r") as f:
            source = f.read()
        tree = ast.parse(source)
    except (SyntaxError, UnicodeDecodeError):
        return violations

    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            is_forbidden, method_name = _is_forbidden_call(node, file_path)
            if is_forbidden:
                violations.append(
                    {
                        "file": str(file_path),
                        "line": node.lineno,
                        "method": method_name,
                    }
                )

    return violations


def _get_python_files(directories: list[Path]) -> list[Path]:
    """Get all Python files in the given directories."""
    files = []
    for directory in directories:
        if directory.exists():
            files.extend(directory.rglob("*.py"))
    return files


class TestDirectBrokerWriteGuard:
    """Static analysis guard against direct broker/exchange calls."""

    def test_no_direct_broker_calls_outside_boundaries(self):
        """Ensure no direct broker calls outside canonical boundaries."""
        python_files = _get_python_files(SCAN_DIRS)
        all_violations = []

        for file_path in python_files:
            if file_path.name.startswith("test_"):
                continue

            violations = _scan_file_for_forbidden_calls(file_path)
            all_violations.extend(violations)

        if all_violations:
            violation_messages = []
            for v in all_violations:
                violation_messages.append(
                    f"  {v['file']}:{v['line']} - direct call to {v['method']}"
                )
            pytest.fail(
                "Direct broker calls detected outside canonical boundaries:\n"
                + "\n".join(violation_messages)
                + "\n\nAll broker calls must flow through BrokerGateway."
            )
