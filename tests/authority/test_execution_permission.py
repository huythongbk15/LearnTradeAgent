"""Regression contracts for permission enforcement at the execution authority."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

from trading_agent.authority.execution import (
    ExecutionAuthority,
    ExecutionValidationInput,
)
from trading_agent.execution.lifecycle.lifecycle import ExecutionHealth
from trading_agent.execution.permission import PermissionReason


def test_execution_authority_honors_a_blocked_permission_result() -> None:
    lifecycle = MagicMock()
    lifecycle.state.execution_health = ExecutionHealth.NORMAL
    lifecycle.state.reconciliation = SimpleNamespace(value="none")
    lifecycle.state.protection_state = {}
    lifecycle.state.manual_blocked = False
    lifecycle.is_kill_switch_active.return_value = False
    authority = ExecutionAuthority(
        lifecycle=lifecycle,
        gateway=MagicMock(),
        planner=MagicMock(),
    )
    input_ = ExecutionValidationInput(
        intent=SimpleNamespace(side="buy", symbol="BTC/USDT", quantity=0.1),
        observation=SimpleNamespace(timestamp=datetime.now(UTC)),
        portfolio_state=SimpleNamespace(
            available_cash=10_000.0,
            existing_quantity=0.0,
            existing_reservations=0.0,
        ),
        price=SimpleNamespace(mid=50_000.0),
        instrument_rules=MagicMock(),
        risk_decision=None,
    )

    allowed, reason, warnings = authority._validate_permission(input_)

    assert allowed is False
    assert reason == PermissionReason.MISSING_RISK_DECISION
    assert warnings == (PermissionReason.MISSING_RISK_DECISION,)
