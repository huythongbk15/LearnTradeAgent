"""CLI commands — decomposed from the legacy monolith. Behavior unchanged."""

from __future__ import annotations

import os
import subprocess
import time
from rich.table import Table
import click
from trading_agent.cli._common import console

# ── system subcommands ────────────────────────────────────────────────────


@click.group()
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

    from trading_agent.execution.engine import (
        register_shutdown_handler,
        setup_graceful_shutdown,
    )

    # Register a test handler
    register_shutdown_handler(
        lambda: console.print("[green]✓ Shutdown handler executed[/green]")
    )

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
                timeout=timeout,
                capture_output=True,
            )
            return r.returncode == 0
        except Exception:
            return False

    checks = [
        # Core infra — TCP port check (no extra CLI tools needed)
        ("TimescaleDB", lambda: _tcp_check("timescaledb", 5432)),
        ("Redis", lambda: _tcp_check("redis", 6379)),
        # HTTP services — curl-based
        ("Grafana", lambda: _http_check("http://grafana:3000/api/health", timeout=5)),
        # Optional services — only checked if DNS resolves
        (
            "Prometheus",
            lambda: (
                _http_check("http://prometheus:9090/-/healthy")
                if _tcp_check("prometheus", 9090)
                else ("skip", "not deployed")
            ),
        ),
        (
            "Loki",
            lambda: (
                _http_check("http://loki:3100/ready")
                if _tcp_check("loki", 3100)
                else ("skip", "not deployed")
            ),
        ),
        (
            "Nginx",
            lambda: (
                _http_check("http://nginx/healthz")
                if _tcp_check("nginx", 80)
                else ("skip", "not deployed")
            ),
        ),
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
    failed = sum(
        1 for _, s, _ in results if "FAIL" in s or "TIMEOUT" in s or "ERROR" in s
    )
    if failed:
        console.print(f"\n[red]❌ {failed} check(s) failed[/red]")
        raise SystemExit(1)
    else:
        console.print("\n[green]✅ All checks passed[/green]")


@system.command("logs")
@click.option("--lines", "-n", default=100, help="Number of lines")
@click.option("--follow", "-f", is_flag=True, help="Follow logs")
@click.option(
    "--component",
    "-c",
    default=None,
    type=click.Choice(["agent", "execution", "data", "risk", "all"]),
    help="Filter by component",
)
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
