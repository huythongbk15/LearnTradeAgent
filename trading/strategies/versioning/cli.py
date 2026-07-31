"""CLI commands for strategy versioning."""

import json
import logging
from pathlib import Path
from typing import Optional

import pandas as pd
import typer
from rich.console import Console
from rich.table import Table
from rich.syntax import Syntax

from trading.strategies.versioning.registry import StrategyRegistry, StrategyMetadata, AssetClass, RiskProfile
from trading.strategies.versioning.git_store import GitVersionStore
from trading.strategies.versioning.abi import StrategyABI, ABIVerifier
from trading.strategies.plugins import BaseStrategy as Strategy

logger = logging.getLogger(__name__)
console = Console()

app = typer.Typer(name="strategy", help="Strategy versioning and management")

# Global instances
_registry: Optional[StrategyRegistry] = None
_git_store: Optional[GitVersionStore] = None


def get_registry() -> StrategyRegistry:
    global _registry
    if _registry is None:
        _registry = StrategyRegistry()
    return _registry


def get_git_store() -> GitVersionStore:
    global _git_store
    if _git_store is None:
        _git_store = GitVersionStore()
    return _git_store


@app.command()
def register(
    name: str = typer.Argument(..., help="Strategy name"),
    version: str = typer.Argument(..., help="Version (semver)"),
    file: Path = typer.Argument(..., help="Path to strategy Python file"),
    author: str = typer.Option("Trading System", help="Author name"),
    description: str = typer.Option("", help="Strategy description"),
    asset_class: str = typer.Option("crypto", help="Asset class: crypto, forex, equities, futures, options"),
    risk_profile: str = typer.Option("moderate", help="Risk profile: conservative, moderate, aggressive"),
    timeframes: str = typer.Option("1h", help="Comma-separated timeframes"),
    symbols: str = typer.Option("", help="Comma-separated symbols"),
    tags: str = typer.Option("", help="Comma-separated tags"),
):
    """Register a new strategy version."""
    # Load strategy file
    source_code = file.read_text()
    
    # Load class to verify it's valid
    namespace = {}
    exec(source_code, namespace)
    
    strategy_class = None
    for obj in namespace.values():
        if isinstance(obj, type) and issubclass(obj, Strategy) and obj != Strategy:
            strategy_class = obj
            break
    
    if not strategy_class:
        console.print("[red]Error: No Strategy subclass found in file[/red]")
        raise typer.Exit(1)
    
    # Extract ABI
    abi = StrategyABI.from_strategy(strategy_class)
    
    # Create metadata
    metadata = StrategyMetadata(
        name=name,
        version=version,
        author=author,
        description=description,
        asset_class=AssetClass(asset_class),
        risk_profile=RiskProfile(risk_profile),
        timeframes=[tf.strip() for tf in timeframes.split(",")],
        symbols=[s.strip() for s in symbols.split(",") if s.strip()],
        params_schema={p.name: p.type for p in abi.params},
        tags=[t.strip() for t in tags.split(",") if t.strip()],
        dependencies=[],
    )
    
    # Register
    registry = get_registry()
    version_obj = registry.register(
        metadata=metadata,
        source_code=source_code,
        abi_hash=abi.hash,
    )
    
    # Save to git
    git_store = get_git_store()
    git_store.save_version(version_obj)
    
    console.print(f"[green]✓[/green] Registered {name} v{version} (hash: {version_obj.source_hash[:8]})")
    console.print(f"  ABI hash: {abi.hash}")
    console.print(f"  Params: {[p.name for p in abi.params]}")
    console.print(f"  Methods: {[m.name for m in abi.methods]}")


@app.command()
def list(
    name: Optional[str] = typer.Argument(None, help="Filter by strategy name"),
    active_only: bool = typer.Option(False, "--active", help="Show only active versions"),
):
    """List registered strategies."""
    registry = get_registry()
    
    if name:
        versions = registry.list_versions(name)
        if not versions:
            console.print(f"[yellow]No versions found for {name}[/yellow]")
            return
        
        table = Table(title=f"Versions for {name}")
        table.add_column("Version", style="cyan")
        table.add_column("Hash", style="dim")
        table.add_column("Active", justify="center")
        table.add_column("Deprecated", justify="center")
        table.add_column("Created", style="dim")
        table.add_column("Tags", style="green")
        
        for v in versions:
            active = "✓" if v.is_active else ""
            deprecated = "⚠" if v.is_deprecated else ""
            table.add_row(
                v.metadata.version,
                v.source_hash[:8],
                active,
                deprecated,
                v.metadata.created_at.strftime("%Y-%m-%d"),
                ", ".join(v.metadata.tags),
            )
        
        console.print(table)
    else:
        strategies = registry.list_strategies()
        if not strategies:
            console.print("[yellow]No strategies registered[/yellow]")
            return
        
        table = Table(title="Registered Strategies")
        table.add_column("Name", style="cyan")
        table.add_column("Active Version", style="green")
        table.add_column("Total Versions", justify="right")
        table.add_column("Asset Class", style="yellow")
        table.add_column("Risk Profile", style="red")
        
        for s in strategies:
            active = registry.get_active(s)
            active_ver = active.metadata.version if active else "none"
            versions = registry.list_versions(s)
            table.add_row(
                s,
                active_ver,
                str(len(versions)),
                versions[0].metadata.asset_class.value if versions else "",
                versions[0].metadata.risk_profile.value if versions else "",
            )
        
        console.print(table)


@app.command()
def activate(
    name: str = typer.Argument(..., help="Strategy name"),
    version: str = typer.Argument(..., help="Version to activate"),
):
    """Activate a strategy version."""
    registry = get_registry()
    if registry.activate(name, version):
        console.print(f"[green]✓[/green] Activated {name} v{version}")
    else:
        console.print(f"[red]Failed to activate {name} v{version}[/red]")
        raise typer.Exit(1)


@app.command()
def deprecate(
    name: str = typer.Argument(..., help="Strategy name"),
    version: str = typer.Argument(..., help="Version to deprecate"),
    reason: str = typer.Option("", help="Deprecation reason"),
):
    """Deprecate a strategy version."""
    registry = get_registry()
    if registry.deprecate(name, version):
        console.print(f"[yellow]⚠[/yellow] Deprecated {name} v{version}")
        if reason:
            console.print(f"  Reason: {reason}")
    else:
        console.print(f"[red]Failed to deprecate {name} v{version}[/red]")
        raise typer.Exit(1)


@app.command()
def show(
    name: str = typer.Argument(..., help="Strategy name"),
    version: Optional[str] = typer.Argument(None, help="Version (default: active)"),
):
    """Show strategy details."""
    registry = get_registry()
    
    if version:
        v = registry.get_version(name, version)
    else:
        v = registry.get_active(name)
    
    if not v:
        console.print(f"[red]Strategy not found: {name} {version or '(active)'}[/red]")
        raise typer.Exit(1)
    
    console.print(f"[bold cyan]{name} v{v.metadata.version}[/bold cyan]")
    console.print(f"  Hash: {v.source_hash[:16]}")
    console.print(f"  ABI Hash: {v.abi_hash}")
    console.print(f"  Author: {v.metadata.author}")
    console.print(f"  Description: {v.metadata.description}")
    console.print(f"  Asset Class: {v.metadata.asset_class.value}")
    console.print(f"  Risk Profile: {v.metadata.risk_profile.value}")
    console.print(f"  Timeframes: {', '.join(v.metadata.timeframes)}")
    console.print(f"  Symbols: {', '.join(v.metadata.symbols) or 'Any'}")
    console.print(f"  Tags: {', '.join(v.metadata.tags) or 'None'}")
    console.print(f"  Active: {'Yes' if v.is_active else 'No'}")
    console.print(f"  Deprecated: {'Yes' if v.is_deprecated else 'No'}")
    console.print(f"  Created: {v.metadata.created_at}")
    console.print(f"  Updated: {v.metadata.updated_at}")
    
    if v.deployed_at:
        console.print(f"  Deployed: {v.deployed_at}")
    if v.retired_at:
        console.print(f"  Retired: {v.retired_at}")
    
    # Show params
    if v.metadata.params_schema:
        console.print("\n[bold]Parameters:[/bold]")
        for param, ptype in v.metadata.params_schema.items():
            console.print(f"  {param}: {ptype}")
    
    # Show backtest metrics
    if v.metadata.backtest_metrics:
        console.print("\n[bold]Backtest Metrics:[/bold]")
        for k, val in v.metadata.backtest_metrics.items():
            console.print(f"  {k}: {val}")


@app.command()
def diff(
    name: str = typer.Argument(..., help="Strategy name"),
    version1: str = typer.Argument(..., help="First version"),
    version2: str = typer.Argument(..., help="Second version"),
    abi_only: bool = typer.Option(False, "--abi", help="Show only ABI diff"),
):
    """Diff two strategy versions."""
    registry = get_registry()
    git_store = get_git_store()
    
    v1 = registry.get_version(name, version1)
    v2 = registry.get_version(name, version2)
    
    if not v1 or not v2:
        console.print("[red]One or both versions not found[/red]")
        raise typer.Exit(1)
    
    if abi_only:
        # Diff ABIs
        # We'd need to load the actual ABIs from git store
        abi_file1 = git_store.repo_path / name / f"{name}_v{version1}_abi.json"
        abi_file2 = git_store.repo_path / name / f"{name}_v{version2}_abi.json"
        
        if abi_file1.exists() and abi_file2.exists():
            abi1 = StrategyABI(**json.loads(abi_file1.read_text()))
            abi2 = StrategyABI(**json.loads(abi_file2.read_text()))
            
            result = ABIVerifier.verify(abi1, abi2)
            
            if result["compatible"]:
                console.print("[green]✓ ABI compatible[/green]")
            else:
                console.print("[red]✗ ABI breaking changes detected[/red]")
            
            if result["breaking_changes"]:
                console.print("\n[bold red]Breaking Changes:[/bold red]")
                for change in result["breaking_changes"]:
                    console.print(f"  - {change}")
            
            if result["warnings"]:
                console.print("\n[bold yellow]Warnings:[/bold yellow]")
                for w in result["warnings"]:
                    console.print(f"  - {w}")
            
            if result["added_params"]:
                console.print("\n[bold green]Added Parameters:[/bold green]")
                for p in result["added_params"]:
                    console.print(f"  + {p}")
            
            if result["removed_params"]:
                console.print("\n[bold red]Removed Parameters:[/bold red]")
                for p in result["removed_params"]:
                    console.print(f"  - {p}")
        else:
            console.print("[yellow]ABI files not found in git store[/yellow]")
    else:
        # Diff source code via git
        try:
            diff = git_store.diff_versions(name, version1, version2)
            if diff:
                syntax = Syntax(diff, "diff", theme="monokai", line_numbers=True)
                console.print(syntax)
            else:
                console.print("[green]No differences[/green]")
        except Exception as e:
            console.print(f"[red]Diff failed: {e}[/red]")


@app.command()
def rollback(
    name: str = typer.Argument(..., help="Strategy name"),
    version: str = typer.Argument(..., help="Version to rollback to"),
):
    """Rollback strategy to a previous version."""
    git_store = get_git_store()
    
    try:
        commit = git_store.rollback(name, version)
        console.print(f"[green]✓[/green] Rolled back {name} to v{version} (commit: {commit[:8]})")
    except Exception as e:
        console.print(f"[red]Rollback failed: {e}[/red]")
        raise typer.Exit(1)


@app.command()
def history(
    name: str = typer.Argument(..., help="Strategy name"),
    limit: int = typer.Option(20, help="Number of commits to show"),
):
    """Show git history for a strategy."""
    git_store = get_git_store()
    history = git_store.get_history(name, limit)
    
    if not history:
        console.print(f"[yellow]No history for {name}[/yellow]")
        return
    
    table = Table(title=f"Git History for {name}")
    table.add_column("Commit", style="dim")
    table.add_column("Date", style="dim")
    table.add_column("Author", style="cyan")
    table.add_column("Message")
    table.add_column("Files", style="green")
    
    for commit in history:
        files = ", ".join(commit.files[:3])
        if len(commit.files) > 3:
            files += f" ... (+{len(commit.files)-3} more)"
        
        table.add_row(
            commit.hash[:8],
            commit.timestamp.strftime("%Y-%m-%d %H:%M"),
            commit.author,
            commit.message[:60],
            files,
        )
    
    console.print(table)


@app.command()
def tag(
    name: str = typer.Argument(..., help="Strategy name"),
    version: str = typer.Argument(..., help="Version to tag"),
    tag: Optional[str] = typer.Argument(None, help="Tag name (default: name/vversion)"),
):
    """Tag a version as release."""
    git_store = get_git_store()
    
    try:
        git_store.tag_release(name, version, tag)
        console.print(f"[green]✓[/green] Tagged {name} v{version}")
    except Exception as e:
        console.print(f"[red]Tag failed: {e}[/red]")
        raise typer.Exit(1)


@app.command()
def tags():
    """List all release tags."""
    git_store = get_git_store()
    tag_list = git_store.list_tags()
    
    if not tag_list:
        console.print("[yellow]No tags[/yellow]")
        return
    
    table = Table(title="Release Tags")
    table.add_column("Tag", style="cyan")
    
    for t in tag_list:
        table.add_row(t)
    
    console.print(table)


@app.command()
def verify(
    name: str = typer.Argument(..., help="Strategy name"),
    version: str = typer.Argument(..., help="Version to verify"),
    deployment_hash: str = typer.Option(..., help="Deployment hash to verify against"),
):
    """Verify deployed code matches registered version."""
    registry = get_registry()
    
    if registry.verify_deployment(name, version, deployment_hash):
        console.print(f"[green]✓[/green] Deployment verified for {name} v{version}")
    else:
        console.print(f"[red]✗[/red] Deployment mismatch for {name} v{version}")
        raise typer.Exit(1)


@app.command()
def abi(
    file: Path = typer.Argument(..., help="Path to strategy Python file"),
    output: Optional[Path] = typer.Option(None, "--output", "-o", help="Output JSON file"),
):
    """Extract and display ABI from strategy file."""
    source_code = file.read_text()
    
    namespace = {}
    exec(source_code, namespace)
    
    strategy_class = None
    for obj in namespace.values():
        if isinstance(obj, type) and issubclass(obj, Strategy) and obj != Strategy:
            strategy_class = obj
            break
    
    if not strategy_class:
        console.print("[red]No Strategy subclass found[/red]")
        raise typer.Exit(1)
    
    abi = StrategyABI.from_strategy(strategy_class)
    
    # Display
    console.print(f"[bold cyan]{abi.name} v{abi.version}[/bold cyan]")
    console.print(f"  ABI Hash: {abi.hash}")
    
    if abi.params:
        console.print("\n[bold]Parameters:[/bold]")
        for p in abi.params:
            req = " (required)" if p.required else f" (default: {p.default})"
            console.print(f"  {p.name}: {p.type}{req}")
    
    if abi.methods:
        console.print("\n[bold]Methods:[/bold]")
        for m in abi.methods:
            params = ", ".join(f"{p.name}: {p.type}" for p in m.params)
            async_str = "async " if m.is_async else ""
            console.print(f"  {async_str}{m.name}({params}) -> {m.return_type}")
    
    console.print(f"\n[bold]Signals:[/bold] {', '.join(abi.signals)}")
    console.print(f"[bold]Required Data:[/bold] {', '.join(abi.required_data)}")
    
    if output:
        output.write_text(json.dumps(abi.__dict__, default=str, indent=2))
        console.print(f"\n[green]ABI saved to {output}[/green]")


@app.command()
def load(
    name: str = typer.Argument(..., help="Strategy name"),
    version: Optional[str] = typer.Argument(None, help="Version (default: active)"),
    params: str = typer.Option("{}", "--params", "-p", help="JSON parameters"),
):
    """Load and instantiate a strategy from registry."""
    from trading.strategies.versioning.registry import StrategyLoader
    
    registry = get_registry()
    loader = StrategyLoader(registry)
    
    try:
        param_dict = json.loads(params)
        strategy = loader.load_with_params(name, param_dict, version)
        console.print(f"[green]✓[/green] Loaded {name} {version or '(active)'}")
        console.print(f"  Instance: {strategy}")
        console.print(f"  Params: {strategy.params}")
    except Exception as e:
        console.print(f"[red]Load failed: {e}[/red]")
        raise typer.Exit(1)


@app.command()
def install(
    source: str = typer.Argument(..., help="Source: local path, git URL, or registry name"),
    name: str = typer.Option(None, "--name", "-n", help="Strategy name (auto-detect if not provided)"),
    version: str = typer.Option(None, "--version", "-v", help="Version to install (latest if not specified)"),
    registry_url: str = typer.Option(None, "--registry", "-r", help="Custom registry URL"),
    force: bool = typer.Option(False, "--force", "-f", help="Overwrite existing version"),
):
    """Install strategy from local file, git repo, or registry."""
    import subprocess
    import tempfile
    
    registry = get_registry()
    git_store = get_git_store()
    
    # Determine source type
    if source.startswith("http://") or source.startswith("https://") or source.startswith("git@"):
        # Git repository
        console.print(f"[bold]Installing from git: {source}[/bold]")
        
        with tempfile.TemporaryDirectory() as tmpdir:
            # Clone repo
            result = subprocess.run(
                ["git", "clone", "--depth", "1", source, tmpdir],
                capture_output=True, text=True
            )
            if result.returncode != 0:
                console.print(f"[red]Git clone failed: {result.stderr}[/red]")
                raise typer.Exit(1)
            
            # Find strategy files
            strategy_files = list(Path(tmpdir).rglob("*strategy*.py"))
            if not strategy_files:
                strategy_files = list(Path(tmpdir).rglob("*.py"))
            
            for sf in strategy_files:
                if sf.name.startswith("__"):
                    continue
                try:
                    source_code = sf.read_text()
                    namespace = {}
                    exec(source_code, namespace)
                    
                    for obj in namespace.values():
                        if isinstance(obj, type) and issubclass(obj, Strategy) and obj != Strategy:
                            # Found strategy class
                            meta = obj.get_metadata()
                            strategy_name = name or meta.name
                            strategy_version = version or meta.version
                            
                            # Check if exists
                            existing = registry.get_metadata(strategy_name, strategy_version)
                            if existing and not force:
                                console.print(f"[yellow]Strategy {strategy_name}@{strategy_version} exists. Use --force to overwrite.[/yellow]")
                                continue
                            
                            # Register
                            abi = StrategyABI.from_strategy(obj)
                            metadata = StrategyMetadata(
                                name=strategy_name,
                                version=strategy_version,
                                author=meta.author,
                                description=meta.description,
                                asset_class=meta.asset_class,
                                risk_profile=meta.risk_profile,
                                timeframes=meta.timeframes,
                                symbols=meta.symbols,
                                params_schema={p.name: p.type for p in abi.params},
                                tags=meta.tags,
                                dependencies=[],
                            )
                            
                            version_obj = registry.register(
                                metadata=metadata,
                                source_code=source_code,
                                abi_hash=abi.hash,
                            )
                            git_store.save_version(version_obj)
                            
                            console.print(f"[green]✓[/green] Installed {strategy_name} v{strategy_version} from git")
                except Exception as e:
                    logger.debug(f"Failed to load {sf}: {e}")
    
    elif Path(source).exists():
        # Local file or directory
        path = Path(source)
        if path.is_file():
            files = [path]
        else:
            files = list(path.rglob("*.py"))
        
        for f in files:
            if f.name.startswith("__"):
                continue
            try:
                source_code = f.read_text()
                namespace = {}
                exec(source_code, namespace)
                
                for obj in namespace.values():
                    if isinstance(obj, type) and issubclass(obj, Strategy) and obj != Strategy:
                        meta = obj.get_metadata()
                        strategy_name = name or meta.name
                        strategy_version = version or meta.version
                        
                        existing = registry.get_metadata(strategy_name, strategy_version)
                        if existing and not force:
                            console.print(f"[yellow]Strategy {strategy_name}@{strategy_version} exists. Use --force to overwrite.[/yellow]")
                            continue
                        
                        abi = StrategyABI.from_strategy(obj)
                        metadata = StrategyMetadata(
                            name=strategy_name,
                            version=strategy_version,
                            author=meta.author,
                            description=meta.description,
                            asset_class=meta.asset_class,
                            risk_profile=meta.risk_profile,
                            timeframes=meta.timeframes,
                            symbols=meta.symbols,
                            params_schema={p.name: p.type for p in abi.params},
                            tags=meta.tags,
                            dependencies=[],
                        )
                        
                        version_obj = registry.register(
                            metadata=metadata,
                            source_code=source_code,
                            abi_hash=abi.hash,
                        )
                        git_store.save_version(version_obj)
                        
                        console.print(f"[green]✓[/green] Installed {strategy_name} v{strategy_version} from {f}")
            except Exception as e:
                logger.debug(f"Failed to load {f}: {e}")
    
    else:
        # Assume registry name (e.g., "ma_crossover@1.0.0")
        if "@" in source:
            reg_name, reg_version = source.split("@", 1)
        else:
            reg_name = source
            reg_version = version or "latest"
        
        console.print(f"[yellow]Registry install not yet implemented for {reg_name}@{reg_version}[/yellow]")
        console.print("Use local file or git URL for now.")


@app.command()
def run(
    name: str = typer.Argument(..., help="Strategy name"),
    symbol: str = typer.Argument(..., help="Symbol to trade (e.g., BTC/USDT)"),
    timeframe: str = typer.Option("1h", "--timeframe", "-t", help="Timeframe"),
    version: str = typer.Option(None, "--version", "-v", help="Strategy version (default: active)"),
    param = typer.Option([], "--param", "-p", help="Parameters: key=value"),
    capital: float = typer.Option(10000, "--capital", "-c", help="Initial capital"),
    days: int = typer.Option(365, "--days", "-d", help="Days of historical data"),
    exchange: str = typer.Option("binance", "--exchange", "-e", help="Exchange"),
    live: bool = typer.Option(False, "--live", help="Run live (paper trading)"),
):
    """Run strategy on historical data or live (paper)."""
    from trading.strategies.versioning.registry import StrategyLoader
    from trading.exchanges.models import Symbol as ExSymbol, AssetClass, MarketType, Bar
    from trading_agent.data.storage import load_ohlcv
    from decimal import Decimal
    from datetime import datetime
    
    # Parse params
    param_dict = {}
    for p in param:
        if "=" not in p:
            console.print(f"[red]Invalid param: {p}[/red]")
            raise typer.Exit(1)
        k, v = p.split("=", 1)
        try:
            param_dict[k] = float(v) if "." in v else int(v)
        except ValueError:
            param_dict[k] = v
    
    # Load strategy
    registry = get_registry()
    loader = StrategyLoader(registry)
    
    try:
        strategy = loader.load_with_params(name, param_dict, version)
    except Exception as e:
        console.print(f"[red]Failed to load strategy: {e}[/red]")
        raise typer.Exit(1)
    
    meta = registry.get_metadata(name, version)
    console.print(f"[bold]Running {name} v{meta.version if meta else version} on {symbol}[/bold]")
    console.print(f"  Timeframe: {timeframe}, Capital: ${capital:,.0f}, Days: {days}")
    console.print(f"  Params: {param_dict}")
    
    # Load data
    try:
        df = load_ohlcv(exchange, symbol, timeframe)
    except FileNotFoundError:
        console.print(f"[red]Data not found for {symbol} on {exchange}[/red]")
        raise typer.Exit(1)
    
    if df.is_empty():
        console.print("[red]No data[/red]")
        raise typer.Exit(1)
    
    # Filter by days
    cutoff = datetime.utcnow() - pd.Timedelta(days=days)
    df = df.filter(df['timestamp'] >= cutoff)
    
    # Run backtest
    base, quote = symbol.split('/') if '/' in symbol else (symbol, 'USDT')
    sym_obj = ExSymbol(base, quote, AssetClass.CRYPTO, MarketType.SPOT, exchange)
    
    signals = []
    equity = capital
    position = 0
    entry_price = 0
    trades = []
    
    from trading.strategies.plugins import StrategyContext
    
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
            position=None,  # Simplified
            portfolio_value=Decimal(str(equity)),
            available_balance=Decimal(str(equity)),
            current_time=row['timestamp'],
        )
        
        bar_signals = strategy.on_bar(context)
        
        for sig in bar_signals:
            if sig.side.name == "BUY" and position <= 0:
                # Enter long
                size = capital * float(strategy.config.get('position_size', 0.1)) / float(bar.close)
                position = size
                entry_price = float(bar.close)
                equity -= size * float(bar.close)
                trades.append({
                    "type": "BUY",
                    "price": entry_price,
                    "size": size,
                    "time": row['timestamp'],
                })
                signals.append({"time": row['timestamp'], "side": "BUY", "price": entry_price})
            elif sig.side.name == "SELL" and position > 0:
                # Exit long
                pnl = (float(bar.close) - entry_price) * position
                equity += position * float(bar.close)
                trades.append({
                    "type": "SELL",
                    "price": float(bar.close),
                    "size": position,
                    "pnl": pnl,
                    "time": row['timestamp'],
                })
                signals.append({"time": row['timestamp'], "side": "SELL", "price": float(bar.close), "pnl": pnl})
                position = 0
    
    # Final equity
    if position > 0:
        equity += position * float(df['close'][-1])
    
    total_return = (equity - capital) / capital * 100
    
    # Results
    console.print("\n[bold]Results:[/bold]")
    console.print(f"  Final Equity: ${equity:,.2f}")
    console.print(f"  Total Return: {total_return:+.2f}%")
    console.print(f"  Total Trades: {len([t for t in trades if 'pnl' in t])}")
    
    winning = [t for t in trades if t.get('pnl', 0) > 0]
    losing = [t for t in trades if t.get('pnl', 0) < 0]
    if winning or losing:
        win_rate = len(winning) / (len(winning) + len(losing)) * 100
        avg_win = sum(t['pnl'] for t in winning) / len(winning) if winning else 0
        avg_loss = sum(t['pnl'] for t in losing) / len(losing) if losing else 0
        console.print(f"  Win Rate: {win_rate:.1f}%")
        console.print(f"  Avg Win: ${avg_win:.2f}")
        console.print(f"  Avg Loss: ${avg_loss:.2f}")
        if avg_loss != 0:
            console.print(f"  Profit Factor: {abs(avg_win / avg_loss):.2f}")


@app.command()
def backtest(
    name: str = typer.Argument(..., help="Strategy name"),
    symbol: str = typer.Argument(..., help="Symbol to backtest"),
    timeframe: str = typer.Option("1h", "--timeframe", "-t", help="Timeframe"),
    version: str = typer.Option(None, "--version", "-v", help="Strategy version"),
    param = typer.Option([], "--param", "-p", help="Parameters: key=value"),
    capital: float = typer.Option(100000, "--capital", "-c", help="Initial capital"),
    days: int = typer.Option(730, "--days", "-d", help="Days of historical data"),
    exchange: str = typer.Option("binance", "--exchange", "-e", help="Exchange"),
    commission: float = typer.Option(0.0004, "--commission", help="Commission rate"),
    slippage: float = typer.Option(0.0005, "--slippage", help="Slippage rate"),
    save_hash: bool = typer.Option(False, "--save-hash", help="Save backtest hash as reference"),
    output: str = typer.Option(None, "--output", "-o", help="Output results file (JSON)"),
):
    """Run comprehensive backtest with metrics."""
    import hashlib
    from trading_agent.backtest.engine import run_backtest
    from trading.strategies.plugins import get_registry
    
    # Parse params
    param_dict = {}
    for p in param:
        if "=" not in p:
            console.print(f"[red]Invalid param: {p}[/red]")
            raise typer.Exit(1)
        k, v = p.split("=", 1)
        try:
            param_dict[k] = float(v) if "." in v else int(v)
        except ValueError:
            param_dict[k] = v
    
    # Run backtest
    console.print(f"[bold]Backtesting {name} v{version or 'latest'} on {symbol}[/bold]")
    console.print(f"  Timeframe: {timeframe}, Capital: ${capital:,.0f}, Days: {days}")
    console.print(f"  Commission: {commission*100:.3f}%, Slippage: {slippage*100:.3f}%")
    
    try:
        result = run_backtest(
            strategy_name=name,
            symbol=symbol,
            timeframe=timeframe,
            params=param_dict,
            initial_capital=capital,
            commission=commission,
            slippage=slippage,
        )
    except Exception as e:
        console.print(f"[red]Backtest failed: {e}[/red]")
        raise typer.Exit(1)
    
    # Display metrics
    console.print("\n[bold]Backtest Results:[/bold]")
    metrics_table = Table("Metric", "Value")
    metrics_table.add_row("Total Return", f"{result.total_return_pct:+.2f}%")
    metrics_table.add_row("Sharpe Ratio", f"{result.sharpe_ratio:.2f}")
    metrics_table.add_row("Max Drawdown", f"{result.max_drawdown_pct:.2f}%")
    metrics_table.add_row("Win Rate", f"{result.win_rate:.1f}%")
    metrics_table.add_row("Total Trades", str(result.total_trades))
    metrics_table.add_row("Avg Trade", f"{result.avg_trade_pct:+.2f}%")
    metrics_table.add_row("Best Trade", f"{result.best_trade_pct:+.2f}%")
    metrics_table.add_row("Worst Trade", f"{result.worst_trade_pct:+.2f}%")
    metrics_table.add_row("Profit Factor", f"{result.profit_factor:.2f}")
    console.print(metrics_table)
    
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
    
    # Check against registry
    registry = get_registry()
    meta = registry.get_metadata(name, version)
    
    console.print(f"\nBacktest Hash: {actual_hash}")
    if meta and meta.backtest_hash:
        console.print(f"Registry Hash: {meta.backtest_hash}")
        if actual_hash == meta.backtest_hash:
            console.print("[green]✓ Hash verified - backtest reproducible[/green]")
        else:
            console.print("[red]✗ Hash mismatch - backtest not reproducible![/red]")
    else:
        console.print("[yellow]No reference hash in registry[/yellow]")
    
    # Save hash if requested
    if save_hash and meta:
        from datetime import datetime
        meta.backtest_hash = actual_hash
        meta.updated_at = datetime.now()
        registry._save_metadata(meta)
        console.print(f"[green]✓ Saved hash: {actual_hash}[/green]")
    
    # Save results
    if output:
        output_data = {
            "strategy": name,
            "version": version or meta.version if meta else "unknown",
            "symbol": symbol,
            "timeframe": timeframe,
            "params": param_dict,
            "capital": capital,
            "commission": commission,
            "slippage": slippage,
            "hash": actual_hash,
            "metrics": {
                "total_return_pct": result.total_return_pct,
                "sharpe_ratio": result.sharpe_ratio,
                "max_drawdown_pct": result.max_drawdown_pct,
                "win_rate": result.win_rate,
                "total_trades": result.total_trades,
                "profit_factor": result.profit_factor,
            },
            "trades": [
                {
                    "entry_date": str(t.entry_date),
                    "exit_date": str(t.exit_date),
                    "entry_price": float(t.entry_price),
                    "exit_price": float(t.exit_price),
                    "size": float(t.size),
                    "pnl": float(t.pnl),
                    "pnl_pct": t.pnl_pct,
                }
                for t in result.trades
            ],
        }
        Path(output).write_text(json.dumps(output_data, indent=2, default=str))
        console.print(f"[green]✓ Results saved to {output}[/green]")


@app.command()
def validate(
    name: str = typer.Argument(..., help="Strategy name"),
    symbol: str = typer.Argument(..., help="Symbol to validate on"),
    timeframe: str = typer.Option("1h", "--timeframe", "-t", help="Timeframe"),
    version: str = typer.Option(None, "--version", "-v", help="Strategy version"),
    param = typer.Option([], "--param", "-p", help="Parameters: key=value"),
    min_sharpe: float = typer.Option(1.0, "--min-sharpe", help="Minimum Sharpe ratio"),
    max_dd: float = typer.Option(20, "--max-dd", help="Maximum drawdown %"),
    min_trades: int = typer.Option(10, "--min-trades", help="Minimum trades"),
    save_hash: bool = typer.Option(False, "--save-hash", help="Save backtest hash"),
):
    """Validate strategy meets minimum criteria."""
    import hashlib
    from trading_agent.backtest.engine import run_backtest
    from trading.strategies.plugins import get_registry
    
    # Parse params
    param_dict = {}
    for p in param:
        if "=" not in p:
            console.print(f"[red]Invalid param: {p}[/red]")
            raise typer.Exit(1)
        k, v = p.split("=", 1)
        try:
            param_dict[k] = float(v) if "." in v else int(v)
        except ValueError:
            param_dict[k] = v
    
    console.print(f"[bold]Validating {name} v{version or 'latest'} on {symbol}[/bold]")
    console.print(f"  Criteria: Sharpe > {min_sharpe}, MaxDD < {max_dd}%, Trades > {min_trades}")
    
    try:
        result = run_backtest(
            strategy_name=name,
            symbol=symbol,
            timeframe=timeframe,
            params=param_dict,
        )
    except Exception as e:
        console.print(f"[red]Backtest failed: {e}[/red]")
        raise typer.Exit(1)
    
    # Check criteria
    checks = [
        ("Sharpe Ratio", result.sharpe_ratio, min_sharpe, ">"),
        ("Max Drawdown", result.max_drawdown_pct, max_dd, "<"),
        ("Total Trades", result.total_trades, min_trades, ">"),
    ]
    
    all_passed = True
    table = Table("Metric", "Value", "Threshold", "Status")
    for metric_name, value, threshold, op in checks:
        if op == ">":
            passed = value > threshold
        else:
            passed = value < threshold
        
        status = "[green]✓ PASS[/green]" if passed else "[red]✗ FAIL[/red]"
        if not passed:
            all_passed = False
        table.add_row(metric_name, f"{value:.2f}", f"{op} {threshold}", status)
    
    console.print(table)
    
    # Hash verification
    result_dict = {
        'total_return_pct': result.total_return_pct,
        'sharpe_ratio': result.sharpe_ratio,
        'max_drawdown_pct': result.max_drawdown_pct,
        'win_rate': result.win_rate,
        'total_trades': result.total_trades,
        'trades': [
            {'entry_date': str(t.entry_date), 'exit_date': str(t.exit_date), 'pnl_pct': t.pnl_pct}
            for t in result.trades
        ],
    }
    result_str = json.dumps(result_dict, sort_keys=True, default=str)
    actual_hash = hashlib.sha256(result_str.encode()).hexdigest()[:16]
    
    registry = get_registry()
    meta = registry.get_metadata(name, version)
    
    console.print(f"\nBacktest Hash: {actual_hash}")
    if meta and meta.backtest_hash:
        if actual_hash == meta.backtest_hash:
            console.print("[green]✓ Hash verified - reproducible[/green]")
        else:
            console.print("[red]✗ Hash mismatch![/red]")
            all_passed = False
    else:
        console.print("[yellow]No reference hash[/yellow]")
    
    if save_hash and meta:
        from datetime import datetime
        meta.backtest_hash = actual_hash
        meta.updated_at = datetime.now()
        registry._save_metadata(meta)
        console.print(f"[green]✓ Saved hash: {actual_hash}[/green]")
    
    if all_passed:
        console.print("\n[bold green]✅ VALIDATION PASSED[/bold green]")
    else:
        console.print("\n[bold red]❌ VALIDATION FAILED[/bold red]")
        raise typer.Exit(1)


if __name__ == "__main__":
    app()