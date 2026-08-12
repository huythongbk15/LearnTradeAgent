"""CLI package — entry point `trading_agent.cli:main` preserved."""

from trading_agent.cli.app import main
from trading_agent.cli.commands.live import _live_adapters, _paper_execution_error

__all__ = ["main", "_live_adapters", "_paper_execution_error"]
