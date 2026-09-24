"""
Live Data Pipeline — Binance Spot/Futures → Router Feed.

Stubs the bridge from Binance (spot + perpetual futures) to the
AdaptiveStrategyRouter via CCXT -> Candle -> RegimePosterior ->
RoutingDecision.

Data flow:

    CCXT (Binance) --> CCXTSource --> Candle[]
                                 |
                                 v
                   RuleBasedStrategy (fast regime on bar-close)
                   + add_regime_indicators (ATR volatility percentile)
                                 |
                                 v
             RegimeState --> regime_posterior_from_state --> RegimePosterior
                                 |
                                 v
            MarketContext (LLM-enrichment, advisory-only)

Usage::

    pipe = LivePipeline(symbols=["BTC_USDT"], source=MockSource())
    async for stub in pipe.run(timeframe="1d", max_iterations=3):
        ...
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timezone, UTC
from typing import AsyncIterator, Optional

import numpy as np
import polars as pl

from trading_agent.data.pipeline import CCXTSource, Candle, DataSource
from trading_agent.exchanges.models import AssetClass, MarketType, Symbol
from trading_agent.authority.adaptive_router import AdaptiveStrategyRouter
from trading_agent.llm.context_enrichment import MarketContext
from trading_agent.ml.regime_detection import (
    MarketRegime,
    RegimePosterior,
    RuleBasedStrategy,
    regime_posterior_from_state,
)
from trading_agent.regime import add_regime_indicators

logger = logging.getLogger(__name__)


@dataclass
class RoutingDecisionStub:
    """Lightweight routing decision (dry-run / observation-only mode)."""

    symbol: str
    timeframe: str
    posterior: RegimePosterior
    context: MarketContext
    observed_at: datetime
    decision: str = "observe"


# Known quote currencies (longest first for greedy match)
_QUOTE_SUFFIXES = ["USDT", "USDC", "BUSD", "USD", "BTC", "ETH", "BNB", "EUR", "JPY"]


def _coerce_symbol(s: str | Symbol) -> Symbol:
    """Parse 'BTCUSDT' or 'BTCUSDT:USDT' to Symbol.

    Handles common Binance spot/futures notation:
      - "BTCUSDT"       -> spot BTC/USDT
      - "BTC/USDT"      -> spot BTC/USDT
      - "BTCUSDT:USDT"  -> perpetual BTC/USDT
    """
    if isinstance(s, Symbol):
        return s
    s = s.replace("/", "")
    if ":" in s:
        parts = s.split(":")
        settle = parts[1]
        # Parse the base from the part before : (e.g., "BTCUSDT" from "BTCUSDT:USDT")
        symbol_part = parts[0]
        base_str = symbol_part[:3]  # BTC from BTCUSDT
        return Symbol(base=base_str, quote=settle,
                      asset_class=AssetClass.FUTURES,
                      market_type=MarketType.PERPETUAL, exchange="binance")
    else:
        # Match longest known quote suffix
        for q in _QUOTE_SUFFIXES:
            if s.upper().endswith(q) and len(s) > len(q):
                base_str = s[: len(s) - len(q)]
                return Symbol(base=base_str, quote=q,
                              asset_class=AssetClass.CRYPTO,
                              market_type=MarketType.SPOT, exchange="binance")
        # Fallback: assume 3-char base, rest is quote
        base_str = s[:3]
        quote = s[3:]
        return Symbol(base=base_str, quote=quote,
                      asset_class=AssetClass.CRYPTO,
                      market_type=MarketType.SPOT, exchange="binance")


class LivePipeline:
    """
    Live data pipeline: Binance (spot + perpetual) → router feed.

    Parameters
    ----------
    symbols : list[str] | list[Symbol]
        Trading pairs to monitor.

    source : DataSource | None
        Defaults to CCXTSource(exchange_id="binance").
        Pass MockSource() for tests / dry runs.

    router : AdaptiveStrategyRouter | None
        Strategy router consuming live regime posteriors.
        If None, runs in observation-only mode.
    """

    def __init__(
        self,
        symbols: list[str] | list[Symbol],
        *,
        source: DataSource | None = None,
        router: Optional[AdaptiveStrategyRouter] = None,
        environment: str = "research",
        min_lookback: int = 300,
        poll_interval: float = 5.0,
        dry_run: bool = True,
    ) -> None:
        self.symbols = [_coerce_symbol(s) for s in symbols]
        self.source = source or CCXTSource(exchange_id="binance")
        self.router = router
        self.environment = environment
        self.min_lookback = min_lookback
        self.poll_interval = poll_interval
        self.dry_run = dry_run
        self._stop_event = asyncio.Event()
        self._regime_detector = RuleBasedStrategy()
        self._last_bar: dict[str, datetime] = {}

    @classmethod
    def from_binance(
        cls,
        symbols: list[str],
        *,
        api_key: str | None = None,
        secret: str | None = None,
        testnet: bool = True,
        router: Optional[AdaptiveStrategyRouter] = None,
        environment: str = "production",
        dry_run: bool = False,
    ) -> "LivePipeline":
        """Build a live pipeline from Binance (spot + perpetual futures)."""
        import ccxt.async_support as ccxt_async

        has_futures = any(":" in s for s in symbols)
        default_type = "future" if has_futures else "spot"
        kwargs: dict = {
            "enableRateLimit": True,
            "options": {"defaultType": default_type},
        }
        if api_key:
            kwargs["apiKey"] = api_key
        if secret:
            kwargs["secret"] = secret
        if testnet:
            if default_type == "spot":
                kwargs["urls"] = {"api": "https://testnet.binance.vision/api/v3"}
            else:
                kwargs["urls"] = {"api": "https://testnet.binancefutureS.com/fapi/v1"}

        exchange = ccxt_async.binance(kwargs)

        class _CCXTAdapter:
            def __init__(self, ex):
                self.exchange = ex

        source = CCXTSource(exchange_id="binance", adapter=_CCXTAdapter(exchange))
        return cls(
            symbols=symbols,
            source=source,
            router=router,
            environment=environment,
            dry_run=dry_run,
        )

    async def _fetch_recent_bars(
        self, symbol: Symbol, timeframe: str, limit: int
    ) -> list[Candle]:
        """Fetch the most recent *limit* completed bars for *symbol*."""
        return await self.source.fetch_recent(symbol, timeframe, limit=limit)

    def _candles_to_polars(self, candles: list[Candle]) -> pl.DataFrame:
        """Convert list[Candle] → Polars OHLCV DataFrame."""
        n = len(candles)
        if n == 0:
            return pl.DataFrame(
                schema={
                    "timestamp": pl.Datetime("us", UTC),
                    "open": pl.Float64, "high": pl.Float64,
                    "low": pl.Float64, "close": pl.Float64,
                    "volume": pl.Float64, "symbol": pl.Utf8,
                }
            )
        return pl.DataFrame({
            "timestamp": [c.timestamp for c in candles],
            "open": [float(c.open) for c in candles],
            "high": [float(c.high) for c in candles],
            "low": [float(c.low) for c in candles],
            "close": [float(c.close) for c in candles],
            "volume": [float(c.volume) for c in candles],
            "symbol": [c.symbol.pair for c in candles],
        })

    def _regime_posterior(
        self, df: pl.DataFrame, symbol: Symbol, now: datetime
    ) -> tuple[RegimePosterior, MarketContext]:
        """Build RegimePosterior + MarketContext from OHLCV DataFrame."""
        import pandas as pd

        prices = np.array(df["close"].to_numpy(), dtype=float)
        vol_pct = float(np.std(np.diff(prices) / prices[:-1], ddof=1)) if len(prices) > 1 else 0.0

        # Fast rule-based regime detection (deterministic)
        price_series = pd.Series(prices)
        regime_state = self._regime_detector.detect(price_series)

        # Enrich with volatility percentile
        enriched = add_regime_indicators(df, atr_period=14, lookback=50)
        vol_regime = "normal"
        if "vol_regime" in enriched.columns:
            vol_regime = str(enriched.select("vol_regime").row(-1)[0]).replace("_vol", "")

        # RegimeState -> RegimePosterior
        posterior = regime_posterior_from_state(regime_state)

        # Build MarketContext
        regime_tags = {"trend": regime_state.regime.value, "volatility": vol_regime}
        anomaly_flags = []
        if vol_pct > 0.05:
            anomaly_flags.append("extreme_volatility")
        if regime_state.regime == MarketRegime.CRISIS:
            anomaly_flags.append("crisis_regime")
        if regime_state.regime == MarketRegime.HIGH_VOLATILITY:
            anomaly_flags.append("high_vol_regime")

        # Confidence adjustment clamped [0.5, 1.5]
        if regime_state.regime == MarketRegime.CRISIS:
            confidence = 0.5
        elif regime_state.regime == MarketRegime.HIGH_VOLATILITY:
            confidence = 0.7
        else:
            confidence = max(0.5, min(1.5, regime_state.confidence))

        context = MarketContext(
            regime_tags=regime_tags,
            anomaly_flags=anomaly_flags,
            confidence_adjustment=confidence,
            reasoning=f"Regime: {regime_state.regime.value}, vol={vol_pct:.4f}",
        )
        return posterior, context

    async def run(
        self,
        timeframe: str = "1d",
        lookback: int = 300,
        max_iterations: int | None = None,
    ) -> AsyncIterator[RoutingDecisionStub | object]:
        """Main pipeline loop. Yields routing decisions."""
        if max_iterations is not None:
            assert lookback >= self.min_lookback, \
                f"lookback must be >= {self.min_lookback} bars"

        iteration = 0
        while not self._stop_event.is_set():
            for symbol in self.symbols:
                try:
                    candles = await self._fetch_recent_bars(symbol, timeframe, lookback)
                    if len(candles) < lookback:
                        logger.debug(f"Not enough bars for {symbol.pair}: {len(candles)}/{lookback}")
                        continue

                    df = self._candles_to_polars(candles)
                    now = datetime.now(timezone.utc)

                    # Skip if already processed this bar
                    last_ts = self._last_bar.get(symbol.pair)
                    if last_ts is not None and df["timestamp"][-1] == last_ts:
                        continue
                    self._last_bar[symbol.pair] = df["timestamp"][-1]

                    posterior, context = self._regime_posterior(df, symbol, now)

                    if self.router is not None:
                        decision = self.router.route(
                            symbol=symbol.pair,
                            timeframe=timeframe,
                            posterior=posterior,
                            observed_at=now,
                            position_is_flat=True,
                            position_owner_strategy_id=None,
                            market_context=context,
                        )
                        yield decision
                    else:
                        yield RoutingDecisionStub(
                            symbol=symbol.pair,
                            timeframe=timeframe,
                            posterior=posterior,
                            context=context,
                            observed_at=now,
                        )
                except Exception as e:
                    logger.error(f"Error processing {symbol.pair}: {e}")
                    continue

            iteration += 1
            if max_iterations is not None and iteration >= max_iterations:
                break
            await asyncio.sleep(self.poll_interval)

    def stop(self) -> None:
        self._stop_event.set()

    async def __aenter__(self) -> "LivePipeline":
        return self

    async def __aexit__(self, *exc_info) -> None:
        await self.close()

    async def close(self) -> None:
        """Close CCXT exchange connection."""
        self.stop()
        if hasattr(self.source, "_get_exchange"):
            ex = self.source._get_exchange()
            if hasattr(ex, "close"):
                await ex.close()


# Re-export for convenience
__all__ = ["LivePipeline", "RoutingDecisionStub", "_coerce_symbol"]
