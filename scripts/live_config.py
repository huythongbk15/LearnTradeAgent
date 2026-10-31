#!/usr/bin/env python3
"""
Cấu hình dùng chung cho các live runner (P1.4 — tránh config rải rác mỗi file).

Được import bởi:
  - scripts/live_enhanced_ma.py          (Alpaca paper — không có lịch chạy)
  - scripts/live_enhanced_ma_binance.py  (Binance/testnet — runner đang chạy theo lịch)
  - scripts/live_cron_runner.py          (wrapper cron)
"""

from __future__ import annotations

# ── Chiến lược ──────────────────────────────────────────────────────────
# Tham số runner, chọn từ sweep 2026-08-11 (~9.000 combos): MA crossover + ADX + ATR
# trailing stop + DD circuit breaker (cooldown 0 → price-recovery reset) +
# price confirmation (chỉ long khi close > slow MA). Đây không phải strategy đã kiểm chứng:
# evidence đúng bộ tham số này bị từ chối, xem data/live_strategy_evidence.rejected.json
# và docs/operations/RESEARCH_SYNTHESIS_20261030.md mục 2.
# Trailing + breaker nằm trong strategy. Research grid dùng tham số khác, và fill, stop,
# nhịp chạy của research cũng khác runner.
TIMEFRAME = "1h"
LOOKBACK = 1000
STRATEGY_PARAMS = {
    "fast_period": 10,
    "slow_period": 60,
    "adx_threshold": 40,
    "max_dd_pct": 0.12,
    "trailing_atr_mult": 2.0,
    "dd_recovery_pct": 0.03,
    "require_close_above_slow": True,
}

# ── Danh mục Alpaca (paper) — full-capital 100% deployed ───────────────
# NOTE: Alpaca không hỗ trợ BNB — dùng ETH thay thế nếu cần.
# (market_symbol, alpaca_symbol, allocation)
SYMBOLS_ALPACA = [
    ("BTC/USDT", "BTCUSD", 0.40),  # 40% capital
    ("SOL/USDT", "SOLUSD", 0.30),  # 30%
    ("AVAX/USDT", "AVAXUSD", 0.30),  # 30%
]

# ── Risk guard (P0) ────────────────────────────────────────────────────
# ATR trailing stop: đóng lệnh khi giá phá stop = max(initial, peak - k*ATR)
ATR_SL_MULT = 2.0  # k — khớp default enhanced_ma
ATR_SL_WINDOW = 48  # trailing window (48h trên TF 1h)

# ── Trusted time (P0.3) ────────────────────────────────────────────────
# Clock skew tối đa giữa máy local và Binance server trước khi refuse run.
# Exchange timestamp lệch quá mức này = không tin dữ liệu → dừng toàn batch.
DEFAULT_CLOCK_SKEW_S = 2.0
# Drawdown tiers: giảm dần vị thế, stop hoàn toàn ở mốc cuối
DRAWDOWN_TIERS = [
    (0.05, 0.75),  # -5%  → còn 75% vị thế
    (0.10, 0.50),  # -10% → 50%
    (0.15, 0.25),  # -15% → 25%
    (0.20, 0.00),  # -20% → HALT (đóng hết)
]
