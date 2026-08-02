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

        # 5. Build report
        return AgentAnalysisReport(
            symbol=symbol,
            timeframe=timeframe,
            current_price=current_price,
            agent_messages=messages,
            final_decision=final,
            indicators=self._extract_indicators(df),
        )

    def _compute_indicators(self, df: pl.DataFrame) -> pl.DataFrame:
        """Compute all indicators using existing strategies."""
        try:
            df = MaCrossover({"fast_period": 20, "slow_period": 50}).compute_indicators(df)
        except Exception:
            pass
        try:
            df = RsiStrategy({"period": 14}).compute_indicators(df)
        except Exception:
            pass
        try:
            df = BBandsStrategy({"period": 20, "std_dev": 2.0}).compute_indicators(df)
        except Exception:
            pass
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
