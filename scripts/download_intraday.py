"""Tải dữ liệu intraday (1m/5m/15m/30m) cho các pairs chính, 3 năm gần nhất.

Chạy: python scripts/download_intraday.py [--since 2023-08-10] [--workers 3]
Kết quả lưu vào data/raw/binance/<SYMBOL>/<tf>.parquet (append + merge unique).
"""

from __future__ import annotations

import argparse
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta

from trading_agent.data.collector import Collector
from trading_agent.data.storage import save_ohlcv

SYMBOLS = [
    "BTC/USDT",
    "ETH/USDT",
    "SOL/USDT",
    "BNB/USDT",
    "DOGE/USDT",
    "ADA/USDT",
    "XRP/USDT",
    "DOT/USDT",
    "AVAX/USDT",
    "LINK/USDT",
]
TIMEFRAMES = ["1m", "5m", "15m", "30m"]

T0 = time.time()


def log(msg: str) -> None:
    print(f"[{time.time() - T0:7.0f}s] {msg}", flush=True)


def download_one(symbol: str, tf: str, since: str) -> dict:
    collector = Collector("binance")
    t_start = time.time()
    try:
        df = collector.fetch_ohlcv(symbol, tf, since=since, progress=False)
        if df.is_empty():
            raise ValueError("no data returned")
        path = save_ohlcv(df, "binance", symbol, tf)
        rows = df.height
        dt = time.time() - t_start
        log(
            f"OK   {symbol:10s} {tf:3s} → {rows:>8,} rows trong {dt / 60:.1f} phút | {path.name}"
        )
        return {"symbol": symbol, "tf": tf, "rows": rows, "ok": True}
    except Exception as exc:  # noqa: BLE001
        log(f"FAIL {symbol:10s} {tf:3s} → {exc}")
        return {"symbol": symbol, "tf": tf, "rows": 0, "ok": False, "error": str(exc)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--since", default=(date.today() - timedelta(days=365 * 3)).isoformat()
    )
    parser.add_argument("--workers", type=int, default=3)
    args = parser.parse_args()

    log(
        f"Download {len(SYMBOLS)} symbols × {len(TIMEFRAMES)} tf, since={args.since}, workers={args.workers}"
    )
    tasks = [(s, tf, args.since) for s in SYMBOLS for tf in TIMEFRAMES]
    results: list[dict] = []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(download_one, s, tf, since) for s, tf, since in tasks]
        for fut in as_completed(futures):
            if fut.exception():
                log(f"EXC  {fut.exception()}")
            else:
                results.append(fut.result())

    ok = [r for r in results if r.get("ok")]
    fail = [r for r in results if not r.get("ok")]
    total_rows = sum(r["rows"] for r in ok)
    log(
        f"DONE {len(ok)}/{len(results)} OK, {len(fail)} FAIL | tổng {total_rows:,} rows | {time.time() - T0:.0f}s"
    )
    for r in fail:
        log(f"  FAIL: {r['symbol']} {r['tf']} → {r.get('error')}")
    return 0 if not fail else 1


if __name__ == "__main__":
    sys.exit(main())
