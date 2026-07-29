"""
Command-line interface for the Trading Agent System.
"""

from __future__ import annotations

from pathlib import Path

import click
from rich.console import Console
from rich.table import Table

from trading_agent.config.loader import config
from trading_agent.data.collector import Collector, download_all_symbols
from trading_agent.data.storage import list_datasets, load_ohlcv

console = Console()


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


# ── data subcommands ──────────────────────────────────────────────────────


@main.group()
def data():
    """Market data operations."""


@data.command("list-exchanges")
def list_exchanges():
    """List configured exchanges."""
    table = Table("Name", "Type", "Enabled")
    for name, cfg in config.exchanges.items():
        enabled = "✅" if cfg.get("enable") else "❌"
        table.add_row(name, cfg.get("type", "spot"), enabled)
    console.print(table)


@data.command("list-symbols")
@click.argument("exchange", default=None, required=False)
def list_symbols(exchange: str | None):
    """List configured symbols for an exchange."""
    exchange = exchange or config.default_exchange
    symbols = config.symbols.get(exchange, [])
    if not symbols:
        console.print(f"[yellow]No symbols configured for {exchange}[/yellow]")
        return

    table = Table(f"Symbols on {exchange}")
    for s in symbols:
        table.add_row(s)
    console.print(table)


@data.command("fetch")
@click.argument("symbol")
@click.option(
    "--exchange",
    "-e",
    default=None,
    help=f"Exchange (default: {config.default_exchange})",
)
@click.option("--timeframe", "-t", default=None, help="Timeframe")
@click.option(
    "--since",
    "-s",
    default=None,
    help="Start date (ISO format, e.g. 2025-01-01)",
)
@click.option(
    "--limit",
    "-l",
    type=int,
    default=None,
    help="Candles per request",
)
@click.option(
    "--save/--no-save",
    default=True,
    help="Save to parquet storage",
)
def fetch_ohlcv(
    symbol: str,
    exchange: str | None,
    timeframe: str | None,
    since: str | None,
    limit: int | None,
    save: bool,
):
    """Fetch OHLCV data for a symbol."""
    exchange = exchange or config.default_exchange
    timeframe = timeframe or config.default_timeframe

    collector = Collector(exchange)
    console.print(f"Fetching [bold]{symbol}[/bold] {timeframe} from {exchange}…")

    df = collector.fetch_ohlcv(symbol, timeframe, since=since, limit=limit)
    console.print(f"Got [green]{len(df)}[/green] candles")

    if df.is_empty():
        console.print("[yellow]No data returned[/yellow]")
        return

    # Print head
    console.print(df.head(5).to_pandas().to_string(index=False))

    if save:
        from trading_agent.data.storage import save_ohlcv

        path = save_ohlcv(df, exchange, symbol, timeframe)
        console.print(f"Saved to [blue]{path}[/blue]")


@data.command("download-all")
@click.option(
    "--exchange",
    "-e",
    default=None,
    help="Exchange to download from",
)
def download_all(exchange: str | None):
    """Download all configured symbols & timeframes."""
    download_all_symbols(exchange)


@data.command("list-datasets")
def list_datasets_cmd():
    """List available datasets in local storage."""
    datasets = list_datasets()
    if not datasets:
        console.print("[yellow]No datasets found. Run `trading-agent data download-all` first.[/yellow]")
        return

    table = Table("Exchange", "Symbol", "Timeframe")
    for ds in datasets:
        table.add_row(ds["exchange"], ds["symbol"], ds["timeframe"])
    console.print(table)


@data.command("inspect")
@click.argument("symbol")
@click.option("--exchange", "-e", default=None)
@click.option("--timeframe", "-t", default=None)
def inspect_data(symbol: str, exchange: str | None, timeframe: str | None):
    """Inspect stored OHLCV data (row count, date range, head)."""
    exchange = exchange or config.default_exchange
    timeframe = timeframe or config.default_timeframe

    try:
        df = load_ohlcv(exchange, symbol, timeframe)
    except FileNotFoundError as e:
        console.print(f"[red]{e}[/red]")
        return

    console.print(f"[bold]{symbol}[/bold] ({exchange}, {timeframe})")
    console.print(f"  Rows: {len(df):,}")
    console.print(f"  Range: {df['timestamp'].min()} → {df['timestamp'].max()}")
    console.print(f"  Columns: {', '.join(df.columns)}")
    console.print(df.head(5).to_pandas().to_string(index=False))


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
    table.add_row("LLM Provider", f"{config.llm_provider} / {config.llm_model}")
    table.add_row("Initial Capital", f"${config.initial_capital:,.2f}")
    table.add_row("Commission", f"{config.commission:.3%}")
    table.add_row("Slippage", f"{config.slippage:.3%}")
    console.print(table)


if __name__ == "__main__":
    main()
