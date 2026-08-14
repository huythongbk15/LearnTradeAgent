"""CLI commands — decomposed from the legacy monolith. Behavior unchanged."""

from __future__ import annotations

from pathlib import Path

import click
from rich.table import Table

from trading_agent.cli._common import config, console

# ── data subcommands ──────────────────────────────────────────────────────


@click.group()
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
    from trading_agent.data.collector import Collector

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
    from trading_agent.data.collector import download_all_symbols

    download_all_symbols(exchange)


@data.command("list-datasets")
def list_datasets_cmd():
    """List available datasets in local storage."""
    from trading_agent.data.storage import list_datasets

    datasets = list_datasets()
    if not datasets:
        console.print(
            "[yellow]No datasets found. Run `trading-agent data download-all` first.[/yellow]"
        )
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
    from trading_agent.data.storage import load_ohlcv

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


@data.command("update")
@click.argument("symbol")
@click.option("--exchange", "-e", default=None)
@click.option("--timeframe", "-t", default=None)
@click.option(
    "--since",
    "-s",
    default=None,
    help="Override start date (ISO format)",
)
def update_ohlcv(
    symbol: str,
    exchange: str | None,
    timeframe: str | None,
    since: str | None,
):
    """Incremental update — fetch only new candles since last stored."""
    from trading_agent.data.collector import Collector

    exchange = exchange or config.default_exchange
    timeframe = timeframe or config.default_timeframe

    collector = Collector(exchange)
    console.print(f"Updating [bold]{symbol}[/bold] {timeframe} on {exchange}…")
    df = collector.update_ohlcv(symbol, timeframe, since=since)
    if df.is_empty():
        console.print("[green]✓ Up to date[/green]")
    else:
        console.print(f"[green]✓ {len(df)} new candles[/green]")


@data.command("validate")
@click.option("--exchange", "-e", default=None)
@click.option(
    "--symbol",
    "-s",
    default=None,
    help="Specific symbol (optional — validate all if omitted)",
)
@click.option("--timeframe", "-t", default=None)
def validate_data(
    exchange: str | None,
    symbol: str | None,
    timeframe: str | None,
):
    """Check data quality: gaps, outliers, completeness."""
    from trading_agent.data.collector import Collector, validate_all_symbols

    exchange = exchange or config.default_exchange

    if symbol and timeframe:
        # Single dataset
        collector = Collector(exchange)
        report = collector.validate_data(symbol, timeframe)
        _print_validation_report(exchange, symbol, timeframe, report)
    elif symbol:
        # All timeframes for one symbol
        collector = Collector(exchange)
        for tf in config.timeframes:
            report = collector.validate_data(symbol, tf)
            _print_validation_report(exchange, symbol, tf, report)
    else:
        # All datasets
        reports = validate_all_symbols(exchange)
        if not reports:
            return

        # Summary table
        table = Table("Symbol", "TF", "Status", "Rows", "Gaps", "Outliers")
        for r in reports:
            gaps = r["checks"].get("gaps", {}).get("count", "-")
            outliers = r["checks"].get("price_outliers", {}).get("count", "-")
            rows = r["checks"].get("row_count", "-")
            icon = (
                "✅"
                if r["status"] == "OK"
                else "⚠️"
                if r["status"] == "ISSUES_FOUND"
                else "❌"
            )
            table.add_row(
                r["symbol"],
                r["timeframe"],
                f"{icon} {r['status']}",
                str(rows),
                str(gaps),
                str(outliers),
            )
        console.print(table)


def _print_validation_report(exchange, symbol, tf, report):
    """Pretty-print a single validation report."""
    status_icon = {
        "OK": "✅",
        "ISSUES_FOUND": "⚠️",
        "NO_DATA": "❌",
        "EMPTY": "⚠️",
        "CORRUPTED": "❌",
    }.get(report["status"], "❓")

    console.print(f"\n{status_icon} [bold]{symbol}[/bold] ({exchange}, {tf})")

    if report["status"] in ("NO_DATA", "EMPTY"):
        console.print(f"  {report['status']}")
        return
    if report["status"] == "CORRUPTED":
        console.print(f"  [red]Corrupted: {report.get('error', '')}[/red]")
        return

    c = report["checks"]
    console.print(f"  Rows: {c['row_count']:,}")
    console.print(f"  Range: {c['date_range']['start']} → {c['date_range']['end']}")

    # Gaps
    gaps = c.get("gaps", {})
    if gaps.get("count", 0) > 0:
        console.print(
            f"  [yellow]⚠ Gaps: {gaps['count']} ({gaps['total_missing_candles']:,} missing candles)[/yellow]"
        )
        for s in gaps.get("samples", [])[:3]:
            console.print(
                f"    [dim]{s['from']} → {s['to']}  "
                f"(missing {s['gap_candles']} candles)[/dim]"
            )

    # Nulls
    nulls = {k: v for k, v in c.get("null_counts", {}).items() if v > 0}
    if nulls:
        console.print(f"  [yellow]⚠ Null values: {nulls}[/yellow]")

    # Price outliers
    outliers = c.get("price_outliers", {})
    if outliers and outliers.get("count", 0) > 0:
        console.print(
            f"  [yellow]⚠ Price outliers: {outliers['count']} "
            f"(threshold ±{outliers.get('threshold_pct', '?')})[/yellow]"
        )


@data.command("export")
@click.argument("symbol")
@click.option("--exchange", "-e", default=None)
@click.option("--timeframe", "-t", default=None)
@click.option(
    "--format",
    "-f",
    type=click.Choice(["csv", "json"]),
    default="csv",
    help="Export format",
)
@click.option(
    "--output",
    "-o",
    default=None,
    help="Output file path (default: data/processed/<symbol>_<tf>.<fmt>)",
)
def export_data(
    symbol: str,
    exchange: str | None,
    timeframe: str | None,
    format: str,
    output: str | None,
):
    """Export stored data to CSV or JSON."""
    from trading_agent.data.storage import load_ohlcv

    exchange = exchange or config.default_exchange
    timeframe = timeframe or config.default_timeframe

    try:
        df = load_ohlcv(exchange, symbol, timeframe)
    except FileNotFoundError as e:
        console.print(f"[red]{e}[/red]")
        return

    # Default output path
    if not output:
        safe_sym = symbol.replace("/", "_").replace(":", "_")
        ext = format
        output = str(
            config.project_root / "data" / "processed" / f"{safe_sym}_{timeframe}.{ext}"
        )

    output_path = Path(output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if format == "csv":
        df.write_csv(output_path)
    elif format == "json":
        df.write_json(output_path)

    console.print(
        f"Exported [green]{len(df):,}[/green] rows → [blue]{output_path}[/blue]"
    )


@data.command("enrich-at")
@click.option("--exchange", "-e", default=None)
@click.option("--symbol", "-s", default=None)
@click.option("--timeframe", "-t", default=None)
@click.option("--period", "-p", default=14, type=int, help="ATR period")
@click.option("--all", "-a", "enrich_all", is_flag=True, help="Enrich all datasets")
def enrich_atr_cmd(
    exchange: str | None,
    symbol: str | None,
    timeframe: str | None,
    period: int,
    enrich_all: bool,
):
    """Pre-compute and store ATR for stored OHLCV data."""
    from trading_agent.data.storage import enrich_all_datasets, enrich_with_atr

    if enrich_all:
        console.print(
            f"[cyan]Enriching all datasets with ATR (period={period})...[/cyan]"
        )
        paths = enrich_all_datasets(period=period)
        console.print(f"[green]Enriched {len(paths)} datasets[/green]")
        return

    if not symbol or not timeframe:
        console.print(
            "[red]--symbol and --timeframe required unless --all is used[/red]"
        )
        return

    exchange = exchange or config.default_exchange

    try:
        path = enrich_with_atr(exchange, symbol, timeframe, period=period)
        console.print(
            f"[green]Enriched {exchange} {symbol} {timeframe} → {path}[/green]"
        )
    except FileNotFoundError as e:
        console.print(f"[red]{e}[/red]")
