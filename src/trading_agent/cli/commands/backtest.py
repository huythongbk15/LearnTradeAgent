"""CLI commands — decomposed from the legacy monolith. Behavior unchanged."""

from __future__ import annotations

from rich.table import Table
from rich.panel import Panel
import click
from trading_agent.cli._common import console

# ── backtest subcommands ──────────────────────────────────────────────────


@click.group()
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
    "--param",
    "-p",
    "params",
    multiple=True,
    help="Strategy params: key=value (e.g. -p fast_period=10 -p slow_period=30)",
)
@click.option("--capital", default=None, type=float, help="Initial capital")
@click.option(
    "--position-sizing",
    type=click.Choice(
        ["fixed", "kelly", "half_kelly", "quarter_kelly", "vol_target", "optimal_f"]
    ),
    default="fixed",
    help="Position sizing method",
)
@click.option(
    "--fixed-pct", default=0.1, type=float, help="Fixed position % (for fixed method)"
)
@click.option(
    "--kelly-fraction",
    default=0.5,
    type=float,
    help="Kelly fraction (0.5=half, 0.25=quarter)",
)
@click.option(
    "--commission",
    default=None,
    type=float,
    help="Commission rate (default from config)",
)
@click.option(
    "--slippage", default=None, type=float, help="Slippage rate (default from config)"
)
@click.option("--long-only/--long-short", default=True, help="Long-only mode")
@click.option(
    "--llm/--no-llm",
    default=False,
    help="Enable LLM agents in backtest (deterministic mode)",
)
@click.option(
    "--llm-provider",
    default="opencode",
    help="LLM provider for backtest (opencode, deepseek, openai, ollama)",
)
@click.option(
    "--llm-model", default="deepseek-v4-flash-free", help="LLM model for backtest"
)
def run_backtest_cmd(
    strategy_name: str,
    symbol: str,
    timeframe: str,
    params: tuple[str],
    capital: float | None,
    position_sizing: str,
    fixed_pct: float,
    kelly_fraction: float,
    commission: float | None,
    slippage: float | None,
    long_only: bool,
    llm: bool,
    llm_provider: str,
    llm_model: str,
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

    # Add LLM params to strategy params
    if llm:
        param_dict["use_llm"] = True
        param_dict["llm_provider"] = llm_provider
        param_dict["llm_model"] = llm_model

    engine_kwargs = {}
    if capital is not None:
        engine_kwargs["initial_capital"] = capital
    engine_kwargs["position_sizing_method"] = position_sizing
    if position_sizing == "fixed":
        engine_kwargs["fixed_position_pct"] = fixed_pct
    elif position_sizing in ["kelly", "half_kelly", "quarter_kelly"]:
        engine_kwargs["kelly_fraction"] = kelly_fraction
    if commission is not None:
        engine_kwargs["commission"] = commission
    if slippage is not None:
        engine_kwargs["slippage"] = slippage
    engine_kwargs["long_only"] = long_only

    console.print(
        f"Running [bold]{strategy_name}[/bold] on [bold]{symbol}[/bold] {timeframe}…"
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
    from rich.table import Table as RichTable

    # Header
    header = f"[bold]{result.strategy_name.upper()}[/bold] on {result.symbol} {result.timeframe}"

    # Metrics table
    t = RichTable.grid(padding=(0, 2))
    t.add_row()
    t.add_row(
        "Return",
        f"[green]{result.total_return_pct:>+8.2f}%[/green]"
        if result.total_return_pct >= 0
        else f"[red]{result.total_return_pct:>+8.2f}%[/red]",
    )
    t.add_row("Ann. Return", f"{result.annualized_return_pct:>+8.2f}%")
    t.add_row("Sharpe", f"{result.sharpe_ratio:>8.2f}")
    t.add_row("Sortino", f"{result.sortino_ratio:>8.2f}")
    t.add_row("Max DD", f"[red]{result.max_drawdown_pct:>8.2f}%[/red]")
    t.add_row("Win Rate", f"{result.win_rate:>8.1%}")
    t.add_row("Profit Factor", f"{result.profit_factor:>8.2f}")
    t.add_row("Trades", f"{result.total_trades:>8d}")
    t.add_row("Avg Hold", f"{result.avg_hold_bars:>8.1f} bars")

    console.print(Panel(t, title=header, border_style="cyan"))
