# QwenPaw tool-name guard — 2026-10-15

Local runtime hotfix; not a trading-system change or upstream release.

## Installed change

Target: `/home/huythong/.qwenpaw/venv/lib/python3.12/site-packages/qwenpaw/agents/react_agent.py`.
In `QwenPawAgent._execute_tool_call`, reject the exact forbidden tool name
`shell` before coercion, permissions, or dispatch. Never rewrite it into a
different tool call or execute its command.

- First alias call: emit an ERROR result with the canonical name and schema hint.
- Second alias call in the same reply: schedule termination through the existing
  pending-stop mechanism and reject subsequent tool calls in that reply.
- Missing canonical published schema: schedule termination immediately.
- Valid corrected calls still use the original permission/dispatch funnel.
- Other unknown names retain existing framework behavior. This patch is not a
  generic unknown-tool retry policy.

## Evidence and limitations

Original backup: `data/qwenpaw_tool_guard_20261015/react_agent.py.original`.
Original SHA256: `3c3268502ff6b5d2c28757b110f688680c60e9b075626f6d70a0ab41c6524547`.

Result: `data/qwenpaw_tool_guard_20261015/test_result.json`:
23 passed (6 new dispatcher-method tests + 17 existing framework audit tests).
New tests execute the installed method extracted through AST with base dispatch
and error-event spies. They do not construct a live agent or call a model,
broker, or command tool. Full service/session behavior remains unverified.

An atexit sandbox cleanup error (`ctypes.WinDLL` unavailable on Linux) occurred
after test success; preserved in the result, not repaired as part of this scope.

No service restart, account access, campaign, policy activation, commit or push.
Existing processes may keep the old imported module. Adopt the change through
an operator-controlled service restart when safe, then verify in a fresh turn.

## Maintenance

This is a site-packages hotfix and may be overwritten by QwenPaw upgrades.
Port it to the maintained QwenPaw source before upgrading. Do not automatically
overwrite a newer runtime with this backup. For rollback, compare the current
file against the installed change first and restore only under operator control.
