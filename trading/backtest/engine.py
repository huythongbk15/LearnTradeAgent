"""
Backtest Engine - Core backtesting logic.
"""

import hashlib
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any

import numpy as np
import pandas as pd
import polars as pl

from trading.strategies.plugins import get_registry
from trading.exchanges.models import Symbol as ExSymbol, AssetClass, MarketType, Bar

logger = logging.getLogger(__name__)


@dataclass
class Trade:
    """Single trade record."""
    entry_date: datetime
    exit_date: datetime
    entry_price: Decimal
    exit_price: Decimal
    size: Decimal
    pnl: Decimal
    pnl_pct: float
    side: str  # 'long' or 'short'
    strategy: str
    symbol: str


@dataclass
class BacktestResult:
    """Complete backtest result."""
    strategy: str
    symbol: str
    timeframe: str
    params: dict
    initial_capital: Decimal
    final_equity: Decimal
    total_return_pct: float
    sharpe_ratio: float
    max_drawdown_pct: float
    win_rate: float
    total_trades: int
    avg_trade_pct: float
    best_trade_pct: float
    worst_trade_pct: float
    profit_factor: float
    trades: list[Trade]
    equity_curve: list[tuple[datetime, float]] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.utcnow)


def compute_metrics(equity_curve: list[float], trades: list[Trade]) -> dict:
    """Compute backtest metrics from equity curve and trades."""
    if not equity_curve:
        return {
            'total_return_pct': 0.0,
            'sharpe_ratio': 0.0,
            'max_drawdown_pct': 0.0,
            'win_rate': 0.0,
            'total_trades': 0,
            'avg_trade_pct': 0.0,
            'best_trade_pct': 0.0,
            'worst_trade_pct': 0.0,
            'profit_factor': 0.0,
        }
    
    equity = np.array(equity_curve)
    returns = np.diff(equity) / equity[:-1]
    
    # Total return
    total_return = (equity[-1] - equity[0]) / equity[0] * 100
    
    # Sharpe (assuming daily returns, 252 trading days)
    if len(returns) > 1 and returns.std() > 0:
        sharpe = returns.mean() / returns.std() * np.sqrt(252)
    else:
        sharpe = 0.0
    
    # Max drawdown
    peak = np.maximum.accumulate(equity)
    drawdown = (equity - peak) / peak * 100
    max_dd = drawdown.min()
    
    # Trade metrics
    if trades:
        trade_returns = [t.pnl_pct for t in trades]
        winning = [r for r in trade_returns if r > 0]
        losing = [r for r in trade_returns if r < 0]
        
        win_rate = len(winning) / len(trade_returns) * 100 if trade_returns else 0
        avg_trade = np.mean(trade_returns)
        best_trade = max(trade_returns) if trade_returns else 0
        worst_trade = min(trade_returns) if trade_returns else 0
        
        if losing:
            profit_factor = abs(sum(winning) / sum(losing))
        else:
            profit_factor = float('inf') if winning else 0
    else:
        win_rate = 0.0
        avg_trade = 0.0
        best_trade = 0.0
        worst_trade = 0.0
        profit_factor = 0.0
    
    return {
        'total_return_pct': float(total_return),
        'sharpe_ratio': float(sharpe),
        'max_drawdown_pct': float(max_dd),
        'win_rate': float(win_rate),
        'total_trades': len(trades),
        'avg_trade_pct': float(avg_trade),
        'best_trade_pct': float(best_trade),
        'worst_trade_pct': float(worst_trade),
        'profit_factor': float(profit_factor),
    }


def load_data(exchange: str, symbol: str, timeframe: str) -> pd.DataFrame:
    """Load OHLCV data from parquet storage."""
    from pathlib import Path
    from trading_agent.data.storage import load_ohlcv as storage_load_ohlcv
    
    try:
        return storage_load_ohlcv(exchange, symbol, timeframe)
    except FileNotFoundError:
        # Try alternative path
        base = Path.cwd() / "data" / "parquet"
        path = base / exchange / symbol.replace('/', '_') / timeframe
        if path.exists():
            parquet_files = list(path.glob("*.parquet"))
            if parquet_files:
                return pd.read_parquet(parquet_files[0])
        raise FileNotFoundError(f"No data found for {symbol} on {exchange} {timeframe}")


def run_backtest(
    strategy_name: str,
    symbol: str,
    timeframe: str,
    params: dict[str, Any] | None = None,
    initial_capital: Decimal = Decimal("100000"),
    commission: float = 0.0004,
    slippage: float = 0.0005,
    start_date: str | None = None,
    end_date: str | None = None,
) -> BacktestResult:
    """
    Run backtest for a strategy.
    
    Args:
        strategy_name: Name of strategy from registry
        symbol: Trading symbol (e.g., BTC/USDT)
        timeframe: Timeframe (e.g., 1h)
        params: Strategy parameters
        initial_capital: Starting capital
        commission: Commission rate
        slippage: Slippage rate
        start_date: Start date (ISO format)
        end_date: End date (ISO format)
    
    Returns:
        BacktestResult with metrics and trades
    """
    # Load strategy
    registry = get_registry()
    strategy_class = registry.get(strategy_name)
    
    if not strategy_class:
        raise ValueError(f"Strategy not found: {strategy_name}")
    
    strategy = strategy_class(params or {})
    
    # Load data
    df = load_data("binance", symbol, timeframe)
    
    # Parse dates
    if 'timestamp' in df.columns:
        df = df.with_columns(pl.col('timestamp').cast(pl.Datetime))
    else:
        raise ValueError("Data must have 'timestamp' column")
    
    if start_date:
        df = df.filter(pl.col('timestamp') >= start_date)
    if end_date:
        df = df.filter(pl.col('timestamp') <= end_date)
    
    if df.is_empty():
        raise ValueError("No data after filtering")
    
    # Sort by timestamp
    df = df.sort('timestamp')
    
    # Convert to list of dicts for iteration
    rows = df.to_dicts()
    
    # Parse symbol
    base, quote = symbol.split('/') if '/' in symbol else (symbol, 'USDT')
    sym_obj = ExSymbol(base, quote, AssetClass.CRYPTO, MarketType.SPOT, "binance")
    
    # Run backtest
    equity = float(initial_capital)
    equity_curve = [(rows[0]['timestamp'], equity)]
    position = 0.0
    entry_price = 0.0
    entry_time = None
    trades = []
    
    from trading.strategies.plugins import StrategyContext
    
    for row in rows:
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
            portfolio_value=Decimal(str(equity)),
            available_balance=Decimal(str(equity)),
            current_time=row['timestamp'],
        )
        
        signals = strategy.on_bar(context)
        
        for sig in signals:
            price = float(bar.close)
            size_pct = float(strategy.config.get('position_size', 0.1))
            
            if sig.side.name == "BUY" and position <= 0:
                # Close short if any
                if position < 0:
                    pnl = (entry_price - price) * abs(position)
                    equity += pnl
                    trades.append(Trade(
                        entry_date=entry_time,
                        exit_date=row['timestamp'],
                        entry_price=Decimal(str(entry_price)),
                        exit_price=Decimal(str(price)),
                        size=Decimal(str(abs(position))),
                        pnl=Decimal(str(pnl)),
                        pnl_pct=pnl / entry_price * 100,
                        side='short',
                        strategy=strategy_name,
                        symbol=symbol,
                    ))
                
                # Enter long
                position_size = equity * size_pct / price * (1 - commission - slippage)
                position = position_size
                entry_price = price * (1 + commission + slippage)
                entry_time = row['timestamp']
                equity -= position * entry_price
                
            elif sig.side.name == "SELL" and position >= 0:
                # Close long if any
                if position > 0:
                    pnl = (price - entry_price) * position
                    equity += pnl
                    trades.append(Trade(
                        entry_date=entry_time,
                        exit_date=row['timestamp'],
                        entry_price=Decimal(str(entry_price)),
                        exit_price=Decimal(str(price)),
                        size=Decimal(str(position)),
                        pnl=Decimal(str(pnl)),
                        pnl_pct=pnl / entry_price * 100,
                        side='long',
                        strategy=strategy_name,
                        symbol=symbol,
                    ))
                
                # Enter short
                position_size = equity * size_pct / price * (1 - commission - slippage)
                position = -position_size
                entry_price = price * (1 - commission - slippage)
                entry_time = row['timestamp']
                equity += abs(position) * entry_price  # Short adds to equity
        
        equity_curve.append((row['timestamp'], equity))
    
    # Close final position
    if position != 0 and rows:
        final_price = float(rows[-1]['close'])
        if position > 0:
            pnl = (final_price - entry_price) * position
        else:
            pnl = (entry_price - final_price) * abs(position)
        equity += pnl
        trades.append(Trade(
            entry_date=entry_time,
            exit_date=rows[-1]['timestamp'],
            entry_price=Decimal(str(entry_price)),
            exit_price=Decimal(str(final_price)),
            size=Decimal(str(abs(position))),
            pnl=Decimal(str(pnl)),
            pnl_pct=pnl / entry_price * 100,
            side='long' if position > 0 else 'short',
            strategy=strategy_name,
            symbol=symbol,
        ))
    
    # Compute metrics
    equity_values = [e[1] for e in equity_curve]
    metrics = compute_metrics(equity_values, trades)
    
    return BacktestResult(
        strategy=strategy_name,
        symbol=symbol,
        timeframe=timeframe,
        params=params or {},
        initial_capital=initial_capital,
        final_equity=Decimal(str(equity)),
        trades=trades,
        equity_curve=equity_curve,
        **metrics,
    )


def verify_backtest_hash(result: BacktestResult) -> str:
    """Compute deterministic hash of backtest result."""
    # Create deterministic representation
    data = {
        'strategy': result.strategy,
        'symbol': result.symbol,
        'timeframe': result.timeframe,
        'params': result.params,
        'total_return_pct': round(result.total_return_pct, 4),
        'sharpe_ratio': round(result.sharpe_ratio, 4),
        'max_drawdown_pct': round(result.max_drawdown_pct, 4),
        'win_rate': round(result.win_rate, 4),
        'total_trades': result.total_trades,
        'trades': [
            {
                'entry_date': t.entry_date.isoformat(),
                'exit_date': t.exit_date.isoformat(),
                'pnl_pct': round(t.pnl_pct, 4),
            }
            for t in result.trades
        ],
    }
    
    json_str = json.dumps(data, sort_keys=True)
    return hashlib.sha256(json_str.encode()).hexdigest()[:16]


if __name__ == "__main__":
    # Quick test
    result = run_backtest("ma_crossover", "BTC/USDT", "1h", initial_capital=Decimal("10000"))
    print(f"Return: {result.total_return_pct:.2f}%")
    print(f"Sharpe: {result.sharpe_ratio:.2f}")
    print(f"Max DD: {result.max_drawdown_pct:.2f}%")
    print(f"Trades: {result.total_trades}")
    print(f"Hash: {verify_backtest_hash(result)}")