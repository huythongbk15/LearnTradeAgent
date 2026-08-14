#!/usr/bin/env python3
"""
Test Phase 2: Multi-Agent System

Chạy test cho từng component trong Phase 2:
  1. LLM client (chat completion)
  2. Technical Analyst
  3. Sentiment Analyst
  4. Risk Manager
  5. Trader (weighted voting)
  6. Orchestrator (full cycle)

Usage:
    USE_LLM=false python scripts/test_phase2.py   # rule-based only (fast)
    python scripts/test_phase2.py                 # full LLM (slower)
"""

import os
import time
from datetime import datetime, timedelta
from pathlib import Path

import pytest

# Integration test này cần data thật (không có trên CI runner).
# Thiếu data local → skip cả module.
_DATA_FILE = (
    Path(__file__).resolve().parents[1]
    / "data"
    / "raw"
    / "binance"
    / "BTC_USDT"
    / "1h.parquet"
)
if not _DATA_FILE.exists():
    pytest.skip(
        f"Bỏ qua integration test: thiếu data local {_DATA_FILE}",
        allow_module_level=True,
    )

# ── Imports ──────────────────────────────────────────────────────────────

from trading_agent.agents.base import AgentMessage, AnalysisContext
from trading_agent.agents.orchestrator import Orchestrator, print_report
from trading_agent.agents.risk import RiskManager
from trading_agent.agents.sentiment import SentimentAnalyst
from trading_agent.agents.technical import TechnicalAnalyst
from trading_agent.agents.trader import Trader
from trading_agent.config.loader import config

USE_LLM = os.getenv("USE_LLM", "true").lower() != "false"

pass_count = 0
fail_count = 0


def heading(text):
    print(f"\n{'=' * 60}")
    print(f"  {text}")
    print(f"{'=' * 60}")


def check(name, condition, detail=""):
    global pass_count, fail_count
    if condition:
        pass_count += 1
        print(f"  ✅ {name}")
    else:
        fail_count += 1
        print(f"  ❌ {name} — {detail}")


# ══════════════════════════════════════════════════════════════════════════
#  1. LLM Client Test
# ══════════════════════════════════════════════════════════════════════════
heading("1. LLM Client")

from trading_agent.agents.llm import LLMError, ask_agent, chat

if USE_LLM:
    try:
        resp = chat(
            [{"role": "user", "content": "Say 'LLM_OK' and nothing else."}],
            max_tokens=10,
        )
        check("chat() returns response", bool(resp.content))
        check(f"chat() provider={resp.provider}", bool(resp.provider))
        print(f"     → {resp.content[:50]} | tokens={resp.tokens_used}")
    except LLMError as e:
        check("chat() works", False, str(e))
else:
    try:
        resp = chat([{"role": "user", "content": "hi"}], max_tokens=5)
        check("chat() raises when USE_LLM=false", False, "should have raised")
    except LLMError:
        check("chat() raises LLMError when USE_LLM=false", True)

result = ask_agent("test", "output JSON with signal=BUY")
check("ask_agent returns dict", isinstance(result, dict))
check("ask_agent has signal key", "signal" in result)
print(f"     → {result}")


# ══════════════════════════════════════════════════════════════════════════
#  2. Build mock context (for agent tests)
# ══════════════════════════════════════════════════════════════════════════
heading("2. Build test context")

import numpy as np
import polars as pl

# Create synthetic OHLCV data
np.random.seed(42)
n = 100
base = 60000.0
closes = base + np.cumsum(np.random.randn(n) * 100)
opens = closes + np.random.randn(n) * 20
highs = np.maximum(opens, closes) + abs(np.random.randn(n) * 30)
lows = np.minimum(opens, closes) - abs(np.random.randn(n) * 30)
volumes = np.random.rand(n) * 1000 + 500

# Build timestamps
start_ts = datetime(2026, 7, 1, 0, 0, 0)
timestamps = [start_ts]
for i in range(1, n):
    timestamps.append(timestamps[-1] + timedelta(hours=1))

df = pl.DataFrame(
    {
        "timestamp": timestamps,
        "open": opens,
        "high": highs,
        "low": lows,
        "close": closes,
        "volume": volumes,
    }
)

# Compute basic indicators
df = df.with_columns(
    [
        pl.col("close").rolling_mean(window_size=5).alias("ma_5"),
        pl.col("close").rolling_mean(window_size=10).alias("ma_10"),
        pl.col("close").rolling_mean(window_size=20).alias("ma_20"),
    ]
)
# Fake RSI
rsi_vals = 50 + np.cumsum(np.random.randn(n) * 5)
rsi_vals = np.clip(rsi_vals, 0, 100)
df = df.with_columns(pl.Series("rsi", rsi_vals))
# Fake BBands
df = df.with_columns(
    [
        (
            pl.col("close").rolling_mean(window_size=20)
            + 2 * pl.col("close").rolling_std(window_size=20)
        ).alias("bb_upper"),
        (
            pl.col("close").rolling_mean(window_size=20)
            - 2 * pl.col("close").rolling_std(window_size=20)
        ).alias("bb_lower"),
        pl.col("close").rolling_mean(window_size=20).alias("bb_mid"),
    ]
)

current_price = float(df["close"].tail(1).item())

indicators = {
    "ma_5": float(df["ma_5"].tail(1).item()),
    "ma_10": float(df["ma_10"].tail(1).item()),
    "ma_20": float(df["ma_20"].tail(1).item()),
    "rsi": float(df["rsi"].tail(1).item()),
    "bb_upper": float(df["bb_upper"].tail(1).item()),
    "bb_lower": float(df["bb_lower"].tail(1).item()),
}

# Extra indicators (same as orchestrator._compute_extra)
returns_20 = np.diff(closes[-21:]) / closes[-21:-1]
extra = {
    "price_now": current_price,
    "change_5": float((closes[-1] / closes[-5] - 1) * 100),
    "change_20": float((closes[-1] / closes[-21] - 1) * 100),
    "volatility_20": float(np.std(returns_20) * 100),
    "volume_ratio_5_20": float(volumes[-5:].mean() / volumes[-20:].mean())
    if volumes[-20:].mean() > 0
    else 1.0,
}
indicators["_extra"] = extra

ctx = AnalysisContext(
    symbol="BTC/USDT",
    timeframe="1h",
    current_price=current_price,
    ohlcv=df,
    indicators=indicators,
    current_position_pct=0.0,
    portfolio_value=10000.0,
    price_change_1d=float((closes[-1] / closes[-25] - 1) * 100)
    if len(closes) > 25
    else 0,
    price_change_1w=float((closes[-1] / closes[-169] - 1) * 100)
    if len(closes) > 169
    else 0,
    price_change_1m=None,
)

check("Context built with {} indicators".format(len(indicators)), len(indicators) >= 4)
check(f"Current price = ${current_price:.2f}", current_price > 0)
print(f"  RSI: {indicators['rsi']:.1f} | Vol: {extra['volatility_20']:.2f}%")
print(f"  MA5: ${indicators['ma_5']:.0f} | MA10: ${indicators['ma_10']:.0f}")


# ══════════════════════════════════════════════════════════════════════════
#  3. Technical Analyst
# ══════════════════════════════════════════════════════════════════════════
heading("3. Technical Analyst")

tech = TechnicalAnalyst()
t0 = time.time()
tech_msg = tech.analyze(ctx)
t_tech = time.time() - t0

check("returns AgentMessage", isinstance(tech_msg, AgentMessage))
check("signal in (BUY, SELL, HOLD)", tech_msg.signal in ("BUY", "SELL", "HOLD"))
check("confidence 0.0-1.0", 0 <= tech_msg.confidence <= 1.0)
check("has reasoning", len(tech_msg.reasoning) > 5)
check("has details", bool(tech_msg.details))
print(f"  Signal: {tech_msg.signal} (conf={tech_msg.confidence:.0%})")
print(f"  Reason: {tech_msg.reasoning}")
print(
    f"  Details: trend={tech_msg.details.get('trend')}, momentum={tech_msg.details.get('momentum')}"
)
print(f"  ⏱ {t_tech:.2f}s")


# ══════════════════════════════════════════════════════════════════════════
#  4. Sentiment Analyst
# ══════════════════════════════════════════════════════════════════════════
heading("4. Sentiment Analyst")

sent = SentimentAnalyst()
ctx.agent_messages = [tech_msg]  # provide technical signal as context
t0 = time.time()
sent_msg = sent.analyze(ctx)
t_sent = time.time() - t0

check("returns AgentMessage", isinstance(sent_msg, AgentMessage))
check("signal valid", sent_msg.signal in ("BUY", "SELL", "HOLD"))
check("confidence valid", 0 <= sent_msg.confidence <= 1.0)
check("has sentiment in details", "sentiment" in sent_msg.details)
print(f"  Signal: {sent_msg.signal} (conf={sent_msg.confidence:.0%})")
print(f"  Sentiment: {sent_msg.details.get('sentiment')}")
print(f"  Reason: {sent_msg.reasoning}")
print(f"  ⏱ {t_sent:.2f}s")


# ══════════════════════════════════════════════════════════════════════════
#  5. Risk Manager
# ══════════════════════════════════════════════════════════════════════════
heading("5. Risk Manager")

risk = RiskManager()
ctx.agent_messages = [tech_msg, sent_msg]
t0 = time.time()
risk_msg = risk.analyze(ctx)
t_risk = time.time() - t0

check("returns AgentMessage", isinstance(risk_msg, AgentMessage))
check("signal valid", risk_msg.signal in ("BUY", "SELL", "HOLD"))
check("risk_level defined", risk_msg.risk_level in ("LOW", "MEDIUM", "HIGH", "EXTREME"))
check(
    "max_position_size_pct is 0-1",
    risk_msg.max_position_size_pct is None or 0 <= risk_msg.max_position_size_pct <= 1,
)
print(f"  Signal: {risk_msg.signal} (conf={risk_msg.confidence:.0%})")
print(f"  Risk: {risk_msg.risk_level}")
print(f"  Max Position: {risk_msg.max_position_size_pct}")
print(f"  Reason: {risk_msg.reasoning}")
print(f"  Warnings: {risk_msg.warnings}")
print(f"  ⏱ {t_risk:.2f}s")


# ══════════════════════════════════════════════════════════════════════════
#  6. Trader (weighted voting)
# ══════════════════════════════════════════════════════════════════════════
heading("6. Trader — Weighted Voting")

trader = Trader()
all_msgs = [tech_msg, sent_msg, risk_msg]
ctx.agent_messages = all_msgs

for m in all_msgs:
    print(
        f"  Input: [{m.role}] {m.signal} (conf={m.confidence:.0%})"
        + (f" risk={m.risk_level}" if m.risk_level else "")
    )

t0 = time.time()
final = trader.analyze(ctx)
t_trader = time.time() - t0

check("returns AgentMessage", isinstance(final, AgentMessage))
check("signal valid", final.signal in ("BUY", "SELL", "HOLD"))
check("confidence valid", 0 <= final.confidence <= 1.0)
check("has reasoning", len(final.reasoning) > 5)
check("details contains agent_signals", "agent_signals" in final.details)
print(
    f"  Final Signal: {final.signal} (conf={final.confidence:.0%}, risk={final.risk_level})"
)
print(f"  Weighted score: {final.details.get('weighted_score')}")
print(f"  Reason: {final.reasoning}")
print(f"  ⏱ {t_trader:.2f}s")


# ══════════════════════════════════════════════════════════════════════════
#  7. Orchestrator — Full Cycle
# ══════════════════════════════════════════════════════════════════════════
heading("7. Orchestrator — Full Multi-Agent Cycle")

t_start = time.time()

orch = Orchestrator()
t0 = time.time()
try:
    report = orch.analyze(
        symbol="BTC/USDT",
        timeframe="1h",
        current_position_pct=0.0,
        portfolio_value=10000.0,
    )
    t_orch = time.time() - t0

    check("returns AgentAnalysisReport", report is not None)
    check("has final_decision", bool(report.final_decision))
    check("final signal valid", report.final_decision.signal in ("BUY", "SELL", "HOLD"))
    check(
        "has 4 agent messages (tech + sentiment + risk + trader)",
        len(report.agent_messages) == 4,
    )

    print(
        f"\n  🎯 Final: {report.final_decision.signal} (conf={report.final_decision.confidence:.0%})"
    )
    print(f"  ⏱ {t_orch:.2f}s")
    for msg in report.agent_messages:
        print(f"    [{msg.role:20s}] {msg.signal:4s}  conf={msg.confidence:.0%}")

    # Pretty print
    print("\n  ── Pretty Report ──")
    print_report(report)

except Exception as e:
    t_orch = time.time() - t0
    check("Orchestrator runs without error", False, str(e))
    import traceback

    traceback.print_exc()


# ══════════════════════════════════════════════════════════════════════════
#  Summary
# ══════════════════════════════════════════════════════════════════════════
heading("SUMMARY")
print(f"  ✅ Passed: {pass_count}")
print(f"  ❌ Failed: {fail_count}")
print(f"  ⏱  Total: {time.time() - t_start:.1f}s" if fail_count == 0 else "")
print(f"\n{'=' * 60}")

if USE_LLM:
    print(f"  Mode: FULL LLM (using {config.llm_provider}/{config.llm_model})")
else:
    print("  Mode: RULE-BASED (USE_LLM=false)")

if fail_count == 0:
    print("  ✅ ALL TESTS PASSED\n")
else:
    print(f"  ❌ {fail_count} TEST(S) FAILED\n")
    # Don't sys.exit here - let pytest handle it
    raise AssertionError(f"{fail_count} tests failed")
