"""
CLI application entry point. Legacy monolith decomposed into
src/trading_agent/cli/commands/* — behavior unchanged.
"""

from __future__ import annotations

import click
from rich.table import Table

from trading_agent.cli._common import config, console


@click.group()
@click.option(
    "--config",
    "-c",
    "config_path",
    type=click.Path(exists=True, dir_okay=False),
    default=None,
    help="Path to config.yaml",
)
@click.version_option("0.1.0", prog_name="trading-agent")
def main(config_path: str | None) -> None:
    """Trading Agent System — Multi-Agent AI Crypto Trading."""
    if config_path:
        from trading_agent.config.loader import Config

        Config(config_path)
        console.print(f"[dim]Loaded config: {config_path}[/dim]")


# ── info ──────────────────────────────────────────────────────────────────


@main.command("info")
def info():
    """Show system configuration summary."""
    console.print("[bold]Trading Agent System[/bold] v0.1.0\n")

    table = Table("Key", "Value")
    table.add_row("Default Exchange", config.default_exchange)
    table.add_row("Default Timeframe", config.default_timeframe)
    table.add_row("Data Storage", config.data_storage)
    table.add_row("Storage Path", str(config.storage_abs_path))
    table.add_row("Enabled Exchanges", ", ".join(config.enabled_exchanges))
    table.add_row("Symbols Tracked", str(sum(len(v) for v in config.symbols.values())))
    table.add_row("Timeframes", ", ".join(config.timeframes))
    table.add_row("LLM Provider", f"{config.llm_provider} / {config.llm_model}")
    table.add_row("Initial Capital", f"${config.initial_capital:,.2f}")
    table.add_row("Commission", f"{config.commission:.3%}")
    table.add_row("Slippage", f"{config.slippage:.3%}")
    console.print(table)

    # Data count
    from trading_agent.data.storage import list_datasets

    datasets = list_datasets()
    if datasets:
        total_rows = 0
        for ds in datasets:
            try:
                from trading_agent.data.storage import get_date_range

                rng = get_date_range(ds["exchange"], ds["symbol"], ds["timeframe"])
                total_rows += rng["count"]
            except Exception:
                pass
        console.print(
            f"\n📊 [bold]{len(datasets)} datasets[/bold], "
            f"[bold]{total_rows:,}[/bold] total candles"
        )


# ── command groups (decomposed) ──────────────────────────────────────────
from trading_agent.cli.commands.agents import agents
from trading_agent.cli.commands.backtest import backtest
from trading_agent.cli.commands.data import data
from trading_agent.cli.commands.deployment import config_group, portfolio
from trading_agent.cli.commands.live import execution, live
from trading_agent.cli.commands.research import llm, meta, options, projection
from trading_agent.cli.commands.system import system

main.add_command(data)
main.add_command(backtest)
main.add_command(config_group)
main.add_command(agents)
main.add_command(execution)
main.add_command(system)
main.add_command(llm)
main.add_command(portfolio)
main.add_command(live)
main.add_command(meta)
main.add_command(projection)
main.add_command(options)


if __name__ == "__main__":
    main()
