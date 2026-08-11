"""
AgentStrategy — wraps multi-agent system as a backtest strategy.

Cho phép chạy backtest với tín hiệu từ các AI agents (rule-based mode).
Dùng USE_LLM=false để đảm bảo tốc độ.

Hỗ trợ LLM deterministic mode cho backtest:
    from trading_agent.agents.llm import enable_backtest_mode
    enable_backtest_mode()  # temp=0, seed=0, fixed provider, no cache

Cách dùng:
    # CLI (rule-based, fast)
    USE_LLM=false trading-agent backtest run agent_ensemble -s BTC/USDT -t 1h

    # CLI (LLM deterministic)
    trading-agent backtest run agent_ensemble -s BTC/USDT -t 1h --llm

    # Python
    strategy = AgentStrategy({"threshold_buy": 0.2, "use_llm": True})
    engine = BacktestEngine(strategy, initial_capital=10000)
    result = engine.run(df)
"""

from __future__ import annotations

import logging
import os
from typing import Any

import numpy as np
import polars as pl

from trading_agent.agents.base import AnalysisContext
from trading_agent.agents.llm import enable_backtest_mode, disable_backtest_mode, is_backtest_mode
from trading_agent.regime import add_regime_indicators
from trading_agent.agents.risk import RiskManager
from trading_agent.agents.sentiment import SentimentAnalyst
from trading_agent.agents.technical import TechnicalAnalyst
from trading_agent.agents.trader import Trader
from trading_agent.strategies.base import Strategy, register_strategy

logger = logging.getLogger(__name__)


@register_strategy("agent_ensemble")
class AgentStrategy(Strategy):
    """Backtest strategy that uses multi-agent signals.

    Supports both rule-based mode (fast, USE_LLM=false) and LLM deterministic mode.

    Parameters:
        threshold_buy (float): Score > this → enter long (default: 0.3)
        threshold_exit (float): Score < this → exit (default: 0.0)
        lookback (int): Indicator history bars (default: 100)
        max_hold_bars (int): Max bars per trade (default: 48)
        use_llm (bool): Enable LLM agents in backtest (default: False)
        llm_provider (str): LLM provider for backtest (default: "opencode")
        llm_model (str): LLM model for backtest (default: "deepseek-v4-flash-free")
    """

    def __init__(self, params: dict[str, Any] | None = None):
        params = params or {}
        super().__init__(params)
        self.threshold_buy = float(params.get("threshold_buy", 0.3))
        self.threshold_exit = float(params.get("threshold_exit", 0.0))
        self.lookback = int(params.get("lookback", 100))
        self.max_hold_bars = int(params.get("max_hold_bars", 48))
        
        # LLM mode for backtest
        self.use_llm = bool(params.get("use_llm", False))
        self.llm_provider = params.get("llm_provider", "opencode")
        self.llm_model = params.get("llm_model", "deepseek-v4-flash-free")
        
        # Initialize agents
        self.technical = TechnicalAnalyst()
        self.sentiment = SentimentAnalyst()
        self.risk = RiskManager()
        self.trader = Trader()
        
        # Enable backtest LLM mode if requested
        if self.use_llm:
            enable_backtest_mode(
                provider=self.llm_provider,
                model=self.llm_model,
                temperature=0.0,
                max_tokens=500,
                seed=0,
                use_cache=False,
            )
            # Force enable LLM for this process
            os.environ["USE_LLM"] = "true"
        else:
            # Ensure rule-based mode
            os.environ["USE_LLM"] = "false"

    def __del__(self):
        """Clean up backtest mode on destruction."""
        if is_backtest_mode():
            disable_backtest_mode()

    @property
    def name(self) -> str:
        return "agent_ensemble"

    def compute_indicators(self, df: pl.DataFrame) -> pl.DataFrame:
        # Vectorized regime indicators ONCE on the full frame (~0.3s) instead of
        # re-running add_regime_indicators(window) per bar (~3.5ms x N bars = 110s).
        # Semantics are matched exactly to the old per-window path: the old
        # window was 100 bars < regime lookback (252) so atr_pctl was always
        # None and vol_regime always fell through to "high_vol"; adx /
        # trend_regime / trend_dir (rolling 14/25) are identical either way.
        df = add_regime_indicators(df)
        return df.with_columns(
            pl.lit(None, pl.Float64).alias("atr_pctl"),
            pl.lit("high_vol").alias("vol_regime"),
        )

    def generate_signals(self, df: pl.DataFrame) -> pl.Series:
        """Bar-by-bar signal generation with state tracking."""
        n = len(df)
        signals = np.zeros(n, dtype=np.float64)
        closes = df["close"].to_numpy()
        vols = df["volume"].to_numpy() if "volume" in df.columns else np.ones(n) * 1000

        # Pre-compute indicators once (vectorized)
        df_i = df.with_columns([
            pl.Series("ma_5", _sma(closes, 5)),
            pl.Series("ma_10", _sma(closes, 10)),
            pl.Series("ma_20", _sma(closes, 20)),
            pl.Series("ma_50", _sma(closes, 50)),
            pl.Series("rsi", _rsi(closes, 14)),
        ])
        sma20 = _sma(closes, 20)
        std20 = _rolling_std(closes, 20)
        df_i = df_i.with_columns([
            pl.Series("bb_upper", sma20 + 2 * std20),
            pl.Series("bb_lower", sma20 - 2 * std20),
            pl.Series("bb_mid", sma20),
        ])

        # Pre-extract indicator columns to numpy once — tránh polars .item() mỗi bar
        _IND_COLS = ["ma_5", "ma_10", "ma_20", "ma_50", "rsi", "bb_upper", "bb_lower", "bb_mid"]
        _arr = {c: df_i[c].to_numpy() for c in _IND_COLS}

        # Precomputed regime indicators (vectorized in compute_indicators).
        # Match old per-window semantics: atr_pctl=None, vol_regime="high_vol";
        # adx / trend_regime / trend_dir carry the real rolling values.
        _regime = {
            c: df_i[c].to_numpy()
            for c in ("atr", "atr_pctl", "vol_regime", "adx", "trend_regime", "trend_dir")
        }

        # Position state
        in_position = False
        bars_in_position = 0

        for i in range(self.lookback, n):
            window = df_i.slice(i - self.lookback + 1, self.lookback)

            # Extract indicators (numpy indexing, không còn .slice().tail().item() per bar)
            ind: dict[str, Any] = {}
            for col in _IND_COLS:
                v = _arr[col][i]
                if v is not None and not (isinstance(v, float) and np.isnan(v)):
                    ind[col] = float(v)

            extra: dict[str, Any] = {}
            if i >= 5:
                extra["change_5"] = float((closes[i] / closes[i - 5] - 1) * 100)
            if i >= 20:
                extra["change_20"] = float((closes[i] / closes[i - 21] - 1) * 100)
            if i >= 21:
                extra["volatility_20"] = float(np.std(np.diff(closes[i - 20:i + 1]) / closes[i - 20:i]) * 100)
            if i >= 20:
                v5 = float(vols[i - 4:i + 1].mean())
                v20 = float(vols[i - 19:i + 1].mean())
                extra["volume_ratio_5_20"] = v5 / v20 if v20 > 0 else 1.0
            # Regime values with the old `if v else None` convention (0 -> None)
            extra["_regime"] = {
                "atr": float(_regime["atr"][i]) if _regime["atr"][i] else None,
                "atr_pctl": None,  # old window path always produced None
                "vol_regime": "high_vol",  # atr_pctl None -> otherwise branch
                "adx": float(_regime["adx"][i]) if _regime["adx"][i] else None,
                "trend_regime": _regime["trend_regime"][i],
                "trend_dir": _regime["trend_dir"][i],
            }
            ind["_extra"] = extra

            ctx = AnalysisContext(
                symbol="BACKTEST", timeframe="1h",
                current_price=float(closes[i]), ohlcv=window,
                indicators=ind,
                current_position_pct=1.0 if in_position else 0.0,
            )

            # Run 4 agents
            try:
                tm = self.technical.analyze(ctx)
                ctx.agent_messages = [tm]
                sm = self.sentiment.analyze(ctx)
                ctx.agent_messages = [tm, sm]
                rm = self.risk.analyze(ctx)
                ctx.agent_messages = [tm, sm, rm]
                final = self.trader.analyze(ctx)
            except Exception as e:
                logger.warning(f"Agent ensemble failed for symbol {i} at bar: {e}")
                signals[i] = 0.0
                continue

            weighted = final.details.get("weighted_score", 0.0)
            risk_level = final.risk_level or "LOW"

            if not in_position:
                # Flat → check entry
                if weighted > self.threshold_buy and risk_level not in ("HIGH", "EXTREME"):
                    signals[i] = 1.0
                    in_position = True
                    bars_in_position = 0
            else:
                # In position → check exit
                exit_signal = False
                if weighted < self.threshold_exit:
                    exit_signal = True
                if risk_level in ("HIGH", "EXTREME"):
                    exit_signal = True
                if bars_in_position >= self.max_hold_bars:
                    exit_signal = True  # time-based exit

                if exit_signal:
                    signals[i] = -1.0  # trigger exit
                    in_position = False
                    bars_in_position = 0
                else:
                    signals[i] = 0.0  # maintain
                    bars_in_position += 1

        return pl.Series("signal", signals)


# ── Helper functions ──────────────────────────────────────────────────────


def _sma(values: np.ndarray, period: int) -> np.ndarray:
    out = np.full_like(values, np.nan)
    if len(values) < period:
        return out
    cum = np.cumsum(values)
    out[period - 1:] = (cum[period - 1:] - np.concatenate([[0], cum[:-period]])) / period
    return out


def _rolling_std(values: np.ndarray, period: int) -> np.ndarray:
    out = np.full_like(values, np.nan)
    for i in range(period - 1, len(values)):
        out[i] = float(np.std(values[i - period + 1:i + 1]))
    return out


def _rsi(values: np.ndarray, period: int = 14) -> np.ndarray:
    out = np.full_like(values, np.nan)
    if len(values) < period + 1:
        return out
    deltas = np.diff(values)
    gains = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)
    avg_gain = _sma(gains, period)
    avg_loss = _sma(losses, period)
    for i in range(period, len(values)):
        rs = avg_gain[i - 1] / max(avg_loss[i - 1], 1e-9)
        out[i] = 100 - (100 / (1 + rs))
    return out
