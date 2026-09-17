#!/usr/bin/env python3
"""
Strategy Tournament Monitoring Dashboard

Real-time monitoring of promotion/demotion events, incumbent status, and
shadow Sharpe rankings across all configured symbols.

Usage:
    # One-shot status:
    python scripts/monitor_tournament.py

    # Continuous monitoring (polls every 60s):
    python scripts/monitor_tournament.py --watch --interval 60

    # Single pair focus:
    python scripts/monitor_tournament.py --symbol BTC/USDT
"""
from __future__ import annotations

import argparse
import json
import os
import time
from datetime import datetime, UTC
from pathlib import Path

from rich.console import Console
from rich.table import Table
from rich.live import Live
from rich.panel import Panel
from rich import box

console = Console()

SHADOW_ROOT = Path("data/tournament_shadow")
SYMBOLS = ["BTC/USDT", "ETH/USDT", "BNB/USDT", "XRP/USDT"]


def load_shadow_results(symbol: str) -> dict | None:
    """Load shadow_results.json for a symbol."""
    safe = symbol.replace("/", "_")
    path = SHADOW_ROOT / safe / "shadow_results.json"
    if not path.exists():
        return None
    return json.loads(path.read_text())


def load_tournament_state(symbol: str, timeframe: str = "1h") -> dict | None:
    """Load tournament_state JSON for a symbol."""
    safe = symbol.replace("/", "_").replace(":", "_")
    state_dir = SHADOW_ROOT / safe / "tournament_state"
    if not state_dir.exists():
        return None
    state_file = state_dir / f"{safe}__{timeframe}.json"
    if not state_file.exists():
        return None
    return json.loads(state_file.read_text())


def compute_sharpe(state: dict) -> dict[str, float]:
    """Compute shadow Sharpe for each strategy from state."""
    sharpes: dict[str, float] = {}
    for sid, metrics in state.get("shadow_metrics", {}).items():
        rets = metrics.get("returns", [])
        if len(rets) < 2:
            sharpes[sid] = 0.0
            continue
        n = len(rets)
        mean = sum(rets) / n
        std = (sum((r - mean) ** 2 for r in rets) / n) ** 0.5 if n > 1 else 0.0
        sharpes[sid] = mean / std if std > 0 else 0.0
    return sharpes


def build_status_table(symbol: str) -> Table:
    """Build a Rich table showing current status for a symbol."""
    shadow = load_shadow_results(symbol)
    state = load_tournament_state(symbol)

    safe = symbol.replace("/", "_")
    table = Table(title=f"Tournament — {symbol}", box=box.ROUNDED, width=100)

    table.add_column("Strategy", style="cyan", no_wrap=True)
    table.add_column("Sharpe", justify="right", style="green" if (shadow := load_shadow_results(symbol)) else "white")
    table.add_column("ActiveBars", justify="right", style="yellow")
    table.add_column("Status", style="white")

    if shadow is None and state is None:
        table.add_row("No data", "-", "-", "Run shadow first")
        return table

    # Load shadow performance or compute from state
    if shadow:
        perf = shadow.get("shadow_performance", {})
        bars = shadow.get("bars_evaluated", 0)
        incumbent = shadow.get("incumbent", "unknown")
        for sid in sorted(perf, key=lambda s: perf.get(s, {}).get("sharpe", 0), reverse=True):
            p = perf[sid]
            is_incumbent = " 👑" if sid == incumbent else ""
            table.add_row(
                sid + is_incumbent,
                f"{p.get('sharpe', 0):.2f}",
                f"{p.get('active_bars', 0)}/{bars}",
                "PROMOTE" if p.get("sharpe", 0) > 0.2 else "demote",
            )
    elif state:
        sharpes = compute_sharpe(state)
        incumbent = state.get("incumbent_strategy_id", "NONE")
        for sid in sorted(sharpes, key=lambda s: sharpes[s], reverse=True):
            is_incumbent = " 👑" if sid == incumbent else ""
            table.add_row(
                sid + is_incumbent,
                f"{sharpes[sid]:.2f}",
                "-",
                "PROMOTE" if sharpes[sid] > 0.20 else "demote",
            )

    # Kill switch status
    env_mode = os.getenv("TOURNAMENT_SHADOW_MODE", "1")
    ks_status = "ACTIVE (shadow)" if env_mode == "1" else "INACTIVE (LIVE)"
    ks_color = "green" if env_mode == "1" else "red"

    return Panel(
        table,
        title=f"[bold]{symbol}[/bold] | Kill: [{ks_color}]{ks_status}[/]",
        border_style="blue",
    )


def run_once(symbols: list[str]) -> None:
    """Print one-shot status for all symbols."""
    console.print(f"\n[bold]Strategy Tournament Monitor[/bold] — {datetime.now(UTC).strftime('%Y-%m-%d %H:%M:%S UTC')}\n")
    for sym in symbols:
        console.print(build_status_table(sym))
    console.print()


def run_watch(symbols: list[str], interval: int) -> None:
    """Continuously monitor, refreshing every `interval` seconds."""
    console.print(f"[yellow]Watching {len(symbols)} symbols, refreshing every {interval}s. Ctrl+C to stop.[/yellow]\n")
    try:
        with Live(build_watch_layout(symbols), console=console, refresh_per_second=2) as live:
            while True:
                time.sleep(interval)
                live.update(build_watch_layout(symbols))
    except KeyboardInterrupt:
        console.print("\n[yellow]Monitoring stopped.[/yellow]")


def build_watch_layout(symbols: list[str]) -> Panel:
    """Build a combined layout table for live mode."""
    table = Table(title="Strategy Tournament — Live Monitor", box=box.ROUNDED)
    table.add_column("Symbol", style="cyan")
    table.add_column("Incumbent", style="green")
    table.add_column("# PROMOTE", justify="right", style="green")
    table.add_column("# Demote", justify="right", style="red")
    table.add_column("Last Update", style="dim")

    for sym in symbols:
        shadow = load_shadow_results(sym)
        state = load_tournament_state(sym)

        if shadow:
            inc = shadow.get("incumbent", "?")
            perf = shadow.get("shadow_performance", {})
            promote = sum(1 for v in perf.values() if v.get("sharpe", 0) > 0.20)
            demote = sum(1 for v in perf.values() if v.get("sharpe", 0) < -0.10)
            table.add_row(sym, inc, str(promote), str(demote), "from shadow_results.json")
        elif state:
            inc = state.get("incumbent_strategy_id", "?")
            sharpes = compute_sharpe(state)
            promote = sum(1 for v in sharpes.values() if v > 0.20)
            demote = sum(1 for v in sharpes.values() if v < -0.10)
            table.add_row(sym, inc, str(promote), str(demote), "from tournament_state.json")
        else:
            table.add_row(sym, "-", "-", "-", "No data")

    env_mode = os.getenv("TOURNAMENT_SHADOW_MODE", "1")
    ks = "ACTIVE" if env_mode == "1" else "INACTIVE (LIVE)"

    return Panel(
        table,
        title=f"Kill switch: {ks}",
        border_style="blue",
    )


def main():
    parser = argparse.ArgumentParser(description="Strategy Tournament Monitor")
    parser.add_argument("--symbols", nargs="+", default=SYMBOLS)
    parser.add_argument("--watch", action="store_true", help="Continuous monitoring mode")
    parser.add_argument("--interval", type=int, default=60, help="Refresh interval (seconds)")
    args = parser.parse_args()

    if args.watch:
        run_watch(args.symbols, args.interval)
    else:
        run_once(args.symbols)


if __name__ == "__main__":
    main()
