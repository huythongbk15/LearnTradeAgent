"""Focused tests of the installed dispatcher method, without model/broker calls.

Extract the actual method via AST to avoid constructing an agent or loading
credentials. Base dispatch and error events are spies; this is not an E2E test.

Version dependency
------------------
This file tests a *site-packages hotfix* on the installed QwenPaw runtime
(docs/operations/QWENPAW_TOOL_NAME_GUARD.md).  The hotfix was applied on
2026-10-15 and was **overwritten by the QwenPaw 2.2.2 install on 2026-10-16**.
When the installed runtime no longer contains the guard, the tests below
cannot pass — they are skipped with an explicit reason instead of failing,
so the drift stays visible without turning an environment problem into a
repository failure.

Restore path (operator-controlled, NOT automatic): see
docs/operations/QWENPAW_TOOL_NAME_GUARD.md.  The upstream backup must not be
copied over a newer runtime.
"""
import ast
import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

QP_SITE = Path('/home/huythong/.qwenpaw/venv/lib/python3.12/site-packages')

# Markers that only exist once the guard hotfix is present in the runtime.
_GUARD_MARKERS = ("_shell_guard_count", "TOOL_NAME_LOOP_STOP")


def _installed_runtime_has_guard() -> bool:
    """True when the installed react_agent carries the shell-name guard."""
    source = (QP_SITE / 'qwenpaw/agents/react_agent.py').read_text()
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)) \
                and node.name == '_execute_tool_call':
            body = ast.dump(node)
            return all(marker in body for marker in _GUARD_MARKERS)
    return False


_GUARD_PRESENT = _installed_runtime_has_guard()

requires_guard = pytest.mark.skipif(
    not _GUARD_PRESENT,
    reason=(
        "installed QwenPaw runtime does not contain the shell-name guard "
        "hotfix (docs/operations/QWENPAW_TOOL_NAME_GUARD.md); the hotfix was "
        "overwritten by the 2026-10-16 package install. Restore it under "
        "operator control to re-enable these contract tests."
    ),
)


@pytest.fixture
def agent():
    sys.path.insert(0, str(QP_SITE))
    try:
        from agentscope.message import ToolResultState
        from qwenpaw.loop.gates.base import StopAction, StopHandlerResult
        source = (QP_SITE / 'qwenpaw/agents/react_agent.py').read_text()
        tree = ast.parse(source)
        cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'QwenPawAgent')
        method = next(n for n in cls.body if isinstance(n, ast.AsyncFunctionDef) and n.name == '_execute_tool_call')

        class Base:
            async def _execute_tool_call(self, call, kept_rules=None):
                self.dispatched.append((call.name, kept_rules))
                yield 'dispatched'

            def _coerce_tool_call_input(self, call):
                self.coerced.append(call.name)

            async def _handle_error_tool_call(self, call, message, state):
                assert state == ToolResultState.ERROR
                self.errors.append(message)
                yield 'error'

        wrapper = ast.ClassDef(name='GuardAgent', bases=[ast.Name(id='Base', ctx=ast.Load())], keywords=[], body=[method], decorator_list=[])
        module = ast.fix_missing_locations(ast.Module(body=[wrapper], type_ignores=[]))
        namespace = dict(Base=Base, Any=object, StopAction=StopAction, StopHandlerResult=StopHandlerResult)
        exec(compile(module, str(QP_SITE / 'qwenpaw/agents/react_agent.py'), 'exec'), namespace)
        instance = namespace['GuardAgent']()
        instance.state = SimpleNamespace(reply_id='reply-1')
        instance._tool_schema_index = {'execute_shell_command': {}}
        instance.dispatched, instance.coerced, instance.errors = [], [], []
        yield instance
    finally:
        sys.path.remove(str(QP_SITE))


def invoke(agent, name, rules=None):
    async def run():
        return [evt async for evt in agent._execute_tool_call(SimpleNamespace(name=name, input='{}'), rules)]
    return asyncio.run(run())


@requires_guard
def test_first_alias_returns_canonical_hint_without_dispatch(agent):
    assert invoke(agent, 'shell') == ['error']
    assert not agent.dispatched and not agent.coerced
    assert 'execute_shell_command' in agent.errors[-1]
    assert getattr(agent, '_gate_pending_stop', None) is None


@requires_guard
def test_corrected_call_preserves_base_permission_funnel(agent):
    invoke(agent, 'shell')
    rules = []
    assert invoke(agent, 'execute_shell_command', rules) == ['dispatched']
    assert agent.dispatched == [('execute_shell_command', rules)]


@requires_guard
def test_second_alias_stops_and_blocks_later_batch_calls(agent):
    invoke(agent, 'shell')
    invoke(agent, 'shell')
    assert 'TOOL_NAME_LOOP_STOP' in agent._gate_pending_stop.reason
    assert invoke(agent, 'execute_shell_command') == ['error']
    assert not agent.dispatched


@requires_guard
def test_missing_canonical_schema_stops_immediately(agent):
    agent._tool_schema_index = {}
    invoke(agent, 'shell')
    assert 'TOOL_UNAVAILABLE' in agent._gate_pending_stop.reason
    assert not agent.dispatched


@requires_guard
def test_new_reply_does_not_inherit_alias_count(agent):
    invoke(agent, 'shell')
    invoke(agent, 'shell')
    agent.state.reply_id = 'reply-2'
    agent._gate_pending_stop = None
    invoke(agent, 'shell')
    assert agent._shell_guard_count == 1
    assert agent._gate_pending_stop is None


def test_unrelated_tool_keeps_original_dispatch(agent):
    invoke(agent, 'read_file')
    assert agent.dispatched == [('read_file', None)]
