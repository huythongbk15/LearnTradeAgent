"""CLI commands — decomposed from the legacy monolith. Behavior unchanged."""

from __future__ import annotations

import click
from trading_agent.cli._common import console

# ── agents subcommands ─────────────────────────────────────────────────────


@click.group()
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
@click.option("--ablation", type=click.Choice(["A", "B", "C", "D"]), default="A",
              help="Ablation preset: A=all agents, B=no sentiment, C=no risk override, D=technical only")
def analyze_signal(
    symbol: str,
    timeframe: str,
    position: float,
    capital: float,
    quiet: bool,
    ablation: str,
):
    """Run multi-agent AI analysis on a symbol with ablation support.
    
    Ablation presets:
    - A: All agents (baseline)
    - B: Technical + Risk (no Sentiment)
    - C: Technical + Sentiment + Risk (no Risk override)
    - D: Technical only (no Sentiment, no Risk)
    """
    from trading_agent.agents.orchestrator import Orchestrator, print_report

    console.print(f"Running multi-agent analysis on [bold]{symbol}[/bold] {timeframe}…")
    console.print(f"Ablation preset: [bold]{ablation}[/bold]")

    orchestrator = Orchestrator(ablation_preset=ablation)
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


