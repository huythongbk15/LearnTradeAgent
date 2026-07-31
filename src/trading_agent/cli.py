"""
Command-line interface for the Trading Agent System.
"""

from __future__ import annotations

import pickle
import subprocess
import time
from pathlib import Path

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
    from rich.panel import Panel
    from rich.table import Table as RichTable

    from trading_agent.execution.engine import ExecutionEngine

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
    from rich.table import Table as RichTable

    from trading_agent.execution.engine import ExecutionEngine

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
    from rich.panel import Panel
    from rich.table import Table as RichTable

    from trading_agent.execution.engine import ExecutionEngine
    from trading_agent.execution.risk_controller import RiskController

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
                  "   No open position")

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
    from rich.prompt import Confirm

    from trading_agent.execution.engine import ExecutionEngine

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


@system.command("serve")
@click.option("--port", "-p", default=8000, type=int, help="Metrics server port")
def system_serve(port: int):
    """Start Prometheus metrics server (blocking)."""
    from trading_agent.execution.engine import setup_graceful_shutdown
    from trading_agent.monitoring.metrics_server import serve_forever
    setup_graceful_shutdown()
    serve_forever(port=port)


@system.command("shutdown-test")
def system_shutdown_test():
    """Test graceful shutdown handler installation."""
    import os

    from trading_agent.execution.engine import (
        register_shutdown_handler,
        setup_graceful_shutdown,
    )

    # Register a test handler
    register_shutdown_handler(lambda: console.print("[green]✓ Shutdown handler executed[/green]"))

    # Install signal handlers
    setup_graceful_shutdown()

    console.print("[bold]Graceful shutdown handlers installed[/bold]")
    console.print("Send SIGTERM (Ctrl+C) to test...")
    console.print(f"PID: {os.getpid()}")

    # Wait for signal
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted by user[/yellow]")


@system.command("daily")
@click.option("--send-telegram", is_flag=True, help="Send summary via Telegram")
def system_daily(send_telegram: bool):
    """Generate and print daily performance summary."""
    from trading_agent.execution.engine import ExecutionEngine
    engine = ExecutionEngine()
    summary = engine.get_summary()
    positions = engine.get_positions_summary()
    trades = engine.get_trade_history(limit=100)

    # Compute stats
    total_trades = summary.get("total_trades", 0)
    closed_trades = [t for t in trades if t.get("pnl") is not None]
    wins = sum(1 for t in closed_trades if t.get("pnl", 0) > 0)
    win_rate = wins / len(closed_trades) if closed_trades else 0.0
    total_pnl = sum(t.get("pnl", 0) for t in closed_trades)
    sharpe = summary.get("sharpe_ratio", 0)
    max_dd = summary.get("max_drawdown_pct", 0)

    stats = {
        "total_trades": total_trades,
        "win_rate": win_rate,
        "total_pnl": total_pnl,
        "sharpe_ratio": sharpe,
        "max_drawdown_pct": max_dd,
    }

    # Print to console
    console.print("[bold]📊 Daily Summary[/bold]")
    t = Table("Metric", "Value")
    t.add_row("Total Trades", str(total_trades))
    t.add_row("Win Rate", f"{win_rate:.1%}")
    t.add_row("Total P&L", f"${total_pnl:+.2f}")
    t.add_row("Sharpe", f"{sharpe:.2f}")
    t.add_row("Max DD", f"{max_dd:.2f}%")
    t.add_row("Open Positions", str(len(positions)))
    t.add_row("Equity", f"${summary.get('equity', 0):.2f}")
    console.print(t)

    # Send via Telegram if requested
    if send_telegram:
        from trading_agent.monitoring.alerter import send_daily_summary
        send_daily_summary(stats)
        console.print("[green]✅ Summary sent to Telegram[/green]")


@system.command("health")
def system_health():
    """Comprehensive health check of all components."""
    import socket

    def _tcp_check(host: str, port: int, timeout: float = 3.0) -> bool:
        """Check if a TCP port is open (stdlib only)."""
        try:
            s = socket.create_connection((host, port), timeout=timeout)
            s.close()
            return True
        except OSError:
            return False

    def _http_check(url: str, timeout: float = 5.0) -> bool:
        """HTTP GET via curl (available in runtime image)."""
        try:
            r = subprocess.run(
                ["curl", "-sf", url],
                timeout=timeout, capture_output=True,
            )
            return r.returncode == 0
        except Exception:
            return False

    checks = [
        # Core infra — TCP port check (no extra CLI tools needed)
        ("TimescaleDB", lambda: _tcp_check("timescaledb", 5432)),
        ("Redis",       lambda: _tcp_check("redis", 6379)),
        # HTTP services — curl-based
        ("Grafana",     lambda: _http_check("http://grafana:3000/api/health", timeout=5)),
        # Optional services — only checked if DNS resolves
        ("Prometheus",  lambda: _http_check("http://prometheus:9090/-/healthy") if _tcp_check("prometheus", 9090) else ("skip", "not deployed")),
        ("Loki",        lambda: _http_check("http://loki:3100/ready") if _tcp_check("loki", 3100) else ("skip", "not deployed")),
        ("Nginx",       lambda: _http_check("http://nginx/healthz") if _tcp_check("nginx", 80) else ("skip", "not deployed")),
    ]

    console.print("[bold]Running health checks...[/bold]\n")
    results = []

    for name, check_fn in checks:
        start = time.time()
        try:
            result = check_fn()
            elapsed = time.time() - start
            if isinstance(result, tuple) and result[0] == "skip":
                status = f"[dim]— {result[1]}[/dim]"
            else:
                ok = bool(result)
                status = "[green]✓ OK[/green]" if ok else "[red]✗ FAIL[/red]"
            results.append((name, status, f"{elapsed:.2f}s"))
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


# ── llm subcommands ───────────────────────────────────────────────────────


@main.group()
def llm():
    """LLM cache & cost management."""


@llm.command("cache-stats")
def llm_cache_stats():
    """Show LLM cache statistics."""
    from trading_agent.agents.llm import _CACHE_DIR, _CACHE_TTL_SECONDS

    cache_files = list(_CACHE_DIR.glob("*.pkl"))
    total_size = sum(f.stat().st_size for f in cache_files)
    now = time.time()

    valid = 0
    expired = 0
    for f in cache_files:
        try:
            with open(f, "rb") as fp:
                data = pickle.load(fp)
            cached_time = data.get("timestamp", 0)
            if now - cached_time < _CACHE_TTL_SECONDS:
                valid += 1
            else:
                expired += 1
        except Exception:
            expired += 1

    t = Table("Metric", "Value")
    t.add_row("Cache Directory", str(_CACHE_DIR))
    t.add_row("Total Files", str(len(cache_files)))
    t.add_row("Valid (TTL)", str(valid))
    t.add_row("Expired", str(expired))
    t.add_row("Total Size", f"{total_size / 1024 / 1024:.2f} MB")
    t.add_row("TTL", f"{_CACHE_TTL_SECONDS / 3600:.0f} hours")
    console.print(t)


@llm.command("cache-clear")
@click.option("--all", "clear_all", is_flag=True, help="Clear all cache (including valid)")
@click.confirmation_option(prompt="Clear LLM cache?")
def llm_cache_clear(clear_all: bool):
    """Clear LLM cache."""
    from trading_agent.agents.llm import _CACHE_DIR

    cache_files = list(_CACHE_DIR.glob("*.pkl"))
    removed = 0
    for f in cache_files:
        try:
            with open(f, "rb") as fp:
                data = pickle.load(fp)
            cached_time = data.get("timestamp", 0)
            if clear_all or time.time() - cached_time >= _CACHE_TTL_SECONDS:
                f.unlink()
                removed += 1
        except Exception:
            f.unlink()
            removed += 1

    console.print(f"[green]✅ Cleared {removed} cache files[/green]")


@llm.command("cost-estimate")
@click.option("--daily-trades", default=12, help="Estimated trades per day")
@click.option("--calls-per-trade", default=4, help="LLM calls per trade (4 agents)")
@click.option("--tokens-per-call", default=1500, help="Avg tokens per call")
def llm_cost_estimate(daily_trades: int, calls_per_trade: int, tokens_per_call: int):
    """Estimate monthly LLM cost."""
    daily_calls = daily_trades * calls_per_trade
    daily_tokens = daily_calls * tokens_per_call
    monthly_tokens = daily_tokens * 30

    # Free tier estimates (rough)
    console.print("[bold]📊 Monthly LLM Cost Estimate[/bold]")
    t = Table("Provider (Free Tier)", "Monthly Calls", "Monthly Tokens", "Est. Cost")
    t.add_row("OpenCode (deepseek-v4-flash-free)", f"{daily_calls * 30:,}", f"{monthly_tokens:,}", "$0.00")
    t.add_row("DeepSeek API (free tier)", f"{daily_calls * 30:,}", f"{monthly_tokens:,}", "$0.00*")
    t.add_row("NVIDIA NIM (free tier)", f"{daily_calls * 30:,}", f"{monthly_tokens:,}", "$0.00*")
    t.add_row("Groq (free tier)", f"{daily_calls * 30:,}", f"{monthly_tokens:,}", "$0.00*")
    t.add_row("OpenRouter (free models)", f"{daily_calls * 30:,}", f"{monthly_tokens:,}", "$0.00*")
    console.print(t)

    console.print(f"\nWith cache (80% hit rate): {monthly_tokens * 0.2:,.0f} tokens → $0.00")
    console.print("[dim]* Free tier limits apply (rate limits, context window)[/dim]")


# ── portfolio subcommands ─────────────────────────────────────────────────


@main.group()
def portfolio():
    """Portfolio optimization, rebalancing, and management."""


@portfolio.command("optimize")
@click.argument("symbols", nargs=-1, required=True)
@click.option("--timeframe", "-t", default="1h", help="Timeframe for returns")
@click.option(
    "--method",
    "-m",
    type=click.Choice([
        "max_sharpe", "min_variance", "mean_variance", "hrp",
        "black_litterman", "risk_parity", "max_div", "equal_weight"
    ]),
    default="max_sharpe",
    help="Optimization method",
)
@click.option("--risk-free", default=0.02, type=float, help="Risk-free rate")
@click.option("--lookback", default=252, type=int, help="Lookback period (days)")
@click.option("--cov-method", default="ledoit_wolf",
              type=click.Choice(["sample", "ledoit_wolf", "ewma"]),
              help="Covariance estimation method")
@click.option("--min-weight", default=0.0, type=float, help="Minimum weight per asset")
@click.option("--max-weight", default=1.0, type=float, help="Maximum weight per asset")
@click.option("--target-return", default=None, type=float,
              help="Target return for mean-variance optimization")
@click.option("--turnover", default=None, type=float,
              help="Max turnover from current portfolio")
@click.option("--view", "views", multiple=True, help="Black-Litterman view: SYMBOL=RETURN (e.g., BTC/USDT=0.15)")
@click.option("--confidence", "confidences", multiple=True, type=float, help="Confidence for each view (0-1)")
def portfolio_optimize(
    symbols: tuple[str],
    timeframe: str,
    method: str,
    risk_free: float,
    lookback: int,
    cov_method: str,
    min_weight: float,
    max_weight: float,
    target_return: float | None,
    turnover: float | None,
    views: tuple[str],
    confidences: tuple[float],
):
    """Optimize portfolio weights using various methods."""
    from rich.panel import Panel
    from rich.table import Table as RichTable

    from trading.portfolio.portfolio_optimizer import (
        PortfolioOptimizer, OptimizerMethod, OptimizationConstraints,
    )
    from trading.exchanges.models import Symbol, AssetClass, MarketType
    from trading_agent.data.storage import load_ohlcv

    console.print(f"[bold]Optimizing portfolio with {len(symbols)} assets using {method}...[/bold]")

    # Load data for all symbols
    returns_data = {}
    symbol_objs = []
    for sym_str in symbols:
        try:
            df = load_ohlcv(config.default_exchange, sym_str, timeframe)
            if len(df) == 0:
                console.print(f"[red]No data for {sym_str}[/red]")
                return
            # Polars DataFrame -> pandas Series for pct_change
            import pandas as pd
            close_series = pd.Series(df['close'].to_numpy())
            returns = close_series.pct_change().dropna()
            returns_data[sym_str] = returns
            # Create Symbol object
            base, quote = sym_str.split('/') if '/' in sym_str else (sym_str, 'USDT')
            symbol_objs.append(Symbol(base, quote, AssetClass.CRYPTO, MarketType.SPOT, "binance"))
        except FileNotFoundError:
            console.print(f"[red]Data not found for {sym_str}. Run `trading-agent data fetch {sym_str}` first.[/red]")
            return
        except Exception as e:
            console.print(f"[red]Error loading {sym_str}: {e}[/red]")
            return

    # Align returns
    returns_df = pd.DataFrame(returns_data)
    returns_df = returns_df.dropna()
    console.print(f"Aligned data: {len(returns_df)} observations")

    # Current weights (equal for now)
    current_weights = {s: 1.0 / len(symbols) for s in symbol_objs}

    # Setup constraints
    constraints = OptimizationConstraints(
        min_weight=min_weight,
        max_weight=max_weight,
        max_turnover=turnover,
        current_weights=current_weights,
        target_return=target_return,
    )

    # Create optimizer
    opt_method = OptimizerMethod(method)
    optimizer = PortfolioOptimizer(
        risk_free_rate=risk_free,
        method=opt_method,
        constraints=constraints,
        cov_method=cov_method,
        lookback=lookback,
    ).set_universe(symbol_objs, returns_df, current_weights)

    # Handle Black-Litterman views
    bl_views = None
    if method == "black_litterman" and views:
        from trading.portfolio.portfolio_optimizer import BlackLittermanViews
        absolute_views = {}
        confidence_dict = {}
        for i, view_str in enumerate(views):
            if "=" in view_str:
                sym, ret = view_str.split("=", 1)
                absolute_views[sym.strip()] = float(ret)
                if i < len(confidences):
                    confidence_dict[('absolute', sym.strip())] = confidences[i]
        bl_views = BlackLittermanViews(absolute=absolute_views, confidence=confidence_dict)

    # Run optimization
    result = optimizer.optimize(views=bl_views)

    if not result.success:
        console.print(f"[red]Optimization failed: {result.message}[/red]")
        return

    # Display results
    t = RichTable("Asset", "Weight", "Exp. Return", "Exp. Vol")
    for s, w in result.weights.items():
        t.add_row(
            f"{s.base}/{s.quote}",
            f"{float(w):.2%}",
            f"{float(result.expected_return):.2%}",
            f"{float(result.expected_volatility):.2%}",
        )
    console.print(Panel(t, title=f"Portfolio Weights ({method})", border_style="cyan"))

    # Metrics
    m = RichTable.grid(padding=(0, 2))
    m.add_row("Expected Return", f"{float(result.expected_return):.2%}")
    m.add_row("Expected Volatility", f"{float(result.expected_volatility):.2%}")
    m.add_row("Sharpe Ratio", f"{float(result.sharpe_ratio):.2f}")
    m.add_row("Diversification Ratio", f"{float(result.diversification_ratio):.2f}")
    m.add_row("VaR (95%)", f"{float(result.var_95):.2%}")
    m.add_row("CVaR (95%)", f"{float(result.cvar_95):.2%}")
    console.print(Panel(m, title="📊 Portfolio Metrics", border_style="green"))

    # Black-Litterman posterior
    if result.posterior_returns:
        console.print("\n[bold]Black-Litterman Posterior Returns:[/bold]")
        bl_table = RichTable("Asset", "Posterior Return")
        for s, r in result.posterior_returns.items():
            bl_table.add_row(f"{s.base}/{s.quote}", f"{float(r):.2%}")
        console.print(bl_table)


@portfolio.command("frontier")
@click.argument("symbols", nargs=-1, required=True)
@click.option("--timeframe", "-t", default="1h", help="Timeframe")
@click.option("--points", default=50, type=int, help="Number of frontier points")
@click.option("--risk-free", default=0.02, type=float, help="Risk-free rate")
@click.option("--lookback", default=252, type=int, help="Lookback period (days)")
@click.option("--min-return", default=None, type=float, help="Min target return")
@click.option("--max-return", default=None, type=float, help="Max target return")
def portfolio_frontier(
    symbols: tuple[str],
    timeframe: str,
    points: int,
    risk_free: float,
    lookback: int,
    min_return: float | None,
    max_return: float | None,
):
    """Generate efficient frontier for visualization."""
    import plotext as plt

    from trading.portfolio.portfolio_optimizer import PortfolioOptimizer, OptimizerMethod, OptimizationConstraints
    from trading.exchanges.models import Symbol, AssetClass, MarketType
    from trading_agent.data.storage import load_ohlcv

    console.print(f"[bold]Generating efficient frontier for {len(symbols)} assets...[/bold]")

    # Load data
    returns_data = {}
    symbol_objs = []
    for sym_str in symbols:
        try:
            df = load_ohlcv(config.default_exchange, sym_str, timeframe)
            import pandas as pd
            close_series = pd.Series(df['close'].to_numpy())
            returns = close_series.pct_change().dropna()
            returns_data[sym_str] = returns
            base, quote = sym_str.split('/') if '/' in sym_str else (sym_str, 'USDT')
            symbol_objs.append(Symbol(base, quote, AssetClass.CRYPTO, MarketType.SPOT, "binance"))
        except FileNotFoundError:
            console.print(f"[red]Data not found for {sym_str}[/red]")
            return

    returns_df = pd.DataFrame(returns_data).dropna()

    optimizer = PortfolioOptimizer(
        risk_free_rate=risk_free,
        method=OptimizerMethod.MEAN_VARIANCE,
        lookback=lookback,
    ).set_universe(symbol_objs, returns_df)

    returns, vols, weights = optimizer.efficient_frontier(
        n_points=points,
        min_return=min_return,
        max_return=max_return,
    )

    if not returns:
        console.print("[red]Could not generate frontier[/red]")
        return

    # Plot using plotext (terminal plotting)
    plt.clear_figure()
    plt.scatter(vols, returns)
    plt.title("Efficient Frontier")
    plt.xlabel("Volatility (Annualized)")
    plt.ylabel("Return (Annualized)")
    plt.show()

    # Print table
    from rich.table import Table as RichTable
    t = RichTable("Return", "Volatility", "Sharpe")
    for r, v in zip(returns, vols):
        sharpe = (r - risk_free) / v if v > 0 else 0
        t.add_row(f"{r:.2%}", f"{v:.2%}", f"{sharpe:.2f}")
    console.print(t)


@portfolio.command("monte-carlo")
@click.argument("symbols", nargs=-1, required=True)
@click.option("--timeframe", "-t", default="1h", help="Timeframe")
@click.option("--method", "-m", default="max_sharpe", help="Optimization method for weights")
@click.option("--simulations", "-n", default=200, type=int, help="Number of simulations")
@click.option("--horizon", "-h", default=252, type=int, help="Time horizon (days)")
@click.option("--capital", "-c", default=100000, type=float, help="Initial capital")
def portfolio_monte_carlo(
    symbols: tuple[str],
    timeframe: str,
    method: str,
    simulations: int,
    horizon: int,
    capital: float,
):
    """Run Monte Carlo portfolio simulation."""
    from rich.panel import Panel
    from rich.table import Table as RichTable

    from trading.portfolio.portfolio_optimizer import PortfolioOptimizer, OptimizerMethod, OptimizationConstraints
    from trading.exchanges.models import Symbol, AssetClass, MarketType
    from trading_agent.data.storage import load_ohlcv

    console.print(f"[bold]Running Monte Carlo simulation ({simulations} paths, {horizon} days)...[/bold]")

    # Load data
    returns_data = {}
    symbol_objs = []
    for sym_str in symbols:
        try:
            df = load_ohlcv(config.default_exchange, sym_str, timeframe)
            import pandas as pd
            close_series = pd.Series(df['close'].to_numpy())
            returns = close_series.pct_change().dropna()
            returns_data[sym_str] = returns
            base, quote = sym_str.split('/') if '/' in sym_str else (sym_str, 'USDT')
            symbol_objs.append(Symbol(base, quote, AssetClass.CRYPTO, MarketType.SPOT, "binance"))
        except FileNotFoundError:
            console.print(f"[red]Data not found for {sym_str}[/red]")
            return

    returns_df = pd.DataFrame(returns_data).dropna()

    # Optimize first to get weights
    optimizer = PortfolioOptimizer(
        method=OptimizerMethod(method),
    ).set_universe(symbol_objs, returns_df)

    opt_result = optimizer.optimize()

    if not opt_result.success:
        console.print(f"[red]Optimization failed: {opt_result.message}[/red]")
        return

    # Run simulation
    mc_result = optimizer.monte_carlo_simulation(
        weights=opt_result.weights,
        n_simulations=simulations,
        time_horizon=horizon,
        initial_value=capital,
    )

    # Display results
    t = RichTable("Metric", "Value")
    t.add_row("Initial Capital", f"${capital:,.2f}")
    t.add_row("Mean Final Value", f"${mc_result['mean_final']:,.2f}")
    t.add_row("Median Final Value", f"${mc_result['median_final']:,.2f}")
    t.add_row("5th Percentile", f"${mc_result['pct_5']:,.2f}")
    t.add_row("95th Percentile", f"${mc_result['pct_95']:,.2f}")
    t.add_row("Probability of Loss", f"{mc_result['prob_loss']:.1%}")
    t.add_row("Probability of >10% Gain", f"{mc_result['prob_10pct_gain']:.1%}")
    t.add_row("Probability of >20% Gain", f"{mc_result['prob_20pct_gain']:.1%}")

    console.print(Panel(t, title="🎲 Monte Carlo Results", border_style="magenta"))

    # Show weight distribution
    console.print("\n[bold]Optimal Weights:[/bold]")
    w_table = RichTable("Asset", "Weight")
    for s, w in opt_result.weights.items():
        w_table.add_row(f"{s.base}/{s.quote}", f"{float(w):.2%}")
    console.print(w_table)


# ── rebalancer subcommands ───────────────────────────────────────────────


@portfolio.group()
def rebalancer():
    """Auto-rebalancer management."""


@rebalancer.command("status")
@click.option("--symbols", "-s", default=None, help="Comma-separated symbols")
@click.option("--exchange", "-e", default=None, help="Exchange")
def rebalancer_status(symbols: str | None, exchange: str | None):
    """Show rebalancer status and pending actions."""
    from rich.table import Table as RichTable

    from trading.portfolio.auto_rebalancer import AutoRebalancer, CalendarRebalanceStrategy
    from trading.exchanges.models import Symbol, AssetClass, MarketType
    from trading_agent.execution.engine import ExecutionEngine

    # Create rebalancer
    engine = ExecutionEngine()
    positions = engine.get_positions_summary()

    if symbols:
        sym_list = [s.strip() for s in symbols.split(',')]
    else:
        sym_list = [p['symbol'] for p in positions]

    console.print(f"[bold]Rebalancer Status for {len(sym_list)} symbols[/bold]")

    t = RichTable("Symbol", "Current %", "Target %", "Drift", "Trigger", "Status")
    for sym_str in sym_list:
        # Find position
        pos = next((p for p in positions if p['symbol'] == sym_str), None)
        current_pct = pos['value'] / engine.exchange.get_total_equity() * 100 if pos else 0
        target_pct = 100 / len(sym_list)  # Equal weight target
        drift = current_pct - target_pct

        # Check if rebalance needed (simple threshold)
        trigger = "threshold" if abs(drift) > 5 else "none"
        status = "⚠️ REBALANCE" if abs(drift) > 5 else "✅ OK"

        t.add_row(
            sym_str,
            f"{current_pct:.1f}%",
            f"{target_pct:.1f}%",
            f"{drift:+.1f}%",
            trigger,
            status,
        )
    console.print(t)


@rebalancer.command("run")
@click.argument("symbols", nargs=-1, required=True)
@click.option("--timeframe", "-t", default="1h", help="Timeframe")
@click.option("--method", "-m",
              type=click.Choice(["calendar", "threshold", "cppi", "risk_budget"]),
              default="threshold",
              help="Rebalancing method")
@click.option("--threshold", "-d", default=0.05, type=float, help="Drift threshold for threshold method")
@click.option("--frequency", "-f", default="1d", help="Frequency for calendar method")
@click.option("--floor", default=0.8, type=float, help="Floor for CPPI")
@click.option("--multiplier", default=3.0, type=float, help="Multiplier for CPPI")
@click.option("--dry-run/--execute", default=True, help="Dry run (no actual trades)")
def rebalancer_run(
    symbols: tuple[str],
    timeframe: str,
    method: str,
    threshold: float,
    frequency: str,
    floor: float,
    multiplier: float,
    dry_run: bool,
):
    """Run rebalancing for specified symbols."""
    from trading.portfolio.auto_rebalancer import (
        AutoRebalancer,
        CalendarRebalanceStrategy,
        ThresholdRebalanceStrategy,
        CPPIRebalanceStrategy,
        RiskBudgetRebalanceStrategy,
        RebalanceConfig,
        RebalanceTrigger,
    )
    from trading.exchanges.models import Symbol, AssetClass, MarketType, Position
    from trading_agent.execution.engine import ExecutionEngine
    from trading_agent.data.storage import load_ohlcv
    from decimal import Decimal
    import asyncio

    console.print(f"[bold]Running {method} rebalancer for {len(symbols)} symbols...[/bold]")

    engine = ExecutionEngine()
    positions = engine.get_positions_summary()
    equity = engine.exchange.get_total_equity()

    # Create rebalancer config
    config = RebalanceConfig(
        threshold_enabled=method == "threshold",
        threshold_band_pct=threshold,
        calendar_enabled=method == "calendar",
        calendar_frequency=frequency,
        cppi_enabled=method == "cppi",
        cppi_multiplier=multiplier,
        cppi_floor_pct=floor,
        risk_budget_enabled=method == "risk_budget",
    )

    # Create strategy (stateless, config passed to methods)
    if method == "calendar":
        strategy = CalendarRebalanceStrategy()
    elif method == "threshold":
        strategy = ThresholdRebalanceStrategy()
    elif method == "cppi":
        strategy = CPPIRebalanceStrategy()
    elif method == "risk_budget":
        strategy = RiskBudgetRebalanceStrategy()
    else:
        strategy = ThresholdRebalanceStrategy()

    rebalancer = AutoRebalancer(config)

    # Get target weights (equal weight for now)
    target_weights = {Symbol(s.split('/')[0], s.split('/')[1] if '/' in s else 'USDT',
                           AssetClass.CRYPTO, MarketType.SPOT, "binance"): 1.0/len(symbols)
                      for s in symbols}

    # Get current prices and create Position objects
    current_prices = {}
    positions_dict = {}
    for sym_str in symbols:
        pos = next((p for p in positions if p['symbol'] == sym_str), None)
        sym = Symbol(sym_str.split('/')[0], sym_str.split('/')[1] if '/' in sym_str else 'USDT',
                      AssetClass.CRYPTO, MarketType.SPOT, "binance")
        price = Decimal(str(pos['current_price'])) if pos else Decimal('0')
        current_prices[sym] = price
        qty = Decimal(str(pos['quantity'])) if pos else Decimal('0')
        # Position uses size (positive=long, negative=short), entry_price, mark_price
        positions_dict[sym] = Position(
            symbol=sym,
            size=qty,
            entry_price=price if qty != 0 else Decimal('0'),
            mark_price=price,
        )

    # Run rebalancer (async)
    events = asyncio.run(rebalancer.check_and_rebalance(
        positions=positions_dict,
        prices=current_prices,
    ))

    if not events:
        console.print("[green]No rebalancing needed[/green]")
        return

    console.print(f"\n[bold]Rebalancing Actions ({len(events)}):[/bold]")
    from rich.table import Table as RichTable
    t = RichTable("Symbol", "Side", "Size", "Price", "Reason", "Execute")
    for e in events:
        execute_str = "DRY RUN" if dry_run else "EXECUTE"
        t.add_row(
            f"{e.symbol.base}/{e.symbol.quote}",
            e.side.value.upper(),
            f"{e.size:.4f}",
            f"${e.price:,.2f}",
            e.reason,
            execute_str,
        )
    console.print(t)

    if not dry_run:
        console.print("[yellow]⚠️  Actual execution not implemented yet[/yellow]")


# ── strategy registry subcommands ────────────────────────────────────────


@portfolio.group()
def strategy():
    """Strategy registry and marketplace."""


@strategy.command("list")
def strategy_list():
    """List all registered strategies."""
    from rich.table import Table as RichTable

    from trading.strategies.plugins import get_registry

    registry = get_registry()
    strategies = registry.list_strategies()

    if not strategies:
        console.print("[yellow]No strategies registered[/yellow]")
        return

    t = RichTable("Name", "Version", "Type", "Risk", "Asset Classes", "Timeframes", "Status")
    for meta in strategies:
        t.add_row(
            meta.name,
            meta.version,
            meta.strategy_type.value,
            meta.risk_profile.value,
            ", ".join(meta.asset_classes),
            ", ".join(meta.timeframes),
            "✅" if meta.backtest_hash else "⚠️",
        )
    console.print(t)


@strategy.command("info")
@click.argument("name")
@click.option("--version", "-v", default=None, help="Version (default: latest)")
def strategy_info(name: str, version: str | None):
    """Show detailed strategy information."""
    from rich.panel import Panel
    from rich.table import Table as RichTable

    from trading.strategies.plugins import get_registry

    registry = get_registry()
    meta = registry.get_metadata(name, version)

    if not meta:
        console.print(f"[red]Strategy not found: {name}@{version or 'latest'}[/red]")
        return

    info_text = (
        f"[bold]Name:[/bold] {meta.name}\n"
        f"[bold]Version:[/bold] {meta.version}\n"
        f"[bold]Author:[/bold] {meta.author}\n"
        f"[bold]Description:[/bold] {meta.description}\n"
        f"[bold]Type:[/bold] {meta.strategy_type.value}\n"
        f"[bold]Risk Profile:[/bold] {meta.risk_profile.value}\n"
        f"[bold]Asset Classes:[/bold] {', '.join(meta.asset_classes)}\n"
        f"[bold]Timeframes:[/bold] {', '.join(meta.timeframes)}\n"
        f"[bold]Backtest Hash:[/bold] {meta.backtest_hash or 'Not validated'}\n"
        f"[bold]Created:[/bold] {meta.created_at.strftime('%Y-%m-%d')}\n"
        f"[bold]Updated:[/bold] {meta.updated_at.strftime('%Y-%m-%d')}"
    )
    console.print(Panel(info_text, title=f"Strategy: {meta.name}", border_style="cyan"))

    if meta.parameters:
        t = RichTable("Parameter", "Type", "Default", "Min", "Max", "Required")
        for k, v in meta.parameters.items():
            t.add_row(
                k,
                v.get('type', 'any'),
                str(v.get('default', '')),
                str(v.get('min', '')),
                str(v.get('max', '')),
                "✅" if v.get('required', False) else "❌",
            )
        console.print(Panel(t, title="Parameters", border_style="green"))


@strategy.command("run")
@click.argument("name")
@click.argument("symbol")
@click.option("--timeframe", "-t", default="1h", help="Timeframe")
@click.option("--version", "-v", default=None, help="Strategy version")
@click.option("--param", "-p", "params", multiple=True, help="Parameters: key=value")
@click.option("--capital", "-c", default=10000, type=float, help="Initial capital")
def strategy_run(
    name: str,
    symbol: str,
    timeframe: str,
    version: str | None,
    params: tuple[str],
    capital: float,
):
    """Run a strategy on a symbol (paper trading)."""
    from trading.strategies.plugins import get_registry, StrategyContext
    from trading.exchanges.models import Symbol, AssetClass, MarketType, Bar, Position
    from trading_agent.data.storage import load_ohlcv
    from decimal import Decimal
    from datetime import datetime

    # Parse params
    param_dict = {}
    for p in params:
        if "=" not in p:
            console.print(f"[red]Invalid param: {p}[/red]")
            return
        k, v = p.split("=", 1)
        try:
            param_dict[k] = float(v) if "." in v else int(v)
        except ValueError:
            param_dict[k] = v

    registry = get_registry()
    strategy_instance = registry.create_instance(name, version, param_dict)

    if not strategy_instance:
        console.print(f"[red]Strategy not found: {name}@{version or 'latest'}[/red]")
        return

    # Load data
    try:
        df = load_ohlcv(config.default_exchange, symbol, timeframe)
    except FileNotFoundError:
        console.print(f"[red]Data not found for {symbol}[/red]")
        return

    if df.is_empty():
        console.print("[red]No data[/red]")
        return

    # Run strategy on each bar
    base, quote = symbol.split('/') if '/' in symbol else (symbol, 'USDT')
    sym_obj = Symbol(base, quote, AssetClass.CRYPTO, MarketType.SPOT, config.default_exchange)

    signals = []
    for row in df.iter_rows(named=True):
        bar = Bar(
            symbol=sym_obj,
            timestamp=row['timestamp'],
            timeframe=timeframe,
            open=Decimal(str(row['open'])),
            high=Decimal(str(row['high'])),
            low=Decimal(str(row['low'])),
            close=Decimal(str(row['close'])),
            volume=Decimal(str(row['volume'])),
        )

        context = StrategyContext(
            symbol=sym_obj,
            bar=bar,
            position=None,
            portfolio_value=Decimal(str(capital)),
            available_balance=Decimal(str(capital)),
            current_time=row['timestamp'],
        )

        bar_signals = strategy_instance.on_bar(context)
        for sig in bar_signals:
            signals.append(sig)

    console.print(f"[bold]Generated {len(signals)} signals for {name} on {symbol}[/bold]")

    if signals:
        from rich.table import Table as RichTable
        t = RichTable("Time", "Side", "Strength", "Price")
        for sig in signals[-20:]:  # Show last 20
            t.add_row(
                str(sig.timestamp)[:19],
                sig.side.value.upper(),
                f"{float(sig.strength):.2f}",
                f"${float(sig.price):.2f}" if sig.price else "Market",
            )
        console.print(t)


@strategy.command("validate")
@click.argument("name")
@click.argument("symbol")
@click.option("--timeframe", "-t", default="1h", help="Timeframe")
@click.option("--version", "-v", default=None, help="Strategy version")
@click.option("--param", "-p", "params", multiple=True, help="Parameters: key=value")
@click.option("--save-hash", is_flag=True, help="Save backtest hash as reference")
def strategy_validate(
    name: str,
    symbol: str,
    timeframe: str,
    version: str | None,
    params: tuple[str],
    save_hash: bool,
):
    """Validate strategy with backtest and verify hash."""
    import hashlib
    import json

    from trading.strategies.plugins import get_registry
    from trading_agent.data.storage import load_ohlcv
    from trading_agent.backtest.engine import run_backtest

    # Parse params
    param_dict = {}
    for p in params:
        if "=" not in p:
            console.print(f"[red]Invalid param: {p}[/red]")
            return
        k, v = p.split("=", 1)
        try:
            param_dict[k] = float(v) if "." in v else int(v)
        except ValueError:
            param_dict[k] = v

    registry = get_registry()
    meta = registry.get_metadata(name, version)

    if not meta:
        console.print(f"[red]Strategy not found: {name}@{version or 'latest'}[/red]")
        return

    console.print(f"[bold]Validating {name} v{meta.version} on {symbol}...[/bold]")

    try:
        result = run_backtest(
            name,
            symbol=symbol,
            timeframe=timeframe,
            params=param_dict,
        )
    except Exception as e:
        console.print(f"[red]Backtest failed: {e}[/red]")
        return

    # Compute hash
    result_dict = {
        'total_return_pct': result.total_return_pct,
        'sharpe_ratio': result.sharpe_ratio,
        'max_drawdown_pct': result.max_drawdown_pct,
        'win_rate': result.win_rate,
        'total_trades': result.total_trades,
        'trades': [
            {
                'entry_date': str(t.entry_date),
                'exit_date': str(t.exit_date),
                'pnl_pct': t.pnl_pct,
            }
            for t in result.trades
        ],
    }
    result_str = json.dumps(result_dict, sort_keys=True, default=str)
    actual_hash = hashlib.sha256(result_str.encode()).hexdigest()[:16]

    console.print(f"Expected hash: {meta.backtest_hash or 'NOT SET'}")
    console.print(f"Actual hash:   {actual_hash}")

    if meta.backtest_hash:
        if actual_hash == meta.backtest_hash:
            console.print("[green]✅ Hash verified - backtest reproducible[/green]")
        else:
            console.print("[red]❌ Hash mismatch - backtest not reproducible![/red]")
    else:
        console.print("[yellow]⚠️  No reference hash set. Use --save-hash to store.[/yellow]")

    # Save hash if requested
    if save_hash:
        from datetime import datetime
        meta.backtest_hash = actual_hash
        meta.updated_at = datetime.now()
        registry._save_metadata(meta)
        registry.reload()
        console.print(f"[green]✅ Saved hash: {actual_hash}[/green]")


# ── execution multi-symbol ───────────────────────────────────────────────


@execution.command("run-multi")
@click.argument("symbols", nargs=-1, required=True)
@click.option("--timeframe", "-t", default="1h", help="Timeframe")
@click.option("--capital", "-c", default=None, type=float, help="Portfolio value")
@click.option("--stop-loss", "-s", default=0.05, type=float, help="Stop-loss %")
@click.option("--parallel/--sequential", default=True, help="Run agents in parallel")
def execution_run_multi(symbols: tuple[str], timeframe: str, capital: float | None,
                        stop_loss: float, parallel: bool):
    """Run execution cycle for multiple symbols."""
    import concurrent.futures

    from trading_agent.agents.orchestrator import Orchestrator
    from trading_agent.execution.engine import ExecutionEngine
    from trading_agent.execution.risk_controller import RiskController

    engine = ExecutionEngine(initial_capital=capital)
    rc = RiskController(engine)
    orchestrator = Orchestrator()

    console.print(f"[bold]Running multi-symbol execution for: {', '.join(symbols)}[/bold]")

    def process_symbol(symbol: str):
        console.print(f"\n[cyan]=== {symbol} ===[/cyan]")
        try:
            report = orchestrator.analyze(
                symbol=symbol,
                timeframe=timeframe,
                current_position_pct=0.0,
                portfolio_value=capital or engine.exchange.get_total_equity(),
            )
            decision = report.final_decision

            if decision.signal == "HOLD":
                console.print(f"  [yellow]HOLD[/yellow] — {decision.reasoning}")
                return {"symbol": symbol, "signal": "HOLD", "orders": 0, "status": "ok"}

            # Execute
            engine.exchange._last_price_cache[symbol] = report.current_price
            orders = engine.execute_signal(decision)

            if orders:
                for o in orders:
                    console.print(f"  [green]→ {o.side.value.upper()} {o.amount:.4f} {symbol}[/green]")
                if decision.signal == "BUY" and stop_loss > 0:
                    engine.set_stop_loss(symbol, stop_loss)
                    pos = engine.exchange.get_position(symbol)
                    if pos and pos.stop_loss:
                        console.print(f"  🛡️  Stop-loss: ${pos.stop_loss:,.2f}")

            # Risk check
            warnings = rc.check_all()
            if warnings:
                for w in warnings:
                    console.print(f"  [red]⚠ {w}[/red]")

            return {"symbol": symbol, "signal": decision.signal, "orders": len(orders), "status": "ok"}

        except FileNotFoundError as e:
            console.print(f"  [red]Data not found: {e}[/red]")
            return {"symbol": symbol, "signal": "ERROR", "orders": 0, "status": "data_not_found"}
        except Exception as e:
            console.print(f"  [red]Error: {e}[/red]")
            return {"symbol": symbol, "signal": "ERROR", "orders": 0, "status": "error"}

    if parallel:
        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
            results = list(executor.map(process_symbol, symbols))
    else:
        results = [process_symbol(s) for s in symbols]

    # Summary
    console.print("\n[bold]📋 Summary[/bold]")
    t = Table("Symbol", "Signal", "Orders", "Status")
    for r in results:
        status_icon = "✅" if r["status"] == "ok" else "❌"
        t.add_row(r["symbol"], r["signal"], str(r["orders"]), status_icon)
    console.print(t)

    # Show portfolio status
    console.print()
    execution_status.callback()


# ── meta-learning subcommands ───────────────────────────────────────────


@main.group()
def meta():
    """Meta-learning for strategy adaptation (MAML/Reptile/Meta-SGD/ANIL)."""


@meta.command("train")
@click.argument("data_dir", type=click.Path(exists=True, file_okay=False))
@click.option("--algorithm", "-a", type=click.Choice(["maml", "reptile", "metasgd", "anil"]), default="maml")
@click.option("--steps", "-s", default=100, help="Meta-training steps")
@click.option("--meta-lr", default=0.01, help="Meta learning rate")
@click.option("--inner-lr", default=0.1, help="Inner learning rate")
@click.option("--inner-steps", default=5, help="Inner loop steps")
@click.option("--batch-size", default=4, help="Meta batch size")
@click.option("--output", "-o", type=click.Path(), help="Output JSON file")
def meta_train(data_dir: str, algorithm: str, steps: int, meta_lr: float,
               inner_lr: float, inner_steps: int, batch_size: int, output: str | None):
    """Meta-train on multiple market regimes."""
    import asyncio
    from trading.cli.meta_learning import train
    
    asyncio.run(train.callback(
        data_dir=data_dir,
        algorithm=algorithm,
        steps=steps,
        meta_lr=meta_lr,
        inner_lr=inner_lr,
        inner_steps=inner_steps,
        batch_size=batch_size,
        output=output,
    ))


@meta.command("adapt")
@click.argument("model_path", type=click.Path(exists=True))
@click.argument("regime_data", type=click.Path(exists=True))
@click.option("--n-samples", "-n", default=20, help="Samples for adaptation")
@click.option("--output", "-o", type=click.Path(), help="Output JSON file")
def meta_adapt(model_path: str, regime_data: str, n_samples: int, output: str | None):
    """Adapt meta-learned strategy to new regime."""
    import asyncio
    from trading.cli.meta_learning import adapt
    
    asyncio.run(adapt.callback(
        model_path=model_path,
        regime_data=regime_data,
        n_samples=n_samples,
        output=output,
    ))


@meta.command("backtest")
@click.argument("adapted_params", type=click.Path(exists=True))
@click.argument("data", type=click.Path(exists=True))
@click.option("--capital", "-c", default=10000, help="Initial capital")
@click.option("--commission", default=0.0004, help="Commission rate")
@click.option("--slippage", default=0.0005, help="Slippage rate")
def meta_backtest(adapted_params: str, data: str, capital: float, commission: float, slippage: float):
    """Run backtest with meta-learned parameters."""
    import asyncio
    from trading.cli.meta_learning import backtest
    
    asyncio.run(backtest.callback(
        adapted_params=adapted_params,
        data=data,
        initial_capital=capital,
        commission=commission,
        slippage=slippage,
    ))


@meta.command("regimes")
@click.argument("data_dir", type=click.Path(exists=True, file_okay=False))
def meta_regimes(data_dir: str):
    """Analyze available regimes in data directory."""
    import asyncio
    from trading.cli.meta_learning import regimes
    
    asyncio.run(regimes.callback(data_dir=data_dir))


# ── event sourcing projections ───────────────────────────────────────────


@main.group()
def projection():
    """Event sourcing projection management."""


@projection.command("rebuild")
@click.argument("event_store_path")
@click.option("--from-position", default=0, help="Position to rebuild from")
@click.option("--projection", "-p", help="Specific projection to rebuild")
def projection_rebuild(event_store_path: str, from_position: int, projection: str | None):
    """Rebuild projections from event store."""
    import asyncio
    from trading.events.projection_manager import rebuild
    
    asyncio.run(rebuild.callback(
        event_store_path=event_store_path,
        from_position=from_position,
        projection=projection,
    ))


@projection.command("status")
@click.argument("event_store_path")
@click.option("--projection", "-p", help="Specific projection to show")
def projection_status(event_store_path: str, projection: str | None):
    """Show projection status."""
    import asyncio
    from trading.events.projection_manager import status
    
    asyncio.run(status.callback(
        event_store_path=event_store_path,
        projection=projection,
    ))


@projection.command("query")
@click.argument("event_store_path")
@click.argument("projection")
@click.argument("key", required=False)
def projection_query(event_store_path: str, projection: str, key: str | None):
    """Query projection state."""
    import asyncio
    from trading.events.projection_manager import query
    
    asyncio.run(query.callback(
        event_store_path=event_store_path,
        projection=projection,
        key=key,
    ))


if __name__ == "__main__":
    main()
