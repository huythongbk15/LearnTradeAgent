#!/usr/bin/env python
"""
Compare USE_LLM=true vs USE_LLM=false on a small window (1-2 months).
"""

from __future__ import annotations

import os
from datetime import datetime

import polars as pl

from trading_agent.data.storage import load_ohlcv
from trading_agent.agents.technical import TechnicalAnalyst
from trading_agent.agents.sentiment import SentimentAnalyst
from trading_agent.agents.risk import RiskManager
from trading_agent.agents.trader import Trader
from trading_agent.agents.base import AnalysisContext


def run_agents_with_llm(use_llm: bool, symbol: str = "BTC/USDT", timeframe: str = "1h", days: int = 30):
    """Run the multi-agent system with or without LLM."""
    os.environ["USE_LLM"] = "true" if use_llm else "false"

    # Reload modules to pick up USE_LLM change
    import importlib
    import trading_agent.agents.llm as llm_module
    importlib.reload(llm_module)
    import trading_agent.agents.technical as tech_module
    importlib.reload(tech_module)
    import trading_agent.agents.sentiment as sent_module
    importlib.reload(sent_module)
    import trading_agent.agents.risk as risk_module
    importlib.reload(risk_module)
    import trading_agent.agents.trader as trader_module
    importlib.reload(trader_module)


    # Load data
    df = load_ohlcv("binance", symbol, timeframe)
    if df.is_empty():
        print(f"No data for {symbol} {timeframe}")
        return None

    # Get last N days
    cutoff = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    from datetime import timedelta
    cutoff = cutoff - timedelta(days=days)
    df = df.filter(pl.col("timestamp") >= cutoff)

    print(f"Loaded {len(df)} bars for {symbol} {timeframe} (last {days} days)")

    if len(df) < 50:
        print("Insufficient data")
        return None

    # Initialize agents
    tech = TechnicalAnalyst()
    sent = SentimentAnalyst()
    risk = RiskManager()
    trader = Trader()

    # Track signals
    results = {
        "use_llm": use_llm,
        "symbol": symbol,
        "timeframe": timeframe,
        "days": days,
        "bars": len(df),
        "signals": [],
    }

    # Run agents on each bar (simulate real-time)
    for i in range(50, len(df)):
        window = df.slice(0, i+1)
        current_price = float(window["close"][-1])
        current_time = window["timestamp"][-1]

        # Build context
        context = AnalysisContext(
            symbol=symbol,
            timeframe=timeframe,
            current_price=current_price,
            ohlcv=window,
            indicators={},  # Will be computed by agents
            current_position_pct=0.0,
            unrealized_pnl_pct=0.0,
            portfolio_value=10000.0,
        )

        # Run technical analyst
        tech_msg = tech.analyze(context)

        # Run sentiment analyst
        sent_msg = sent.analyze(context)

        # Run risk manager
        risk_msg = risk.analyze(context)

        # Run trader
        trader_msg = trader.analyze(context)

        # Record
        results["signals"].append({
            "bar": i,
            "timestamp": str(current_time),
            "price": current_price,
            "technical": {
                "signal": tech_msg.signal,
                "confidence": tech_msg.confidence,
                "reasoning": tech_msg.reasoning,
            },
            "sentiment": {
                "signal": sent_msg.signal,
                "confidence": sent_msg.confidence,
                "reasoning": sent_msg.reasoning,
            },
            "risk": {
                "signal": risk_msg.signal,
                "confidence": risk_msg.confidence,
                "reasoning": risk_msg.reasoning,
                "max_pos_pct": risk_msg.max_position_size_pct,
            },
            "trader": {
                "signal": trader_msg.signal,
                "confidence": trader_msg.confidence,
                "reasoning": trader_msg.reasoning,
            },
        })

    return results


def compare_llm_modes():
    """Run comparison between LLM and rule-based modes."""
    print("=" * 70)
    print("USE_LLM Comparison Test")
    print("=" * 70)

    # Test with LLM (will fallback if no API key)
    print("\n--- Running with USE_LLM=true ---")
    try:
        results_llm = run_agents_with_llm(use_llm=True, days=30)
    except Exception as e:
        print(f"LLM mode failed: {e}")
        results_llm = None

    # Test without LLM (rule-based)
    print("\n--- Running with USE_LLM=false ---")
    try:
        results_rule = run_agents_with_llm(use_llm=False, days=30)
    except Exception as e:
        print(f"Rule-based mode failed: {e}")
        results_rule = None

    # Compare
    print("\n" + "=" * 70)
    print("COMPARISON RESULTS")
    print("=" * 70)

    if results_llm and results_rule:
        # Compare trader signals
        llm_signals = [s["trader"]["signal"] for s in results_llm["signals"]]
        rule_signals = [s["trader"]["signal"] for s in results_rule["signals"]]

        print(f"\nTotal bars analyzed: {len(llm_signals)}")

        # Signal distribution
        for label, signals in [("LLM", llm_signals), ("Rule-based", rule_signals)]:
            buy = signals.count("BUY")
            sell = signals.count("SELL")
            hold = signals.count("HOLD")
            print(f"{label}: BUY={buy} ({buy/len(signals)*100:.1f}%), SELL={sell} ({sell/len(signals)*100:.1f}%), HOLD={hold} ({hold/len(signals)*100:.1f}%)")

        # Agreement
        agreements = sum(1 for left, right in zip(llm_signals, rule_signals) if left == right)
        print(f"\nSignal agreement: {agreements}/{len(llm_signals)} ({agreements/len(llm_signals)*100:.1f}%)")

        # Confidence comparison
        llm_conf = [s["trader"]["confidence"] for s in results_llm["signals"]]
        rule_conf = [s["trader"]["confidence"] for s in results_rule["signals"]]
        print(f"Avg confidence - LLM: {sum(llm_conf)/len(llm_conf):.3f}, Rule: {sum(rule_conf)/len(rule_conf):.3f}")

        # Sample first 10 signals
        print("\nFirst 10 bars comparison:")
        print(f"{'Bar':>4} {'Price':>10} {'LLM Signal':>10} {'LLM Conf':>8} {'Rule Signal':>10} {'Rule Conf':>8} {'Match'}")
        for i in range(min(10, len(results_llm["signals"]))):
            s_llm = results_llm["signals"][i]
            s_rule = results_rule["signals"][i]
            match = "✓" if s_llm["trader"]["signal"] == s_rule["trader"]["signal"] else "✗"
            print(f"{i:>4} {s_llm['price']:>10.2f} {s_llm['trader']['signal']:>10} {s_llm['trader']['confidence']:>8.2f} {s_rule['trader']['signal']:>10} {s_rule['trader']['confidence']:>8.2f}   {match}")

    else:
        print("One or both modes failed to produce results")

    return results_llm, results_rule


if __name__ == "__main__":
    compare_llm_modes()