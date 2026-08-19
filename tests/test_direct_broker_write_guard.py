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

# Files that are allowed to call these methods directly
ALLOWED_FILES = {
    "broker_gateway.py",  # BrokerGateway itself
    "paper_exchange.py",  # Exchange adapter implementations
    "runner_adapter.py",  # Canonical wrapper for legacy runners
    "legacy_authorization.py",  # Legacy authorization bridge
    "live_enhanced_ma.py",  # Runtime script (canonical via CanonicalBrokerAdapter)
    "live_enhanced_ma_binance.py",  # Runtime script (canonical via CanonicalBrokerAdapter)
    "test_broker_gateway.py",  # Tests for BrokerGateway
    "close_alpaca_micro_dust.py",  # Canonical lifecycle wrapper
    "cli_adapter.py",  # Canonical CLI adapter bridge for LiveBroker
}

# Directories to scan
SCAN_DIRS = [
    Path("src/trading_agent/execution"),
    Path("src/trading_agent/cli"),
    Path("scripts"),
    Path("webui/backend"),
]


def _is_forbidden_call(node: ast.Call, file_path: Path) -> tuple[bool, str | None]:
    """Check if an AST call node is a forbidden direct broker call."""
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

        # Allowed patterns (canonical boundaries):
        # 1. self._adapter.* in broker_gateway.py
        # 2. self.* in paper_exchange.py, runner_adapter.py, legacy_authorization.py
        # 3. lifecycle.*, store.*, gateway.* (canonical lifecycle/gateway calls)
        # 4. broker.* in live_enhanced_ma*.py (canonical via CanonicalBrokerAdapter)
        # 5. adapter.* in alpaca_adapter.py (adapter implementation)
        if len(attrs) >= 2:
            root = attrs[0]
            child = attrs[1]

            # Canonical lifecycle/gateway/storage calls are safe
            if root in ("lifecycle", "store", "gateway", "lc", "engine"):
                return False, None

            # Adapter implementations can call self.*
            if file_path.name in (
                "paper_exchange.py",
                "runner_adapter.py",
                "legacy_authorization.py",
                "alpaca_adapter.py",
            ):
                if root == "self":
                    return False, None

            # Adapter wrapper delegations (self._adapter.*) are safe in any file
            if root == "self" and child == "_adapter":
                return False, None

            # Legacy scripts using CanonicalBrokerAdapter (broker.*)
            if file_path.name in ("live_enhanced_ma.py", "live_enhanced_ma_binance.py"):
                if root == "broker":
                    return False, None

            # Canonical self.* calls are safe (lifecycle, gateway, store, engine)
            if root == "self" and child in (
                "lifecycle",
                "gateway",
                "store",
                "engine",
                "planner",
                "legacy_adapter",
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
            # Skip test files and allowed files
            if file_path.name in ALLOWED_FILES or file_path.name.startswith("test_"):
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
