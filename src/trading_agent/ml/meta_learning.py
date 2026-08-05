"""
Meta-Learning CLI Integration

Provides CLI commands for meta-learning strategy adaptation (MAML/Reptile).
"""

import json
import logging
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from trading_agent.ml.meta import (
    MetaStrategyAdapter, MetaLearningConfig, 
    MAML, Reptile, MetaSGD, ANIL
)

logger = logging.getLogger(__name__)
console = Console()

app = typer.Typer(name="meta", help="Meta-learning for strategy adaptation")


@app.command()
def train(
    data_dir: str = typer.Argument(..., help="Directory with regime data (CSV/Parquet)"),
    algorithm: str = typer.Option("maml", help="Algorithm: maml, reptile, metasgd, anil"),
    steps: int = typer.Option(100, help="Meta-training steps"),
    meta_lr: float = typer.Option(0.01, help="Meta learning rate"),
    inner_lr: float = typer.Option(0.1, help="Inner learning rate"),
    inner_steps: int = typer.Option(5, help="Inner loop steps"),
    batch_size: int = typer.Option(4, help="Meta batch size"),
    output: Optional[Path] = typer.Option(None, "--output", "-o", help="Output file for meta-params"),
):
    """Meta-train on multiple market regimes."""
    
    import pandas as pd
    import numpy as np
    from pathlib import Path
    
    data_path = Path(data_dir)
    if not data_path.exists():
        console.print(f"[red]Data directory not found: {data_dir}[/red]")
        raise typer.Exit(1)
    
    # Load regime data
    console.print(f"[bold]Loading regime data from {data_dir}...[/bold]")
    
    market_data = {}
    for file in data_path.glob("*.csv"):
        regime_name = file.stem
        try:
            df = pd.read_csv(file)
            # Expect columns: timestamp, open, high, low, close, volume
            if 'close' in df.columns:
                market_data[regime_name] = df['close'].values.astype(np.float32)
                console.print(f"  Loaded {regime_name}: {len(market_data[regime_name])} points")
        except Exception as e:
            console.print(f"[yellow]Failed to load {file}: {e}[/yellow]")
    
    for file in data_path.glob("*.parquet"):
        regime_name = file.stem
        try:
            df = pd.read_parquet(file)
            if 'close' in df.columns:
                market_data[regime_name] = df['close'].values.astype(np.float32)
                console.print(f"  Loaded {regime_name}: {len(market_data[regime_name])} points")
        except Exception as e:
            console.print(f"[yellow]Failed to load {file}: {e}[/yellow]")
    
    if not market_data:
        console.print("[red]No valid regime data found[/red]")
        raise typer.Exit(1)
    
    # Configure meta-learning
    config = MetaLearningConfig(
        meta_lr=meta_lr,
        inner_lr=inner_lr,
        inner_steps=inner_steps,
        meta_batch_size=batch_size,
        first_order=True,
    )
    
    # Create adapter
    adapter = MetaStrategyAdapter(algorithm=algorithm, config=config)
    
    # Meta-train
    console.print(f"[bold]Meta-training with {algorithm.upper()} for {steps} steps...[/bold]")
    console.print(f"  Regimes: {list(market_data.keys())}")
    
    meta_params = adapter.train(market_data, steps=steps)
    
    # Display results
    console.print("\n[bold green]Meta-training complete![/bold green]")
    
    table = Table("Parameter", "Meta-Learned Value")
    for param, value in meta_params.items():
        table.add_row(param, f"{value:.4f}")
    console.print(table)
    
    # Save if requested
    if output:
        output_data = {
            "algorithm": algorithm,
            "config": {
                "meta_lr": meta_lr,
                "inner_lr": inner_lr,
                "inner_steps": inner_steps,
                "meta_batch_size": batch_size,
            },
            "meta_params": meta_params,
            "regimes": list(market_data.keys()),
        }
        output.write_text(json.dumps(output_data, indent=2))
        console.print(f"\n[green]Meta-params saved to {output}[/green]")


@app.command()
def adapt(
    model_path: Path = typer.Argument(..., help="Path to meta-trained model JSON"),
    regime_data: Path = typer.Argument(..., help="Path to new regime data (CSV/Parquet)"),
    n_samples: int = typer.Option(20, help="Number of samples for adaptation"),
    output: Optional[Path] = typer.Option(None, "--output", "-o", help="Output file for adapted params"),
):
    """Adapt meta-learned strategy to new market regime."""
    
    import pandas as pd
    import numpy as np
    
    # Load model
    if not model_path.exists():
        console.print(f"[red]Model not found: {model_path}[/red]")
        raise typer.Exit(1)
    
    model_data = json.loads(model_path.read_text())
    algorithm = model_data.get("algorithm", "maml")
    config_data = model_data.get("config", {})
    meta_params = model_data.get("meta_params", {})
    
    # Load new regime data
    if not regime_data.exists():
        console.print(f"[red]Regime data not found: {regime_data}[/red]")
        raise typer.Exit(1)
    
    if regime_data.suffix == ".csv":
        df = pd.read_csv(regime_data)
    elif regime_data.suffix == ".parquet":
        df = pd.read_parquet(regime_data)
    else:
        console.print("[red]Unsupported file format. Use CSV or Parquet[/red]")
        raise typer.Exit(1)
    
    if 'close' not in df.columns:
        console.print("[red]Data must contain 'close' column[/red]")
        raise typer.Exit(1)
    
    regime_prices = df['close'].values.astype(np.float32)
    
    # Create adapter and load meta-params
    config = MetaLearningConfig(**config_data)
    adapter = MetaStrategyAdapter(algorithm=algorithm, config=config)
    
    # Manually set meta-params
    if algorithm == "maml":
        adapter.learner = MAML(config, meta_params)
    elif algorithm == "reptile":
        adapter.learner = Reptile(config, meta_params)
    elif algorithm == "metasgd":
        adapter.learner = MetaSGD(config, meta_params)
    elif algorithm == "anil":
        adapter.learner = ANIL(config, meta_params, head_keys=["ema_fast", "ema_slow"])
    
    # Adapt to new regime
    console.print(f"[bold]Adapting to new regime ({len(regime_prices)} data points)...[/bold]")
    adapted_params = adapter.adapt_to_regime(regime_prices, n_samples=n_samples)
    
    # Display results
    console.print("\n[bold green]Adaptation complete![/bold green]")
    
    table = Table("Parameter", "Meta Value", "Adapted Value", "Change")
    for param in meta_params:
        meta_val = meta_params[param]
        adapted_val = adapted_params.get(param, meta_val)
        change = adapted_val - meta_val
        table.add_row(
            param,
            f"{meta_val:.4f}",
            f"{adapted_val:.4f}",
            f"{change:+.4f}",
        )
    console.print(table)
    
    # Save if requested
    if output:
        output_data = {
            "algorithm": algorithm,
            "meta_params": meta_params,
            "adapted_params": adapted_params,
            "regime_data_points": len(regime_prices),
            "n_samples": n_samples,
        }
        output.write_text(json.dumps(output_data, indent=2))
        console.print(f"\n[green]Adapted params saved to {output}[/green]")


@app.command()
def backtest(
    adapted_params: Path = typer.Argument(..., help="Path to adapted params JSON"),
    data: Path = typer.Argument(..., help="Path to backtest data (CSV/Parquet)"),
    initial_capital: float = typer.Option(10000, help="Initial capital"),
    commission: float = typer.Option(0.0004, help="Commission rate"),
    slippage: float = typer.Option(0.0005, help="Slippage rate"),
):
    """Run backtest with meta-learned parameters."""
    
    import pandas as pd
    import numpy as np
    
    # Load adapted params
    if not adapted_params.exists():
        console.print(f"[red]Adapted params not found: {adapted_params}[/red]")
        raise typer.Exit(1)
    
    params_data = json.loads(adapted_params.read_text())
    params = params_data.get("adapted_params", params_data.get("meta_params", {}))
    
    # Load backtest data
    if not data.exists():
        console.print(f"[red]Backtest data not found: {data}[/red]")
        raise typer.Exit(1)
    
    if data.suffix == ".csv":
        df = pd.read_csv(data)
    elif data.suffix == ".parquet":
        df = pd.read_parquet(data)
    else:
        console.print("[red]Unsupported file format[/red]")
        raise typer.Exit(1)
    
    required_cols = ['timestamp', 'open', 'high', 'low', 'close', 'volume']
    for col in required_cols:
        if col not in df.columns:
            console.print(f"[red]Missing required column: {col}[/red]")
            raise typer.Exit(1)
    
    # Run simple backtest using adapted parameters
    console.print(f"[bold]Running backtest with {len(df)} bars...[/bold]")
    
    # Extract params
    ema_fast = int(params.get("ema_fast", 12))
    ema_slow = int(params.get("ema_slow", 26))
    rsi_period = int(params.get("rsi_period", 14))
    bb_period = int(params.get("bb_period", 20))
    bb_std = float(params.get("bb_std", 2.0))
    
    # Simple MA crossover backtest
    close = df['close'].values
    high = df['high'].values
    low = df['low'].values
    volume = df['volume'].values
    
    # Calculate indicators
    ema_fast_vals = pd.Series(close).ewm(span=ema_fast, adjust=False).mean().values
    ema_slow_vals = pd.Series(close).ewm(span=ema_slow, adjust=False).mean().values
    
    # RSI
    delta = pd.Series(close).diff()
    gain = delta.where(delta > 0, 0).rolling(rsi_period).mean()
    loss = -delta.where(delta < 0, 0).rolling(rsi_period).mean()
    rs = gain / loss
    rsi = 100 - (100 / (1 + rs))
    
    # Bollinger Bands
    bb_mid = pd.Series(close).rolling(bb_period).mean()
    bb_std_vals = pd.Series(close).rolling(bb_period).std()
    bb_up = bb_mid + bb_std * bb_std_vals
    bb_low = bb_mid - bb_std * bb_std_vals
    
    # Generate signals
    position = 0
    entry_price = 0
    trades = []
    equity = initial_capital
    equity_curve = []
    
    for i in range(max(ema_slow, bb_period), len(close)):
        # Signal logic
        trend_up = ema_fast_vals[i] > ema_slow_vals[i]
        trend_down = ema_fast_vals[i] < ema_slow_vals[i]
        bb_pos = (close[i] - bb_low[i]) / (bb_up[i] - bb_low[i]) if bb_up[i] != bb_low[i] else 0.5
        rsi_val = rsi[i] if not np.isnan(rsi[i]) else 50
        
        signal = 0
        if position == 0:
            if trend_up and rsi_val < 60 and bb_pos < 0.6:
                signal = 1
            elif trend_down and rsi_val > 40 and bb_pos > 0.4:
                signal = -1
        elif position > 0:
            if trend_down or rsi_val > 75 or bb_pos > 0.9:
                signal = -1
        elif position < 0:
            if trend_up or rsi_val < 25 or bb_pos < 0.1:
                signal = 1
        
        if signal != 0 and signal != position:
            # Close existing
            if position != 0:
                pnl = (close[i] - entry_price) * position
                equity += pnl
                trades.append({
                    "entry_price": entry_price,
                    "exit_price": close[i],
                    "size": position,
                    "pnl": pnl,
                    "return_pct": pnl / entry_price * 100 if entry_price else 0,
                })
            
            # Open new
            if signal != 0:
                position = signal * (initial_capital * 0.1 / close[i])  # 10% position
                entry_price = close[i] * (1 + commission + slippage) if signal > 0 else close[i] * (1 - commission - slippage)
        
        equity_curve.append(equity)
    
    # Final close
    if position != 0:
        pnl = (close[-1] - entry_price) * position
        equity += pnl
        trades.append({
            "entry_price": entry_price,
            "exit_price": close[-1],
            "size": position,
            "pnl": pnl,
            "return_pct": pnl / entry_price * 100 if entry_price else 0,
        })
    
    # Calculate metrics
    total_return = (equity - initial_capital) / initial_capital * 100
    
    if trades:
        returns = [t["return_pct"] for t in trades]
        win_rate = sum(1 for r in returns if r > 0) / len(returns) * 100
        avg_win = np.mean([r for r in returns if r > 0]) if any(r > 0 for r in returns) else 0
        avg_loss = np.mean([r for r in returns if r < 0]) if any(r < 0 for r in returns) else 0
        profit_factor = abs(sum(r for r in returns if r > 0) / sum(r for r in returns if r < 0)) if any(r < 0 for r in returns) else float('inf')
    else:
        win_rate = 0
        avg_win = 0
        avg_loss = 0
        profit_factor = 0
    
    # Sharpe (simplified)
    equity_series = pd.Series(equity_curve)
    daily_returns = equity_series.pct_change().dropna()
    sharpe = daily_returns.mean() / daily_returns.std() * np.sqrt(252) if daily_returns.std() > 0 else 0
    
    # Max drawdown
    peak = equity_series.expanding().max()
    drawdown = (equity_series - peak) / peak * 100
    max_drawdown = drawdown.min()
    
    # Display results
    console.print("\n[bold green]Backtest Results[/bold green]")
    
    table = Table("Metric", "Value")
    table.add_row("Initial Capital", f"${initial_capital:,.2f}")
    table.add_row("Final Equity", f"${equity:,.2f}")
    table.add_row("Total Return", f"{total_return:.2f}%")
    table.add_row("Total Trades", str(len(trades)))
    table.add_row("Win Rate", f"{win_rate:.1f}%")
    table.add_row("Avg Win", f"{avg_win:.2f}%")
    table.add_row("Avg Loss", f"{avg_loss:.2f}%")
    table.add_row("Profit Factor", f"{profit_factor:.2f}")
    table.add_row("Sharpe Ratio", f"{sharpe:.2f}")
    table.add_row("Max Drawdown", f"{max_drawdown:.2f}%")
    console.print(table)
    
    # Show parameters used
    console.print("\n[bold]Parameters Used:[/bold]")
    param_table = Table("Parameter", "Value")
    for k, v in params.items():
        param_table.add_row(k, str(v))
    console.print(param_table)


@app.command()
def compare(
    meta_params: Path = typer.Argument(..., help="Path to meta params JSON"),
    adapted_params: Path = typer.Argument(..., help="Path to adapted params JSON"),
    data: Path = typer.Argument(..., help="Path to backtest data"),
):
    """Compare meta-learned vs adapted parameters."""
    
    console.print("[yellow]Comparison feature - run backtest for each and compare manually[/yellow]")
    console.print(f"  Meta params: {meta_params}")
    console.print(f"  Adapted params: {adapted_params}")
    console.print(f"  Data: {data}")


@app.command()
def regimes(
    data_dir: str = typer.Argument(..., help="Directory with regime data"),
):
    """Analyze available regimes in data directory."""
    
    import pandas as pd
    import numpy as np
    from pathlib import Path
    
    data_path = Path(data_dir)
    if not data_path.exists():
        console.print(f"[red]Directory not found: {data_dir}[/red]")
        raise typer.Exit(1)
    
    console.print(f"[bold]Analyzing regimes in {data_dir}[/bold]\n")
    
    table = Table("Regime", "File", "Points", "Date Range", "Volatility", "Trend")
    
    for file in sorted(data_path.glob("*.csv")) + sorted(data_path.glob("*.parquet")):
        try:
            if file.suffix == ".csv":
                df = pd.read_csv(file)
            else:
                df = pd.read_parquet(file)
            
            if 'close' not in df.columns:
                continue
            
            close = df['close'].values
            returns = pd.Series(close).pct_change().dropna()
            
            volatility = returns.std() * np.sqrt(252) * 100  # Annualized %
            trend = (close[-1] - close[0]) / close[0] * 100  # Total return %
            
            date_start = str(df['timestamp'].iloc[0]) if 'timestamp' in df.columns else "N/A"
            date_end = str(df['timestamp'].iloc[-1]) if 'timestamp' in df.columns else "N/A"
            
            table.add_row(
                file.stem,
                file.name,
                str(len(close)),
                f"{date_start} to {date_end}",
                f"{volatility:.1f}%",
                f"{trend:+.1f}%",
            )
        except Exception as e:
            console.print(f"[yellow]Failed to analyze {file}: {e}[/yellow]")
    
    console.print(table)


if __name__ == "__main__":
    app()