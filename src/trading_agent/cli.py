"""
Command-line interface for the Trading Agent System.
"""

from __future__ import annotations

from pathlib import Path
import subprocess
import time

import click
from rich.console import Console
from rich.table import Table

# ── Lazy imports ─────────────────────────────────────────────────────────
# Heavy modules (polars, ccxt, llm, etc.) are imported inside each function
# to keep CLI startup fast for simple commands (status, reset, etc.).


# ── Lazy module singleton ─────────────────────────────────────────────────
class _LazyConfig:
    """Config loaded on first access — avoids heavy deps at import time."""

    _cached = None

    def __getattr__(self, name):
        if self._cached is None:
            from trading_agent.config.loader import config as _cfg
            self.__class__._cached = _cfg
        return getattr(self._cached, name)


config = _LazyConfig()
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
    from trading_agent.data.collector import download_all_symbols
    download_all_symbols(exchange)


@data.command("list-datasets")
def list_datasets_cmd():
    """List available datasets in local storage."""
    from trading_agent.data.storage import list_datasets
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
    console.print(
        f"Updating [bold]{symbol}[/bold] {timeframe} on {exchange}…"
    )
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
            icon = "✅" if r["status"] == "OK" else "⚠️" if r["status"] == "ISSUES_FOUND" else "❌"
            table.add_row(
                r["symbol"], r["timeframe"],
                f"{icon} {r['status']}",
                str(rows), str(gaps), str(outliers),
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
    console.print(
        f"  Range: {c['date_range']['start']} → {c['date_range']['end']}"
    )

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
        output = str(config.project_root / "data" / "processed" / f"{safe_sym}_{timeframe}.{ext}")

    output_path = Path(output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if format == "csv":
        df.write_csv(output_path)
    elif format == "json":
        df.write_json(output_path)

    console.print(f"Exported [green]{len(df):,}[/green] rows → [blue]{output_path}[/blue]")


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
    table.add_row("Symbols Tracked",
                  str(sum(len(v) for v in config.symbols.values())))
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
        console.print(f"\n📊 [bold]{len(datasets)} datasets[/bold], "
                      f"[bold]{total_rows:,}[/bold] total candles")


# ── backtest subcommands ──────────────────────────────────────────────────


@main.group()
def backtest():
    """Backtest strategies."""


@backtest.command("list")
def list_strategies_cmd():
    """List all registered strategies."""
    from trading_agent.strategies.base import list_strategies
    strategies = list_strategies()
    if not strategies:
        console.print("[yellow]No strategies registered[/yellow]")
        return

    table = Table("Name", "Description")
    for name, desc in strategies.items():
        table.add_row(name, desc)
    console.print(table)


@backtest.command("run")
@click.argument("strategy_name")
@click.option("--symbol", "-s", default="BTC/USDT", help="Symbol")
@click.option("--timeframe", "-t", default="1h", help="Timeframe")
@click.option(
    "--param", "-p", "params", multiple=True,
    help="Strategy params: key=value (e.g. -p fast_period=10 -p slow_period=30)",
)
@click.option("--capital", default=None, type=float, help="Initial capital")
def run_backtest_cmd(
    strategy_name: str,
    symbol: str,
    timeframe: str,
    params: tuple[str],
    capital: float | None,
):
    """Run a backtest for a strategy on a symbol."""
    from trading_agent.backtest.engine import run_backtest
    # Parse params
    param_dict = {}
    for p in params:
        if "=" not in p:
            console.print(f"[red]Invalid param format: {p} (expected key=value)[/red]")
            return
        k, v = p.split("=", 1)
        # Try numeric parsing
        try:
            if "." in v:
                param_dict[k] = float(v)
            else:
                param_dict[k] = int(v)
        except ValueError:
            param_dict[k] = v

    engine_kwargs = {}
    if capital is not None:
        engine_kwargs["initial_capital"] = capital

    console.print(
        f"Running [bold]{strategy_name}[/bold] on "
        f"[bold]{symbol}[/bold] {timeframe}…"
    )

    try:
        result = run_backtest(
            strategy_name,
            symbol=symbol,
            timeframe=timeframe,
            params=param_dict,
            **engine_kwargs,
        )
    except FileNotFoundError as e:
        console.print(f"[red]Data not found: {e}[/red]")
        console.print("[yellow]Run `trading-agent data fetch` first[/yellow]")
        return
    except KeyError as e:
        console.print(f"[red]{e}[/red]")
        return

    # Print results
    _print_backtest_result(result)

    # Print recent trades
    if result.trades:
        console.print("\n[bold]Recent Trades:[/bold]")
        from rich.table import Table as RichTable
        t_table = RichTable("Entry", "Exit", "P&L%", "Bars")
        for trade in result.trades[-5:]:
            icon = "🟢" if trade.pnl_pct > 0 else "🔴"
            t_table.add_row(
                str(trade.entry_date)[:19],
                str(trade.exit_date)[:19],
                f"{icon} {trade.pnl_pct:>+7.2f}%",
                str(trade.bars_held),
            )
        console.print(t_table)


def _print_backtest_result(result):
    """Pretty-print backtest results."""
    from rich.panel import Panel
    from rich.table import Table as RichTable

    # Header
    header = f"[bold]{result.strategy_name.upper()}[/bold] on {result.symbol} {result.timeframe}"

    # Metrics table
    t = RichTable.grid(padding=(0, 2))
    t.add_row()
    t.add_row("Return",  f"[green]{result.total_return_pct:>+8.2f}%[/green]" if result.total_return_pct >= 0 else f"[red]{result.total_return_pct:>+8.2f}%[/red]")
    t.add_row("Ann. Return", f"{result.annualized_return_pct:>+8.2f}%")
    t.add_row("Sharpe", f"{result.sharpe_ratio:>8.2f}")
    t.add_row("Sortino", f"{result.sortino_ratio:>8.2f}")
    t.add_row("Max DD", f"[red]{result.max_drawdown_pct:>8.2f}%[/red]")
    t.add_row("Win Rate", f"{result.win_rate:>8.1%}")
    t.add_row("Profit Factor", f"{result.profit_factor:>8.2f}")
    t.add_row("Trades", f"{result.total_trades:>8d}")
    t.add_row("Avg Hold", f"{result.avg_hold_bars:>8.1f} bars")

    console.print(Panel(t, title=header, border_style="cyan"))


# ── config subcommands ────────────────────────────────────────────────────


@main.group()
def config_group():
    """Configuration management."""


@config_group.command("validate")
@click.option("--config", "-c", "config_path", default=None, help="Config path")
def validate_config(config_path: str | None):
    """Validate config.yaml and report any issues."""
    path = Path(config_path) if config_path else Config.default_path()
    try:
        cfg = Config(path)
        console.print(f"[green]✅ Config valid:[/green] {path}")

        table = Table("Section", "Check", "Status")
        table.add_row("exchanges", f"{len(cfg.exchanges)} configured",
                      "✅" if cfg.enabled_exchanges else "⚠️  none enabled")
        table.add_row("data", f"tf={cfg.default_timeframe}, storage={cfg.data_storage}", "✅")
        table.add_row("symbols",
                      f"{sum(len(v) for v in cfg.symbols.values())} total", "✅")
        table.add_row("backtest",
                      f"capital=${cfg.initial_capital:,.0f}", "✅")
        console.print(table)

    except ConfigError as e:
        console.print(f"[red]❌ Config error: {e}[/red]")
        raise SystemExit(1) from e
    except FileNotFoundError as e:
        console.print(f"[red]❌ File not found: {e}[/red]")
        raise SystemExit(1) from e


# ── agents subcommands ─────────────────────────────────────────────────────


@main.group()
def agents():
    """Multi-agent AI analysis."""


@agents.command("analyze")
@click.argument("symbol", default="BTC/USDT", required=False)
@click.option("--timeframe", "-t", default="1h", help="Timeframe")
@click.option("--position", "-p", default=0.0, type=float,
              help="Current position % (0.0 = flat)")
@click.option("--capital", "-c", default=10000.0, type=float,
              help="Portfolio value")
@click.option("--quiet", "-q", is_flag=True, help="Only print final signal")
def analyze_signal(
    symbol: str,
    timeframe: str,
    position: float,
    capital: float,
    quiet: bool,
):
    """Run multi-agent AI analysis on a symbol."""
    from trading_agent.agents.orchestrator import Orchestrator, print_report

    console.print(f"Running multi-agent analysis on [bold]{symbol}[/bold] {timeframe}…")

    orchestrator = Orchestrator()
    try:
        report = orchestrator.analyze(
            symbol=symbol,
            timeframe=timeframe,
            current_position_pct=position / 100.0,
            portfolio_value=capital,
        )
    except FileNotFoundError as e:
        console.print(f"[red]Data not found: {e}[/red]")
        console.print("[yellow]Run `trading-agent data fetch` first[/yellow]")
        return

    if quiet:
        decision = report.final_decision
        color = "green" if decision.signal == "BUY" else "red" if decision.signal == "SELL" else "yellow"
        console.print(f"[{color}]{decision.signal}[/{color}]  "
                      f"conf={decision.confidence:.0%}  price=${report.current_price:,.2f}  "
                      f"risk={decision.risk_level}")
    else:
        print_report(report)


@agents.command("list")
def list_agents():
    """List available AI agents."""
    from rich.table import Table as RichTable

    agents_info = [
        ("technical_analyst", "Phân tích kỹ thuật: trend, momentum, volatility", "40%"),
        ("sentiment_analyst", "Phân tích sentiment: RSI extremes, volume", "20%"),
        ("risk_manager", "Quản lý rủi ro: volatility, position sizing", "40%"),
        ("trader", "Tổng hợp tín hiệu, weighted voting, final decision", "—"),
    ]

    t = RichTable("Agent", "Role", "Weight")
    for name, desc, weight in agents_info:
        t.add_row(name, desc, weight)
    console.print(t)
    console.print("\n[dim]Flow: Technical → Sentiment → Risk → Trader[/dim]")


# ── execution subcommands ────────────────────────────────────────────────


@main.group()
def execution():
    """Paper trading execution & risk management."""


@execution.command("status")
def execution_status():
    """Show current portfolio status, positions, P&L."""
    from trading_agent.execution.engine import ExecutionEngine
    from rich.table import Table as RichTable
    from rich.panel import Panel

    engine = ExecutionEngine()
    summary = engine.get_summary()
    positions = engine.get_positions_summary()

    # Summary panel
    ret_str = f"[green]{summary['return_pct']:+.2f}%[/green]" if summary['return_pct'] >= 0 else f"[red]{summary['return_pct']:+.2f}%[/red]"
    pnl_str = f"[green]{summary['unrealized_pnl']:+.2f}[/green]" if summary['unrealized_pnl'] >= 0 else f"[red]{summary['unrealized_pnl']:+.2f}[/red]"

    summary_text = (
        f"Equity: [bold]${summary['equity']:,.2f}[/bold]  ({ret_str})\n"
        f"Cash: ${summary['cash']:,.2f}  |  "
        f"Positions: ${summary['positions_value']:,.2f}\n"
        f"Unrealized P&L: {pnl_str}  |  "
        f"Trades: {summary['total_trades']}  |  "
        f"Open Orders: {summary['open_orders']}"
    )
    console.print(Panel(summary_text, title="💰 Portfolio Summary", border_style="green"))

    # Positions table
    if positions:
        t = RichTable("Symbol", "Qty", "Entry", "Current", "P&L%", "Value", "Stop")
        for p in positions:
            color = "green" if p["pnl_pct"] >= 0 else "red"
            stop_str = f"${p['stop_loss']:.1f}" if p['stop_loss'] else "—"
            t.add_row(
                p["symbol"],
                f"{p['quantity']:.4f}",
                f"${p['entry_price']:.2f}",
                f"${p['current_price']:.2f}",
                f"[{color}]{p['pnl_pct']:+.2f}%[/{color}]",
                f"${p['value']:,.2f}",
                stop_str,
            )
        console.print(t)
    else:
        console.print("[dim]No open positions[/dim]")


@execution.command("trades")
@click.option("--limit", "-n", default=10, type=int, help="Number of trades to show")
def execution_trades(limit: int):
    """Show recent trade history."""
    from trading_agent.execution.engine import ExecutionEngine
    from rich.table import Table as RichTable

    engine = ExecutionEngine()
    trades = engine.get_trade_history(limit)

    if not trades:
        console.print("[yellow]No trades yet[/yellow]")
        return

    t = RichTable("Date", "Symbol", "Side", "Entry", "Exit", "P&L%", "Reason")
    for tr in trades:
        pnl_color = "green" if tr.get("pnl_pct", 0) >= 0 else "red"
        entry_time = tr.get("entry_time", "")[:16] if tr.get("entry_time") else "?"
        t.add_row(
            entry_time,
            tr.get("symbol", "?"),
            tr.get("side", "?").upper(),
            f"${tr.get('entry_price', 0):.2f}",
            f"${tr.get('exit_price', 0):.2f}" if tr.get("exit_price") else "—",
            f"[{pnl_color}]{tr.get('pnl_pct', 0):+.2f}%[/{pnl_color}]",
            tr.get("reason", "—"),
        )
    console.print(t)


@execution.command("risk")
def execution_risk_status():
    """Show risk controller status."""
    from trading_agent.execution.engine import ExecutionEngine
    from trading_agent.execution.risk_controller import RiskController
    from rich.panel import Panel
    from rich.table import Table as RichTable

    engine = ExecutionEngine()
    rc = RiskController(engine)
    status = rc.get_status()

    # Run checks to get current warnings
    warnings = rc.check_all()

    # Status table
    t = RichTable("Check", "Current", "Limit", "Status")
    t.add_row(
        "Circuit Breaker",
        "ACTIVE" if status["circuit_breaker_active"] else "OK",
        "—",
        "🔴" if status["circuit_breaker_active"] else "✅",
    )
    t.add_row(
        "Drawdown",
        f"{status['drawdown_pct']:.2f}%",
        f"{status['max_drawdown_limit_pct']:.0f}%",
        "🔴" if status['drawdown_pct'] >= status['max_drawdown_limit_pct'] else "✅",
    )
    t.add_row(
        "Daily Loss",
        f"{status['daily_loss_pct']:.2f}%",
        f"{status['daily_loss_limit_pct']:.0f}%",
        "🔴" if status['daily_loss_pct'] >= status['daily_loss_limit_pct'] else "✅",
    )
    t.add_row(
        "Cooldown",
        "ACTIVE" if status["cooldown_active"] else "OK",
        f"{rc.cooldown_hours:.0f}h",
        "🟡" if status["cooldown_active"] else "✅",
    )

    console.print(Panel(t, title="🛡️ Risk Controller Status", border_style="red"))

    if warnings:
        console.print("\n[bold red]⚠ Active Warnings:[/bold red]")
        for w in warnings:
            console.print(f"  • {w}")

    if status["circuit_breaker_active"]:
        console.print(f"\n[bold red]🔴 CIRCUIT BREAKER: {status['circuit_breaker_reason']}[/bold red]")
        console.print("[yellow]Run `trading-agent execution reset` to reset[/yellow]")


@execution.command("run")
@click.argument("symbol", default="BTC/USDT", required=False)
@click.option("--timeframe", "-t", default="1h", help="Timeframe")
@click.option("--capital", "-c", default=None, type=float, help="Portfolio value override")
@click.option("--stop-loss", "-s", default=0.05, type=float,
              help="Stop-loss distance (e.g. 0.05 = 5%)")
@click.option("--confirm/--auto", default=False,
              help="Prompt before executing trade")
def execution_run(symbol: str, timeframe: str, capital: float | None,
                  stop_loss: float, confirm: bool):
    """Run agents → execute signal → paper trade.

    Full cycle: loads data → runs 4 agents → places order → sets stop-loss.
    """
    from trading_agent.agents.orchestrator import Orchestrator, print_report
    from trading_agent.execution.engine import ExecutionEngine
    from trading_agent.execution.risk_controller import RiskController

    # 1. Get current position if any
    engine = ExecutionEngine(initial_capital=capital)
    rc = RiskController(engine)
    existing_pos = engine.exchange.get_position(symbol)
    current_pos_pct = (existing_pos.quantity * existing_pos.entry_price / engine.exchange.get_total_equity()) if existing_pos and existing_pos.is_active else 0.0
    port_value = capital or engine.exchange.get_total_equity()

    console.print(f"🧠 Running multi-agent analysis for [bold]{symbol}[/bold] {timeframe}…")
    console.print(f"   Current position: {existing_pos.quantity:.4f} {symbol} "
                  f"({current_pos_pct * 100:.1f}% of portfolio)" if existing_pos and existing_pos.is_active else
                  f"   No open position")

    # 2. Run agents
    orchestrator = Orchestrator()
    try:
        report = orchestrator.analyze(
            symbol=symbol,
            timeframe=timeframe,
            current_position_pct=current_pos_pct,
            portfolio_value=port_value,
        )
    except FileNotFoundError as e:
        console.print(f"[red]Data not found: {e}[/red]")
        return

    print_report(report)

    # 3. Execute signal
    decision = report.final_decision
    signal_str = decision.signal

    if signal_str == "HOLD":
        console.print("[yellow]Signal: HOLD — no trade[/yellow]")
        # Still update prices for P&L tracking
        engine.update_from_dataframe(symbol, orchestrator._last_df)
        return

    # Confirm if requested
    if confirm:
        from rich.prompt import Confirm
        if not Confirm.ask(f"Execute {signal_str} signal for {symbol}?"):
            console.print("[yellow]Trade cancelled[/yellow]")
            return

    # 4. Place order
    engine.exchange._last_price_cache[symbol] = report.current_price
    orders = engine.execute_signal(decision)

    if orders:
        for o in orders:
            console.print(f"[green]→ Order placed: {o.side.value.upper()} {o.amount:.4f} {symbol} "
                          f"@ ${o.avg_fill_price or report.current_price:,.2f}[/green]")

        # 5. Set stop-loss if bought
        if signal_str == "BUY" and stop_loss > 0:
            engine.set_stop_loss(symbol, stop_loss)
            pos = engine.exchange.get_position(symbol)
            if pos and pos.stop_loss:
                console.print(f"🛡️  Stop-loss set: ${pos.stop_loss:,.2f} "
                              f"({stop_loss * 100:.1f}% below entry)")

    # 6. Run risk checks
    warnings = rc.check_all()
    if warnings:
        console.print("\n[bold red]⚠ Risk Warnings:[/bold red]")
        for w in warnings:
            console.print(f"  • {w}")
        if rc._circuit_breaker_active:
            console.print("[bold red]🔴 CIRCUIT BREAKER ACTIVATED — all positions closed[/bold red]")

    # Show updated status
    console.print()
    execution_status.callback()


@execution.command("close")
@click.argument("symbol", default=None, required=False)
@click.option("--all", "-a", "close_all", is_flag=True, help="Close all positions")
def execution_close(symbol: str | None, close_all: bool):
    """Close a position or all positions (kill switch)."""
    from trading_agent.execution.engine import ExecutionEngine
    from rich.prompt import Confirm

    engine = ExecutionEngine()

    if close_all or symbol is None:
        if not Confirm.ask("⚠️  Close ALL positions?"):
            return
        engine.close_all(reason="manual_kill")
        console.print("[red]🔴 All positions closed[/red]")
    else:
        pos = engine.exchange.get_position(symbol)
        if not pos or not pos.is_active:
            console.print(f"[yellow]No open position for {symbol}[/yellow]")
            return
        if not Confirm.ask(f"Close {pos.quantity:.4f} {symbol}?"):
            return
        engine.exchange._close_position(symbol, pos.current_price, reason="manual")
        console.print(f"[red]Position closed: {symbol}[/red]")

    execution_status.callback()


@execution.command("reset")
def execution_reset():
    """Reset paper exchange to initial state."""
    from rich.prompt import Confirm
    if not Confirm.ask("⚠️  Reset ALL trade history and state?"):
        return
    from trading_agent.execution.engine import ExecutionEngine
    engine = ExecutionEngine()
    engine.reset()
    console.print("[green]✅ Paper exchange reset[/green]")


# ── system subcommands ────────────────────────────────────────────────────


@main.group()
def system():
    """System health & diagnostics."""


@system.command("health")
def system_health():
    """Comprehensive health check of all components."""
    import subprocess
    import time

    checks = [
        ("Trading Agent HTTP", "curl -sf http://localhost:8000/healthz"),
        ("TimescaleDB", "pg_isready -h timescaledb -p 5432 -U trading -d trading"),
        ("Redis", "redis-cli -h redis -p 6379 ping | grep -q PONG"),
        ("Prometheus", "curl -sf http://prometheus:9090/-/healthy"),
        ("Grafana", "curl -sf http://grafana:3000/api/health | grep -q ok"),
        ("Loki", "curl -sf http://loki:3100/ready"),
        ("Nginx", "curl -sf http://nginx/healthz"),
    ]

    console.print("[bold]Running health checks...[/bold]\n")
    results = []

    for name, cmd in checks:
        start = time.time()
        try:
            result = subprocess.run(cmd, shell=True, timeout=10, capture_output=True)
            elapsed = time.time() - start
            ok = result.returncode == 0
            status = "[green]✓ OK[/green]" if ok else "[red]✗ FAIL[/red]"
            results.append((name, status, f"{elapsed:.2f}s"))
        except subprocess.TimeoutExpired:
            results.append((name, "[red]✗ TIMEOUT[/red]", "10.00s"))
        except Exception as e:
            results.append((name, f"[red]✗ ERROR: {e}[/red]", "—"))

    # Print results table
    from rich.table import Table as RichTable
    t = RichTable("Component", "Status", "Latency")
    for name, status, latency in results:
        t.add_row(name, status, latency)
    console.print(t)

    # Summary
    failed = sum(1 for _, s, _ in results if "FAIL" in s or "TIMEOUT" in s or "ERROR" in s)
    if failed:
        console.print(f"\n[red]❌ {failed} check(s) failed[/red]")
        raise SystemExit(1)
    else:
        console.print("\n[green]✅ All checks passed[/green]")


@system.command("logs")
@click.option("--lines", "-n", default=100, help="Number of lines")
@click.option("--follow", "-f", is_flag=True, help="Follow logs")
@click.option("--component", "-c", default=None,
              type=click.Choice(["agent", "execution", "data", "risk", "all"]),
              help="Filter by component")
def system_logs(lines: int, follow: bool, component: str | None):
    """View recent logs from trading agent container."""
    import subprocess

    # Map component to logger name
    logger_map = {
        "agent": "trading_agent.agents",
        "execution": "trading_agent.execution",
        "data": "trading_agent.data",
        "risk": "trading_agent.execution.risk_controller",
    }

    grep_pattern = logger_map.get(component, "") if component else ""

    cmd = f"docker compose -f docker-compose.prod.yml logs -f --tail {lines} trading-agent"
    if grep_pattern:
        cmd += f" | grep '{grep_pattern}'"

    console.print(f"[dim]Running: {cmd}[/dim]")
    try:
        subprocess.run(cmd, shell=True)
    except KeyboardInterrupt:
        pass


@system.command("metrics")
def system_metrics():
    """Show key Prometheus metrics."""
    import httpx

    console.print("[bold]Fetching metrics...[/bold]\n")

    try:
        response = httpx.get("http://localhost:8000/metrics", timeout=10.0)
        response.raise_for_status()
    except Exception as e:
        console.print(f"[red]Failed to fetch metrics: {e}[/red]")
        return

    # Key metrics to extract
    key_metrics = [
        "trading_equity",
        "trading_cash",
        "trading_positions_value",
        "trading_total_return_pct",
        "trading_sharpe_ratio",
        "trading_max_drawdown_pct",
        "trading_win_rate",
        "trading_trades_total",
        "trading_open_positions",
        "trading_daily_pnl",
        "trading_circuit_breaker_active",
    ]

    metrics = {}
    for line in response.text.splitlines():
        if line.startswith("#") or not line.strip():
            continue
        parts = line.split()
        if len(parts) >= 2:
            name = parts[0]
            # Check if matches any key metric (handles labels)
            if any(km in name for km in key_metrics):
                try:
                    metrics[name] = float(parts[-1])
                except ValueError:
                    metrics[name] = parts[-1]

    if not metrics:
        console.print("[yellow]No matching metrics found[/yellow]")
        return

    from rich.table import Table as RichTable
    t = RichTable("Metric", "Value")
    for k, v in sorted(metrics.items()):
        if isinstance(v, float):
            if "pct" in k or "rate" in k:
                t.add_row(k, f"{v:.2f}%")
            elif "drawdown" in k:
                t.add_row(k, f"[red]{v:.2f}%[/red]" if v > 5 else f"{v:.2f}%")
            elif "sharpe" in k:
                t.add_row(k, f"[green]{v:.2f}[/green]" if v > 1 else f"{v:.2f}")
            elif "win_rate" in k:
                t.add_row(k, f"[green]{v:.1%}[/green]" if v > 0.5 else f"{v:.1%}")
            else:
                t.add_row(k, f"{v:.2f}")
        else:
            t.add_row(k, str(v))
    console.print(t)


if __name__ == "__main__":
    main()
