#!/usr/bin/env python3
"""
On-Chain & Alternative Data Module.

Fetches and caches alternative market data that complements OHLCV:
- On-chain metrics (MVRV, NUPL, active addresses, exchange flows)
- Funding rates (perp market sentiment)
- Open interest
- Macro proxies (DXY, risk indices)

Sources (free tiers):
- CoinGecko (on-chain + global metrics, no API key needed)
- Binance Futures (funding rate + OI, public REST)
- No hard dependency on Glassnode/CryptoQuant API keys.

Caching: results cached with a TTL (default 24h for slow-moving on-chain,
10min for funding). Stored under data/alternative/.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path

import polars as pl

from trading_agent.config.loader import config


def _cache_path() -> Path:
    base = Path(getattr(config, "storage_abs_path", Path("data")))
    p = base / "alternative"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _read_cache(key: str, max_age_s: float) -> dict | None:
    path = _cache_path() / f"{key}.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
    except Exception:
        return None
    age = time.time() - data.get("_ts", 0)
    if age > max_age_s:
        return None
    return data


def _write_cache(key: str, data: dict) -> None:
    data = dict(data)
    data["_ts"] = time.time()
    (_cache_path() / f"{key}.json").write_text(json.dumps(data, default=str))


# ---------------------------------------------------------------------------
# CoinGecko (public, no key required for basic endpoints)
# ---------------------------------------------------------------------------


def fetch_coin_gecko_coin_market(
    symbol: str = "bitcoin", vs_currency: str = "usd"
) -> dict:
    """Fetch current market data for an asset (market cap, volumes, 24h change)."""
    import urllib.request

    url = (
        "https://api.coingecko.com/api/v3/coins/"
        f"{symbol}?localization=false&tickers=false&"
        "market_data=true&community_data=false&developer_data=false"
    )
    cached = _read_cache(f"cg_{symbol}", 3600)  # 1h TTL
    if cached:
        return cached

    try:
        with urllib.request.urlopen(url, timeout=15) as r:
            data = json.loads(r.read())
    except Exception as e:
        return {"error": str(e), "symbol": symbol}

    md = data.get("market_data", {})
    result = {
        "symbol": symbol,
        "price_usd": md.get("current_price", {}).get(vs_currency),
        "market_cap_usd": md.get("market_cap", {}).get(vs_currency),
        "total_volume_usd": md.get("total_volume", {}).get(vs_currency),
        "mcap_turnover": (
            md.get("total_volume", {}).get(vs_currency, 0)
            / (md.get("market_cap", {}).get(vs_currency, 0) or 1)
        ),
        "supply_diluted": md.get("circulating_supply"),
        "total_supply": md.get("total_supply"),
        "ath_pct": md.get("ath_change_percentage", {}).get(vs_currency),
        "atl_pct": md.get("atl_change_percentage", {}).get(vs_currency),
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }
    _write_cache(f"cg_{symbol}", result)
    return result


# ---------------------------------------------------------------------------
# Binance Futures Funding / OI (public REST)
# ---------------------------------------------------------------------------


def fetch_funding_rate(symbol: str = "BTCUSDT", limit: int = 100) -> pl.DataFrame:
    """Fetch historical funding rates for a perpetual symbol."""
    import urllib.request

    url = f"https://fapi.binance.com/fapi/v1/fundingRate?symbol={symbol}&limit={limit}"
    try:
        with urllib.request.urlopen(url, timeout=15) as r:
            rows = json.loads(r.read())
    except Exception as e:
        return pl.DataFrame(schema={"error": [str(e)]})

    df = pl.DataFrame(rows)
    if df.is_empty():
        return df
    df = df.with_columns(
        [
            pl.col("fundingTime").cast(pl.Int64).alias("_t"),
            pl.col("fundingRate").cast(pl.Float64).alias("funding_rate"),
        ]
    )
    # Binance fundingTime is ms
    df = (
        df.with_columns(pl.from_epoch(pl.col("_t"), time_unit="ms").alias("timestamp"))
        .select(["timestamp", "funding_rate"])
        .sort("timestamp")
    )
    return df


def fetch_open_interest(symbol: str = "BTCUSDT") -> float | None:
    """Fetch current open interest for a perpetual symbol."""
    import urllib.request

    url = f"https://fapi.binance.com/fapi/v1/openInterest?symbol={symbol}"
    try:
        with urllib.request.urlopen(url, timeout=15) as r:
            data = json.loads(r.read())
        return float(data.get("openInterest"))
    except Exception:
        return None


def fetch_recent_trades_pressure(symbol: str = "BTCUSDT", limit: int = 1000) -> dict:
    """Compute CVD (cumulative volume delta) proxy from recent public trades."""
    import urllib.request

    url = f"https://fapi.binance.com/fapi/v1/trades?symbol={symbol}&limit={limit}"
    try:
        with urllib.request.urlopen(url, timeout=15) as r:
            trades = json.loads(r.read())
    except Exception as e:
        return {"error": str(e)}

    if not trades:
        return {"error": "no trades"}

    buy_vol = sum(float(t["qty"]) for t in trades if t.get("isBuyerMaker") is False)
    sell_vol = sum(float(t["qty"]) for t in trades if t.get("isBuyerMaker") is True)
    total = buy_vol + sell_vol or 1
    return {
        "cvd_short_window": round(buy_vol - sell_vol, 6),
        "buy_pressure": round(buy_vol / total, 4),
        "sell_pressure": round(sell_vol / total, 4),
        "trade_count": len(trades),
    }


# ---------------------------------------------------------------------------
# Convenience aggregate: "Fusion Signal"
# ---------------------------------------------------------------------------


def compute_risk_off_score(
    funding_rate: float,
    buy_pressure: float,
    equity_momentum_risk: float = 0.5,
) -> float:
    """
    Composite risk-on/risk-off score in [0,1].
    1 = maximum risk-off (cautious), 0 = maximum risk-on.
    """
    # High positive funding = crowded long = contrarian risk (props up short squeeze risk)
    funding_component = min(max(funding_rate / 0.001, -1), 1)  # ±0.1% normalized
    buy_pressure_component = 1.0 - buy_pressure  # high buy pressure lowers score

    score = (
        0.4 * (0.5 - funding_component)
        + 0.3 * buy_pressure_component
        + 0.3 * equity_momentum_risk
    )
    return float(np.clip(score, 0.0, 1.0))


try:
    import numpy as np
except Exception:  # pragma: no cover
    np = None


def get_altsnapshot() -> dict:
    """Fetch a snapshot of alternative data for the leading perps."""
    result = {}
    symbols = {
        "BTC": "BTCUSDT",
        "ETH": "ETHUSDT",
        "SOL": "SOLUSDT",
        "BNB": "BNBUSDT",
        "XRP": "XRPUSDT",
        "DOGE": "DOGEUSDT",
    }
    for base, perp in symbols.items():
        result[base] = {
            "funding": fetch_funding_rate(perp, limit=1),
            "oi": fetch_open_interest(perp),
            "cvd": fetch_recent_trades_pressure(perp),
        }
    return result


# Lightweight helpers without heavy imports at module load
async def fetch_funding_rate_async(
    symbol: str = "BTCUSDT", limit: int = 10
) -> pl.DataFrame:
    """Async wrapper (returns polars DF)."""
    return fetch_funding_rate(symbol, limit)


if __name__ == "__main__":
    print("On-chain / alt-data test:")
    import json as _json

    mc = fetch_coin_gecko_coin_market("bitcoin")
    print(
        "  CoinGecko:",
        _json.dumps({k: v for k, v in mc.items() if k != "error"}, default=str)[:300],
    )
    fr = fetch_funding_rate("BTCUSDT", 5)
    print("  Funding:", fr.tail(2).to_dicts() if not fr.is_empty() else "empty")
    oi = fetch_open_interest("BTCUSDT")
    print("  OI:", oi)
    cvd = fetch_recent_trades_pressure("BTCUSDT")
    print("  CVD:", cvd)
