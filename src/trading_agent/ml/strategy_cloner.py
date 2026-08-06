#!/usr/bin/env python3
"""
Strategy Cloning — learn from successful traders' behavior.

Components:
1. TradeCloner — analyze trade history and extract patterns
2. BehaviorExtractor — position sizing, timing, risk profile
3. StrategyCloner — build a rule-based replica of a trading style

Design:
    cloner = TradeCloner()
    profile = cloner.analyze_trades(trade_history)
    replica = StrategyCloner()
    rules = replica.extract_rules(profile)
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import Optional

import numpy as np


@dataclass
class TraderProfile:
    """Extracted behavioral profile of a trader."""
    name: str = ""
    total_trades: int = 0
    win_rate: float = 0.0
    avg_hold_time_hours: float = 0.0
    avg_position_size_pct: float = 0.0     # % of portfolio
    avg_profit_per_trade: float = 0.0
    avg_loss_per_trade: float = 0.0
    profit_factor: float = 0.0
    max_drawdown: float = 0.0
    sharpe_ratio: float = 0.0
    preferred_assets: list[str] = field(default_factory=list)
    preferred_timeframe: str = ""          # "intraday", "swing", "position"
    risk_tolerance: str = ""               # "conservative", "moderate", "aggressive"
    entry_patterns: list[dict] = field(default_factory=list)
    exit_patterns: list[dict] = field(default_factory=list)
    sizing_model: str = ""                 # "fixed", "kelly", "volatility_target"


class TradeCloner:
    """Analyzes trade history to extract behavioral patterns."""

    def analyze_trades(self, trades: list[dict], name: str = "trader") -> TraderProfile:
        """
        Analyze a list of trades (each: {entry_time, exit_time, entry_price, exit_price,
        side, size, asset, pnl_pct}).
        """
        if not trades:
            return TraderProfile(name=name)

        pnls = [t.get("pnl_pct", 0) for t in trades]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p < 0]

        # Hold times
        hold_times = []
        for t in trades:
            if "entry_time" in t and "exit_time" in t:
                try:
                    et = t["entry_time"]
                    xt = t["exit_time"]
                    if isinstance(et, (int, float)) and isinstance(xt, (int, float)):
                        hold_times.append((xt - et) / 3600)
                except Exception:
                    pass

        # Position sizes
        sizes = [t.get("size_pct", t.get("size", 0)) for t in trades]

        # Profit factor
        gross_profit = sum(wins) if wins else 0
        gross_loss = abs(sum(losses)) if losses else 1
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")

        # Max drawdown
        equity = np.cumsum(pnls)
        running_max = np.maximum.accumulate(equity)
        drawdown = running_max - equity
        max_dd = float(np.max(drawdown)) if len(drawdown) > 0 else 0

        # Sharpe
        if len(pnls) > 1:
            mean_ret = np.mean(pnls)
            std_ret = np.std(pnls)
            sharpe = mean_ret / (std_ret + 1e-9) * math.sqrt(252)
        else:
            sharpe = 0

        # Timeframe classification
        avg_hold = np.mean(hold_times) if hold_times else 24
        if avg_hold < 4:
            timeframe = "intraday"
        elif avg_hold < 168:
            timeframe = "swing"
        else:
            timeframe = "position"

        # Risk tolerance
        if max_dd < 5:
            risk = "conservative"
        elif max_dd < 15:
            risk = "moderate"
        else:
            risk = "aggressive"

        # Preferred assets
        asset_counts = {}
        for t in trades:
            a = t.get("asset", "unknown")
            asset_counts[a] = asset_counts.get(a, 0) + 1
        preferred = sorted(asset_counts.keys(), key=lambda x: asset_counts[x], reverse=True)[:5]

        # Sizing model detection
        size_std = np.std(sizes) if sizes else 0
        if size_std < 0.5:
            sizing = "fixed"
        elif any(t.get("vol_adjusted", False) for t in trades):
            sizing = "volatility_target"
        else:
            sizing = "kelly"

        # Entry patterns
        entry_patterns = self._extract_entry_patterns(trades)
        exit_patterns = self._extract_exit_patterns(trades)

        return TraderProfile(
            name=name, total_trades=len(trades),
            win_rate=len(wins) / len(trades) if trades else 0,
            avg_hold_time_hours=float(avg_hold),
            avg_position_size_pct=float(np.mean(sizes)) if sizes else 0,
            avg_profit_per_trade=float(np.mean(wins)) if wins else 0,
            avg_loss_per_trade=float(np.mean(losses)) if losses else 0,
            profit_factor=profit_factor,
            max_drawdown=max_dd,
            sharpe_ratio=sharpe,
            preferred_assets=preferred,
            preferred_timeframe=timeframe,
            risk_tolerance=risk,
            entry_patterns=entry_patterns,
            exit_patterns=exit_patterns,
            sizing_model=sizing,
        )

    def _extract_entry_patterns(self, trades: list[dict]) -> list[dict]:
        """Extract common entry conditions from trade metadata."""
        patterns = []
        # Group by entry signal if available
        signal_counts = {}
        for t in trades:
            sig = t.get("entry_signal", "unknown")
            signal_counts[sig] = signal_counts.get(sig, 0) + 1
        for sig, count in sorted(signal_counts.items(), key=lambda x: x[1], reverse=True):
            sig_trades = [t for t in trades if t.get("entry_signal") == sig]
            pnls = [t.get("pnl_pct", 0) for t in sig_trades]
            patterns.append({
                "signal": sig, "count": count,
                "win_rate": sum(1 for p in pnls if p > 0) / max(len(pnls), 1),
                "avg_pnl": float(np.mean(pnls)) if pnls else 0,
            })
        return patterns[:5]

    def _extract_exit_patterns(self, trades: list[dict]) -> list[dict]:
        patterns = []
        signal_counts = {}
        for t in trades:
            sig = t.get("exit_signal", "unknown")
            signal_counts[sig] = signal_counts.get(sig, 0) + 1
        for sig, count in sorted(signal_counts.items(), key=lambda x: x[1], reverse=True):
            sig_trades = [t for t in trades if t.get("exit_signal") == sig]
            pnls = [t.get("pnl_pct", 0) for t in sig_trades]
            patterns.append({
                "signal": sig, "count": count,
                "win_rate": sum(1 for p in pnls if p > 0) / max(len(pnls), 1),
                "avg_pnl": float(np.mean(pnls)) if pnls else 0,
            })
        return patterns[:5]


class StrategyCloner:
    """Builds a rule-based replica from a TraderProfile."""

    def extract_rules(self, profile: TraderProfile) -> dict:
        """Convert profile to executable rules."""
        rules = {
            "position_sizing": {
                "model": profile.sizing_model,
                "max_position_pct": min(profile.avg_position_size_pct * 1.5, 20),
                "kelly_fraction": profile.win_rate - (1 - profile.win_rate) / max(profile.profit_factor, 0.1),
            },
            "risk_management": {
                "max_drawdown_pct": profile.max_drawdown * 1.2,
                "stop_loss_pct": abs(profile.avg_loss_per_trade) * 1.5,
                "take_profit_pct": profile.avg_profit_per_trade * 0.8,
                "risk_tolerance": profile.risk_tolerance,
            },
            "entry_rules": {
                "preferred_assets": profile.preferred_assets,
                "timeframe": profile.preferred_timeframe,
                "signals": [p["signal"] for p in profile.entry_patterns[:3] if p["win_rate"] > 0.5],
            },
            "exit_rules": {
                "signals": [p["signal"] for p in profile.exit_patterns[:3]],
                "max_hold_hours": profile.avg_hold_time_hours * 2,
            },
        }
        return rules

    def estimate_performance(self, profile: TraderProfile) -> dict:
        """Estimate expected performance of cloned strategy."""
        kelly = profile.win_rate - (1 - profile.win_rate) / max(profile.profit_factor, 0.1)
        kelly_half = kelly / 2  # half-Kelly for safety

        return {
            "expected_win_rate": profile.win_rate,
            "expected_profit_factor": profile.profit_factor,
            "expected_sharpe": profile.sharpe_ratio * 0.7,  # degrade for cloning loss
            "kelly_full": kelly,
            "kelly_half": kelly_half,
            "recommended_sizing": f"{kelly_half * 100:.1f}% per trade",
            "expected_max_drawdown": profile.max_drawdown * 1.3,
            "confidence": "high" if profile.total_trades > 100 else "medium" if profile.total_trades > 30 else "low",
        }


if __name__ == "__main__":
    import random
    print("=" * 60)
    print("STRATEGY CLONER — DEMO")
    print("=" * 60)

    # Simulate 200 trades
    trades = []
    for i in range(200):
        side = random.choice(["long", "short"])
        asset = random.choice(["BTC", "ETH", "SOL"])
        signal = random.choice(["ma_cross", "rsi_oversold", "breakout", "mean_reversion"])
        entry = random.uniform(90000, 110000)
        pnl = random.gauss(0.5, 3)  # slight edge
        trades.append({
            "entry_time": time.time() - random.uniform(0, 86400 * 30),
            "exit_time": time.time() - random.uniform(0, 86400 * 30),
            "entry_price": entry, "exit_price": entry * (1 + pnl / 100),
            "side": side, "size_pct": random.uniform(1, 5),
            "asset": asset, "pnl_pct": pnl,
            "entry_signal": signal, "exit_signal": random.choice(["tp", "sl", "time", signal]),
        })

    cloner = TradeCloner()
    profile = cloner.analyze_trades(trades, name="whale_trader")
    print(f"\nTrader Profile: {profile.name}")
    print(f"  Trades: {profile.total_trades}")
    print(f"  Win rate: {profile.win_rate:.1%}")
    print(f"  Profit factor: {profile.profit_factor:.2f}")
    print(f"  Sharpe: {profile.sharpe_ratio:.2f}")
    print(f"  Max DD: {profile.max_drawdown:.2f}%")
    print(f"  Avg hold: {profile.avg_hold_time_hours:.1f}h ({profile.preferred_timeframe})")
    print(f"  Risk: {profile.risk_tolerance}")
    print(f"  Assets: {profile.preferred_assets}")
    print(f"  Sizing: {profile.sizing_model}")

    cloner2 = StrategyCloner()
    rules = cloner2.extract_rules(profile)
    print(f"\nCloned Rules:")
    for section, data in rules.items():
        print(f"  {section}:")
        for k, v in data.items():
            print(f"    {k}: {v}")

    perf = cloner2.estimate_performance(profile)
    print(f"\nEstimated Performance:")
    for k, v in perf.items():
        print(f"  {k}: {v}")
