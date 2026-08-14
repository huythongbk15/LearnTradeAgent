"""CLI commands — decomposed from the legacy monolith. Behavior unchanged."""

from __future__ import annotations

import json
from pathlib import Path

import click
from rich.panel import Panel
from rich.table import Table

from trading_agent.cli._common import config, console

# ── config subcommands ────────────────────────────────────────────────────


@click.group()
def config_group():
    """Configuration management."""


@config_group.command("validate")
@click.option("--config", "-c", "config_path", default=None, help="Config path")
def validate_config(config_path: str | None):
    """Validate config.yaml and report any issues."""
    from trading_agent.config.loader import Config, ConfigError

    path = Path(config_path) if config_path else Config.default_path()
    try:
        cfg = Config(path)
        console.print(f"[green]✅ Config valid:[/green] {path}")

        table = Table("Section", "Check", "Status")
        table.add_row(
            "exchanges",
            f"{len(cfg.exchanges)} configured",
            "✅" if cfg.enabled_exchanges else "⚠️  none enabled",
        )
        table.add_row(
            "data", f"tf={cfg.default_timeframe}, storage={cfg.data_storage}", "✅"
        )
        table.add_row(
            "symbols", f"{sum(len(v) for v in cfg.symbols.values())} total", "✅"
        )
        table.add_row("backtest", f"capital=${cfg.initial_capital:,.0f}", "✅")
        console.print(table)

    except ConfigError as e:
        console.print(f"[red]❌ Config error: {e}[/red]")
        raise SystemExit(1) from e
    except FileNotFoundError as e:
        console.print(f"[red]❌ File not found: {e}[/red]")
        raise SystemExit(1) from e


# ── portfolio subcommands ─────────────────────────────────────────────────


@click.group()
def portfolio():
    """Portfolio optimization, rebalancing, and management."""


@portfolio.command("optimize")
@click.argument("symbols", nargs=-1, required=True)
@click.option("--timeframe", "-t", default="1h", help="Timeframe for returns")
@click.option(
    "--method",
    "-m",
    type=click.Choice(
        [
            "max_sharpe",
            "min_variance",
            "mean_variance",
            "hrp",
            "black_litterman",
            "risk_parity",
            "max_div",
            "equal_weight",
        ]
    ),
    default="max_sharpe",
    help="Optimization method",
)
@click.option("--risk-free", default=0.02, type=float, help="Risk-free rate")
@click.option("--lookback", default=252, type=int, help="Lookback period (days)")
@click.option(
    "--cov-method",
    default="ledoit_wolf",
    type=click.Choice(["sample", "ledoit_wolf", "ewma"]),
    help="Covariance estimation method",
)
@click.option("--min-weight", default=0.0, type=float, help="Minimum weight per asset")
@click.option("--max-weight", default=1.0, type=float, help="Maximum weight per asset")
@click.option(
    "--target-return",
    default=None,
    type=float,
    help="Target return for mean-variance optimization",
)
@click.option(
    "--turnover", default=None, type=float, help="Max turnover from current portfolio"
)
@click.option(
    "--view",
    "views",
    multiple=True,
    help="Black-Litterman view: SYMBOL=RETURN (e.g., BTC/USDT=0.15)",
)
@click.option(
    "--confidence",
    "confidences",
    multiple=True,
    type=float,
    help="Confidence for each view (0-1)",
)
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
    from rich.table import Table as RichTable

    from trading_agent.data.storage import load_ohlcv
    from trading_agent.exchanges.models import AssetClass, MarketType, Symbol
    from trading_agent.portfolio.portfolio_optimizer import (
        OptimizationConstraints,
        OptimizerMethod,
        PortfolioOptimizer,
    )

    console.print(
        f"[bold]Optimizing portfolio with {len(symbols)} assets using {method}...[/bold]"
    )

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

            close_series = pd.Series(df["close"].to_numpy())
            returns = close_series.pct_change().dropna()
            returns_data[sym_str] = returns
            # Create Symbol object
            base, quote = sym_str.split("/") if "/" in sym_str else (sym_str, "USDT")
            symbol_objs.append(
                Symbol(base, quote, AssetClass.CRYPTO, MarketType.SPOT, "binance")
            )
        except FileNotFoundError:
            console.print(
                f"[red]Data not found for {sym_str}. Run `trading-agent data fetch {sym_str}` first.[/red]"
            )
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
        from trading_agent.portfolio.portfolio_optimizer import BlackLittermanViews

        absolute_views = {}
        confidence_dict = {}
        for i, view_str in enumerate(views):
            if "=" in view_str:
                sym, ret = view_str.split("=", 1)
                absolute_views[sym.strip()] = float(ret)
                if i < len(confidences):
                    confidence_dict[("absolute", sym.strip())] = confidences[i]
        bl_views = BlackLittermanViews(
            absolute=absolute_views, confidence=confidence_dict
        )

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

    from trading_agent.data.storage import load_ohlcv
    from trading_agent.exchanges.models import AssetClass, MarketType, Symbol
    from trading_agent.portfolio.portfolio_optimizer import (
        OptimizerMethod,
        PortfolioOptimizer,
    )

    console.print(
        f"[bold]Generating efficient frontier for {len(symbols)} assets...[/bold]"
    )

    # Load data
    returns_data = {}
    symbol_objs = []
    for sym_str in symbols:
        try:
            df = load_ohlcv(config.default_exchange, sym_str, timeframe)
            import pandas as pd

            close_series = pd.Series(df["close"].to_numpy())
            returns = close_series.pct_change().dropna()
            returns_data[sym_str] = returns
            base, quote = sym_str.split("/") if "/" in sym_str else (sym_str, "USDT")
            symbol_objs.append(
                Symbol(base, quote, AssetClass.CRYPTO, MarketType.SPOT, "binance")
            )
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
@click.option(
    "--method", "-m", default="max_sharpe", help="Optimization method for weights"
)
@click.option(
    "--simulations", "-n", default=200, type=int, help="Number of simulations"
)
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
    from rich.table import Table as RichTable

    from trading_agent.data.storage import load_ohlcv
    from trading_agent.exchanges.models import AssetClass, MarketType, Symbol
    from trading_agent.portfolio.portfolio_optimizer import (
        OptimizerMethod,
        PortfolioOptimizer,
    )

    console.print(
        f"[bold]Running Monte Carlo simulation ({simulations} paths, {horizon} days)...[/bold]"
    )

    # Load data
    returns_data = {}
    symbol_objs = []
    for sym_str in symbols:
        try:
            df = load_ohlcv(config.default_exchange, sym_str, timeframe)
            import pandas as pd

            close_series = pd.Series(df["close"].to_numpy())
            returns = close_series.pct_change().dropna()
            returns_data[sym_str] = returns
            base, quote = sym_str.split("/") if "/" in sym_str else (sym_str, "USDT")
            symbol_objs.append(
                Symbol(base, quote, AssetClass.CRYPTO, MarketType.SPOT, "binance")
            )
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

    from trading_agent.execution.engine import ExecutionEngine

    # Create rebalancer
    engine = ExecutionEngine()
    positions = engine.get_positions_summary()

    if symbols:
        sym_list = [s.strip() for s in symbols.split(",")]
    else:
        sym_list = [p["symbol"] for p in positions]

    console.print(f"[bold]Rebalancer Status for {len(sym_list)} symbols[/bold]")

    t = RichTable("Symbol", "Current %", "Target %", "Drift", "Trigger", "Status")
    for sym_str in sym_list:
        # Find position
        pos = next((p for p in positions if p["symbol"] == sym_str), None)
        current_pct = (
            pos["value"] / engine.exchange.get_total_equity() * 100 if pos else 0
        )
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
@click.option(
    "--method",
    "-m",
    type=click.Choice(["calendar", "threshold", "cppi", "risk_budget"]),
    default="threshold",
    help="Rebalancing method",
)
@click.option(
    "--threshold",
    "-d",
    default=0.05,
    type=float,
    help="Drift threshold for threshold method",
)
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
    import asyncio
    from decimal import Decimal

    from trading_agent.exchanges.models import AssetClass, MarketType, Position, Symbol
    from trading_agent.execution.engine import ExecutionEngine
    from trading_agent.portfolio.auto_rebalancer import (
        AutoRebalancer,
        CalendarRebalanceStrategy,
        CPPIRebalanceStrategy,
        RebalanceConfig,
        RiskBudgetRebalanceStrategy,
        ThresholdRebalanceStrategy,
    )

    console.print(
        f"[bold]Running {method} rebalancer for {len(symbols)} symbols...[/bold]"
    )

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
    target_weights = {
        Symbol(
            s.split("/")[0],
            s.split("/")[1] if "/" in s else "USDT",
            AssetClass.CRYPTO,
            MarketType.SPOT,
            "binance",
        ): 1.0 / len(symbols)
        for s in symbols
    }

    # Get current prices and create Position objects
    current_prices = {}
    positions_dict = {}
    for sym_str in symbols:
        pos = next((p for p in positions if p["symbol"] == sym_str), None)
        sym = Symbol(
            sym_str.split("/")[0],
            sym_str.split("/")[1] if "/" in sym_str else "USDT",
            AssetClass.CRYPTO,
            MarketType.SPOT,
            "binance",
        )
        price = Decimal(str(pos["current_price"])) if pos else Decimal("0")
        current_prices[sym] = price
        qty = Decimal(str(pos["quantity"])) if pos else Decimal("0")
        # Position uses size (positive=long, negative=short), entry_price, mark_price
        positions_dict[sym] = Position(
            symbol=sym,
            size=qty,
            entry_price=price if qty != 0 else Decimal("0"),
            mark_price=price,
        )

    # Run rebalancer (async)
    events = asyncio.run(
        rebalancer.check_and_rebalance(
            positions=positions_dict,
            prices=current_prices,
        )
    )

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

    from trading_agent.strategies.plugins import get_registry

    registry = get_registry()
    strategies = registry.list_strategies()

    if not strategies:
        console.print("[yellow]No strategies registered[/yellow]")
        return

    t = RichTable(
        "Name", "Version", "Type", "Risk", "Asset Classes", "Timeframes", "Status"
    )
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
    from rich.table import Table as RichTable

    from trading_agent.strategies.plugins import get_registry

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
                v.get("type", "any"),
                str(v.get("default", "")),
                str(v.get("min", "")),
                str(v.get("max", "")),
                "✅" if v.get("required", False) else "❌",
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
    from decimal import Decimal

    from trading_agent.data.storage import load_ohlcv
    from trading_agent.exchanges.models import AssetClass, Bar, MarketType, Symbol
    from trading_agent.strategies.plugins import StrategyContext, get_registry

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
    base, quote = symbol.split("/") if "/" in symbol else (symbol, "USDT")
    sym_obj = Symbol(
        base, quote, AssetClass.CRYPTO, MarketType.SPOT, config.default_exchange
    )

    signals = []
    for row in df.iter_rows(named=True):
        bar = Bar(
            symbol=sym_obj,
            timestamp=row["timestamp"],
            timeframe=timeframe,
            open=Decimal(str(row["open"])),
            high=Decimal(str(row["high"])),
            low=Decimal(str(row["low"])),
            close=Decimal(str(row["close"])),
            volume=Decimal(str(row["volume"])),
        )

        context = StrategyContext(
            symbol=sym_obj,
            bar=bar,
            position=None,
            portfolio_value=Decimal(str(capital)),
            available_balance=Decimal(str(capital)),
            current_time=row["timestamp"],
        )

        bar_signals = strategy_instance.on_bar(context)
        for sig in bar_signals:
            signals.append(sig)

    console.print(
        f"[bold]Generated {len(signals)} signals for {name} on {symbol}[/bold]"
    )

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

    from trading_agent.backtest.engine import run_backtest
    from trading_agent.strategies.plugins import get_registry

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
        "total_return_pct": result.total_return_pct,
        "sharpe_ratio": result.sharpe_ratio,
        "max_drawdown_pct": result.max_drawdown_pct,
        "win_rate": result.win_rate,
        "total_trades": result.total_trades,
        "trades": [
            {
                "entry_date": str(t.entry_date),
                "exit_date": str(t.exit_date),
                "pnl_pct": t.pnl_pct,
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
        console.print(
            "[yellow]⚠️  No reference hash set. Use --save-hash to store.[/yellow]"
        )

    # Save hash if requested
    if save_hash:
        from datetime import datetime

        meta.backtest_hash = actual_hash
        meta.updated_at = datetime.now()
        registry._save_metadata(meta)
        registry.reload()
        console.print(f"[green]✅ Saved hash: {actual_hash}[/green]")
