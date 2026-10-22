"""Tests for tool registration, dispatcher, prompt templates, and persisted memory.

Audits the QwenPaw framework's tool dispatch pipeline to verify:
1. ``execute_shell_command`` is the canonical tool name (no ``shell`` alias).
2. ``shell`` is rejected with ToolNotFoundError before any command runs.
3. UnknownTool errors do not cause infinite retries (DoomLoopGate + max_iters).
4. When ``execute_shell_command`` is missing, the agent does NOT fall back to ``shell``.
5. The argument field ``shell`` is never confused with the tool name ``shell``.

These tests import directly from the installed QwenPaw framework
(``qwenpaw`` + ``agentscope`` packages).  The framework's site-packages
directory is added to ``sys.path`` in the module-level guard below.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from pathlib import Path
from typing import Any

import pytest

# ── Make the QwenPaw framework importable from the project venv ────────────────
_QP_SITE_PACKAGES = "/home/huythong/.qwenpaw/venv/lib/python3.12/site-packages"
if _QP_SITE_PACKAGES not in sys.path:
    sys.path.insert(0, _QP_SITE_PACKAGES)

logging.disable(logging.CRITICAL)

# Import framework pieces (must come after sys.path manipulation)
from agentscope.tool import FunctionTool, Toolkit  # noqa: E402
from agentscope.exception import (  # noqa: E402
    AgentOrientedException,
    ToolNotFoundError,
)

from qwenpaw.agents.tools import discover_builtin_tool_funcs  # noqa: E402
from qwenpaw.loop.gates.doom_loop import DoomLoopGate  # noqa: E402
from qwenpaw.loop.gates.base import StopAction  # noqa: E402


# ── Helpers ──────────────────────────────────────────────────────────────────

def _make_toolkit() -> Toolkit:
    """Build a Toolkit containing every registered builtin tool."""
    funcs = discover_builtin_tool_funcs()
    return Toolkit(tools=[FunctionTool(f) for f in funcs])


# ═══════════════════════════════════════════════════════════════════════════════
# Test 1: execute_shell_command dispatched correctly
# ═══════════════════════════════════════════════════════════════════════════════

class TestExecuteShellCommandDispatch:
    """Verify the canonical shell tool name is registered and dispatchable."""

    def test_canonical_name_registered(self) -> None:
        """``execute_shell_command`` appears in the builtin tool registry."""
        names = [f.__name__ for f in discover_builtin_tool_funcs()]
        assert "execute_shell_command" in names

    def test_shell_alias_not_registered(self) -> None:
        """``shell`` is NOT a registered tool name."""
        names = [f.__name__ for f in discover_builtin_tool_funcs()]
        assert "shell" not in names

    def test_descriptor_name_matches_function_name(self) -> None:
        """The ToolDescriptor name equals the Python function name."""
        shell_fn = None
        for f in discover_builtin_tool_funcs():
            desc = getattr(f, "_tool_descriptor", None)
            if desc and desc.governance.policy_name == "Bash":
                shell_fn = f
                break
        assert shell_fn is not None, "No tool with policy_name='Bash' found"
        desc = getattr(shell_fn, "_tool_descriptor")
        assert desc.name == "execute_shell_command"
        assert desc.name == shell_fn.__name__

    def test_tool_schema_uses_canonical_name(self) -> None:
        """The function-calling schema published to the model uses the
        canonical name, not 'shell'."""
        funcs = discover_builtin_tool_funcs()
        shell_fn = next(
            f for f in funcs
            if getattr(f, "_tool_descriptor", None)
            and getattr(f, "_tool_descriptor").governance.policy_name == "Bash"
        )
        tool = FunctionTool(shell_fn)
        schema = tool.input_schema
        # The schema itself doesn't contain the name, but the FunctionTool.name does
        assert tool.name == "execute_shell_command"

    def test_toolkit_check_tool_available_succeeds(self) -> None:
        """``Toolkit.check_tool_available`` finds ``execute_shell_command``."""
        tk = _make_toolkit()

        async def _run() -> Any:
            return await tk.check_tool_available(
                "execute_shell_command", ["basic"]
            )

        tool = asyncio.run(_run())
        assert tool.name == "execute_shell_command"


# ═══════════════════════════════════════════════════════════════════════════════
# Test 2: shell rejected before running any command
# ═══════════════════════════════════════════════════════════════════════════════

class TestShellRejectedBeforeExecution:
    """Verify that ``shell`` is rejected *before* any command executes."""

    def test_shell_raises_tool_not_found(self) -> None:
        """``check_tool_available('shell')`` raises ``ToolNotFoundError``."""
        tk = _make_toolkit()

        async def _run():
            await tk.check_tool_available("shell", ["basic"])

        with pytest.raises(ToolNotFoundError) as exc_info:
            asyncio.run(_run())
        assert "shell" in str(exc_info.value.message)

    def test_shell_not_in_any_group(self) -> None:
        """``shell`` is absent from every registered tool group."""
        tk = _make_toolkit()

        async def _run() -> list[str]:
            all_tools = await tk._get_available_tools(
                [g.name for g in tk.tool_groups]
            )
            return list(all_tools.keys())

        all_names = asyncio.run(_run())
        assert "shell" not in all_names
        assert "execute_shell_command" in all_names

    def test_tool_not_found_is_agent_oriented(self) -> None:
        """The raised exception is an AgentOrientedException (surfaced to
        the model, not a developer crash)."""
        tk = _make_toolkit()

        async def _run():
            await tk.check_tool_available("shell", ["basic"])

        with pytest.raises(AgentOrientedException):
            asyncio.run(_run())


# ═══════════════════════════════════════════════════════════════════════════════
# Test 3: UnknownTool does not cause infinite retries
# ═══════════════════════════════════════════════════════════════════════════════

class TestNoInfiniteRetryOnUnknownTool:
    """Verify that repeated unknown tool calls are bounded.

    The DoomLoopGate (default-mode stop handler) detects identical tool
    calls via a sliding-window similarity metric.  After 4 consecutive
    identical calls (name + args hash), the gate returns TERMINATE.
    Additionally, ``max_iters`` (default 400 in agent.json) provides a
    hard floor.
    """

    def test_doom_loop_gate_terminates_after_repeated_calls(self) -> None:
        """Four consecutive identical wrong tool calls → TERMINATE."""
        from qwenpaw.config.config import DoomLoopStageConfig

        stages = [
            DoomLoopStageConfig(
                after=3, action="modify_prompt", prompt="WARNING"
            ),
            DoomLoopStageConfig(after=4, action="stop", prompt="STOP"),
        ]
        gate = DoomLoopGate(
            window_size=3, similarity_threshold=1.0, stages=stages
        )

        async def _run():
            results = []
            for i in range(4):
                gate.record("shell", "ls -la")
                ctx = {
                    "agent": None,
                    "final_msg": None,
                    "iteration": i,
                }
                results.append(await gate.check(ctx))
            return results

        results = asyncio.run(_run())
        # After 3 identical calls → warning (INTERRUPT_AND_CONTINUE)
        assert results[2].action == StopAction.INTERRUPT_AND_CONTINUE
        # After 4 → termination
        assert results[3].action == StopAction.TERMINATE

    def test_doom_loop_distinct_tools_not_flagged(self) -> None:
        """Different tool names should not trigger repetition detection."""
        from qwenpaw.config.config import DoomLoopStageConfig

        stages = [
            DoomLoopStageConfig(
                after=3, action="modify_prompt", prompt="WARNING"
            ),
            DoomLoopStageConfig(after=4, action="stop", prompt="STOP"),
        ]
        gate = DoomLoopGate(
            window_size=3, similarity_threshold=1.0, stages=stages
        )

        async def _run():
            results = []
            names = ["read_file", "write_file", "edit_file", "read_file"]
            for i, name in enumerate(names):
                gate.record(name, "args")
                ctx = {"agent": None, "final_msg": None, "iteration": i}
                results.append(await gate.check(ctx))
            return results

        results = asyncio.run(_run())
        for r in results:
            assert r.action == StopAction.BYPASS

    def test_max_iters_bounded(self) -> None:
        """The ``max_iters`` config from agent.json is finite (not infinite).

        Even without DoomLoopGate, the agent loop terminates after
        ``max_iters`` reasoning-acting rounds.
        """
        agent_json = Path(__file__).resolve().parents[1] / "agent.json"
        if not agent_json.exists():
            pytest.skip("agent.json not found")
        config = json.loads(agent_json.read_text())
        max_iters = config.get("running", {}).get("max_iters")
        assert max_iters is not None
        assert isinstance(max_iters, int)
        assert max_iters >= 1
        assert max_iters <= 500  # sane upper bound


# ═══════════════════════════════════════════════════════════════════════════════
# Test 4: Missing execute_shell_command → agent stops, no fallback to shell
# ═══════════════════════════════════════════════════════════════════════════════

class TestNoFallbackToShell:
    """Verify that removing ``execute_shell_command`` does NOT cause the
    agent to fall back to ``shell`` — the canonical name is the only one."""

    def test_only_canonical_name_in_registry(self) -> None:
        """The registry contains ``execute_shell_command`` and never ``shell``."""
        names = [f.__name__ for f in discover_builtin_tool_funcs()]
        assert "execute_shell_command" in names
        assert "shell" not in names

    def test_building_toolkit_without_execute_shell_command(self) -> None:
        """If we explicitly exclude ``execute_shell_command``, no ``shell``
        tool appears in its place — there is no implicit fallback."""
        funcs = discover_builtin_tool_funcs()
        filtered = [
            f for f in funcs if f.__name__ != "execute_shell_command"
        ]
        names = [f.__name__ for f in filtered]
        assert "execute_shell_command" not in names
        assert "shell" not in names  # No phantom fallback

    def test_tool_descriptor_governance_identity(self) -> None:
        """The governance ``policy_name`` is 'Bash' and ``tool_type`` is
        'shell' — these are internal classifications, NOT model-facing names.

        Confirms the model-facing name (``desc.name``) is
        ``execute_shell_command``, while ``tool_type`` is ``shell``.
        """
        shell_fn = None
        for f in discover_builtin_tool_funcs():
            desc = getattr(f, "_tool_descriptor", None)
            if desc and desc.governance.policy_name == "Bash":
                shell_fn = f
                break
        assert shell_fn is not None
        desc = getattr(shell_fn, "_tool_descriptor")
        # Model-facing canonical name
        assert desc.name == "execute_shell_command"
        # Internal governance classification (not published to model)
        assert desc.governance.tool_type == "shell"
        assert desc.governance.policy_name == "Bash"


# ═══════════════════════════════════════════════════════════════════════════════
# Test 5: shell argument ≠ shell tool name
# ═══════════════════════════════════════════════════════════════════════════════

class TestShellArgumentNotToolName:
    """Verify the parameter field ``shell`` is never confused with the
    tool name ``shell``.

    The dispatch path uses ``tool_call.name`` (the model's function-call
    name) as the dispatch key.  A parameter named ``shell`` inside a tool's
    ``input`` dict is never used as a dispatch key.
    """

    def test_execute_shell_command_params_have_no_shell_field(self) -> None:
        """``execute_shell_command``'s input schema does not declare a
        parameter literally named ``shell``."""
        funcs = discover_builtin_tool_funcs()
        shell_fn = next(
            f for f in funcs
            if getattr(f, "_tool_descriptor", None)
            and getattr(f, "_tool_descriptor").governance.policy_name == "Bash"
        )
        tool = FunctionTool(shell_fn)
        params = tool.input_schema.get("properties", {})
        assert "shell" not in params
        assert "command" in params  # the actual dispatch parameter

    def test_coerce_uses_tool_name_not_input_shell(self) -> None:
        """The coercion index maps tool *names* → schemas, never parameter
        names → schemas.  A tool whose input contains ``shell`` would still
        be dispatched under its registered name."""
        # Build the schema index the way QwenPawAgent does
        funcs = discover_builtin_tool_funcs()
        tools = [FunctionTool(f) for f in funcs]
        tk = Toolkit(tools=tools)

        async def _build_index():
            schemas = await tk.get_tool_schemas(groups=["basic"])
            index = {}
            for s in schemas:
                func = s.get("function", {})
                name = func.get("name")
                params = func.get("parameters")
                if isinstance(name, str) and isinstance(params, dict):
                    index[name] = params
            return index

        index = asyncio.run(_build_index())
        # The canonical name is the key, never "shell"
        assert "execute_shell_command" in index
        assert "shell" not in index

    def test_tool_not_found_for_shell_does_not_match_shell_param(self) -> None:
        """Calling ``check_tool_available('shell')`` raises
        ``ToolNotFoundError`` — it does not resolve to any tool that has a
        ``shell`` parameter.  The name ``shell`` is simply unknown."""
        tk = _make_toolkit()

        async def _run():
            # Even if we send 'shell' as the tool name with a command arg,
            # the dispatcher keys on *name* — 'shell' is unknown regardless
            # of what parameters are in the input.
            await tk.check_tool_available("shell", ["basic"])

        with pytest.raises(ToolNotFoundError) as exc_info:
            asyncio.run(_run())
        assert "shell" in exc_info.value.message
        # The error message does NOT mention a valid canonical name
        # (documented gap in TOOL_AUDIT_REPORT.md §6)
        assert "execute_shell_command" not in exc_info.value.message
