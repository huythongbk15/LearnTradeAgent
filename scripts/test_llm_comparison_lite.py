#!/usr/bin/env python
"""
Lightweight USE_LLM comparison — no module reloads per bar.
Runs both modes in a single pass by instantiating agents with explicit config.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

import polars as pl

# Add src to path
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "src"))

from trading_agent.data.storage import load_ohlcv
from trading_agent.agents.base import AnalysisContext, AgentMessage
from trading_agent.agents.llm import chat, LLMError


def _llm_chat(prompt: str, max_tokens: int = 50) -> str | None:
    """Try LLM chat, return None on failure."""
    try:
        resp = chat(
            [{"role": "user", "content": prompt}],
            max_tokens=max_tokens,
            temperature=0.1,
            timeout=10,
        )
        return resp.content
    except (LLMError, Exception):
        return None


class MockTechnicalAnalyst:
    """Rule-based technical analyst (no LLM)."""
    def __init__(self, use_llm: bool = False):
        self.use_llm = use_llm
        self.name = "technical_analyst"

    def analyze(self, context: AnalysisContext) -> AgentMessage:
        df = context.ohlcv
        close = df["close"]
        
        sma_20 = close.rolling_mean(20).tail(1)
        sma_20 = float(sma_20[-1]) if len(sma_20) > 0 else current_price
        sma_50 = close.rolling_mean(50).tail(1)
        sma_50 = float(sma_50[-1]) if len(sma_50) > 0 else current_price
        
        delta = close.diff()
        gain = delta.clip(lower_bound=0).rolling_mean(14)
        loss = (-delta.clip(upper_bound=0)).rolling_mean(14)
        rs = gain / loss
        rsi = (100 - 100 / (1 + rs)).tail(1)
        rsi = float(rsi[-1]) if len(rsi) > 0 else 50.0
        
        current_price = context.current_price
        
        if self.use_llm:
            prompt = f"BTC at ${current_price:.0f}, SMA20={sma_20:.0f}, SMA50={sma_50:.0f}, RSI={rsi:.1f}. Signal: BUY/SELL/HOLD?"
            resp = _llm_chat(prompt, max_tokens=20)
            signal = "BUY" if resp and "buy" in resp.lower() else ("SELL" if resp and "sell" in resp.lower() else "HOLD")
            conf = 0.7 if resp else 0.3
        else:
            if sma_20 > sma_50 and rsi < 70:
                signal, conf = "BUY", 0.65
            elif sma_20 < sma_50 and rsi > 30:
                signal, conf = "SELL", 0.65
            else:
                signal, conf = "HOLD", 0.55
        
        return AgentMessage(
            role="technical_analyst",
            signal=signal,
            confidence=conf,
            reasoning=f"SMA20={sma_20:.0f} vs SMA50={sma_50:.0f}, RSI={rsi:.1f}",
        )


class MockSentimentAnalyst:
    """Rule-based sentiment analyst."""
    def __init__(self, use_llm: bool = False):
        self.use_llm = use_llm
        self.name = "sentiment_analyst"

    def analyze(self, context: AnalysisContext) -> AgentMessage:
        df = context.ohlcv
        vol_ratio = (df["volume"].tail(5).mean() / df["volume"].tail(20).mean())
        vol_ratio = float(vol_ratio) if vol_ratio is not None else 1.0
        
        if self.use_llm:
            prompt = f"Volume ratio {vol_ratio:.2f}. Sentiment: bullish/bearish/neutral?"
            resp = _llm_chat(prompt, max_tokens=20)
            signal = "BUY" if resp and "bull" in resp.lower() else ("SELL" if resp and "bear" in resp.lower() else "HOLD")
            conf = 0.6 if resp else 0.3
        else:
            if vol_ratio > 1.3:
                signal, conf = "BUY", 0.55
            elif vol_ratio < 0.7:
                signal, conf = "SELL", 0.55
            else:
                signal, conf = "HOLD", 0.50
        
        return AgentMessage(
            role="sentiment_analyst",
            signal=signal,
            confidence=conf,
            reasoning=f"Volume ratio: {vol_ratio:.2f}",
        )


class MockRiskManager:
    """Risk manager with volatility-based sizing."""
    def __init__(self, use_llm: bool = False):
        self.use_llm = use_llm
        self.name = "risk_manager"

    def analyze(self, context: AnalysisContext) -> AgentMessage:
        df = context.ohlcv
        returns = df["close"].pct_change()
        vol = returns.tail(20).std() * 100
        vol = float(vol) if vol is not None else 0.0
        
        RISK_PER_TRADE = 0.015
        if vol > 0:
            stop_pct = max(0.03, min(0.08, vol / 100.0))
            risk_based = RISK_PER_TRADE / stop_pct
            vol_cap = 0.40 * min(1.0, 1.5 / vol)
            max_pos = max(0.05, min(risk_based, vol_cap))
            
            if vol > 3.0:
                risk = "HIGH"
            elif vol > 1.5:
                risk = "MEDIUM"
            else:
                risk = "LOW"
        else:
            risk = "MEDIUM"
            max_pos = 0.25
        
        return AgentMessage(
            role="risk_manager",
            signal="HOLD",
            confidence=0.8,
            reasoning=f"Vol={vol:.1f}%, max_pos={max_pos*100:.0f}%",
            max_position_size_pct=max_pos,
            risk_level=risk,
        )


class MockTrader:
    """Final decision maker."""
    def __init__(self, use_llm: bool = False):
        self.use_llm = use_llm
        self.name = "trader"

    def analyze(self, context: AnalysisContext) -> AgentMessage:
        # Get other agents' signals from context
        agent_msgs = context.agent_messages or []
        
        tech_msg = next((m for m in agent_msgs if m.role == "technical_analyst"), None)
        sent_msg = next((m for m in agent_msgs if m.role == "sentiment_analyst"), None)
        risk_msg = next((m for m in agent_msgs if m.role == "risk_manager"), None)
        
        if not all([tech_msg, sent_msg, risk_msg]):
            return AgentMessage(role="trader", signal="HOLD", confidence=0.5, reasoning="Missing agent signals")
        
        weights = {"technical_analyst": 0.4, "sentiment_analyst": 0.2, "risk_manager": 0.4}
        scores = {"BUY": 1, "HOLD": 0, "SELL": -1}
        
        weighted = 0
        total_w = 0
        for role, w in weights.items():
            msg = {"technical_analyst": tech_msg, "sentiment_analyst": sent_msg, "risk_manager": risk_msg}[role]
            weighted += scores.get(msg.signal, 0) * w * msg.confidence
            total_w += w * msg.confidence
        
        final_score = weighted / total_w if total_w > 0 else 0
        max_pos = risk_msg.max_position_size_pct or 0.25
        
        if self.use_llm:
            prompt = f"Tech: {tech_msg.signal}({tech_msg.confidence:.2f}), Sent: {sent_msg.signal}({sent_msg.confidence:.2f}), Risk: max_pos={max_pos*100:.0f}%. Final: BUY/SELL/HOLD?"
            resp = _llm_chat(prompt, max_tokens=20)
            signal = "BUY" if resp and "buy" in resp.lower() else ("SELL" if resp and "sell" in resp.lower() else "HOLD")
            conf = 0.7 if resp else 0.5
        else:
            signal = "BUY" if final_score > 0.2 else ("SELL" if final_score < -0.2 else "HOLD")
            conf = min(0.9, 0.5 + abs(final_score))
        
        return AgentMessage(
            role="trader",
            signal=signal,
            confidence=conf,
            reasoning=f"Weighted score: {final_score:.2f}, max_pos={max_pos*100:.0f}%",
        )


def run_comparison(days: int = 30, symbol: str = "BTC/USDT", timeframe: str = "1h", max_bars: int = 50):
    """Run both LLM and rule-based modes side by side."""
    
    print(f"Loading data for {symbol} {timeframe} (last {days} days)...")
    df = load_ohlcv("binance", symbol, timeframe)
    
    cutoff = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=days)
    df = df.filter(pl.col("timestamp") >= cutoff)
    
    print(f"Loaded {len(df)} bars")
    if len(df) < 60:
        print("Insufficient data")
        return
    
    # Limit bars for speed
    end_idx = min(len(df), 50 + max_bars)
    
    # Check if LLM is available (quick test)
    test_resp = _llm_chat("test", max_tokens=5)
    has_llm = test_resp is not None
    print(f"LLM available: {has_llm}")
    
    # Create agents for both modes
    agents_llm = {
        "tech": MockTechnicalAnalyst(use_llm=True),
        "sent": MockSentimentAnalyst(use_llm=True),
        "risk": MockRiskManager(use_llm=True),
        "trader": MockTrader(use_llm=True),
    }
    agents_rule = {
        "tech": MockTechnicalAnalyst(use_llm=False),
        "sent": MockSentimentAnalyst(use_llm=False),
        "risk": MockRiskManager(use_llm=False),
        "trader": MockTrader(use_llm=False),
    }
    
    results = {"llm": [], "rule": []}
    
    print(f"\nRunning comparison on {end_idx - 50} bars...")
    
    for i in range(50, end_idx):
        window = df.slice(0, i + 1)
        current_price = float(window["close"][-1])
        
        context = AnalysisContext(
            symbol=symbol,
            timeframe=timeframe,
            current_price=current_price,
            ohlcv=window,
            indicators={},
            current_position_pct=0.0,
            unrealized_pnl_pct=0.0,
            portfolio_value=10000.0,
        )
        
        # Run both modes
        for mode_name, agents in [("llm", agents_llm), ("rule", agents_rule)]:
            tech_msg = agents["tech"].analyze(context)
            sent_msg = agents["sent"].analyze(context)
            risk_msg = agents["risk"].analyze(context)
            
            # Inject into context for trader
            context.agent_messages = [tech_msg, sent_msg, risk_msg]
            
            trader_msg = agents["trader"].analyze(context)
            
            results[mode_name].append({
                "bar": i,
                "price": current_price,
                "tech": tech_msg.signal,
                "sent": sent_msg.signal,
                "risk": risk_msg.signal,
                "trader": trader_msg.signal,
                "trader_conf": trader_msg.confidence,
                "max_pos": risk_msg.max_position_size_pct,
            })
        
        if (i - 50) % 20 == 0:
            print(f"  Processed {i - 50}/{end_idx - 50} bars")
    
    # Analysis
    print("\n" + "=" * 70)
    print("COMPARISON RESULTS")
    print("=" * 70)
    
    llm_signals = [r["trader"] for r in results["llm"]]
    rule_signals = [r["trader"] for r in results["rule"]]
    llm_confs = [r["trader_conf"] for r in results["llm"]]
    rule_confs = [r["trader_conf"] for r in results["rule"]]
    
    print(f"\nBars analyzed: {len(llm_signals)}")
    
    for label, signals in [("LLM", llm_signals), ("Rule", rule_signals)]:
        buy = signals.count("BUY")
        sell = signals.count("SELL")
        hold = signals.count("HOLD")
        total = len(signals)
        print(f"  {label}: BUY={buy} ({buy/total*100:.1f}%), SELL={sell} ({sell/total*100:.1f}%), HOLD={hold} ({hold/total*100:.1f}%)")
    
    # Agreement
    agreements = sum(1 for l, r in zip(llm_signals, rule_signals) if l == r)
    print(f"\nSignal agreement: {agreements}/{len(llm_signals)} ({agreements/len(llm_signals)*100:.1f}%)")
    
    # Confidence
    print(f"Avg confidence - LLM: {sum(llm_confs)/len(llm_confs):.3f}, Rule: {sum(rule_confs)/len(rule_confs):.3f}")
    
    # Per-agent agreement
    for agent_key, agent_label in [("tech", "Technical"), ("sent", "Sentiment"), ("risk", "Risk")]:
        llm_a = [r[agent_key] for r in results["llm"]]
        rule_a = [r[agent_key] for r in results["rule"]]
        agr = sum(1 for l, r in zip(llm_a, rule_a) if l == r)
        print(f"  {agent_label} agreement: {agr}/{len(llm_a)} ({agr/len(llm_a)*100:.1f}%)")
    
    # Sample output
    print("\nFirst 15 bars:")
    print(f"{'Bar':>4} {'Price':>10} {'LLM':>6} {'LLM_C':>6} {'Rule':>6} {'Rule_C':>6} {'Match'}")
    for i in range(min(15, len(results["llm"]))):
        l = results["llm"][i]
        r = results["rule"][i]
        match = "✓" if l["trader"] == r["trader"] else "✗"
        print(f"{i:>4} {l['price']:>10.0f} {l['trader']:>6} {l['trader_conf']:>6.2f} {r['trader']:>6} {r['trader_conf']:>6.2f}   {match}")
    
    return results


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=30, help="Days of data to test")
    parser.add_argument("--symbol", default="BTC/USDT")
    parser.add_argument("--timeframe", default="1h")
    parser.add_argument("--max-bars", type=int, default=50, help="Max bars to test (default 50)")
    args = parser.parse_args()
    
    run_comparison(args.days, args.symbol, args.timeframe, args.max_bars)