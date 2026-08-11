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

import numpy as np
from dataclasses import dataclass
from typing import Any, Literal

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
from trading_agent.data.onchain import fetch_funding_rate, fetch_open_interest, fetch_recent_trades_pressure
from trading_agent.data.storage import load_ohlcv
from trading_agent.log_config import get_logger
from trading_agent.strategies.bbands import BBandsStrategy
from trading_agent.strategies.ma_crossover import MaCrossover
from trading_agent.strategies.rsi import RsiStrategy
from trading_agent.risk.position_sizer import PositionSizer, PositionSizingParams

logger = get_logger(__name__)


# ─── Ablation Harness ─────────────────────────────────────────────────────

class AblationConfig:
    """Configuration for agent ablation experiments.
    
    A/B/C/D systematic toggle:
    - A: All agents (baseline)
    - B: Technical + Risk (no Sentiment)
    - C: Technical + Sentiment (no Risk override)
    - D: Technical only (no Sentiment, no Risk)
    """
    
    PRESETS = {
        "A": {"technical": True, "sentiment": True, "risk": True, "risk_override": True},
        "B": {"technical": True, "sentiment": False, "risk": True, "risk_override": True},
        "C": {"technical": True, "sentiment": True, "risk": True, "risk_override": False},
        "D": {"technical": True, "sentiment": False, "risk": False, "risk_override": False},
    }
    
    def __init__(self, preset: Literal["A", "B", "C", "D"] | dict = "A"):
        if isinstance(preset, str):
            resolved = preset if preset in self.PRESETS else "A"
            self.config = self.PRESETS[resolved]
            self.preset_name = resolved
        else:
            self.config = preset
            self.preset_name = "custom"
    
    def should_run(self, agent: str) -> bool:
        return self.config.get(agent, True)
    
    def should_override_risk(self) -> bool:
        return self.config.get("risk_override", True)


class AgentCorrelationTracker:
    """Track rolling correlation between agent signals for diversification discount."""
    
    def __init__(self, window: int = 50):
        self.window = window
        self.signal_history: dict[str, list[float]] = {
            "technical_analyst": [],
            "sentiment_analyst": [],
            "risk_manager": [],
        }
    
    def update(self, messages: list[AgentMessage]) -> None:
        """Add new signals to history."""
        for msg in messages:
            if msg.role in self.signal_history:
                signal_val = {"BUY": 1.0, "SELL": -1.0, "HOLD": 0.0}.get(msg.signal, 0.0)
                self.signal_history[msg.role].append(signal_val * msg.confidence)
                # Keep only window
                if len(self.signal_history[msg.role]) > self.window:
                    self.signal_history[msg.role].pop(0)
    
    def get_correlation_matrix(self) -> np.ndarray | None:
        """Compute rolling correlation matrix between agent signals.
        
        Returns 3x3 matrix or None if insufficient data.
        """
        # Check if all agents have enough history
        if any(len(v) < 10 for v in self.signal_history.values()):
            return None
        
        # Align to shortest length
        min_len = min(len(v) for v in self.signal_history.values())
        if min_len < 10:
            return None
        
        signals = np.array([
            self.signal_history["technical_analyst"][-min_len:],
            self.signal_history["sentiment_analyst"][-min_len:],
            self.signal_history["risk_manager"][-min_len:],
        ])
        
        # Compute correlation
        try:
            with np.errstate(invalid="ignore", divide="ignore"):
                corr = np.corrcoef(signals)
        except Exception:
            return None

        # Constant series (zero variance) make corrcoef return NaN even when
        # agents are perfectly correlated (or anti-correlated).  Substitute
        # ±1.0 from the sign of the constant signal instead of leaving NaN,
        # which previously disabled the diversification discount entirely.
        if not np.isfinite(corr).all():
            for i in range(3):
                if np.std(signals[i]) == 0.0:
                    for j in range(3):
                        if np.std(signals[j]) == 0.0:
                            corr[i, j] = (
                                1.0
                                if (signals[i][0] >= 0) == (signals[j][0] >= 0)
                                else -1.0
                            )
            corr = np.nan_to_num(corr, nan=0.0)
            np.fill_diagonal(corr, 1.0)
        return corr
    
    def get_diversification_discount(self) -> float:
        """Calculate diversification discount based on signal correlations.
        
        High correlation = low diversification = higher discount (reduce weight)
        Low correlation = high diversification = lower discount (keep weight)
        
        Returns discount factor in [0.5, 1.0] to apply to ensemble weights.
        """
        corr = self.get_correlation_matrix()
        if corr is None:
            return 1.0  # No discount if insufficient data
        
        # Average off-diagonal correlation
        off_diag = (corr[0,1] + corr[0,2] + corr[1,2]) / 3
        
        # Discount: 1.0 at corr=0, 0.5 at corr=1
        discount = 1.0 - 0.5 * max(0, off_diag)
        return max(0.5, min(1.0, discount))
    
    def get_per_agent_correlation(self) -> dict[str, float]:
        """Get each agent's average correlation with others."""
        corr = self.get_correlation_matrix()
        if corr is None:
            return {"technical_analyst": 0, "sentiment_analyst": 0, "risk_manager": 0}
        
        return {
            "technical_analyst": (corr[0,1] + corr[0,2]) / 2,
            "sentiment_analyst": (corr[1,0] + corr[1,2]) / 2,
            "risk_manager": (corr[2,0] + corr[2,1]) / 2,
        }


class PerAgentPnLTracker:
    """Track PnL attribution per agent for ablation analysis."""
    
    def __init__(self):
        self.trades: list[dict] = []
        self.agent_signals_at_entry: list[dict] = []
    
    def record_entry(self, messages: list[AgentMessage], price: float, position_pct: float) -> None:
        """Record agent signals at entry for later attribution."""
        signals = {m.role: {"signal": m.signal, "confidence": m.confidence} for m in messages}
        self.agent_signals_at_entry.append({
            "price": price,
            "position_pct": position_pct,
            "signals": signals,
        })
    
    def record_exit(self, entry_price: float, exit_price: float, position_pct: float, 
                    portfolio_value: float) -> dict:
        """Record exit and compute per-agent PnL attribution."""
        if not self.agent_signals_at_entry:
            return {}
        
        entry = self.agent_signals_at_entry.pop(0)
        pnl_pct = (exit_price / entry_price - 1) * 100
        pnl_usd = portfolio_value * position_pct * pnl_pct / 100
        
        # Attribute PnL to each agent based on their signal alignment
        attribution = {}
        for role, sig in entry["signals"].items():
            signal_val = {"BUY": 1.0, "SELL": -1.0, "HOLD": 0.0}.get(sig["signal"], 0.0)
            # If signal aligns with trade direction, credit the agent
            trade_direction = 1.0 if exit_price > entry_price else -1.0
            alignment = signal_val * trade_direction
            weight = sig["confidence"] * max(0, alignment)  # Only positive alignment
            attribution[role] = weight
        
        # Normalize weights
        total = sum(attribution.values()) or 1
        for k in attribution:
            attribution[k] = attribution[k] / total * pnl_usd
        
        self.trades.append({
            "entry_price": entry_price,
            "exit_price": exit_price,
            "pnl_pct": pnl_pct,
            "pnl_usd": pnl_usd,
            "attribution": attribution,
        })
        
        return attribution
    
    def get_attribution_summary(self) -> dict:
        """Get cumulative PnL attribution per agent."""
        total = {role: 0.0 for role in ["technical_analyst", "sentiment_analyst", "risk_manager"]}
        for t in self.trades:
            for role, pnl in t["attribution"].items():
                total[role] += pnl
        return total
    
    def get_ablation_performance(self) -> dict:
        """Compute performance per ablation preset."""
        # This would need full backtest runs per preset
        # Placeholder for integration with backtest engine
        return {}


# Global instances for correlation tracking and PnL attribution
_correlation_tracker = AgentCorrelationTracker()
_pnl_tracker = PerAgentPnLTracker()


def get_correlation_tracker() -> AgentCorrelationTracker:
    return _correlation_tracker


def get_pnl_tracker() -> PerAgentPnLTracker:
    return _pnl_tracker


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
    data_timestamp: Any | None = None


class Orchestrator:
    """Orchestrates the multi-agent analysis cycle."""

    def __init__(self, ablation_preset: Literal["A", "B", "C", "D"] | dict = "A"):
        self.technical = TechnicalAnalyst()
        self.sentiment = SentimentAnalyst()
        self.risk = RiskManager()
        self.trader = Trader()
        self._last_df: pl.DataFrame | None = None  # cached after analyze
        
        # Ablation config for systematic agent toggles
        self.ablation = AblationConfig(ablation_preset)
        
        # Correlation tracker for diversification discount
        self.correlation_tracker = AgentCorrelationTracker()
        
        # PnL attribution tracker
        self.pnl_tracker = PerAgentPnLTracker()
        
        # Ensemble weights for agent signals (base weights before correlation discount)
        self.base_ensemble_weights = {
            "technical_analyst": 0.40,
            "sentiment_analyst": 0.20,
            "risk_manager": 0.40,
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
        """Run the full multi-agent analysis cycle with ablation support.

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

        # 4. Run agents in order (respecting ablation config)
        messages: list[AgentMessage] = []

        # Technical Analyst (always runs - base agent)
        logger.info("Running Technical Analyst...")
        tech_msg = self.technical.analyze(context)
        _log_agent_decision(symbol, timeframe, "technical_analyst", tech_msg, current_price)
        messages.append(tech_msg)

        # Sentiment Analyst (conditional on ablation)
        if self.ablation.should_run("sentiment"):
            context.agent_messages = messages
            logger.info("Running Sentiment Analyst...")
            sent_msg = self.sentiment.analyze(context)
            _log_agent_decision(symbol, timeframe, "sentiment_analyst", sent_msg, current_price)
            messages.append(sent_msg)
        else:
            logger.info("Sentiment Analyst SKIPPED (ablation)")

        # Risk Manager (conditional on ablation)
        if self.ablation.should_run("risk"):
            context.agent_messages = messages
            logger.info("Running Risk Manager...")
            risk_msg = self.risk.analyze(context)
            _log_agent_decision(symbol, timeframe, "risk_manager", risk_msg, current_price)
            messages.append(risk_msg)
        else:
            logger.info("Risk Manager SKIPPED (ablation)")

        # Trader (final decision - always runs)
        context.agent_messages = messages
        logger.info("Running Trader...")
        trader_decision = self.trader.analyze(context)

        # Risk is a hard safety gate, not a weighted vote. This second check
        # protects the execution path even if a custom Trader implementation
        # ignores the Risk Manager message.
        risk_msg = next((m for m in messages if m.role == "risk_manager"), None)
        if risk_msg and risk_msg.risk_level in ("HIGH", "EXTREME"):
            in_position = current_position_pct > 0.001
            trader_decision = AgentMessage(
                role="trader",
                signal="SELL" if in_position else "HOLD",
                confidence=min(trader_decision.confidence, 0.6),
                reasoning=(
                    f"[HARD RISK GATE] {risk_msg.risk_level}: "
                    f"{'exit current position' if in_position else 'block new entries'}.\n"
                    f"{trader_decision.reasoning}"
                ),
                details={**trader_decision.details, "risk_gate": True},
                max_position_size_pct=0.0,
                risk_level=risk_msg.risk_level,
                warnings=[*trader_decision.warnings, *risk_msg.warnings],
            )

        # 5. Apply correlation-based diversification discount to weights
        self.correlation_tracker.update(messages)
        diversification_discount = self.correlation_tracker.get_diversification_discount()
        per_agent_corr = self.correlation_tracker.get_per_agent_correlation()
        
        # Record entry signals for PnL attribution
        self.pnl_tracker.record_entry(
            [*messages, trader_decision], current_price,
            trader_decision.max_position_size_pct or 0.0,
        )

        # 6. Compute dynamic position sizing
        position_size_pct = self._calculate_dynamic_position_size(
            context, trader_decision, df
        )

        # 8. Build final decision with correlation info
        final = AgentMessage(
            role="trader",
            signal=trader_decision.signal,
            confidence=trader_decision.confidence,
            reasoning=trader_decision.reasoning,
            details={
                **trader_decision.details,
                "diversification_discount": diversification_discount,
                "per_agent_correlation": per_agent_corr,
                "base_weights": self.base_ensemble_weights,
                "effective_weights": {
                    k: v * diversification_discount 
                    for k, v in self.base_ensemble_weights.items()
                },
                "max_position_size_pct": position_size_pct,
                "regime": trader_decision.details.get("regime", {}),
                "ablation_preset": self.ablation.preset_name,
            },
            max_position_size_pct=position_size_pct,
            risk_level=trader_decision.risk_level,
            warnings=trader_decision.warnings,
        )
        messages.append(final)
        _log_agent_decision(symbol, timeframe, "trader", final, current_price)

        # 9. Build report
        return AgentAnalysisReport(
            symbol=symbol,
            timeframe=timeframe,
            current_price=current_price,
            agent_messages=messages,
            final_decision=final,
            indicators=self._extract_indicators(df),
            data_timestamp=df["timestamp"].tail(1).item(),
        )

    def _calculate_dynamic_position_size(
        self, context: AnalysisContext, final_msg: AgentMessage, df: pl.DataFrame
    ) -> float:
        """Calculate position size using volatility targeting (no Kelly - needs trade history)."""
        if final_msg.signal != "BUY" or final_msg.risk_level in ("HIGH", "EXTREME"):
            return 0.0

        regime = final_msg.details.get("regime", {})
        trend_regime = regime.get("trend_regime", "ranging")
        vol_regime = regime.get("vol_regime", "mid_vol")
        
        # Base position size: volatility targeting
        extra = context.indicators.get("_extra", {})
        realized_vol = extra.get("volatility_20_annualized", 50) / 100
        
        if realized_vol > 0:
            vol_scale = min(0.15 / realized_vol, 2.0)
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
        confidence = final_msg.confidence
        base_size *= confidence
        
        # Cap at max position
        max_pos = 0.40  # 40% max per position
        position_size_pct = min(base_size, max_pos)
        
        return round(max(0.0, position_size_pct), 4)

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
        extra = self._compute_extra(df, timeframe)

        # Price changes
        closes = df["close"].to_numpy()
        price_change_1d = None
        price_change_1w = None
        price_change_1m = None

        timeframe_minutes = self._timeframe_minutes(timeframe)
        for attr, period_minutes in (
            ("price_change_1d", 24 * 60),
            ("price_change_1w", 7 * 24 * 60),
            ("price_change_1m", 30 * 24 * 60),
        ):
            bars_back = max(1, int(np.ceil(period_minutes / timeframe_minutes)))
            if len(closes) > bars_back:
                value = float((closes[-1] / closes[-1 - bars_back] - 1) * 100)
                if attr == "price_change_1d":
                    price_change_1d = value
                elif attr == "price_change_1w":
                    price_change_1w = value
                else:
                    price_change_1m = value

        # ── Fetch alt-data (funding, OI, CVD) for sentiment ────────────────
        try:
            # Convert symbol format (BTC/USDT -> BTCUSDT for Binance)
            perp_symbol = symbol.replace("/", "").upper()
            
            # Funding rate (latest)
            funding_df = fetch_funding_rate(perp_symbol, limit=1)
            if not funding_df.is_empty():
                latest_funding = funding_df.tail(1)["funding_rate"].item()
                extra["funding_rate"] = float(latest_funding)
            
            # Open Interest
            oi = fetch_open_interest(perp_symbol)
            if oi is not None:
                extra["open_interest"] = float(oi)
            
            # CVD / Buy Pressure
            cvd_data = fetch_recent_trades_pressure(perp_symbol)
            if "error" not in cvd_data:
                extra["cvd_short_window"] = float(cvd_data.get("cvd_short_window", 0))
                extra["buy_pressure"] = float(cvd_data.get("buy_pressure", 0.5))
                extra["sell_pressure"] = float(cvd_data.get("sell_pressure", 0.5))
                
        except Exception as e:
            logger.debug(f"Alt-data fetch failed for {symbol}: {e}")

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

    def _compute_extra(self, df: pl.DataFrame, timeframe: str = "1h") -> dict[str, Any]:
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
        per_bar_vol = float(np.std(returns_20))
        extra["volatility_20"] = per_bar_vol * 100
        bars_per_year = 365.25 * 24 * 60 / self._timeframe_minutes(timeframe)
        extra["volatility_20_annualized"] = per_bar_vol * np.sqrt(bars_per_year) * 100

        # Volume
        if "volume" in df.columns:
            vols = df["volume"].to_numpy()
            if len(vols) > 20:
                avg_20 = float(vols[-20:].mean())
                avg_5 = float(vols[-5:].mean())
                extra["volume_ratio_5_20"] = float(avg_5 / avg_20) if avg_20 > 0 else 1.0

        return extra

    @staticmethod
    def _timeframe_minutes(timeframe: str) -> int:
        tf = timeframe.lower().strip()
        units = {"m": 1, "h": 60, "d": 1440, "w": 10080}
        if len(tf) < 2 or tf[-1] not in units:
            raise ValueError(f"Unsupported timeframe: {timeframe!r}")
        try:
            amount = int(tf[:-1])
        except ValueError as exc:
            raise ValueError(f"Unsupported timeframe: {timeframe!r}") from exc
        if amount <= 0:
            raise ValueError(f"Unsupported timeframe: {timeframe!r}")
        return amount * units[tf[-1]]


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
