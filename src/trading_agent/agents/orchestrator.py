"""
Orchestrator — chạy multi-agent system, collect tín hiệu, in kết quả.

Flow:
1. Load market data + indicators
2. Build AnalysisContext
3. Run Technical Analyst → Sentiment Analyst → Risk Manager → Trader
4. Weighted voting + risk override → final decision
5. Print report
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import polars as pl
from rich.console import Console
from rich.panel import Panel
from rich.table import Table as RichTable
from rich.tree import Tree

from trading_agent.agents.base import AgentMessage, AnalysisContext
from trading_agent.agents.risk import RiskManager
from trading_agent.agents.sentiment import SentimentAnalyst
from trading_agent.agents.technical import TechnicalAnalyst
from trading_agent.agents.trader import Trader
from trading_agent.config.loader import config
from trading_agent.data.storage import load_ohlcv
from trading_agent.log_config import get_logger
from trading_agent.strategies.bbands import BBandsStrategy
from trading_agent.strategies.ma_crossover import MaCrossover
from trading_agent.strategies.rsi import RsiStrategy
from trading_agent.risk.position_sizer import PositionSizer, PositionSizingParams

logger = get_logger(__name__)


def _log_agent_decision(
    symbol: str,
    timeframe: str,
    agent_name: str,
    msg: AgentMessage,
    price: float | None = None,
):
    """Save agent decision to SQLite."""
    try:
        from trading_agent.monitoring.database import init_db, save_agent_decision
        init_db()
        save_agent_decision(
            symbol=symbol,
            agent_name=agent_name,
            signal=msg.signal,
            confidence=msg.confidence,
            reasoning=msg.reasoning or "",
            price=price,
            timeframe=timeframe,
            metadata={k: str(v) for k, v in (msg.details or {}).items() if v is not None},
        )
    except Exception as e:
        logger.warning("Failed to log agent decision: %s", e)
console = Console()


@dataclass
class AgentAnalysisReport:
    """Full analysis report from the multi-agent system."""

    symbol: str
    timeframe: str
    current_price: float
    agent_messages: list[AgentMessage]
    final_decision: AgentMessage
    indicators: dict[str, Any]


class Orchestrator:
    """Orchestrates the multi-agent analysis cycle."""

    def __init__(self):
        self.technical = TechnicalAnalyst()
        self.sentiment = SentimentAnalyst()
        self.risk = RiskManager()
        self.trader = Trader()
        self._last_df: pl.DataFrame | None = None  # cached after analyze
        
        # Ensemble weights for agent signals
        self.ensemble_weights = {
            "technical_analyst": 0.40,
            "sentiment_analyst": 0.20,
            "risk_manager": 0.20,
            "trader": 0.20,
        }
        
        # Dynamic position sizer
        self.position_sizer = PositionSizer(PositionSizingParams(
            method="half_kelly",
            kelly_fraction=0.5,
            target_annual_vol=0.15,
            max_position_pct=0.25,
            max_portfolio_heat=0.8,
        ))

    def analyze(
        self,
        symbol: str = "BTC/USDT",
        timeframe: str = "1h",
        *,
        current_position_pct: float = 0.0,
        portfolio_value: float = 10000.0,
        df: pl.DataFrame | None = None,
    ) -> AgentAnalysisReport:
        """Run the full multi-agent analysis cycle.

        Parameters
        ----------
        df : pl.DataFrame | None
            Optional pre-loaded OHLCV window (thường là dữ liệu tới hiện tại,
            dùng cho backtest/simulation). Nếu None, tự load mới (production).
        """

        # 1. Load data
        if df is None:
            df = load_ohlcv(config.default_exchange, symbol, timeframe).sort("timestamp")
        else:
            df = df.sort("timestamp")
        self._last_df = df  # cache for downstream (e.g. execution)

        # 2. Compute indicators (using existing strategies)
        df = self._compute_indicators(df)

        # 3. Build context
        current_price = float(df["close"].tail(1).item())
        context = self._build_context(
            df, symbol, timeframe, current_price,
            current_position_pct, portfolio_value,
        )

        # 4. Run agents in order
        messages: list[AgentMessage] = []

        # Technical Analyst
        logger.info("Running Technical Analyst...")
        tech_msg = self.technical.analyze(context)
        _log_agent_decision(symbol, timeframe, "technical_analyst", tech_msg, current_price)
        messages.append(tech_msg)

        # Sentiment Analyst (gets technical output for reference)
        context.agent_messages = messages
        logger.info("Running Sentiment Analyst...")
        sent_msg = self.sentiment.analyze(context)
        _log_agent_decision(symbol, timeframe, "sentiment_analyst", sent_msg, current_price)
        messages.append(sent_msg)

        # Risk Manager
        context.agent_messages = messages
        logger.info("Running Risk Manager...")
        risk_msg = self.risk.analyze(context)
        _log_agent_decision(symbol, timeframe, "risk_manager", risk_msg, current_price)
        messages.append(risk_msg)

        # Trader (final decision)
        context.agent_messages = messages
        logger.info("Running Trader...")
        final = self.trader.analyze(context)
        _log_agent_decision(symbol, timeframe, "trader", final, current_price)

        # 5. Ensemble voting: combine agent signals with dynamic weights
        ensemble_decision = self._ensemble_vote(messages, context)
        
        # 6. Dynamic position sizing based on strategy performance
        position_size_pct = self._calculate_dynamic_position_size(
            context, ensemble_decision, df
        )
        
        # Override final decision with ensemble + position sizing
        final = AgentMessage(
            role="trader",
            signal=ensemble_decision["signal"],
            confidence=ensemble_decision["confidence"],
            reasoning=ensemble_decision["reasoning"],
            details={
                **ensemble_decision.get("details", {}),
                "ensemble": True,
                "agent_weights": self.ensemble_weights,
                "max_position_size_pct": position_size_pct,
                "regime": ensemble_decision.get("regime", {}),
            },
            max_position_size_pct=position_size_pct,
            risk_level=ensemble_decision.get("risk_level", "medium"),
        )
        _log_agent_decision(symbol, timeframe, "trader", final, current_price)

        # 7. Build report
        return AgentAnalysisReport(
            symbol=symbol,
            timeframe=timeframe,
            current_price=current_price,
            agent_messages=messages,
            final_decision=final,
            indicators=self._extract_indicators(df),
        )

    def _ensemble_vote(self, messages: list[AgentMessage], context: AnalysisContext) -> dict:
        """Combine agent signals using weighted voting with regime awareness.
        
        Simplified: Trust the trader agent as final decision maker, use others as confirmation.
        """
        # Get regime from technical analyst
        tech_msg = next((m for m in messages if m.role == "technical_analyst"), None)
        regime = tech_msg.details.get("regime", {}) if tech_msg else {}
        trend_regime = regime.get("trend_regime", "ranging")
        vol_regime = regime.get("vol_regime", "mid_vol")
        trend_dir = regime.get("trend_dir", "up")
        adx = regime.get("adx")
        
        # Get trader's decision (final say)
        trader_msg = next((m for m in messages if m.role == "trader"), None)
        if trader_msg:
            base_signal = trader_msg.signal
            base_confidence = trader_msg.confidence
        else:
            # Fallback to technical
            tech_msg = next((m for m in messages if m.role == "technical_analyst"), None)
            base_signal = tech_msg.signal if tech_msg else "HOLD"
            base_confidence = tech_msg.confidence if tech_msg else 0.3
        
        # Apply regime filter - only override if strong regime disagreement
        reasons = []
        final_signal = base_signal
        confidence = base_confidence
        
        # In high vol ranging, be more conservative
        if vol_regime == "high_vol" and trend_regime == "ranging":
            if base_signal in ("BUY", "SELL"):
                # Reduce confidence, maybe flip to HOLD if weak
                if base_confidence < 0.5:
                    final_signal = "HOLD"
                    confidence = 0.4
                    reasons.append("High vol ranging — weak signal downgraded to HOLD")
                else:
                    confidence = base_confidence * 0.8
                    reasons.append("High vol ranging — confidence reduced")
        
        # In trending, boost trend-aligned signals
        elif trend_regime == "trending" and adx and adx > 25:
            if base_signal == "BUY" and trend_dir == "up":
                confidence = min(base_confidence * 1.2, 0.8)
                reasons.append(f"Trending up (ADX {adx:.0f}) — BUY boosted")
            elif base_signal == "SELL" and trend_dir == "down":
                confidence = min(base_confidence * 1.2, 0.8)
                reasons.append(f"Trending down (ADX {adx:.0f}) — SELL boosted")
            elif base_signal in ("BUY", "SELL"):
                # Counter-trend signal - reduce confidence
                confidence = base_confidence * 0.7
                reasons.append("Counter-trend signal — confidence reduced")
        
        # Details
        details = {
            "agent_votes": {m.role: {"signal": m.signal, "confidence": m.confidence} for m in messages},
            "regime": regime,
        }
        
        return {
            "signal": final_signal,
            "confidence": confidence,
            "reasoning": "Ensemble: " + " | ".join(reasons) if reasons else f"Trader: {base_signal} ({base_confidence:.0%})",
            "details": details,
            "risk_level": "high" if vol_regime == "high_vol" else "medium",
            "regime": regime,
        }

    def _calculate_dynamic_position_size(
        self, context: AnalysisContext, ensemble_decision: dict, df: pl.DataFrame
    ) -> float:
        """Calculate position size using volatility targeting (no Kelly - needs trade history)."""
        regime = ensemble_decision.get("regime", {})
        trend_regime = regime.get("trend_regime", "ranging")
        vol_regime = regime.get("vol_regime", "mid_vol")
        
        # Base position size: volatility targeting
        extra = context.indicators.get("_extra", {})
        realized_vol = extra.get("volatility_20", 50) / 100  # Daily vol as decimal
        
        # Target 15% annual vol = ~1% daily (15% / sqrt(252))
        target_daily_vol = 0.15 / (252 ** 0.5)
        
        if realized_vol > 0:
            vol_scale = min(target_daily_vol / realized_vol, 2.0)
        else:
            vol_scale = 1.0
        
        # Base size 20% adjusted by vol
        base_size = 0.20 * vol_scale
        
        # Regime adjustments
        if trend_regime == "trending":
            base_size *= 1.3  # More conviction in trends
        elif trend_regime == "ranging":
            base_size *= 0.7  # Less in choppy markets
        
        if vol_regime == "high_vol":
            base_size *= 0.6
        elif vol_regime == "low_vol":
            base_size *= 1.2
        
        # Signal confidence adjustment
        confidence = ensemble_decision.get("confidence", 0.5)
        base_size *= confidence
        
        # Cap at max position
        max_pos = 0.40  # 40% max per position
        position_size_pct = min(base_size, max_pos)
        
        # Minimum threshold
        if position_size_pct < 0.05:
            position_size_pct = 0.05
        
        return round(position_size_pct, 4)

    def _compute_indicators(self, df: pl.DataFrame) -> pl.DataFrame:
        """Compute all indicators using existing strategies."""
        try:
            df = MaCrossover({"fast_period": 20, "slow_period": 50}).compute_indicators(df)
        except Exception as e:
            logger.warning(f"MA crossover indicators failed: {e}")
        try:
            df = RsiStrategy({"period": 14}).compute_indicators(df)
        except Exception as e:
            logger.warning(f"RSI indicators failed: {e}")
        try:
            df = BBandsStrategy({"period": 20, "std_dev": 2.0}).compute_indicators(df)
        except Exception as e:
            logger.warning(f"BBands indicators failed: {e}")
        return df

    def _build_context(
        self,
        df: pl.DataFrame,
        symbol: str,
        timeframe: str,
        current_price: float,
        current_position_pct: float,
        portfolio_value: float,
    ) -> AnalysisContext:
        """Build AnalysisContext from loaded data."""
        indicators = self._extract_indicators(df)
        extra = self._compute_extra(df)

        # Price changes
        closes = df["close"].to_numpy()
        price_change_1d = None
        price_change_1w = None
        price_change_1m = None

        if len(closes) > 24:
            price_change_1d = float((closes[-1] / closes[-25] - 1) * 100)
        if len(closes) > 168:
            price_change_1w = float((closes[-1] / closes[-169] - 1) * 100)
        if len(closes) > 720:
            price_change_1m = float((closes[-1] / closes[-721] - 1) * 100)

        return AnalysisContext(
            symbol=symbol,
            timeframe=timeframe,
            current_price=current_price,
            ohlcv=df,
            indicators={**indicators, "_extra": extra},
            current_position_pct=current_position_pct,
            portfolio_value=portfolio_value,
            price_change_1d=price_change_1d,
            price_change_1w=price_change_1w,
            price_change_1m=price_change_1m,
        )

    def _extract_indicators(self, df: pl.DataFrame) -> dict[str, Any]:
        """Extract latest indicator values from DataFrame."""
        ind = {}
        if df.is_empty():
            return ind

        last = df.tail(1)

        # MAs
        for col in df.columns:
            if col.startswith("ma_"):
                val = last[col].item()
                if val is not None:
                    ind[col] = float(val)

        # RSI
        if "rsi" in df.columns:
            val = last["rsi"].item()
            if val is not None:
                ind["rsi"] = float(val)

        # BBands
        for col in ["bb_upper", "bb_lower", "bb_mid"]:
            if col in df.columns:
                val = last[col].item()
                if val is not None:
                    ind[col] = float(val)

        return ind

    def _compute_extra(self, df: pl.DataFrame) -> dict[str, Any]:
        """Compute extra contextual indicators."""
        extra = {}
        closes = df["close"].to_numpy()
        if len(closes) < 20:
            return extra

        # Price changes
        extra["price_now"] = float(closes[-1])
        extra["change_5"] = float((closes[-1] / closes[-5] - 1) * 100)
        extra["change_20"] = float((closes[-1] / closes[-21] - 1) * 100)

        # Volatility
        import numpy as np
        returns_20 = np.diff(closes[-21:]) / closes[-21:-1]
        extra["volatility_20"] = float(np.std(returns_20) * 100)

        # Volume
        if "volume" in df.columns:
            vols = df["volume"].to_numpy()
            if len(vols) > 20:
                avg_20 = float(vols[-20:].mean())
                avg_5 = float(vols[-5:].mean())
                extra["volume_ratio_5_20"] = float(avg_5 / avg_20) if avg_20 > 0 else 1.0

        return extra


# ── Pretty printing ──────────────────────────────────────────────────────


def print_report(report: AgentAnalysisReport):
    """Pretty-print the full multi-agent analysis report."""
    decision = report.final_decision

    # Header
    signal_color = {
        "BUY": "green",
        "SELL": "red",
        "HOLD": "yellow",
    }.get(decision.signal, "white")

    console.print(f"\n[bold]Multi-Agent Analysis[/bold] — "
                  f"{report.symbol} ({report.timeframe})")
    console.print(f"Price: [bold]${report.current_price:,.2f}[/bold]")
    console.print(f"Final Signal: [{signal_color}]{'🟢' if decision.signal == 'BUY' else '🔴' if decision.signal == 'SELL' else '🟡'} {decision.signal}[/{signal_color}]  "
                  f"(confidence: {decision.confidence:.0%}, "
                  f"risk: {decision.risk_level})")
    if decision.max_position_size_pct and decision.max_position_size_pct < 1.0:
        console.print(f"Max Position: [bold]{decision.max_position_size_pct * 100:.0f}%[/bold]")

    # Agent tree
    tree = Tree("🧠 [bold]Agent Decisions[/bold]")
    for msg in report.agent_messages:
        color = "green" if msg.signal == "BUY" else "red" if msg.signal == "SELL" else "yellow"
        branch = tree.add(f"[bold]{msg.role}[/bold] — [{color}]{msg.signal}[/{color}] "
                          f"(conf: {msg.confidence:.0%})")
        branch.add(f"[dim]{msg.reasoning}[/dim]")
        if msg.details:
            for k, v in msg.details.items():
                if k not in ("key_levels", "agent_signals") and v:
                    branch.add(f"[dim]{k}: {v}[/dim]")

    console.print(tree)

    # Indicators
    ind = report.indicators
    if ind:
        t = RichTable("Indicator", "Value", title="📊 Key Indicators", show_header=False)
        for k, v in sorted(ind.items()):
            if isinstance(v, (int, float)):
                t.add_row(k, f"{v:.2f}")
        console.print(Panel(t, border_style="blue"))

    # Full reasoning
    console.print(Panel(
        decision.reasoning,
        title="🧾 Reasoning",
        border_style="cyan",
    ))

    # Warnings
    if decision.warnings:
        console.print("[bold red]⚠ Warnings:[/bold red]")
        for w in decision.warnings:
            console.print(f"  • {w}")
