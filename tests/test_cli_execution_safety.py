from __future__ import annotations

from types import SimpleNamespace

from click.testing import CliRunner

from trading_agent.cli import _paper_execution_error, main


def facade(*, paper: bool):
    return SimpleNamespace(adapter=SimpleNamespace(config=SimpleNamespace(paper=paper)))


def test_execution_gate_requires_all_paper_invariants(monkeypatch):
    monkeypatch.delenv("TRADING_EXECUTION_ENABLED", raising=False)
    assert "not true" in _paper_execution_error("alpaca", facade(paper=True))

    monkeypatch.setenv("TRADING_EXECUTION_ENABLED", "true")
    monkeypatch.setenv("TRADING_MODE", "live")
    assert "TRADING_MODE=paper" in _paper_execution_error("alpaca", facade(paper=True))

    monkeypatch.setenv("TRADING_MODE", "paper")
    assert "restricted" in _paper_execution_error("oanda", facade(paper=True))
    assert "not a verified Paper" in _paper_execution_error("alpaca", facade(paper=False))
    assert _paper_execution_error("alpaca", facade(paper=True)) is None


def test_live_money_connect_is_refused_before_network_access():
    result = CliRunner().invoke(main, ["live", "connect", "--live"])
    assert result.exit_code == 0
    assert "Live-money connections are disabled" in result.output


def test_broken_generic_live_loop_is_explicitly_disabled():
    result = CliRunner().invoke(main, ["live", "run", "BTC/USD", "--execute"])
    assert result.exit_code == 0
    assert "Generic `live run` is disabled" in result.output
