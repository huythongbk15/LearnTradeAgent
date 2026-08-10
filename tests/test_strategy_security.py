from __future__ import annotations

import asyncio

from typer.testing import CliRunner

from trading_agent.strategies.sandbox import SandboxConfig, SubprocessSandbox
from trading_agent.strategies.versioning import cli as strategy_cli


def test_subprocess_environment_does_not_inherit_secrets(monkeypatch):
    monkeypatch.setenv("BINANCE_API_SECRET", "must-not-leak")
    monkeypatch.setenv("OPENAI_API_KEY", "must-not-leak")
    environment = SubprocessSandbox(SandboxConfig())._subprocess_environment()
    assert "BINANCE_API_SECRET" not in environment
    assert "OPENAI_API_KEY" not in environment


def test_sandbox_ast_blocks_dynamic_import():
    sandbox = SubprocessSandbox(SandboxConfig())
    result = asyncio.run(sandbox.validate("module = __import__('os')"))
    assert not result.success
    assert "Forbidden call" in result.error


def test_strategy_install_requires_explicit_trust(tmp_path, monkeypatch):
    strategy_file = tmp_path / "strategy.py"
    strategy_file.write_text("raise RuntimeError('must not execute')", encoding="utf-8")

    def must_not_initialize_registry():
        raise AssertionError("registry initialized before trust gate")

    monkeypatch.setattr(strategy_cli, "get_registry", must_not_initialize_registry)
    result = CliRunner().invoke(strategy_cli.app, ["install", str(strategy_file)])
    assert result.exit_code == 2
    assert "Refused" in result.stdout
    assert "must not execute" not in result.stdout
