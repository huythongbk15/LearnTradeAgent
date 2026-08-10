#!/usr/bin/env python3
"""Test /api/data/fetch với nhiều symbol × timeframe qua API."""
import json
import sys
import time
import urllib.request

BASE = "http://127.0.0.1:8000"
CASES = [
    # (symbol, timeframe, since)
    ("BTC/USDT", "1h", "2026-08-09T00:00:00"),
    ("BTC/USDT", "15m", "2026-08-09T00:00:00"),
    ("BTC/USDT", "4h", "2026-07-01T00:00:00"),
    ("BTC/USDT", "1d", "2026-06-01T00:00:00"),
    ("ETH/USDT", "1h", "2026-08-09T00:00:00"),
    ("BNB/USDT", "1h", "2026-08-09T00:00:00"),
    ("AVAX/USDT", "1h", "2026-08-09T00:00:00"),
    ("SOL/USDT", "1h", "2026-08-09T00:00:00"),
    ("SLP/USDT", "1h", "2026-08-09T00:00:00"),   # altcoin nhỏ
    ("BTC/USDT", "1h", None),                      # mặc định (không since)
]


def post(url, payload):
    req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read())


def main() -> int:
    """Run the manual API smoke test against a locally running server."""
    ok = fail = 0
    for symbol, tf, since in CASES:
        payload = {"symbol": symbol, "timeframe": tf}
        if since:
            payload["since"] = since
        try:
            job = post(f"{BASE}/api/data/fetch", payload).get("job_id")
        except Exception as exc:
            print(f"✗ {symbol:12s} {tf:4s} since={since}  — POST lỗi: {exc}")
            fail += 1
            continue
        # poll tối đa 60s
        lines, status, err = [], "running", ""
        for _ in range(30):
            try:
                with urllib.request.urlopen(f"{BASE}/api/backtest/{job}", timeout=10) as r:
                    d = json.loads(r.read())
            except Exception:
                time.sleep(2)
                continue
            status = d.get("status")
            lines = d.get("lines") or []
            err = d.get("error") or ""
            if status in ("done", "error"):
                break
            time.sleep(2)
        got = next((line for line in lines if "Got " in line), "")
        n = got.split("Got ")[-1] if got else "?"
        if status == "done" and "Got " in "".join(lines):
            print(f"✓ {symbol:12s} {tf:4s} since={since}  → {n.strip()}")
            ok += 1
        else:
            print(f"✗ {symbol:12s} {tf:4s} since={since}  — {status} {err[:100]}")
            fail += 1

    print(f"\n==> {ok} OK / {fail} FAIL")
    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(main())
