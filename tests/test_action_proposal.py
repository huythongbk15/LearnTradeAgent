"""Tests for structured action proposal and execution contract."""

from __future__ import annotations

import pytest

from trading_agent.execution.proposal import (
    ActionProposal,
    ActionProposalValidationError,
    ActionType,
    compute_context_delta,
    enforce_idempotency_key,
    is_context_stale,
    validate_action_proposal,
    validate_structured_output,
)
from trading_agent.execution.contract import (
    BudgetEnvelope,
    ExecutionContractRegistry,
    ExecutionResult,
    SkillManifest,
    get_registry,
    register_skill,
)
from trading_agent.execution.proposal_executor import ProposalExecutionContext
from trading_agent.execution.boundaries import (
    BoundaryGuard,
    get_boundary_guard,
)


class TestActionType:
    def test_budget_category(self):
        assert ActionType.HOLD.budget_category == "read_only"
        assert ActionType.EXECUTE.budget_category == "write"
        assert ActionType.CANCEL.budget_category == "destructive"

    def test_is_mutable(self):
        assert ActionType.HOLD.is_mutable is False
        assert ActionType.EXECUTE.is_mutable is True

    def test_is_destructive(self):
        assert ActionType.CANCEL.is_destructive is True
        assert ActionType.EXECUTE.is_destructive is False


class TestActionProposal:
    def test_valid_proposal(self):
        proposal = ActionProposal(
            action_type=ActionType.EXECUTE,
            symbol="BTC/USDT",
            params={"quantity": 0.01},
            budget={"category": "write"},
            idempotency_key="abc123",
            context_delta={"price": {"old": 100, "new": 101}},
            justification="enter long",
        )
        assert proposal.proposal_id
        assert proposal.action_type == ActionType.EXECUTE

    def test_to_dict_roundtrip(self):
        proposal = ActionProposal(
            action_type=ActionType.HOLD,
            symbol="ETH/USDT",
            params={},
            budget={"category": "read_only"},
            idempotency_key="def456",
            context_delta={},
            justification="no signal",
        )
        data = proposal.to_dict()
        restored = ActionProposal.from_dict(data)
        assert restored.symbol == proposal.symbol
        assert restored.action_type == proposal.action_type

    def test_invalid_action_type_via_validation(self):
        with pytest.raises(ActionProposalValidationError):
            validate_action_proposal(
                {
                    "action_type": "INVALID",
                    "symbol": "BTC/USDT",
                    "params": {},
                    "budget": {},
                    "idempotency_key": "x",
                    "context_delta": {},
                    "justification": "bad",
                }
            )


class TestValidateActionProposal:
    def test_valid_dict(self):
        proposal = validate_action_proposal(
            {
                "action_type": "execute",
                "symbol": "BTC/USDT",
                "params": {"quantity": 0.01},
                "budget": {"category": "write"},
                "idempotency_key": "key123",
                "context_delta": {},
                "justification": "test",
            }
        )
        assert proposal.symbol == "BTC/USDT"
        assert proposal.action_type == ActionType.EXECUTE

    def test_missing_symbol(self):
        with pytest.raises(ActionProposalValidationError):
            validate_action_proposal(
                {
                    "action_type": "hold",
                    "symbol": "",
                    "params": {},
                    "budget": {},
                    "idempotency_key": "key123",
                    "context_delta": {},
                    "justification": "test",
                }
            )

    def test_mismatched_budget_category(self):
        with pytest.raises(ActionProposalValidationError):
            validate_action_proposal(
                {
                    "action_type": "hold",
                    "symbol": "BTC/USDT",
                    "params": {},
                    "budget": {"category": "write"},
                    "idempotency_key": "key123",
                    "context_delta": {},
                    "justification": "test",
                }
            )

    def test_validate_structured_output(self):
        raw = (
            '{"action_type":"hold","symbol":"BTC/USDT","params":{},'
            '"budget":{"category":"read_only"},"idempotency_key":"k",'
            '"context_delta":{},"justification":"ok"}'
        )
        proposal = validate_structured_output(raw)
        assert proposal.action_type == ActionType.HOLD

    def test_invalid_json(self):
        with pytest.raises(ActionProposalValidationError):
            validate_structured_output("not json")


class TestEnforceIdempotencyKey:
    def test_valid(self):
        proposal = ActionProposal(
            action_type=ActionType.HOLD,
            symbol="BTC/USDT",
            params={},
            budget={"category": "read_only"},
            idempotency_key="a" * 20,
            context_delta={},
            justification="ok",
        )
        enforce_idempotency_key(proposal)

    def test_missing(self):
        proposal = ActionProposal(
            action_type=ActionType.HOLD,
            symbol="BTC/USDT",
            params={},
            budget={"category": "read_only"},
            idempotency_key="",
            context_delta={},
            justification="ok",
        )
        with pytest.raises(ActionProposalValidationError):
            enforce_idempotency_key(proposal)


class TestContextDelta:
    def test_compute_delta(self):
        old = {"price": 100, "regime": "SIDEWAYS"}
        new = {"price": 101, "regime": "SIDEWAYS"}
        delta = compute_context_delta(old, new)
        assert "price" in delta
        assert delta["price"]["old"] == 100
        assert delta["price"]["new"] == 101
        assert "regime" not in delta

    def test_stale_detection(self):
        proposal_delta = {"price": 100}
        current = {"price": 100, "regime": "BULL"}
        assert is_context_stale(proposal_delta, current) is True

    def test_not_stale(self):
        proposal_delta = {"price": 100, "regime": "BULL"}
        current = {"price": 100, "regime": "BULL"}
        assert is_context_stale(proposal_delta, current) is False


class TestBudgetEnvelope:
    def test_read_only_always_affordable(self):
        budget = BudgetEnvelope(category="read_only")
        assert budget.can_afford(9999) is True
        budget.consume(9999)

    def test_write_budget_enforcement(self):
        budget = BudgetEnvelope(
            category="write",
            remaining_hourly=10.0,
            remaining_daily=10.0,
        )
        budget.consume(5.0)
        assert budget.remaining_hourly == 5.0
        with pytest.raises(ValueError):
            budget.consume(10.0)

    def test_reset_if_needed(self):
        budget = BudgetEnvelope(
            category="write",
            remaining_hourly=1.0,
            remaining_daily=1.0,
        )
        # Simulate time passing by monkeypatching datetime
        import datetime as dt
        from unittest.mock import patch
        future = dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=2)
        with patch("trading_agent.execution.contract.datetime") as mock_dt:
            mock_dt.now.return_value = future
            mock_dt.timezone = dt.timezone
            budget.reset_if_needed()
        assert budget.remaining_hourly == budget.total_hourly_budget


class TestExecutionContractRegistry:
    def test_register_and_validate(self):
        registry = get_registry()
        manifest = SkillManifest(
            skill_name="test_skill",
            action_types=[ActionType.HOLD, ActionType.EXECUTE],
            input_schema={},
            output_schema={},
            side_effects=[],
        )

        def handler(proposal, kwargs):
            return ExecutionResult(
                success=True,
                action_type=proposal.action_type,
                skill_name="test_skill",
                output={},
                side_effects=[],
                idempotency_key=proposal.idempotency_key,
            )

        register_skill(
            skill_name="test_skill",
            action_types=[ActionType.HOLD, ActionType.EXECUTE],
            input_schema={},
            output_schema={},
            side_effects=[],
            handler=handler,
        )
        proposal = ActionProposal(
            action_type=ActionType.HOLD,
            symbol="BTC/USDT",
            params={},
            budget={"category": "read_only"},
            idempotency_key="key",
            context_delta={},
            justification="test",
            metadata={"skill_name": "test_skill"},
        )
        validated = registry.validate_proposal(proposal)
        assert validated is not None
        assert validated.skill_name == "test_skill"

    def test_disallowed_action_type(self):
        registry = get_registry()
        proposal = ActionProposal(
            action_type=ActionType.CANCEL,
            symbol="BTC/USDT",
            params={},
            budget={"category": "destructive"},
            idempotency_key="key",
            context_delta={},
            justification="test",
            metadata={"skill_name": "test_skill"},
        )
        with pytest.raises(ActionProposalValidationError):
            registry.validate_proposal(proposal)


class TestProposalExecutionContext:
    def test_execute_valid_proposal(self):
        ctx = ProposalExecutionContext()
        proposal = ActionProposal(
            action_type=ActionType.HOLD,
            symbol="BTC/USDT",
            params={},
            budget={"category": "read_only"},
            idempotency_key="unique_key_1",
            context_delta={"price": 100},
            justification="observe",
            metadata={"skill_name": "test_skill"},
        )

        def handler(p, kwargs):
            return ExecutionResult(
                success=True,
                action_type=p.action_type,
                skill_name="test_skill",
                output={},
                side_effects=[],
                idempotency_key=p.idempotency_key,
            )

        from trading_agent.execution.contract import register_skill
        register_skill(
            skill_name="test_skill",
            action_types=[ActionType.HOLD],
            input_schema={},
            output_schema={},
            side_effects=[],
            handler=handler,
        )
        ctx.update_context({"price": 100})
        result = ctx.execute(proposal)
        assert result.success is True

    def test_duplicate_rejected(self):
        ctx = ProposalExecutionContext()
        proposal = ActionProposal(
            action_type=ActionType.HOLD,
            symbol="BTC/USDT",
            params={},
            budget={"category": "read_only"},
            idempotency_key="dup_key",
            context_delta={},
            justification="first",
            metadata={"skill_name": "test_skill"},
        )

        def handler(p, kwargs):
            return ExecutionResult(
                success=True,
                action_type=p.action_type,
                skill_name="test_skill",
                output={},
                side_effects=[],
                idempotency_key=p.idempotency_key,
            )

        from trading_agent.execution.contract import register_skill
        register_skill(
            skill_name="test_skill",
            action_types=[ActionType.HOLD],
            input_schema={},
            output_schema={},
            side_effects=[],
            handler=handler,
        )
        ctx.update_context({})
        ctx.execute(proposal)
        with pytest.raises(ActionProposalValidationError):
            ctx.execute(proposal)

    def test_stale_context_rejected(self):
        ctx = ProposalExecutionContext(max_staleness_bars=0)
        proposal = ActionProposal(
            action_type=ActionType.HOLD,
            symbol="BTC/USDT",
            params={},
            budget={"category": "read_only"},
            idempotency_key="stale_key",
            context_delta={"price": 100},
            justification="stale",
        )
        ctx.update_context({"price": 101, "regime": "BULL"})
        with pytest.raises(ActionProposalValidationError):
            ctx.execute(proposal)


class TestBoundaryGuard:
    def test_allowed_import(self):
        guard = get_boundary_guard()
        # Should not raise for allowed imports
        guard.check_import("executor", "proposal")

    def test_disallowed_import(self):
        guard = BoundaryGuard()
        with pytest.raises(Exception):
            guard.check_import("monitor", "executor")
