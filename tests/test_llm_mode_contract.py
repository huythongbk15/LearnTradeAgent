"""Process-isolated contracts for the optional LLM runtime mode."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_llm_disabled_contract_is_independent_of_pytest_import_order() -> None:
    """A fresh process must bind the disabled implementation deterministically."""
    env = os.environ.copy()
    env["USE_LLM"] = "false"
    probe = """
from trading_agent.agents.llm import LLMError, ask_agent, chat, llm_enabled

assert llm_enabled() is False
try:
    chat([{"role": "user", "content": "must stay offline"}], max_tokens=1)
except LLMError:
    pass
else:
    raise AssertionError("chat() must fail closed when USE_LLM=false")

fallback = ask_agent("test", "return JSON")
assert fallback["signal"] == "HOLD"
assert fallback["confidence"] == 0.3
"""

    completed = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=PROJECT_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
