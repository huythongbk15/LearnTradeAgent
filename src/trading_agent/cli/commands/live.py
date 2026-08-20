"""CLI commands — decomposed from the legacy monolith. Behavior unchanged."""

from __future__ import annotations

import os
import time
import uuid
from typing import Any

import click
from rich.panel import Panel
from rich.table import Table

from trading_agent.cli._common import console

# ── execution subcommands ────────────────────────────────────────────────


@click.group()
def execution():
    """Paper trading execution & risk management."""


@execution.command("status")
def execution_status():
    """Show current portfolio status, positions, P&L."""
    from rich.table import Table as RichTable

    from trading_agent.execution.engine import ExecutionEngine

    engine = ExecutionEngine()
    summary = engine.get_summary()
    positions = engine.get_positions_summary()

    # Summary panel
    ret_str = (
        f"[green]{summary['return_pct']:+.2f}%[/green]"
        if summary["return_pct"] >= 0
        else f"[red]{summary['return_pct']:+.2f}%[/red]"
    )
    pnl_str = (
        f"[green]{summary['unrealized_pnl']:+.2f}[/green]"
        if summary["unrealized_pnl"] >= 0
        else f"[red]{summary['unrealized_pnl']:+.2f}[/red]"
    )

    summary_text = (
        f"Equity: [bold]${summary['equity']:,.2f}[/bold]  ({ret_str})\n"
        f"Cash: ${summary['cash']:,.2f}  |  "
        f"Positions: ${summary['positions_value']:,.2f}\n"
        f"Unrealized P&L: {pnl_str}  |  "
        f"Trades: {summary['total_trades']}  |  "
        f"Open Orders: {summary['open_orders']}"
    )
    console.print(
        Panel(summary_text, title="💰 Portfolio Summary", border_style="green")
    )

    # Positions table
    if positions:
        t = RichTable("Symbol", "Qty", "Entry", "Current", "P&L%", "Value", "Stop")
        for p in positions:
            color = "green" if p["pnl_pct"] >= 0 else "red"
            stop_str = f"${p['stop_loss']:.1f}" if p["stop_loss"] else "—"
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
@click.option(
    "--all", "-a", "show_all", is_flag=True, help="Include open positions (unrealized)"
)
def execution_trades(limit: int, show_all: bool):
    """Show recent trade history."""
    from rich.table import Table as RichTable

    from trading_agent.execution.engine import ExecutionEngine

    engine = ExecutionEngine()

    # Get closed trades
    trades = engine.get_trade_history(limit)

    # Get open positions if requested
    open_positions = engine.exchange.get_all_positions() if show_all else []

    if not trades and not open_positions:
        console.print("[yellow]No trades yet[/yellow]")
        return

    t = RichTable(
        "Date",
        "Symbol",
        "Side",
        "Entry",
        "Current/Exit",
        "P&L%",
        "Status",
        "Reason",
        "Sizing",
    )

    # Show open positions first (unrealized)
    for pos in open_positions:
        pnl_color = "green" if pos.unrealized_pnl_pct >= 0 else "red"
        opened = pos.opened_at.isoformat()[:16] if pos.opened_at else "?"
        sizing = pos.metadata.get("sizing_method", "?")
        t.add_row(
            opened,
            pos.symbol,
            pos.side.value.upper(),
            f"${pos.entry_price:.2f}",
            f"${pos.current_price:.2f}",
            f"[{pnl_color}]{pos.unrealized_pnl_pct:+.2f}%[/{pnl_color}]",
            "[yellow]OPEN[/yellow]",
            "—",
            sizing,
        )

    # Show closed trades
    for tr in trades:
        pnl_color = "green" if tr.get("pnl_pct", 0) >= 0 else "red"
        entry_time = tr.get("entry_time", "")[:16] if tr.get("entry_time") else "?"
        status = "CLOSED" if tr.get("exit_price") else "OPEN"
        sizing = tr.get("metadata", {}).get("sizing_method", "?")
        t.add_row(
            entry_time,
            tr.get("symbol", "?"),
            tr.get("side", "?").upper(),
            f"${tr.get('entry_price', 0):.2f}",
            f"${tr.get('exit_price', 0):.2f}" if tr.get("exit_price") else "—",
            f"[{pnl_color}]{tr.get('pnl_pct', 0):+.2f}%[/{pnl_color}]",
            status,
            tr.get("reason", "—"),
            sizing,
        )
    console.print(t)

    # Summary
    if trades:
        total_realized = sum(t.get("pnl", 0) for t in trades)
        total_realized_pct = (
            sum(t.get("pnl_pct", 0) for t in trades) / len(trades) if trades else 0
        )
        console.print(
            f"\n[bold]Realized P&L (last {len(trades)} trades):[/bold] ${total_realized:.2f} ({total_realized_pct:+.2f}% avg)"
        )
    if open_positions:
        total_unrealized = sum(p.unrealized_pnl for p in open_positions)
        console.print(
            f"[bold]Unrealized P&L ({len(open_positions)} open):[/bold] ${total_unrealized:.2f}"
        )


@execution.command("risk")
def execution_risk_status():
    """Show risk controller status."""
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
        "🔴" if status["drawdown_pct"] >= status["max_drawdown_limit_pct"] else "✅",
    )
    t.add_row(
        "Daily Loss",
        f"{status['daily_loss_pct']:.2f}%",
        f"{status['daily_loss_limit_pct']:.0f}%",
        "🔴" if status["daily_loss_pct"] >= status["daily_loss_limit_pct"] else "✅",
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
        console.print(
            f"\n[bold red]🔴 CIRCUIT BREAKER: {status['circuit_breaker_reason']}[/bold red]"
        )
        console.print("[yellow]Run `trading-agent execution reset` to reset[/yellow]")


@execution.command("run")
@click.argument("symbol", default="BTC/USDT", required=False)
@click.option("--timeframe", "-t", default="1h", help="Timeframe")
@click.option(
    "--capital", "-c", default=None, type=float, help="Portfolio value override"
)
@click.option(
    "--stop-loss",
    "-s",
    default=0.05,
    type=float,
    help="Stop-loss distance (e.g. 0.05 = 5%)",
)
@click.option("--confirm/--auto", default=False, help="Prompt before executing trade")
def execution_run(
    symbol: str, timeframe: str, capital: float | None, stop_loss: float, confirm: bool
):
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
    current_pos_pct = (
        (
            existing_pos.quantity
            * existing_pos.entry_price
            / engine.exchange.get_total_equity()
        )
        if existing_pos and existing_pos.is_active
        else 0.0
    )
    port_value = capital or engine.exchange.get_total_equity()

    console.print(
        f"🧠 Running multi-agent analysis for [bold]{symbol}[/bold] {timeframe}…"
    )
    console.print(
        f"   Current position: {existing_pos.quantity:.4f} {symbol} "
        f"({current_pos_pct * 100:.1f}% of portfolio)"
        if existing_pos and existing_pos.is_active
        else "   No open position"
    )

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
        engine.update_from_dataframe(symbol, orchestrator._last_df, timeframe)
        return

    # Confirm if requested
    if confirm:
        from rich.prompt import Confirm

        if not Confirm.ask(f"Execute {signal_str} signal for {symbol}?"):
            console.print("[yellow]Trade cancelled[/yellow]")
            return

    # 4. Place order
    engine.update_market_price(
        symbol,
        report.current_price,
        report.data_timestamp,
        timeframe,
    )
    orders = engine.execute_signal(decision)

    if orders:
        for o in orders:
            console.print(
                f"[green]→ Order placed: {o.side.value.upper()} {o.amount:.4f} {symbol} "
                f"@ ${o.avg_fill_price or report.current_price:,.2f}[/green]"
            )

        # 5. Set stop-loss if bought
        if signal_str == "BUY" and stop_loss > 0:
            engine.set_stop_loss(symbol, stop_loss)
            pos = engine.exchange.get_position(symbol)
            if pos and pos.stop_loss:
                console.print(
                    f"🛡️  Stop-loss set: ${pos.stop_loss:,.2f} "
                    f"({stop_loss * 100:.1f}% below entry)"
                )

    # 6. Run risk checks
    warnings = rc.check_all()
    if warnings:
        console.print("\n[bold red]⚠ Risk Warnings:[/bold red]")
        for w in warnings:
            console.print(f"  • {w}")
        if rc._circuit_breaker_active:
            console.print(
                "[bold red]🔴 CIRCUIT BREAKER ACTIVATED — all positions closed[/bold red]"
            )

    # Show updated status
    console.print()
    execution_status.callback()


@execution.command("close")
@click.argument("symbol", default=None, required=False)
@click.option("--all", "-a", "close_all", is_flag=True, help="Close all positions")
@click.option(
    "--yes", "-y", is_flag=True, help="Skip confirmation prompt (for automation)"
)
def execution_close(symbol: str | None, close_all: bool, yes: bool):
    """Close a position or all positions (kill switch)."""
    from rich.prompt import Confirm

    from trading_agent.execution.engine import ExecutionEngine

    engine = ExecutionEngine()

    if close_all or symbol is None:
        if not yes and not Confirm.ask("⚠️  Close ALL positions?"):
            return
        result = engine.close_all(reason="manual_kill")
        remaining = [
            pos.symbol
            for pos in engine.exchange.get_all_positions()
            if pos.is_active and pos.quantity > 0
        ]
        if remaining:
            console.print(
                f"[bold red]Close-all incomplete; remaining: "
                f"{', '.join(remaining)} (fresh prices required)[/bold red]"
            )
        else:
            console.print("[red]🔴 All positions closed[/red]")
    else:
        pos = engine.exchange.get_position(symbol)
        if not pos or not pos.is_active:
            console.print(f"[yellow]No open position for {symbol}[/yellow]")
            return
        if not yes and not Confirm.ask(f"Close {pos.quantity:.4f} {symbol}?"):
            return
        order = engine.close_position(symbol, reason="manual")
        if order:
            console.print(f"[red]Position closed: {symbol} (order {order.id})[/red]")
        else:
            console.print(
                f"[yellow]Close failed for {symbol} (no fresh price)[/yellow]"
            )

    execution_status.callback()


@execution.command("reset")
@click.option(
    "--yes", "-y", is_flag=True, help="Skip confirmation prompt (for automation)"
)
def execution_reset(yes: bool):
    """Reset paper exchange to initial state."""
    from rich.prompt import Confirm

    if not yes and not Confirm.ask("⚠️  Reset ALL trade history and state?"):
        return
    from trading_agent.execution.engine import ExecutionEngine

    engine = ExecutionEngine()
    engine.reset()
    console.print("[green]✅ Paper exchange reset[/green]")


# ── execution multi-symbol ───────────────────────────────────────────────


@execution.command("run-multi")
@click.argument("symbols", nargs=-1, required=True)
@click.option("--timeframe", "-t", default="1h", help="Timeframe")
@click.option("--capital", "-c", default=None, type=float, help="Portfolio value")
@click.option("--stop-loss", "-s", default=0.05, type=float, help="Stop-loss %")
@click.option("--parallel/--sequential", default=True, help="Run agents in parallel")
def execution_run_multi(
    symbols: tuple[str],
    timeframe: str,
    capital: float | None,
    stop_loss: float,
    parallel: bool,
):
    """Run execution cycle for multiple symbols."""
    from trading_agent.agents.orchestrator import Orchestrator
    from trading_agent.execution.engine import ExecutionEngine
    from trading_agent.execution.risk_controller import RiskController

    engine = ExecutionEngine(initial_capital=capital)
    rc = RiskController(engine)
    console.print(
        f"[bold]Running multi-symbol execution for: {', '.join(symbols)}[/bold]"
    )

    def process_symbol(symbol: str):
        console.print(f"\n[cyan]=== {symbol} ===[/cyan]")
        try:
            local_orchestrator = Orchestrator()
            report = local_orchestrator.analyze(
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
            engine.update_market_price(
                symbol,
                report.current_price,
                report.data_timestamp,
                timeframe,
            )
            orders = engine.execute_signal(decision)

            if orders:
                for o in orders:
                    console.print(
                        f"  [green]→ {o.side.value.upper()} {o.amount:.4f} {symbol}[/green]"
                    )
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

            return {
                "symbol": symbol,
                "signal": decision.signal,
                "orders": len(orders),
                "status": "ok",
            }

        except FileNotFoundError as e:
            console.print(f"  [red]Data not found: {e}[/red]")
            return {
                "symbol": symbol,
                "signal": "ERROR",
                "orders": 0,
                "status": "data_not_found",
            }
        except Exception as e:
            console.print(f"  [red]Error: {e}[/red]")
            return {"symbol": symbol, "signal": "ERROR", "orders": 0, "status": "error"}

    if parallel:
        console.print(
            "[yellow]Parallel order execution is disabled; processing symbols "
            "sequentially against the shared portfolio.[/yellow]"
        )
    results = [process_symbol(symbol) for symbol in symbols]

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


# ── live trading subcommands ─────────────────────────────────────────────


@click.group()
def live():
    """Broker monitoring; execution is restricted to Alpaca Paper."""


def _paper_execution_error(broker: str, broker_facade: Any) -> str | None:
    """Return a fail-closed reason, or None for an authorized Paper account."""
    if os.getenv("TRADING_EXECUTION_ENABLED", "false").lower() != "true":
        return "TRADING_EXECUTION_ENABLED is not true"
    if os.getenv("TRADING_MODE", "paper").lower() != "paper":
        return "only TRADING_MODE=paper is supported"
    if broker != "alpaca":
        return "order execution is restricted to Alpaca Paper"
    adapter = getattr(broker_facade, "adapter", None)
    if getattr(getattr(adapter, "config", None), "paper", None) is not True:
        return "the connected Alpaca adapter is not a verified Paper account"
    return None


def _place_order_via_gateway(live_broker, order):
    """Route a manual CLI order through canonical lifecycle + BrokerGateway."""
    import asyncio
    import math
    from datetime import UTC, datetime

    from trading_agent.execution.canonical import (
        AuthorizedOrder,
        BrokerGateway,
        UnifiedRiskDecision,
        RiskLevel,
        EvidenceState,
    )
    from trading_agent.execution.canonical.cli_adapter import CliBrokerAdapter
    from trading_agent.execution.lifecycle import ExecutionEventStore
    from trading_agent.execution.lifecycle.lifecycle import (
        ExecutionLifecycle,
        ExecutionHealth,
        TrustedPrice,
        ExposureEffect,
    )
    from trading_agent.execution.permission import (
        PermissionContext,
        evaluate_order_permission,
    )

    adapter = CliBrokerAdapter(live_broker)
    store = ExecutionEventStore("data/execution/events.db").connect()

    def _price_source(symbol):
        try:
            ticker = asyncio.run(live_broker.adapter.fetch_ticker(symbol))
            last = getattr(ticker, "last", None) or getattr(ticker, "price", None)
            if last is not None and math.isfinite(last) and last > 0:
                return TrustedPrice(
                    price=float(last),
                    exchange_timestamp=datetime.now(UTC),
                    received_at=datetime.now(UTC),
                )
        except Exception:
            pass
        return None

    def _inventory_source(symbol, side):
        if side != "sell":
            return 0.0
        try:
            positions = live_broker.get_positions()
            sym_str = symbol.pair if hasattr(symbol, "pair") else str(symbol)
            for pos in positions:
                if pos.get("symbol") == sym_str:
                    return float(pos.get("qty", 0))
        except Exception:
            pass
        return 0.0

    lifecycle = ExecutionLifecycle(
        store,
        price_source=_price_source,
        inventory_source=_inventory_source,
    )

    intent_id = f"cli-{uuid.uuid4().hex}"
    symbol = order.symbol
    side = "buy" if order.side.value.lower() == "buy" else "sell"
    size = float(order.size)

    # 1. Create intent (draft mode allows missing risk decision)
    lifecycle.create_order_intent(
        intent_id=intent_id,
        symbol=symbol,
        side=side,
        size=size,
        idempotency_key=order.client_order_id,
    )

    # 2. MANUAL CLI ORDERS REQUIRE REAL RISK EVIDENCE.
    # Synthetic perfect risk (calibration_ece=0.0, etc.) is FORBIDDEN.
    # Operator must provide real risk evidence via risk policy/evidence.
    # For now, we BLOCK manual BUY/SELL that would increase exposure.
    # Only REDUCE (sell) with trusted inventory is allowed for manual override.
    if side == "buy":
        raise RuntimeError(
            "Manual BUY orders require real risk evidence from risk policy. "
            "Synthetic risk evidence is forbidden. Provide --risk-decision-id or "
            "configure risk policy to generate real risk evidence."
        )

    # For SELL (reduce-only), we can proceed with minimal risk decision
    # since we're reducing exposure and have inventory evidence
    risk_decision = UnifiedRiskDecision(
        decision_id=f"cli-risk-{intent_id}",
        forecast_fingerprint="cli-manual-reduce",
        model_artifact_id="cli-manual",
        requested_target_exposure=0.0,
        allowed_target_exposure=0.0,
        max_new_exposure=0.0,
        reduce_only=True,
        risk_level=RiskLevel.HIGH,
        reason_codes=("MANUAL_REDUCE",),
        calibration_state=EvidenceState.KNOWN,
        calibration_artifact_id="cli-manual",
        calibration_ece=1.0,  # Max uncertainty - no calibration evidence
        ood_state=EvidenceState.KNOWN,
        ood_score=1.0,  # Max uncertainty - no OOD evidence
        regime_state=EvidenceState.KNOWN,
        regime_entropy=1.0,  # Max uncertainty - no regime evidence
        interval_width=1.0,  # Max uncertainty
        created_at=datetime.now(UTC),
    )

    # 3. Approve risk (reduce-only allowed with minimal evidence)
    lifecycle.approve_risk(intent_id, risk_decision=risk_decision)

    # 4. Evaluate permission
    exposure_effect = (
        ExposureEffect.INCREASE if side == "buy" else ExposureEffect.REDUCE
    )
    permission = evaluate_order_permission(
        PermissionContext(
            execution_health=ExecutionHealth.NORMAL,
            exposure_effect=exposure_effect.value,
            risk_decision=risk_decision,
            trusted_price=None,  # No price for manual reduce
            max_price_age_seconds=60.0,
            reconciliation_state="none",
            protection_state="none",
            manual_blocked=False,
            kill_switch_active=False,
            data_trust="trusted",
            inventory_state="known",  # We have inventory from broker
            free_inventory=0.0,
            authorized_sellable_inventory=size,  # We have inventory
            order_size=size,
            order_side="sell",
            require_fresh_market_data=True,  # Enable for safety
            enforce_inventory=True,  # Enable for safety
            broker_state=None,
            draft=False,
        )
    )

    if not permission.allowed():
        raise RuntimeError(
            f"Order blocked by permission: {permission.reason.value} — {permission.detail}"
        )

    # 5. Authorize order (lifecycle derives all fields from durable state)
    auth_event = lifecycle.authorize_order(
        intent_id=intent_id,
        idempotency_key=order.client_order_id or intent_id,
    )

    # 6. Request broker submission (durable pre-submission event)
    lifecycle.request_broker_submission(intent_id)

    # 7. Build AuthorizedOrder with original order metadata
    from trading_agent.execution.canonical.broker_gateway import _AUTHORIZED_TOKEN

    authorized = AuthorizedOrder(
        token=_AUTHORIZED_TOKEN,
        intent_id=intent_id,
        symbol=symbol,
        side=side,
        quantity=size,
        idempotency_key=order.client_order_id or intent_id,
        price_reference=0.0,
        risk_decision_id=risk_decision.decision_id,
        forecast_fingerprint=risk_decision.forecast_fingerprint,
        model_artifact_id=risk_decision.model_artifact_id,
        permission_result=permission.permission.value,
        authorization_id=auth_event.payload["authorization_id"],
        lifecycle_event_id=auth_event.event_id,
        correlation_id=intent_id,
        exposure_effect=exposure_effect.value,
        current_exposure=0.0,
        resulting_exposure=size if side == "buy" else 0.0,
        authorized_at=datetime.now(UTC).isoformat(),
        authorization_hash=auth_event.payload["payload_hash"],
        metadata={
            "order_type": order.type.value.lower(),
            "price": float(order.price) if order.price is not None else None,
            "stop_price": float(order.stop_price)
            if order.stop_price is not None
            else None,
            "time_in_force": order.time_in_force.value.lower(),
        },
    )

    # 8. Submit via gateway using AuthorizedOrder object
    gateway = BrokerGateway(adapter=adapter, store=store)
    result = gateway.submit(authorized, correlation_id=intent_id)

    if result.success and result.broker_order_id:
        lifecycle.submit_order(
            intent_id=intent_id,
            exchange_order_id=result.broker_order_id,
        )

    return {
        "id": result.broker_order_id,
        "status": "submitted" if result.success else "rejected",
        "filled_qty": 0.0,
        "avg_fill_price": 0.0,
        "error": result.error,
    }


@live.command("connect")
@click.option(
    "--broker",
    "-b",
    type=click.Choice(["alpaca", "oanda", "ccxt"]),
    default="alpaca",
    help="Broker to connect to",
)
@click.option(
    "--paper/--live", default=True, help="Paper trading mode (default) or live"
)
@click.option(
    "--api-key",
    envvar="ALPACA_API_KEY",
    default=None,
    help="API key (or set ALPACA_API_KEY env)",
)
@click.option(
    "--api-secret",
    envvar="ALPACA_API_SECRET",
    default=None,
    help="API secret (or set ALPACA_API_SECRET env)",
)
@click.option(
    "--base-url",
    default=None,
    help="Base URL (for Alpaca: paper=https://paper-api.alpaca.markets, live=https://api.alpaca.markets)",
)
@click.option(
    "--account-id", envvar="OANDA_ACCOUNT_ID", default=None, help="OANDA account ID"
)
def live_connect(
    broker: str,
    paper: bool,
    api_key: str | None,
    api_secret: str | None,
    base_url: str | None,
    account_id: str | None,
):
    """Connect to a broker and test connection."""
    if not paper:
        console.print(
            "[bold red]Live-money connections are disabled; use --paper.[/bold red]"
        )
        return
    import asyncio

    from trading_agent.exchanges.alpaca_adapter import (
        AlpacaAdapter,
        AlpacaConfig,
    )
    from trading_agent.exchanges.live_broker import LiveBroker
    from trading_agent.exchanges.oanda_adapter import (
        OANDAAdapter,
        OANDAConfig,
    )

    console.print(
        f"[bold]Connecting to {broker.upper()} ({'paper' if paper else 'live'})...[/bold]"
    )

    try:
        if broker == "alpaca":
            if not api_key or not api_secret:
                console.print(
                    "[red]API key and secret required (use --api-key/--api-secret or ALPACA_API_KEY/ALPACA_API_SECRET env vars)[/red]"
                )
                return

            base = base_url or (
                "https://paper-api.alpaca.markets"
                if paper
                else "https://api.alpaca.markets"
            )
            adapter = AlpacaAdapter(
                AlpacaConfig(
                    api_key=api_key,
                    secret_key=api_secret,
                    paper=paper,
                    base_url=base,
                )
            )

        elif broker == "oanda":
            if not api_key or not api_secret or not account_id:
                console.print(
                    "[red]API key, secret, and account ID required for OANDA[/red]"
                )
                return

            base = base_url or (
                "https://api-fxpractice.oanda.com"
                if paper
                else "https://api-fxtrade.oanda.com"
            )
            adapter = OANDAAdapter(
                OANDAConfig(
                    access_token=api_key,
                    account_id=account_id,
                    environment="practice" if paper else "live",
                )
            )

        elif broker == "ccxt":
            from trading_agent.exchanges.ccxt_adapter import (
                CCXTAdapter,
                ExchangeConfig,
            )
            from trading_agent.exchanges.models import MarketType

            # Binance spot via env: BINANCE_API_KEY / BINANCE_API_SECRET
            binance_key = os.environ.get("BINANCE_API_KEY") or api_key
            binance_secret = os.environ.get("BINANCE_API_SECRET") or api_secret
            if not binance_key or not binance_secret:
                console.print(
                    "[red]Binance API key/secret required — set BINANCE_API_KEY / BINANCE_API_SECRET env vars (or --api-key/--api-secret)[/red]"
                )
                return
            adapter = CCXTAdapter(
                ExchangeConfig(
                    id="binance",
                    name="Binance",
                    api_key=binance_key,
                    secret=binance_secret,
                    sandbox=paper,
                    markets=[MarketType.SPOT, MarketType.FUTURES],
                    options={"defaultType": "spot"},
                )
            )

        # Test connection (async connect → sync facade)
        asyncio.run(adapter.connect())
        broker_face = LiveBroker(broker, adapter)

        console.print("[green]✅ Connected successfully![/green]")
        account = broker_face.get_account()
        console.print(f"  Account ID: {account.get('id', 'N/A')}")
        console.print(f"  Status: {account.get('status', 'N/A')}")
        console.print(f"  Currency: {account.get('currency', 'N/A')}")
        console.print(f"  Cash: ${float(account.get('cash', 0)):,.2f}")
        console.print(
            f"  Portfolio Value: ${float(account.get('portfolio_value', 0)):,.2f}"
        )
        console.print(f"  Buying Power: ${float(account.get('buying_power', 0)):,.2f}")

        # Store facade for subsequent commands (in-memory for session)
        from trading_agent.cli import _live_adapters

        _live_adapters[broker] = broker_face
        console.print(
            "\n[dim]Adapter cached for session. Use `trading-agent live balance` etc.[/dim]"
        )

    except Exception as e:
        console.print(f"[red]❌ Connection failed: {e}[/red]")
        import traceback

        traceback.print_exc()


# In-memory adapter storage for session
_live_adapters: dict = {}


@live.command("balance")
@click.option(
    "--broker",
    "-b",
    type=click.Choice(["alpaca", "oanda", "ccxt"]),
    default="alpaca",
    help="Broker",
)
def live_balance(broker: str):
    """Show account balance and portfolio value."""
    from trading_agent.cli import _live_adapters

    adapter = _live_adapters.get(broker)
    if not adapter:
        console.print(
            f"[yellow]Not connected to {broker}. Run `trading-agent live connect --broker {broker}` first.[/yellow]"
        )
        return

    try:
        account = adapter.get_account()
        console.print(f"[bold]{broker.upper()} Account Balance[/bold]")
        t = Table("Metric", "Value")
        t.add_row("Cash", f"${float(account.get('cash', 0)):,.2f}")
        t.add_row(
            "Portfolio Value", f"${float(account.get('portfolio_value', 0)):,.2f}"
        )
        t.add_row("Buying Power", f"${float(account.get('buying_power', 0)):,.2f}")
        t.add_row("Equity", f"${float(account.get('equity', 0)):,.2f}")
        t.add_row(
            "Long Market Value", f"${float(account.get('long_market_value', 0)):,.2f}"
        )
        t.add_row(
            "Short Market Value", f"${float(account.get('short_market_value', 0)):,.2f}"
        )
        t.add_row("Unrealized P&L", f"${float(account.get('unrealized_pl', 0)):+,.2f}")
        t.add_row(
            "Realized P&L (Day)", f"${float(account.get('realized_pl_day', 0)):+,.2f}"
        )
        console.print(t)
    except Exception as e:
        console.print(f"[red]Error fetching balance: {e}[/red]")


@live.command("positions")
@click.option(
    "--broker",
    "-b",
    type=click.Choice(["alpaca", "oanda", "ccxt"]),
    default="alpaca",
    help="Broker",
)
def live_positions(broker: str):
    """Show current open positions."""
    from trading_agent.cli import _live_adapters

    adapter = _live_adapters.get(broker)
    if not adapter:
        console.print(
            f"[yellow]Not connected to {broker}. Run `trading-agent live connect --broker {broker}` first.[/yellow]"
        )
        return

    try:
        positions = adapter.get_positions()

        if not positions:
            console.print("[dim]No open positions[/dim]")
            return

        console.print(f"[bold]{broker.upper()} Open Positions[/bold]")
        t = Table(
            "Symbol", "Side", "Qty", "Entry", "Current", "P&L", "P&L%", "Market Value"
        )
        for pos in positions:
            pnl = float(pos.get("unrealized_pl", 0))
            pnl_pct = float(pos.get("unrealized_plpc", 0)) * 100
            color = "green" if pnl >= 0 else "red"
            side = pos.get("side", "long")
            t.add_row(
                pos.get("symbol", "N/A"),
                side.upper(),
                f"{float(pos.get('qty', 0)):.4f}",
                f"${float(pos.get('avg_entry_price', 0)):,.2f}",
                f"${float(pos.get('current_price', 0)):,.2f}",
                f"[{color}]${pnl:+,.2f}[/{color}]",
                f"[{color}]{pnl_pct:+.2f}%[/{color}]",
                f"${float(pos.get('market_value', 0)):,.2f}",
            )
        console.print(t)
    except Exception as e:
        console.print(f"[red]Error fetching positions: {e}[/red]")


@live.command("orders")
@click.option(
    "--broker",
    "-b",
    type=click.Choice(["alpaca", "oanda", "ccxt"]),
    default="alpaca",
    help="Broker",
)
@click.option(
    "--status",
    "-s",
    type=click.Choice(["open", "closed", "all"]),
    default="open",
    help="Order status filter",
)
@click.option("--limit", "-n", default=20, type=int, help="Number of orders to show")
def live_orders(broker: str, status: str, limit: int):
    """Show order history."""
    from trading_agent.cli import _live_adapters

    adapter = _live_adapters.get(broker)
    if not adapter:
        console.print(
            f"[yellow]Not connected to {broker}. Run `trading-agent live connect --broker {broker}` first.[/yellow]"
        )
        return

    try:
        orders = adapter.get_orders(status=status, limit=limit)

        if not orders:
            console.print(f"[dim]No {status} orders[/dim]")
            return

        console.print(
            f"[bold]{broker.upper()} {status.title()} Orders (last {limit})[/bold]"
        )
        t = Table(
            "ID", "Symbol", "Side", "Type", "Qty", "Filled", "Price", "Status", "Time"
        )
        for o in orders:
            filled = float(o.get("filled_qty", 0))
            total = float(o.get("qty", 0))
            avg_price = (
                float(o.get("avg_fill_price", 0)) if o.get("avg_fill_price") else 0
            )
            t.add_row(
                o.get("id", "N/A")[:8],
                o.get("symbol", "N/A"),
                o.get("side", "N/A").upper(),
                o.get("type", "N/A").upper(),
                f"{total:.4f}",
                f"{filled:.4f}",
                f"${avg_price:,.2f}" if avg_price else "—",
                o.get("status", "N/A").upper(),
                o.get("submitted_at", "N/A")[:19].replace("T", " "),
            )
        console.print(t)
    except Exception as e:
        console.print(f"[red]Error fetching orders: {e}[/red]")


@live.command("order")
@click.argument("symbol")
@click.argument("side", type=click.Choice(["buy", "sell"]))
@click.argument("qty", type=float)
@click.option(
    "--broker",
    "-b",
    type=click.Choice(["alpaca", "oanda", "ccxt"]),
    default="alpaca",
    help="Broker",
)
@click.option(
    "--type",
    "order_type",
    type=click.Choice(["market", "limit", "stop", "stop_limit", "twap", "vwap"]),
    default="market",
    help="Order type",
)
@click.option("--price", "-p", default=None, type=float, help="Limit/stop price")
@click.option(
    "--stop-price", default=None, type=float, help="Stop price for stop_limit"
)
@click.option(
    "--time-in-force",
    type=click.Choice(["day", "gtc", "ioc", "fok"]),
    default="day",
    help="Time in force",
)
@click.option("--dry-run/--execute", default=True, help="Dry run (default) or execute")
def live_order(
    symbol: str,
    side: str,
    qty: float,
    broker: str,
    order_type: str,
    price: float | None,
    stop_price: float | None,
    time_in_force: str,
    dry_run: bool,
):
    """Place an order."""
    from decimal import Decimal

    from trading_agent.cli import _live_adapters
    from trading_agent.exchanges.models import (
        Order,
        OrderSide,
        OrderType,
        TimeInForce,
        crypto_symbol,
        forex_symbol,
        stock_symbol,
    )

    adapter = _live_adapters.get(broker)
    if not adapter:
        console.print(
            f"[yellow]Not connected to {broker}. Run `trading-agent live connect --broker {broker}` first.[/yellow]"
        )
        return

    if order_type in ["limit", "stop_limit"] and price is None:
        console.print(f"[red]Price required for {order_type} orders[/red]")
        return

    if order_type == "stop_limit" and stop_price is None:
        console.print("[red]Stop price required for stop_limit orders[/red]")
        return

    # Determine unified symbol by broker asset class
    if broker == "alpaca":
        sym = stock_symbol(symbol, "alpaca")
    elif broker == "oanda":
        base, _, quote = symbol.partition("/")
        sym = forex_symbol(base, quote, "oanda")
    else:
        sym = crypto_symbol(symbol, "binance")

    # twap/vwap are smart-execution types, not broker types → execute as market
    broker_type = (
        order_type
        if order_type in ("market", "limit", "stop", "stop_limit")
        else "market"
    )

    order = Order(
        id=f"cli_{uuid.uuid4().hex}",
        symbol=sym,
        side=OrderSide.BUY if side == "buy" else OrderSide.SELL,
        type=OrderType(broker_type.upper()),
        size=Decimal(str(qty)),
        price=Decimal(str(price)) if price else None,
        stop_price=Decimal(str(stop_price)) if stop_price else None,
        time_in_force=TimeInForce(time_in_force.upper()),
    )

    console.print(
        f"[bold]Order: {side.upper()} {qty:.4f} {sym.pair} @ {order_type.upper()}{f' ${price:,.2f}' if price else ''}[/bold]"
    )
    console.print(f"  Time in force: {time_in_force.upper()}")

    if dry_run:
        console.print("[yellow]DRY RUN - order not placed[/yellow]")
        return

    execution_error = _paper_execution_error(broker, adapter)
    if execution_error:
        console.print(f"[bold red]Execution refused: {execution_error}[/bold red]")
        return

    # Confirm
    from rich.prompt import Confirm

    if not Confirm.ask("Execute this order?"):
        console.print("[yellow]Cancelled[/yellow]")
        return

    try:
        result = _place_order_via_gateway(adapter, order)
        console.print("[green]✅ Order placed![/green]")
        console.print(f"  Order ID: {result.get('id', 'N/A')}")
        console.print(f"  Status: {result.get('status', 'N/A')}")
        if result.get("filled_qty"):
            console.print(
                f"  Filled: {float(result['filled_qty']):.4f} @ ${float(result['avg_fill_price']):,.2f}"
            )
        if result.get("error"):
            console.print(f"[red]  Error: {result['error']}[/red]")
    except Exception as e:
        console.print(f"[red]❌ Order failed: {e}[/red]")


@live.command("run")
@click.argument("symbol")
@click.option("--timeframe", "-t", default="1h", help="Timeframe")
@click.option(
    "--broker",
    "-b",
    type=click.Choice(["alpaca", "oanda", "ccxt"]),
    default="alpaca",
    help="Broker",
)
@click.option("--strategy", "-s", default="regime_switching", help="Strategy to run")
@click.option(
    "--capital", "-c", default=None, type=float, help="Portfolio value override"
)
@click.option("--stop-loss", default=0.05, type=float, help="Stop-loss distance")
@click.option("--interval", "-i", default=300, type=int, help="Run interval in seconds")
@click.option(
    "--iterations",
    "-n",
    default=0,
    type=int,
    help="Number of iterations (0 = infinite)",
)
@click.option("--dry-run/--execute", default=True, help="Dry run (default) or execute")
def live_run(
    symbol: str,
    timeframe: str,
    broker: str,
    strategy: str,
    capital: float | None,
    stop_loss: float,
    interval: int,
    iterations: int,
    dry_run: bool,
):
    """Deprecated generic loop; use the reviewed Alpaca Paper cycle instead."""
    console.print(
        "[bold red]Generic `live run` is disabled because its broker adapter path "
        "is not execution-safe. Use the Alpaca Paper Web cycle or "
        "scripts/live_enhanced_ma.py.[/bold red]"
    )


@live.command("monitor")
@click.option(
    "--broker",
    "-b",
    type=click.Choice(["alpaca", "oanda", "ccxt"]),
    default="alpaca",
    help="Broker",
)
@click.option(
    "--interval", "-i", default=30, type=int, help="Update interval in seconds"
)
@click.option(
    "--iterations",
    "-n",
    default=0,
    type=int,
    help="Number of iterations (0 = infinite)",
)
def live_monitor(broker: str, interval: int, iterations: int):
    """Read-only monitor for broker positions, P&L, and open orders."""
    from trading_agent.cli import _live_adapters

    adapter = _live_adapters.get(broker)
    if not adapter:
        console.print(
            f"[yellow]Not connected to {broker}. Run `trading-agent live connect --broker {broker}` first.[/yellow]"
        )
        return

    console.print(f"[bold]Monitoring {broker.upper()}...[/bold]")
    console.print("[dim]Press Ctrl+C to stop[/dim]\n")

    try:
        iteration = 0
        while iterations == 0 or iteration < iterations:
            iteration += 1
            console.print(
                f"\n[cyan]=== Monitor Update {iteration} @ {time.strftime('%H:%M:%S')} ===[/cyan]"
            )

            # Portfolio summary
            account = adapter.get_account()
            equity = float(account.get("portfolio_value", 0))
            cash = float(account.get("cash", 0))
            unrealized = float(account.get("unrealized_pl", 0))

            ret_str = (
                f"[green]{unrealized:+,.2f}[/green]"
                if unrealized >= 0
                else f"[red]{unrealized:+,.2f}[/red]"
            )
            console.print(
                f"  Equity: ${equity:,.2f}  Cash: ${cash:,.2f}  Unrealized P&L: {ret_str}"
            )

            # Positions
            positions = adapter.get_positions()
            if positions:
                t = Table("Symbol", "Qty", "Entry", "Current", "P&L", "P&L%")
                for p in positions:
                    pnl = float(p.get("unrealized_pl", 0))
                    pnl_pct = float(p.get("unrealized_plpc", 0)) * 100
                    color = "green" if pnl >= 0 else "red"
                    t.add_row(
                        p.get("symbol", "N/A"),
                        f"{float(p.get('qty', 0)):.4f}",
                        f"${float(p.get('avg_entry_price', 0)):,.2f}",
                        f"${float(p.get('current_price', 0)):,.2f}",
                        f"[{color}]${pnl:+,.2f}[/{color}]",
                        f"[{color}]{pnl_pct:+.2f}%[/{color}]",
                    )
                console.print(t)
            else:
                console.print("  [dim]No open positions[/dim]")

            # Open orders
            orders = adapter.get_orders(status="open", limit=10)
            if orders:
                console.print("  [bold]Open Orders:[/bold]")
                t = Table("Symbol", "Side", "Type", "Qty", "Filled", "Status")
                for o in orders:
                    t.add_row(
                        o.get("symbol", "N/A"),
                        o.get("side", "N/A").upper(),
                        o.get("type", "N/A").upper(),
                        f"{float(o.get('qty', 0)):.4f}",
                        f"{float(o.get('filled_qty', 0)):.4f}",
                        o.get("status", "N/A").upper(),
                    )
                console.print(t)

            if iterations == 0 or iteration < iterations:
                time.sleep(interval)

    except KeyboardInterrupt:
        console.print("\n[yellow]Monitor stopped[/yellow]")
