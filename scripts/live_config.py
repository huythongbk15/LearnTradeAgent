#!/usr/bin/env python3
"""
Cấu hình dùng chung cho các live runner (P1.4 — tránh config rải rác mỗi file).

Được import bởi:
  - scripts/live_enhanced_ma.py          (Alpaca paper — runner chính)
  - scripts/live_enhanced_ma_binance.py  (Binance/testnet)
  - scripts/live_cron_runner.py          (wrapper cron)
"""
from __future__ import annotations

# ── Chiến lược ──────────────────────────────────────────────────────────
# Champion đã verify: fast_period=20, slow_period=80, adx_threshold=40
TIMEFRAME = "1h"
LOOKBACK = 1000
STRATEGY_PARAMS = {
    "fast_period": 20,
    "slow_period": 80,
    "adx_threshold": 40,
}

# ── Danh mục Alpaca (paper) — full-capital 100% deployed ───────────────
# NOTE: Alpaca không hỗ trợ BNB — dùng ETH thay thế nếu cần.
# (market_symbol, alpaca_symbol, allocation)
SYMBOLS_ALPACA = [
    ("BTC/USDT", "BTCUSD", 0.40),   # 40% capital
    ("SOL/USDT", "SOLUSD", 0.30),   # 30%
    ("AVAX/USDT", "AVAXUSD", 0.30), # 30%
]

# ── Risk guard (P0) ────────────────────────────────────────────────────
# ATR trailing stop: đóng lệnh khi giá phá stop = max(initial, peak - k*ATR)
ATR_SL_MULT = 2.0          # k — khớp default enhanced_ma
ATR_SL_WINDOW = 48         # trailing window (48h trên TF 1h)
# Drawdown tiers: giảm dần vị thế, stop hoàn toàn ở mốc cuối
DRAWDOWN_TIERS = [
    (0.05, 0.75),   # -5%  → còn 75% vị thế
    (0.10, 0.50),   # -10% → 50%
    (0.15, 0.25),   # -15% → 25%
    (0.20, 0.00),   # -20% → HALT (đóng hết)
]