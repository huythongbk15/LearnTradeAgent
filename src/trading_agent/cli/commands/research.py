"""CLI commands — decomposed from the legacy monolith. Behavior unchanged."""

from __future__ import annotations

import math
import json
import time
from rich.table import Table
from rich.panel import Panel
import click
from trading_agent.cli._common import console

# ── llm subcommands ───────────────────────────────────────────────────────


@click.group()
def llm():
    """LLM cache & cost management."""


@llm.command("cache-stats")
def llm_cache_stats():
    """Show LLM cache statistics."""
    from trading_agent.agents.llm import _CACHE_DIR, _CACHE_TTL_SECONDS

    cache_files = list(_CACHE_DIR.glob("*.json")) if _CACHE_DIR.exists() else []
    total_size = sum(f.stat().st_size for f in cache_files)
    now = time.time()

    valid = 0
    expired = 0
    for f in cache_files:
        try:
            with f.open(encoding="utf-8") as fp:
                data = json.load(fp)
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


@llm.command("calibration")
@click.option(
    "--pairs",
    type=click.Path(exists=True, dir_okay=False),
    default=None,
    help="JSON file with [confidence, correct] pairs, e.g. [[0.8, true], ...]",
)
def llm_calibration(pairs):
    """Show confidence reliability diagram + ECE (calibration audit).

    Reads (confidence, correct) observations from a JSON file of
    [confidence, correct] pairs, e.g.::

        [[0.8, true], [0.6, false], [0.9, true], ...]

    Prints per-bin reliability (avg confidence vs empirical accuracy) and
    the Expected Calibration Error (ECE).  ECE close to 0 means the
    model's confidence matches its hit rate.
    """
    from trading_agent.agents.calibration import ConfidenceCalibrator

    if pairs is None:
        raise click.ClickException(
            "need --pairs FILE (JSON list of [confidence, correct]) — "
            "e.g. `trading-agent llm calibration --pairs pairs.json`"
        )

    with open(pairs, encoding="utf-8") as fp:
        raw = json.load(fp)
    if not isinstance(raw, list):
        raise click.ClickException(
            "--pairs must be a JSON list of [confidence, correct]"
        )

    cal = ConfidenceCalibrator(bins=10)
    for item in raw:
        if not (isinstance(item, list) and len(item) == 2):
            raise click.ClickException(f"invalid pair: {item!r}")
        cal.add_observation(float(item[0]), bool(item[1]))

    report = cal.report()
    if report["n"] == 0:
        console.print("[yellow]No observations loaded.[/yellow]")
        return

    t = Table("Confidence bin", "Count", "Avg confidence", "Accuracy", "Gap")
    for bin_ in report["bins"]:
        gap = abs(bin_["avg_confidence"] - bin_["accuracy"])
        t.add_row(
            f"{bin_['low']:.1f}-{bin_['high']:.1f}",
            str(bin_["count"]),
            f"{bin_['avg_confidence']:.3f}",
            f"{bin_['accuracy']:.3f}",
            f"{gap:.3f}",
        )
    console.print(t)
    console.print(f"Observations: [bold]{report['n']}[/bold]")
    console.print(
        f"Expected Calibration Error (ECE): [bold]{report['ece']:.4f}[/bold] "
        "[green](0 = perfectly calibrated)[/green]"
    )


@llm.command("cache-clear")
@click.option(
    "--all", "clear_all", is_flag=True, help="Clear all cache (including valid)"
)
@click.confirmation_option(prompt="Clear LLM cache?")
def llm_cache_clear(clear_all: bool):
    """Clear LLM cache."""
    from trading_agent.agents.llm import _CACHE_DIR, _CACHE_TTL_SECONDS

    cache_files = list(_CACHE_DIR.glob("*.json")) if _CACHE_DIR.exists() else []
    removed = 0
    for f in cache_files:
        try:
            with f.open(encoding="utf-8") as fp:
                data = json.load(fp)
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
    t.add_row(
        "OpenCode (deepseek-v4-flash-free)",
        f"{daily_calls * 30:,}",
        f"{monthly_tokens:,}",
        "$0.00",
    )
    t.add_row(
        "DeepSeek API (free tier)",
        f"{daily_calls * 30:,}",
        f"{monthly_tokens:,}",
        "$0.00*",
    )
    t.add_row(
        "NVIDIA NIM (free tier)",
        f"{daily_calls * 30:,}",
        f"{monthly_tokens:,}",
        "$0.00*",
    )
    t.add_row(
        "Groq (free tier)", f"{daily_calls * 30:,}", f"{monthly_tokens:,}", "$0.00*"
    )
    t.add_row(
        "OpenRouter (free models)",
        f"{daily_calls * 30:,}",
        f"{monthly_tokens:,}",
        "$0.00*",
    )
    console.print(t)

    console.print(
        f"\nWith cache (80% hit rate): {monthly_tokens * 0.2:,.0f} tokens → $0.00"
    )
    console.print("[dim]* Free tier limits apply (rate limits, context window)[/dim]")


# ── meta-learning subcommands ───────────────────────────────────────────


@click.group()
def meta():
    """Meta-learning for strategy adaptation (MAML/Reptile/Meta-SGD/ANIL)."""


@meta.command("train")
@click.argument("data_dir", type=click.Path(exists=True, file_okay=False))
@click.option(
    "--algorithm",
    "-a",
    type=click.Choice(["maml", "reptile", "metasgd", "anil"]),
    default="maml",
)
@click.option("--steps", "-s", default=100, help="Meta-training steps")
@click.option("--meta-lr", default=0.01, help="Meta learning rate")
@click.option("--inner-lr", default=0.1, help="Inner learning rate")
@click.option("--inner-steps", default=5, help="Inner loop steps")
@click.option("--batch-size", default=4, help="Meta batch size")
@click.option("--output", "-o", type=click.Path(), help="Output JSON file")
def meta_train(
    data_dir: str,
    algorithm: str,
    steps: int,
    meta_lr: float,
    inner_lr: float,
    inner_steps: int,
    batch_size: int,
    output: str | None,
):
    """Meta-train on multiple market regimes."""
    import asyncio
    from trading_agent.ml.meta_learning import train

    asyncio.run(
        train.callback(
            data_dir=data_dir,
            algorithm=algorithm,
            steps=steps,
            meta_lr=meta_lr,
            inner_lr=inner_lr,
            inner_steps=inner_steps,
            batch_size=batch_size,
            output=output,
        )
    )


@meta.command("adapt")
@click.argument("model_path", type=click.Path(exists=True))
@click.argument("regime_data", type=click.Path(exists=True))
@click.option("--n-samples", "-n", default=20, help="Samples for adaptation")
@click.option("--output", "-o", type=click.Path(), help="Output JSON file")
def meta_adapt(model_path: str, regime_data: str, n_samples: int, output: str | None):
    """Adapt meta-learned strategy to new regime."""
    import asyncio
    from trading_agent.ml.meta_learning import adapt

    asyncio.run(
        adapt.callback(
            model_path=model_path,
            regime_data=regime_data,
            n_samples=n_samples,
            output=output,
        )
    )


@meta.command("backtest")
@click.argument("adapted_params", type=click.Path(exists=True))
@click.argument("data", type=click.Path(exists=True))
@click.option("--capital", "-c", default=10000, help="Initial capital")
@click.option("--commission", default=0.0004, help="Commission rate")
@click.option("--slippage", default=0.0005, help="Slippage rate")
def meta_backtest(
    adapted_params: str, data: str, capital: float, commission: float, slippage: float
):
    """Run backtest with meta-learned parameters."""
    import asyncio
    from trading_agent.ml.meta_learning import backtest

    asyncio.run(
        backtest.callback(
            adapted_params=adapted_params,
            data=data,
            initial_capital=capital,
            commission=commission,
            slippage=slippage,
        )
    )


@meta.command("regimes")
@click.argument("data_dir", type=click.Path(exists=True, file_okay=False))
def meta_regimes(data_dir: str):
    """Analyze available regimes in data directory."""
    from trading_agent.ml.meta_learning import regimes

    regimes(data_dir=data_dir)


# ── event sourcing projections ───────────────────────────────────────────


@click.group()
def projection():
    """Event sourcing projection management."""


@projection.command("rebuild")
@click.argument("event_store_path")
@click.option("--from-position", default=0, help="Position to rebuild from")
@click.option("--projection", "-p", help="Specific projection to rebuild")
def projection_rebuild(
    event_store_path: str, from_position: int, projection: str | None
):
    """Rebuild projections from event store."""
    import asyncio
    from trading_agent.events.projection_manager import rebuild

    asyncio.run(
        rebuild.callback(
            event_store_path=event_store_path,
            from_position=from_position,
            projection=projection,
        )
    )


@projection.command("status")
@click.argument("event_store_path")
@click.option("--projection", "-p", help="Specific projection to show")
def projection_status(event_store_path: str, projection: str | None):
    """Show projection status."""
    import asyncio
    from trading_agent.events.projection_manager import status

    asyncio.run(
        status.callback(
            event_store_path=event_store_path,
            projection=projection,
        )
    )


@projection.command("query")
@click.argument("event_store_path")
@click.argument("projection")
@click.argument("key", required=False)
def projection_query(event_store_path: str, projection: str, key: str | None):
    """Query projection state."""
    import asyncio
    from trading_agent.events.projection_manager import query

    asyncio.run(
        query.callback(
            event_store_path=event_store_path,
            projection=projection,
            key=key,
        )
    )


# ── options strategies subcommands ───────────────────────────────────────


@click.group()
def options():
    """Options strategies: vol selling, gamma scalping, dispersion."""


@options.command("chain")
@click.argument("symbol", default="BTC")
@click.option("--expiry", "-e", default=None, help="Expiry date (YYYY-MM-DD)")
@click.option("--spot", "-s", default=0.0, type=float, help="Spot price (0 = auto)")
def options_chain(symbol: str, expiry: str | None, spot: float):
    """Display option chain for a symbol."""
    from rich.table import Table as RichTable
    from trading_agent.strategies.options_strategies import OptionChainProvider

    provider = OptionChainProvider(dry_run=True)
    chain = provider.get_chain(symbol, expiry=expiry, spot=spot)

    console.print(
        f"\n[bold]{symbol} Options Chain[/bold] — Spot: ${chain.spot:,.0f}, Expiry: {chain.expiry}"
    )

    # Calls
    t = RichTable(
        "Strike", "IV", "Delta", "Gamma", "Theta", "Vega", "Volume", "OI", "Bid/Ask"
    )
    for c in sorted(chain.calls, key=lambda x: x.volume, reverse=True)[:10]:
        g = c.greeks
        t.add_row(
            f"${c.strike:,.0f}",
            f"{c.iv:.1%}",
            f"{g.get('delta', 0):.3f}",
            f"{g.get('gamma', 0):.5f}",
            f"{g.get('theta', 0):.2f}",
            f"{g.get('vega', 0):.2f}",
            f"{c.volume:,}",
            f"{c.open_interest:,}",
            f"{c.bid:.2f}/{c.ask:.2f}",
        )
    console.print(Panel(t, title="Calls (Top 10 by Volume)", border_style="green"))

    # Puts
    t = RichTable(
        "Strike", "IV", "Delta", "Gamma", "Theta", "Vega", "Volume", "OI", "Bid/Ask"
    )
    for p in sorted(chain.puts, key=lambda x: x.volume, reverse=True)[:10]:
        g = p.greeks
        t.add_row(
            f"${p.strike:,.0f}",
            f"{p.iv:.1%}",
            f"{g.get('delta', 0):.3f}",
            f"{g.get('gamma', 0):.5f}",
            f"{g.get('theta', 0):.2f}",
            f"{g.get('vega', 0):.2f}",
            f"{p.volume:,}",
            f"{p.open_interest:,}",
            f"{p.bid:.2f}/{p.ask:.2f}",
        )
    console.print(Panel(t, title="Puts (Top 10 by Volume)", border_style="red"))


@options.command("flow")
@click.argument("symbol", default="BTC")
@click.option("--hours", "-h", default=24, type=int, help="Lookback hours")
def options_flow(symbol: str, hours: int):
    """Analyze options flow: unusual volume, put/call ratio, delta exposure."""
    from trading_agent.strategies.options_strategies import OptionChainProvider

    provider = OptionChainProvider(dry_run=True)
    flow = provider.analyze_flow(symbol, lookback_hours=hours)

    console.print(f"\n[bold]{symbol} Options Flow[/bold] ({hours}h lookback)")
    t = Table("Metric", "Value")
    t.add_row("Call Volume", f"{flow.total_call_volume:,}")
    t.add_row("Put Volume", f"{flow.total_put_volume:,}")
    t.add_row("Put/Call Ratio", f"{flow.put_call_ratio:.2f}")
    t.add_row("Unusual Trades", str(len(flow.unusual_trades)))
    t.add_row("Net Delta Exposure", f"{flow.net_delta_exposure:,.0f}")
    t.add_row("25Δ Skew", f"{flow.skew_25d:.4f}")
    console.print(t)

    if flow.unusual_trades:
        console.print("\n[bold]Unusual Trades:[/bold]")
        ut = Table("Strike", "Type", "Volume", "IV", "OI")
        for ut in flow.unusual_trades:
            ut.add_row(
                f"${ut['strike']:,.0f}",
                ut["type"].upper(),
                f"{ut['volume']:,}",
                f"{ut['iv']:.1%}",
                f"{ut['oi']:,}",
            )
        console.print(ut)


@options.command("covered-call")
@click.argument("symbol", default="BTC")
@click.option(
    "--delta", "-d", default=0.20, type=float, help="Target delta for short call"
)
@click.option("--dte-min", default=7, type=int, help="Min DTE")
@click.option("--dte-max", default=45, type=int, help="Max DTE")
@click.option("--min-yield", default=0.05, type=float, help="Min annualized yield")
def options_covered_call(
    symbol: str, delta: float, dte_min: int, dte_max: int, min_yield: float
):
    """Find covered call opportunities."""
    from rich.table import Table as RichTable
    from trading_agent.strategies.options_strategies import (
        CoveredCallStrategy,
        OptionChainProvider,
    )

    provider = OptionChainProvider(dry_run=True)
    strategy = CoveredCallStrategy(
        symbol,
        provider,
        config={
            "delta_target": delta,
            "dte_min": dte_min,
            "dte_max": dte_max,
            "min_annual_yield": min_yield,
        },
    )
    chain = provider.get_chain(symbol)
    signals = strategy.generate_signals(chain)

    if not signals:
        console.print("[yellow]No covered call signals found[/yellow]")
        return

    console.print(
        f"\n[bold]Covered Call Signals for {symbol}[/bold] (Spot: ${chain.spot:,.0f})"
    )
    t = RichTable("Strike", "Delta", "Bid", "Annual Yield", "DTE")
    for s in signals:
        c = s["contract"]
        t.add_row(
            f"${c.strike:,.0f}",
            f"{s['delta']:.2f}",
            f"${c.bid:.2f}",
            f"{s['premium_yield_annual']:.1%}",
            f"{strategy._dte(chain.expiry)}",
        )
    console.print(t)


@options.command("cash-secured-put")
@click.argument("symbol", default="BTC")
@click.option(
    "--delta", "-d", default=0.20, type=float, help="Target delta for short put"
)
@click.option("--dte-min", default=7, type=int, help="Min DTE")
@click.option("--dte-max", default=45, type=int, help="Max DTE")
@click.option("--min-yield", default=0.05, type=float, help="Min annualized yield")
def options_cash_secured_put(
    symbol: str, delta: float, dte_min: int, dte_max: int, min_yield: float
):
    """Find cash-secured put opportunities."""
    from rich.table import Table as RichTable
    from trading_agent.strategies.options_strategies import (
        CashSecuredPutStrategy,
        OptionChainProvider,
    )

    provider = OptionChainProvider(dry_run=True)
    strategy = CashSecuredPutStrategy(
        symbol,
        provider,
        config={
            "delta_target": delta,
            "dte_min": dte_min,
            "dte_max": dte_max,
            "min_annual_yield": min_yield,
        },
    )
    chain = provider.get_chain(symbol)
    signals = strategy.generate_signals(chain)

    if not signals:
        console.print("[yellow]No cash-secured put signals found[/yellow]")
        return

    console.print(
        f"\n[bold]Cash-Secured Put Signals for {symbol}[/bold] (Spot: ${chain.spot:,.0f})"
    )
    t = RichTable("Strike", "Delta", "Bid", "Cash Required", "Annual Yield", "DTE")
    for s in signals:
        p = s["contract"]
        t.add_row(
            f"${p.strike:,.0f}",
            f"{s['delta']:.2f}",
            f"${p.bid:.2f}",
            f"${s['cash_required']:,.0f}",
            f"{s['premium_yield_annual']:.1%}",
            f"{strategy._dte(chain.expiry)}",
        )
    console.print(t)


@options.command("iron-condor")
@click.argument("symbol", default="BTC")
@click.option("--delta-short", default=0.15, type=float, help="Short strike delta")
@click.option("--delta-long", default=0.05, type=float, help="Long wing delta")
@click.option("--dte-min", default=14, type=int, help="Min DTE")
@click.option("--dte-max", default=60, type=int, help="Max DTE")
def options_iron_condor(
    symbol: str, delta_short: float, delta_long: float, dte_min: int, dte_max: int
):
    """Find iron condor opportunities."""
    from rich.table import Table as RichTable
    from trading_agent.strategies.options_strategies import (
        IronCondorStrategy,
        OptionChainProvider,
    )

    provider = OptionChainProvider(dry_run=True)
    strategy = IronCondorStrategy(
        symbol,
        provider,
        config={
            "delta_short": delta_short,
            "delta_long": delta_long,
            "dte_min": dte_min,
            "dte_max": dte_max,
        },
    )
    chain = provider.get_chain(symbol)
    signals = strategy.generate_signals(chain)

    if not signals:
        console.print("[yellow]No iron condor signals found[/yellow]")
        return

    console.print(
        f"\n[bold]Iron Condor Signals for {symbol}[/bold] (Spot: ${chain.spot:,.0f})"
    )
    t = RichTable(
        "Short Call",
        "Short Put",
        "Long Call",
        "Long Put",
        "Credit",
        "Max Loss",
        "R/R",
        "Prob Profit",
        "DTE",
    )
    for s in signals:
        t.add_row(
            f"${s['short_call'].strike:,.0f}",
            f"${s['short_put'].strike:,.0f}",
            f"${s['long_call'].strike:,.0f}",
            f"${s['long_put'].strike:,.0f}",
            f"${s['credit']:.0f}",
            f"${s['max_loss']:.0f}",
            f"{s['risk_reward']:.2f}",
            f"{s['prob_profit']:.1%}",
            f"{s['dte']}",
        )
    console.print(t)


@options.command("gamma-scalp")
@click.argument("symbol", default="BTC")
@click.option(
    "--simulate", "-s", is_flag=True, help="Run simulation with random price path"
)
@click.option("--steps", default=50, type=int, help="Simulation steps")
@click.option(
    "--vol", default=0.5, type=float, help="Realized volatility for simulation"
)
def options_gamma_scalp(symbol: str, simulate: bool, steps: int, vol: float):
    """Gamma scalping: buy straddle + dynamic delta hedge."""
    import random

    from trading_agent.strategies.options_strategies import (
        GammaScalpStrategy,
        OptionChainProvider,
    )

    provider = OptionChainProvider(dry_run=True)
    strategy = GammaScalpStrategy(symbol, provider)
    chain = provider.get_chain(symbol)

    # Enter straddle
    enter_result = strategy.enter_straddle(chain)
    if not enter_result:
        console.print("[red]Could not enter straddle[/red]")
        return

    console.print("\n[bold]Gamma Scalp Entered[/bold]")
    console.print(f"  Strike: ${enter_result['strike']:,.0f}")
    console.print(f"  Cost: ${enter_result['total_cost']:.2f}")
    console.print(f"  Initial Delta: {enter_result['initial_delta']:.4f}")
    console.print(f"  Initial Gamma: {enter_result['initial_gamma']:.6f}")
    console.print(f"  Initial Hedge: {enter_result['hedge_qty']:.4f} {symbol}")

    if simulate:
        console.print(f"\n[bold]Simulating {steps} steps with vol={vol:.0%}...[/bold]")
        spot = chain.spot
        for i in range(steps):
            # Random walk
            dt = 1 / (252 * 6.5)  # 1 hour steps
            spot *= math.exp(
                (vol**2 * -0.5) * dt + vol * math.sqrt(dt) * random.gauss(0, 1)
            )
            new_chain = provider.get_chain(symbol, spot=spot)
            strategy.rebalance_delta(spot, new_chain)

        # Exit
        exit_result = strategy.exit_straddle(provider.get_chain(symbol, spot=spot))
        console.print("\n[bold]Simulation Complete[/bold]")
        console.print(f"  Final Spot: ${spot:,.0f}")
        console.print(f"  Straddle P&L: ${exit_result['straddle_pnl']:+.2f}")
        console.print(f"  Scalp P&L: ${exit_result['scalp_pnl']:+.2f}")
        console.print(f"  Total P&L: ${exit_result['total_pnl']:+.2f}")
        console.print(f"  Rebalances: {exit_result['rebalance_count']}")


@options.command("calendar")
@click.argument("symbol", default="BTC")
@click.option("--spot", "-s", default=0.0, type=float, help="Spot price")
def options_calendar(symbol: str, spot: float):
    """Find calendar spread opportunities."""
    from rich.table import Table as RichTable
    from trading_agent.strategies.options_strategies import (
        CalendarSpreadStrategy,
        OptionChainProvider,
    )

    provider = OptionChainProvider(dry_run=True)
    strategy = CalendarSpreadStrategy(symbol, provider)
    signals = strategy.generate_signals(spot or 0)

    if not signals:
        console.print("[yellow]No calendar spread signals found[/yellow]")
        return

    console.print(f"\n[bold]Calendar Spread Signals for {symbol}[/bold]")
    t = RichTable(
        "Type",
        "Strike",
        "Near Expiry",
        "Far Expiry",
        "Near IV",
        "Far IV",
        "Term Struct",
        "Theta Carry",
        "Net Debit",
    )
    for s in signals:
        t.add_row(
            s["action"].replace("BUY_CALENDAR_", ""),
            f"${s['strike']:,.0f}",
            s["near_expiry"],
            s["far_expiry"],
            f"{s['iv_near']:.1%}",
            f"{s['iv_far']:.1%}",
            f"{s['term_structure']:.4f}",
            f"{s['theta_carry']:.4f}",
            f"${s['net_debit']:.2f}",
        )
    console.print(t)


@options.command("dispersion")
@click.argument("index_symbol", default="BTC")
@click.option(
    "--components",
    "-c",
    default="ETH,SOL,ADA",
    help="Component symbols (comma-separated)",
)
def options_dispersion(index_symbol: str, components: str):
    """Dispersion trading: index vs component vol."""
    from trading_agent.strategies.options_strategies import (
        DispersionStrategy,
        OptionChainProvider,
    )

    provider = OptionChainProvider(dry_run=True)
    comp_list = [c.strip() for c in components.split(",")]

    # Get component spots
    comp_spots = {}
    for c in comp_list:
        chain = provider.get_chain(c)
        comp_spots[c] = chain.spot

    strategy = DispersionStrategy(index_symbol, comp_list, provider)
    signals = strategy.generate_signals(
        index_spot=provider.get_chain(index_symbol).spot,
        component_spots=comp_spots,
    )

    if not signals:
        console.print("[yellow]No dispersion signals found[/yellow]")
        return

    console.print("\n[bold]Dispersion Signals[/bold]")
    console.print(f"  Index: {index_symbol}")
    console.print(f"  Components: {', '.join(comp_list)}")
    for s in signals:
        console.print(f"  Index IV: {s['index_iv']:.1%}")
        console.print(f"  Avg Component IV: {s['avg_component_iv']:.1%}")
        console.print(f"  Dispersion Spread: {s['dispersion_spread']:.4f}")
        console.print(f"  Implied Correlation: {s['implied_correlation']:.2%}")
