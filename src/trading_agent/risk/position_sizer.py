#!/usr/bin/env python3
"""
Dynamic Position Sizing Module

Implements multiple position sizing methods:
1. Fixed Fractional (baseline)
2. Kelly Criterion (full/half/quarter)
3. Volatility Targeting
4. Risk Parity / Equal Risk Contribution
5. Optimal f (Ralph Vince)
"""

from dataclasses import dataclass
from typing import Optional


@dataclass
class PositionSizingParams:
    """Configuration for position sizing."""
    method: str = "half_kelly"  # fixed, kelly, half_kelly, quarter_kelly, vol_target, risk_parity, optimal_f
    
    # Fixed fractional
    fixed_fraction: float = 0.02  # 2% per trade
    
    # Kelly criterion
    kelly_fraction: float = 0.5   # 0.5 = half-Kelly, 0.25 = quarter-Kelly
    min_win_rate: float = 0.01    # Minimum win rate to avoid division by zero
    min_avg_win: float = 0.0001   # Minimum avg win
    
    # Volatility targeting
    target_annual_vol: float = 0.15  # 15% annual portfolio volatility
    vol_lookback: int = 252          # Days for realized vol estimation
    max_leverage: float = 2.0        # Max portfolio leverage
    
    # Risk parity
    risk_budget: Optional[list] = None  # Risk budget per asset (None = equal)
    
    # Safety caps
    max_position_pct: float = 1.0     # Max 100% in single position
    max_portfolio_heat: float = 0.8   # Max 80% total deployed
    min_position_pct: float = 0.001   # Min 0.1% position


def calculate_kelly_fraction(win_rate: float, avg_win: float, avg_loss: float) -> float:
    """
    Calculate Kelly fraction: f* = (p * avg_win - q * avg_loss) / avg_win
    where p = win_rate, q = 1 - win_rate
    
    Simplified: f* = (win_rate * avg_win - (1-win_rate) * avg_loss) / avg_win
    Or: f* = win_rate - (1-win_rate) * (avg_loss / avg_win)
    
    Returns fraction of capital to bet (0 to 1).
    """
    if win_rate <= 0:
        return 0.0
    if win_rate >= 1:
        return 0.99  # Cap at 99% instead of 100% to avoid infinite leverage
    if avg_win <= 0:
        return 0.0
    if avg_loss <= 0:
        return win_rate  # If no losses, bet win_rate fraction
    
    kelly = win_rate - (1 - win_rate) * (avg_loss / avg_win)
    return max(0.0, min(1.0, kelly))


def calculate_half_kelly(win_rate: float, avg_win: float, avg_loss: float) -> float:
    """Half-Kelly: more conservative, better risk-adjusted."""
    return calculate_kelly_fraction(win_rate, avg_win, avg_loss) * 0.5


def calculate_quarter_kelly(win_rate: float, avg_win: float, avg_loss: float) -> float:
    """Quarter-Kelly: very conservative."""
    return calculate_kelly_fraction(win_rate, avg_win, avg_loss) * 0.25


def calculate_optimal_f(trades_pnl: list, min_trades: int = 30) -> float:
    """
    Calculate Optimal f (Ralph Vince) from trade PnL sequence.
    
    Optimal f = fraction that maximizes geometric growth.
    Found by iterating f from 0 to 1 and computing TWR (Terminal Wealth Relative).
    
    Requires sufficient trade history (>= 30 trades).
    """
    if len(trades_pnl) < min_trades:
        return 0.0
    
    # Normalize trades to % returns
    # Assuming each trade risks 1 unit, PnL is in units
    returns = [pnl for pnl in trades_pnl if pnl != 0]
    if not returns:
        return 0.0
    
    worst_loss = min(returns)  # Most negative (largest loss)
    if worst_loss >= 0:
        return 1.0  # No losses
    
    best_f = 0.0
    best_twr = 0.0
    
    # Search f from 0.01 to 1.0
    for f in [i * 0.01 for i in range(1, 101)]:
        twr = 1.0
        for ret in returns:
            hpr = 1 + (f * ret / abs(worst_loss))  # Holding Period Return
            if hpr <= 0:
                twr = 0
                break
            twr *= hpr
        
        if twr > best_twr:
            best_twr = twr
            best_f = f
    
    return best_f


def calculate_vol_target_size(
    current_vol: float,
    target_vol: float,
    current_equity: float,
    price: float,
    max_leverage: float = 2.0
) -> float:
    """
    Calculate position size to target portfolio volatility.
    
    position_size = (target_vol / current_vol) * equity / price
    Capped at max_leverage.
    """
    if current_vol <= 0:
        return 0.0
    
    vol_ratio = target_vol / current_vol
    leverage = min(vol_ratio, max_leverage)
    position_value = current_equity * leverage
    position_size = position_value / price
    
    return position_size


def calculate_risk_parity_weights(
    cov_matrix: list,  # NxN covariance matrix
    risk_budget: Optional[list] = None
) -> list:
    """
    Calculate Risk Parity weights (Equal Risk Contribution).
    
    Uses iterative algorithm (Newton-Raphson) to find weights where
    each asset contributes equally to portfolio risk.
    
    Args:
        cov_matrix: NxN covariance matrix of returns
        risk_budget: Target risk contribution per asset (None = equal)
    
    Returns:
        List of weights summing to 1.0
    """
    n = len(cov_matrix)
    if n == 0:
        return []
    if n == 1:
        return [1.0]
    
    if risk_budget is None:
        risk_budget = [1.0 / n] * n
    
    # Initialize with equal weights
    weights = [1.0 / n] * n
    
    # Iterative solver (simplified - in production use scipy.optimize)
    for _ in range(1000):
        # Portfolio variance
        port_var = sum(weights[i] * weights[j] * cov_matrix[i][j] 
                       for i in range(n) for j in range(n))
        
        if port_var <= 0:
            break
        
        # Marginal risk contribution
        mrc = [sum(weights[j] * cov_matrix[i][j] for j in range(n)) / port_var for i in range(n)]
        
        # Risk contribution
        rc = [weights[i] * mrc[i] for i in range(n)]
        
        # Check convergence
        error = sum((rc[i] - risk_budget[i])**2 for i in range(n))
        if error < 1e-10:
            break
        
        # Update weights (simplified gradient step)
        for i in range(n):
            weights[i] *= risk_budget[i] / (rc[i] + 1e-12)
        
        # Normalize
        total = sum(weights)
        weights = [w / total for w in weights]
    
    return weights


class PositionSizer:
    """
    Main position sizing class that computes position sizes
    based on strategy performance and risk parameters.
    """
    
    def __init__(self, params: PositionSizingParams):
        self.params = params
        self.trade_history: list = []  # List of dicts with pnl, win/loss
    
    def update_trade(self, pnl: float, entry_price: float, exit_price: float, size: float):
        """Record a completed trade for Kelly/Optimal-f calculation."""
        self.trade_history.append({
            "pnl": pnl,
            "entry": entry_price,
            "exit": exit_price,
            "size": size,
            "return_pct": pnl / (entry_price * size) if entry_price * size != 0 else 0,
            "win": pnl > 0
        })
    
    def get_kelly_stats(self) -> tuple:
        """Calculate win rate, avg win, avg loss from history."""
        if not self.trade_history:
            return 0.55, 0.02, 0.01  # Defaults: 55% WR, 2:1 R:R
        
        wins = [t["return_pct"] for t in self.trade_history if t["win"]]
        losses = [abs(t["return_pct"]) for t in self.trade_history if not t["win"]]
        
        win_rate = len(wins) / len(self.trade_history)
        avg_win = sum(wins) / len(wins) if wins else 0.02
        avg_loss = sum(losses) / len(losses) if losses else 0.01
        
        return win_rate, avg_win, avg_loss
    
    def calculate_position_size(
        self,
        equity: float,
        price: float,
        atr: Optional[float] = None,
        current_portfolio_value: float = 0,
        current_positions: int = 0,
    ) -> float:
        """
        Calculate position size for a new trade.
        
        Returns:
            Position size in base currency units (e.g., BTC amount)
        """
        method = self.params.method
        
        # Safety checks
        available_capital = equity - current_portfolio_value
        if available_capital <= self.params.min_position_pct * equity:
            return 0.0
        
        if current_positions >= 10:  # Max 10 concurrent positions
            return 0.0
        
        # Method-specific calculation
        if method == "fixed":
            risk_amount = equity * self.params.fixed_fraction
            
        elif method in ["kelly", "half_kelly", "quarter_kelly"]:
            win_rate, avg_win, avg_loss = self.get_kelly_stats()
            
            if method == "kelly":
                kelly_f = calculate_kelly_fraction(win_rate, avg_win, avg_loss)
            elif method == "half_kelly":
                kelly_f = calculate_half_kelly(win_rate, avg_win, avg_loss)
            else:  # quarter_kelly
                kelly_f = calculate_quarter_kelly(win_rate, avg_win, avg_loss)
            
            risk_amount = equity * kelly_f
            
        elif method == "vol_target":
            # Need realized volatility - placeholder
            # In practice, pass current_vol from strategy
            current_vol = 0.5  # Placeholder 50% annual vol
            risk_amount = equity * min(
                self.params.target_annual_vol / current_vol,
                self.params.max_leverage
            )
            
        elif method == "optimal_f":
            if len(self.trade_history) >= 30:
                pnl_list = [t["pnl"] for t in self.trade_history]
                optimal_f = calculate_optimal_f(pnl_list)
                risk_amount = equity * optimal_f * self.params.kelly_fraction  # Scale down
            else:
                risk_amount = equity * self.params.fixed_fraction  # Fallback
                
        else:
            risk_amount = equity * self.params.fixed_fraction
        
        # Apply ATR-based stop loss sizing if available
        if atr and atr > 0:
            # Risk per unit = ATR * multiplier (default 2.0)
            risk_per_unit = atr * 2.0
            if risk_per_unit > 0:
                position_size = risk_amount / risk_per_unit
            else:
                position_size = risk_amount / price
        else:
            position_size = risk_amount / price
        
        # Cap at max position %
        max_size = equity * self.params.max_position_pct / price
        position_size = min(position_size, max_size)
        
        # Cap at available capital
        max_affordable = available_capital * 0.95 / price  # 5% buffer
        position_size = min(position_size, max_affordable)
        
        # Minimum size
        min_size = equity * self.params.min_position_pct / price
        if position_size < min_size:
            return 0.0
        
        return position_size
    
    def get_portfolio_weights(
        self,
        symbols: list,
        cov_matrix: list,
        equity: float,
        prices: dict,
        atr_dict: dict
    ) -> dict:
        """
        Calculate position sizes for multiple symbols simultaneously.
        Used for portfolio-level risk parity / vol targeting.
        """
        if self.params.method == "risk_parity":
            weights = calculate_risk_parity_weights(cov_matrix, self.params.risk_budget)
        else:
            # Equal weight fallback
            weights = [1.0 / len(symbols)] * len(symbols)
        
        positions = {}
        for i, symbol in enumerate(symbols):
            price = prices.get(symbol, 1)
            atr = atr_dict.get(symbol)
            allocated_equity = equity * weights[i]
            size = self.calculate_position_size(
                equity=allocated_equity,
                price=price,
                atr=atr,
            )
            positions[symbol] = size
        
        return positions


# Convenience functions for quick use
def kelly_size(equity: float, win_rate: float, avg_win: float, avg_loss: float, fraction: float = 0.5) -> float:
    """Quick Kelly position size calculation."""
    kelly_f = calculate_kelly_fraction(win_rate, avg_win, avg_loss) * fraction
    return equity * kelly_f


def fixed_fraction_size(equity: float, fraction: float = 0.02) -> float:
    """Quick fixed fractional size."""
    return equity * fraction


def vol_target_size(equity: float, current_vol: float, target_vol: float = 0.15, max_lev: float = 2.0) -> float:
    """Quick volatility targeting size."""
    if current_vol <= 0:
        return 0.0
    lev = min(target_vol / current_vol, max_lev)
    return equity * lev


if __name__ == "__main__":
    # Demo
    params = PositionSizingParams(method="half_kelly")
    sizer = PositionSizer(params)
    
    # Simulate some trades
    import random
    for _ in range(100):
        win = random.random() > 0.45  # 55% win rate
        pnl = random.uniform(0.01, 0.03) if win else -random.uniform(0.005, 0.02)
        sizer.update_trade(pnl, 50000, 50000 * (1 + pnl), 1.0)
    
    win_rate, avg_win, avg_loss = sizer.get_kelly_stats()
    print(f"Win Rate: {win_rate:.2%}")
    print(f"Avg Win: {avg_win:.4f}")
    print(f"Avg Loss: {avg_loss:.4f}")
    print(f"Kelly: {calculate_kelly_fraction(win_rate, avg_win, avg_loss):.4f}")
    print(f"Half-Kelly: {calculate_half_kelly(win_rate, avg_win, avg_loss):.4f}")
    print(f"Quarter-Kelly: {calculate_quarter_kelly(win_rate, avg_win, avg_loss):.4f}")
    print(f"Optimal f: {calculate_optimal_f([t['pnl'] for t in sizer.trade_history]):.4f}")
    print(f"Position size (100k equity, 50k price): {sizer.calculate_position_size(100000, 50000):.6f}")