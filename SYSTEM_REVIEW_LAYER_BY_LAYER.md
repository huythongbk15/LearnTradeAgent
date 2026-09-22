# Hệ Thống Trading Agent — Rà Soát 10 Tầng Chi Tiết

**Ngày:** 2026-09-22  
**Phương pháp:** 4 câu hỏi mỗi tầng: A. Code làm gì? B. Đúng không? C. Có leakage/overfitting không? D. Giữ/sửa/loại bỏ?

---

## Kiến trúc tổng quan

```
┌─────────────────────────────────────────────────────────────────────────┐
│         LIVE DATA INGESTION        │  BINANCE 1h/1d + YFINANCE 18 stocks │
│         (pipeline.py)              │  → parquet, validated, cached       │
└──────────────┬──────────────────────────────────────────────────────────┘
               │
┌──────────────▼──────────────┐  ┌───────────────────────────────────────┐
│  DATA PIPELINE            │  │  STRATEGY EDGE (16 candidates)        │
│  - collector.py            │  │  - enhanced_ma, trend_pullback, bbands│
│  - market_data.py          │  │  - rsi, range_mean_reversion, stat_arb│
│  - onchain.py (DEX)        │  │  - cross_sectional_momentum, ensemble │
│  → OHLCV parquet           │  │  → signal {-1, 0, 1}                │
└──────────────┬─────────────┘  └───────────────┬────────────────────────┘
               │                               │
┌──────────────▼────────────────────────────────▼────────────────────────┐
│  REGIME DETECTION                              │                      │
│  - ml/regime_detection.py                      │                      │
│    • HMM (hmmlearn) → RegimePosterior{p_trend,│                      │
│      p_mr, p_hv, p_crisis, ood_score}          │                      │
│  - regime.py                                    │                      │
│    • Rule-based fallback: ATR percentile +    │                      │
│      ADX > 25 → trending/ranging              │                      │
│    • get_regime_params() adjusts MA/ADX       │                      │
│      thresholds per regime                    │                      │
└──────────────┬─────────────────────────────────┘                      │
               │                                                            │
┌──────────────▼──────────────────────────────────────────────────────────┐
│  STRATEGY TOURNAMENT (extends Router)                                    │
│  - authority/strategy_tournament.py                                      │
│    • Shadow mode (default): score all 16 strategies, log, no execute    │
│    • Live mode: promote/demote based on shadow Sharpe                   │
│    • Kill switch: TOURNAMENT_SHADOW_MODE env var                         │
│    • Risk controls: max-DD 30%, pos cap 85%, Sharpe breaker -0.50       │
│    • Bonferroni-corrected Welch t-test for significance                  │
│    • SQLiteAuditLog + router_audit.jsonl (483+ entries)                  │
│    • Monitoring dashboard (PID 159 ALIVE)                                 │
└──────────────┬──────────────────────────────────────────────────────────┘
               │ RiskDecision
┌──────────────▼──────────────────────────────────────────────────────────┐
│  FORECAST RISK POLICY (deterministic)                                   │
│  - agents/risk.py → ForecastRiskPolicy                                 │
│    • NO LLM — pure math: vol + volume_ratio + drawdown                 │
│    • vol > 3% → HIGH (pos=0), 1.5-3% → MEDIUM, < 1.5% → LOW              │
│    • risk_based = 0.015 / stop_pct, vol_cap = 0.40 * (1.5/vol)            │
│    • max_pos = max(0.05, min(risk_based, vol_cap))                     │
└──────────────┬──────────────────────────────────────────────────────────┘
               │ Position sizing
┌──────────────▼──────────────────────────────────────────────────────────┐
│  BACKTEST ENGINE (vectorized)                                           │
│  - backtest/engine.py                                                    │
│    • Signal[t] → position[t+1], fill at open[t+1] (no look-ahead)       │
│    • Fee+slippage+spread on BOTH entry+exit                             │
│    • SL/TP/trailing via ATR (intrabar high/low check)                   │
│    • Kelly/half-Kelly/half-Kelly position sizing                       │
│    • Equity curve, trade list, metrics (Sharpe, DD, PF, Calmar)          │
└──────────────┬──────────────────────────────────────────────────────────┘
               │
┌──────────────▼──────────────────────────────────────────────────────────┐
│  EXECUTION SIMULATOR (paper exchange)                                    │
│  - execution/simulator/                                                 │
│    • fill_model.py: FillModel (v{version})                               │
│      - fill_market: sweep order book levels, apply impact_bps            │
│      - fill_limit: rest passive, queue position deterministic (hash)   │
│    • impact_model.py: temporary_impact_bps (power-law), adverse_sel      │
│    • orderbook.py: OrderBookState (bid/ask ladder, mid, spread)          │
│    • fee_model.py, ledger.py, metrics.py                                  │
│  - execution/canonical/order_planner.py                                  │
│    • _select_order_type(): market/limit/TWAP/VWAP based on vol×liquidity │
│    • MarketContext.confidence_adjustment scales exposure (clamped)        │
│    • anomaly_flags/regime_tags: advisory, NEVER block routing           │
└──────────────┬──────────────────────────────────────────────────────────┘
               │
┌──────────────▼──────────────────────────────────────────────────────────┐
│  LLM ENRICHMENT (advisory-only, deterministic mode)                       │
│  - llm/context_enrichment.py → MarketContext                             │
│    • regime_tags: {trend, volatility, momentum, seasonality}             │
│    • anomaly_flags: [regime_flip, gap, funding_extreme, oi_spike]         │
│    • cross_asset_signals: {ETH/USDT: signal, confidence}                  │
│    • confidence_adjustment: clamped [0.5, 1.5] (NOT a direct exposure)  │
│  - llm/client.py → backtest_ask_agent (temp=0, seed=42, no-cache)         │
│  - features/llm/ (earnings, news, social — enrichment features)            │
│  - llm/research_memory.py (checksum'd bar-indexed replay store)           │
└──────────────┬──────────────────────────────────────────────────────────┘
               │
┌──────────────▼──────────────────────────────────────────────────────────┐
│  LIVE EXECUTION                                                          │
│  - exchanges/live_broker.py                                              │
│  - execution/paper_exchange.py                                           │
│  - exchanges/futures/binance_futures.py                                  │
│  - exchanges/alpaca_adapter.py (equities)                                │
│  - TOURNAMENT_SHADOW_MODE=1 (default): kill switch ON                    │
└──────────────────────────────────────────────────────────────────────────┘
```

---

## 1. DATA PIPELINE

### A. Code đang làm gì?
Pipeline thu thập dữ liệu từ 3 nguồn: Binance 1h/1d (crypto), yfinance (18 cổ phiếu), DEX. Dữ liệu được crawl về parquet, validate bằng `validate_live_hourly_bars()`. Kiểm tra: số lượng bars, timestamp sort, OHLC consistency, null values, stale candle rejection, time gap detection.

### B. Đúng về quantitative/trading?
✅ **Cơ bản đúng** — có validation đầy đủ. Tuy nhiên:
- **Stale candle check**: chỉ reject nếu gap > 2h, nhưng trong thực tế Binance có thể có gaps lớn hơn trong downtime → có thể bỏ sót.
- **OHLC consistency**: chặt chẽ (`high >= max(open,close)`, `low <= min(open,close)`).
- **Data integrity hash**: SHA256 của raw bars, validated on load — tốt.

### C. Leakage / overfitting / hidden assumption?
⚠️ **Potential leakage**: `bar_range = (high - low) / mid` trong T1C — sử dụng intrabar high/low để tính correlation, nhưng signal chỉ biết từ close. Tuy nhiên trong T1C đây chỉ là phân tích chiến lược chứ không phải signal generation, nên acceptable.

⚠️ **Assumption**: Dữ liệu Binance không có chênh lệch giá shadow-vs-live. Trong thực tế, Binance có latency + rate limiting.

### D. Giữ / sửa / loại bỏ?
**Giữ** với cải thiện:
- 🛠️ **Fix**: Thêm circuit breaker khi data feed lỗi liên tục 3 lần → switch sang mode degraded.
- 🛠️ **Fix**: Stale candle threshold tăng từ 2h → 4h (tránh miss data trong Binance maintenance windows).

---

## 2. STRATEGY EDGE

### A. Code đang làm gì?
16 chiến lược đăng ký trong `canonical/candidates.py`:
- **enhanced_ma**: MA crossover (20/80) + ADX filter (>25) + ATR SL/TP + max-DD circuit breaker + trailing ATR
- **trend_pullback**: MA crossover + pullback entry (reversal pattern)
- **bbands**: Bollinger Bands mean reversion (2σ band touch)
- **rsi**: RSI(14) mean reversion (oversold<30, overbought>70)
- **ma_crossover**: Simple MA(10/30) crossover
- **range_mean_reversion**: Z-score based (price vs rolling mean)
- **stat_arbitrage_lo/ls**: Cointegration mean reversion (long-only / long-short)
- **cross_sectional_momentum**: Relative momentum across 10+ assets
- **ensemble_ma_adx**: Weighted combination (fast/med/slow MA + RSI + BB)
- **ma_vol_target**: MA + volatility targeting position sizing
- **regime_switching**: Dynamic parameter adjustment per regime
- **volatility_breakout**: ATR breakout
- ... + funding_carry, agent_ensemble

### B. Đúng về quantitative/trading?
✅ **Phần lớn đúng**:
- MA crossover: Classic, well-tested, có edge trong trending markets.
- RSI mean reversion: Valid trong ranging markets, nhưng cần market regime filter.
- Bollinger Bands: Valid mean reversion, nhưng tham số 2σ có thể quá hẹ trong trending.
- Stat arbitrage: Cần cointegration test (cái này có ko ở đây? cần kiểm tra).
- Cross-sectional momentum: Valid, nhưng cần điều chỉnh cross-sectional correlation.

⚠️ **Vấn đề**:
- `enhanced_ma` có `dd_recovery_pct=0.03` — chỉ resume trading khi giá hồi phục 3% từ `trip_close`. Nhưng trong down trend, giá có thể không bao giờ recover → strategy stay flat mãi mãi. Đây là **conservative design**.

### C. Leakage / overfitting / hidden assumption?
⚠️ **Overfitting alert** — `add_regime_indicators()` sử dụng `rolling_map` với `lookback=252` để tính ATR percentile. Đây là **look-ahead bias tiềm ẩn** vì hàm rolling của polars trong `rolling_map` có thể dùng cả window để tính percentile (bảo gồm giá trị hiện tại).

⚠️ **Hidden assumption**: Các chiến lược đều là **long-only** (BacktestEngine chỉ hỗ trợ long-only). `trend_pullback` có signal -1 (short) nhưng engine sẽ reject. → **Signal không được thực thi đúng**.

⚠️ **T1B hit rate = 0.5031 ± 0.0** — ngư�ôngng 0.45 là hợp lý (slightly better than random), nhưng các chiến lược như `enhanced_ma` (23 signals/1500 bars) quá sparse → **low signal density = low statistical confidence**.

### D. Giữ / sửa / loại bỏ?
**Giữ** enhanced_ma, rsi, bbands, range_mean_reversion, cross_sectional_momentum:
- 🛠️ **Fix**: `trend_pullback` và `ma_crossover` có shorts — cần bổ sung short-mode trong backtest engine hoặc loại bỏ short signals trong long-only mode (signal -1 → 0).
- 🛠️ **Fix**: `enhanced_ma` DD recovery: thay `dd_recovery_pct=0.03` bằng `dd_cooldown_bars=14` (time-based recovery, tránh bị stuck trong down trend).
- 🛠️ **Fix**: `add_regime_indicators()` — replace `rolling_map` với manual rolling percentile computation (exclude current bar) để tránh look-ahead.
- **Loại bỏ** volatility_breakout (overlap mạnh với bbands, low Sharpe).

---

## 3. REGIME DETECTION

### A. Code đang làm gì?
Hai tầng regime detection:
1. **ML layer** (`ml/regime_detection.py`): HMM (hmmlearn) + GMM (sklearn) + rule-based hybrid. `RegimePosterior` chứa 5 soft probabilities (trend, mean_reversion, high_vol, crisis, other) + OOD score. `conviction_multiplier = 1 - normalized_entropy`.
2. **Rule-based layer** (`regime.py`): ATR percentile (lookback=252) → low_vol/mid_vol/high_vol. ADX > 25 → trending/ranging. DI+/- > DI ∓ → trend direction. `get_regime_params()` adjusts ADX threshold + MA periods.

### B. Đúng về quantitative/trading?
✅ **HMM approach is valid** — 5-state HMM là reasonable cho crypto (bull, bear, sideways, crisis, recovery). Conviction multiplier (entropy-based) là proper uncertainty quantification.

⚠️ **Vấn đề**:
- `OOD score` trong `RegimePosterior` — cách tính chưa rõ (có phải dựa trên HMM posterior entropy không?).
- `is_production_ready()`: `max_ood_score=0.5` — ngưỡng này quá rộng. Nên tighten thành 0.3.
- Rule-based layer sử dụng `rolling_map` — **look-ahead risk** (polars rolling_map bao gồm current bar).

### C. Leakage / overfitting / hidden assumption?
⚠️ **Look-ahead bias confirmed**: Trong `add_regime_indicators()`, ATR percentile computed bằng `rolling_map` trên window_size=252 — polars `rolling_map` trả về values tính trên cả window (bao gồm current bar). Điều này means regime labels biết trước hết high/low của bar hiện tại → **leakage**.

⚠️ **Overfitting**: ADX threshold = 25 là giá trị "classic" nhưng không được backtest-specific tuning. Có thể 25 không optimal cho crypto.

⚠️ **Hidden assumption**: HMM được fit trên toàn bộ history trước khi trading → trong live, phải online-update HMM every bar. `fitted_start/fitted_end` validation ngăn cản stale models, nhưng **re-fit frequency chưa rõ**.

### D. Giữ / sửa / loại bỏ?
**Giữ** HMM + rule-based hybrid:
- 🛠️ **Fix**: Trong `add_regime_indicators()`, replace `rolling_map` với manual `rolling_quantile` computation (shift 1 trước khi tính percentile).
- 🛠️ **Fix**: Tighten `max_ood_score` từ 0.5 → 0.3 trong production.
- 🛠️ **Fix**: Re-fit HMM model mỗi khi `is_fresh()` fails (max_age=7200s = 2h).
- **Loại bỏ** GMM phương pháp (HMM đủ, GMM thêm complexity không đáng).

---

## 4. STRATEGY TOURNAMENT SELECTION

### A. Code đang làm gì?
`StrategyTournament` extends `AdaptiveStrategyRouter`:
- **Shadow mode** (default, kill switch ON): Score all 16 strategies, log performance, không execute.
- **Live mode** (`TOURNAMENT_SHADOW_MODE=0`): Tự động promote/demote incumbent dựa trên rolling shadow Sharpe.
- **Risk controls**: Max-DD 30%, position cap 85%, Sharpe circuit breaker (-0.50 over 288 bars).
- **Statistical test**: Welch's t-test với Bonferroni correction (significance_alpha=0.05).
- **Audit trail**: SQLiteAuditLog + router_audit.jsonl. Kill switch persistence qua `TournamentStateStore`.

### B. Đúng về quantitative/trading?
✅ **Rất tốt**:
- Shadow scoring trước khi promote là best practice (như Renaissance, AQR).
- Sharpe circuit breaker (-0.50) reasonable — giống như Citadel's "risk off" trigger.
- Bonferroni correction tránh false discovery khi test nhiều strategies.

⚠️ **Vấn đề**:
- `shadow_lookback=1440` bars = 2 tháng (hourly) → adequate cho Sharpe estimation (cần tối thiểu 63 bars).
- `promotion_persistence=6` bars = 6 phút (hourly) → **quá ngắn** để confirm promotion. Nên 24-48 bars.
- `min_shadow_bars=288` = 2 tuần → adequate (cần tối thiểu 100 bars).
- `score_margin=0.10` (Sharpe delta) — có thể quá cao cho crypto (Sharpe thấp hơn stocks).

### C. Leakage / overfitting / hidden assumption?
⚠️ **Transaction cost leakage**: Shadow Sharpe tính từ `bar_return` chứ không phải net-of-fees. Nếu strategy A có Sharpe 0.21 và B có 0.20, promotion margin = 0.10 → A promoted, nhưng **sau fee** A có thể thua. Cần shadow Sharpe net-of-fees.

⚠️ **Overfitting risk**: Promotion dựa trên rolling Sharpe có thể overfit tới recent performance. Cần walk-forward validation.

⚠️ **Hidden assumption**: `bar_return` passed vào `route()` là raw market return — nhưng tournament chạy per-strategy signal × market return. Nếu signal đã tính từ cùng một data window → **no leakage**. Nhưng nếu signal dùng future data → leakage.

### D. Giữ / sửa / loại bỏ?
**Giữ** tournament architecture:
- 🛠️ **Fix**: `promotion_persistence` tăng từ 6 → 24 bars (đủ để confirm).
- 🛠️ **Fix**: Shadow Sharpe trừ fixed fee model (0.1% round-trip) trước khi ranking.
- 🛠️ **Fix**: `score_margin` giảm từ 0.10 → 0.05 (crypto Sharpe thấp hơn).
- 🛠️ **Fix**: Thêm walk-forward validation — re-fit promotion thresholds every 960 bars.

---

## 5. BACKTEST METHODOLOGY

### A. Code đang làm gì?
`BacktestEngine` (vectorized, long-only):
1. Signal[t] → position[t+1] (shift 1, no look-ahead)
2. Entry fill at `open[t+1]`, exit fill at `open[t+1]`
3. Fee + slippage + spread on BOTH entry and exit
4. SL/TP/trailing stop: intrabar high/low check (ATR-based)
5. Position sizing: fixed / Kelly / half-Kelly / vol-target / optimal-f
6. Equity curve (compounding), metrics (Sharpe, Sortino, DD, PF, Calmar)

### B. Đúng về quantitative/trading?
✅ **Rất đúng**:
- No look-ahead: signal shift + open[t+1] fill là textbook.
- Fee/slippage/spread on both sides — realistic.
- SL/TP intrabar check (dùng high/low) — captures exit condition within bar.

⚠️ **Vấn đề**:
- **Chỉ hỗ trợ long-only** (`if not long_only: raise NotImplementedError`). Các chiến lược có short signals (trend_pullback signal=-1) → bị silent drop hoặc error.
- **Open[t+1] fill assumption**: Giả định fill luôn ở open giữa bar t+1. Trong thực tế, nếu signal emit ở cuối bar t, execution có thể ở bất kỳ giá nào trong bar t+1. Open fill là conservative (usually worst case for breakout strategies).
- **Position sizing**: Kelly dựa vào lịch sử trade PnL — nhưng backtest chưa chạy đủ trades để estimate reliable → **over-betting risk**.
- **No survivorship bias check**: Data pipeline crawl từ 2020-2026 — nếu symbols delisted trong period đó, chưa handle.

### C. Leakage / overfitting / hidden assumption?
⚠️ **Position sizing leakage**: `PositionSizer.update_trade()` cập nhật trade history, và Kelly fraction được tính từ lịch sử trades. Nhưng trong backtest, `update_trade` được gọi sau khi trade đã đóng — nên không có look-ahead. Tuy nhiên **nếu dùng full history để compute Kelly, mà backtest chưa đủ trades** → unreliable estimate.

⚠️ **No walk-forward**: Backtest chạy trên toàn bộ 2020-2026 — **overfitting to future**. Cần walk-forward (train 2020-2022, test 2023-2024, v.v.).

⚠️ **Assumption**: `open[t+1]` fill giả định liquidity luôn đủ — không xét slips nếu position lớn.

### D. Giữ / sửa / loại bỏ?
**Giữ** backtest engine:
- 🛠️ **Fix**: Thêm short-side support (position có thể âm).
- 🛠️ **Fix**: Thêm walk-forward config — `train_window`, `test_window`, `n_splits`.
- 🛠️ **Fix**: Kelly warm-up: chỉ dùng Kelly khi có > 50 trades, nếu không dùng fixed fraction.
- 🛠️ **Fix**: Thêm slippage model non-linear: slippage ~ (size/market_volume)^0.5.

---

## 6. RISK

### A. Code đang làm gì?
Hai tầng:
1. **Trader Agent** (`agents/trader.py`): Weighted voting across Technical + Sentiment + Risk agents. Returns `AgentMessage` với confidence-weighted recommendation.
2. **ForecastRiskPolicy** (`agents/risk.py`): Deterministic replacement cho LLM RiskManager. Vol + volume_ratio + drawdown → position size. Threshold: vol > 3% → pos=0 (HIGH risk).

### B. Đúng về quantitative/trading?
✅ **ForecastRiskPolicy là chuẩn**:
- vol > 3% → 0% position là reasonable (similar to Citadel's 3-sigma vol halt).
- `risk_based = 0.015 / stop_pct` follow Kelly-style risk budgeting.
- `vol_cap = 0.40 * min(1.0, 1.5 / vol)` — inverse vol scaling, reasonable.

⚠️ **Vấn đề**:
- **Trader Agent**: weighted voting giữa Technical + Sentiment + Risk — nhưng sentiment agent dùng LLM (news/social) → **LLM trong decision path**. Điều này vi phạm "core execution path LLM-free" constraint nếu Trader trực tiếp vote vào order.
- **RiskDecision**: không có stop-loss specific level, trailing stop level — chỉ có max position size.

### C. Leakage / overfitting / hidden assumption?
⚠️ **LLM in Trader Agent**: Nếu Trader Agent dùng sentiment agent (LLM-powered news), và output trực tiếp vào risk decision → **LLM in execution path**. Constraint nói: "LLM enrichment-only on exec path — no LLM in execute_signal()". `execute_signal()` đã bị deprecated, nhưng Trader Agent vẫn là entry point.

⚠️ **Volume ratio assumption**: `volume_ratio_5_20` tính từ 5-bar vs 20-bar volume — nhưng trong backtest, volume có thể không khả dụng (crypto spot volume không đáng tin cậy).

⚠️ **Hidden assumption**: `ForecastRiskPolicy` chỉ dùng daily vol, nhưng system chủ yếu trade trên hourly → **volatility understatement** (hourly vol < daily vol). Cần annualize nhất quán.

### D. Giấy / sửa / loại bỏ?
**Giữ** ForecastRiskPolicy:
- 🛠️ **Fix**: Trader Agent — LLM sentiment chỉ advisory (weight = 0 trong live mode), chỉ Technical + Risk agent vote vào position sizing.
- 🛠️ **Fix**: Thêm stop-loss level trong RiskDecision (ATR-based, 2x ATR).
- 🛠️ **Fix**: Annualize vol nhất quán theo timeframe (hourly: ×√8760, daily: ×√365).
- **Loại bỏ** LLM sentiment voting trong core path — chuyển sang advisory metadata.

---

## 7. EXECUTION SIMULATOR

### A. Code đang làm gì?
`FillModel` (simulation):
- `fill_market`: Sweep order book levels, apply `impact_bps` (power-law). Partial fills nếu book không đủ.
- `fill_limit`: Passive limit, deterministic queue position (hash-based) + `passive_fill_prob`.
- `ImpactModel`: temporary impact (power-law), adverse selection, post-fill mid windows.
- `OrderBookState`: bid/ask ladder, mid, spread.
- `order_planner.py`: `_select_order_type()` based on vol × liquidity.

### B. Đúng về quantitative/trading?
✅ **Tốt**:
- `fill_market` sweep theo book levels — realistic.
- `fill_limit` queue position deterministic (hash) → reproducible.
- `passive_fill_prob = exp(-hourly_vol / 0.025)` — reasonable, drops ~95% fill at vol=0.5%, ~2% at vol=5%.
- T2C per-symbol slippage: BTC=9.5bps, ETH=12.8bps, BNB=11.6bps — realistic.

⚠️ **Vấn đề**:
- `fill_limit` chỉ có 1 level rest (limit price), không có price improvement model.
- `impact_bps` power-law exponent chưa tune — có thể over/under-estimate impact cho crypto.
- `_select_order_type()` trong order_planner chưa được đọc kỹ — cần verify logic.
- **No latency model** — market order fill giả định ngay lập tức, không có reaction delay.

### C. Leakage / overfitting / hidden assumption?
⚠️ **Order book snapshot**: `OrderBookState` — làm sao lấy order book trong backtest? Nếu dùng reconstructed bằng tick data, có thể accurate. Nhưng nếu chỉ dùng OHLCV → không có depth → **assumed book shape**.

⚠️ **Impact model overfitting**: `impact_bps` có thể fit qua historical data → trong live có thể khác biệt.

⚠️ **Hidden assumption**: Latency = 0 trong execution. Trong thực tế, Binance có latency ~50-100ms → trong high-freq vol regime, slippage sẽ cao hơn.

### D. Giữ / sửa / loại bỏ?
**Giữ** simulator:
- 🛠️ **Fix**: Thêm latency model: market order bị slip thêm `latency_bps` (configurable, default 5bps).
- 🛠️ **Fix**: Multi-level limit order (rest ở n levels, không chỉ 1).
- 🛠️ **Fix**: Cross-check `impact_bps` model vs out-of-sample T2C data.
- **Loại bỏ** `fill_limit` queue jitter nếu impact không đáng tin — đơn giản hóa.

---

## 8. LIVE DATA PIPELINE

### A. Code đang làm gì?
`live_data_pipeline.py` (1007 lines):
- `BinanceDataFeed`: dry-run validated 20 bars. Crawl hourly + daily.
- `validate_live_hourly_bars()`: 13 fail-closed gates (timestamp, OHLC consistency, gap detection, stale candle, range check).
- `live_data_pipeline_main()`: CLI entry point.

### B. Đúng về quantitative/trading?
✅ **Rất tốt**:
- 13 fail-closed gates — enterprise-grade validation.
- Dry-run 20 bars PASS → ready for live.
- OHLC consistency checks (high >= max(open,close), low <= min(open,close)).

⚠️ **Vấn đề**:
- Chưa kết hợp equity data (yfinance) trong live mode — chỉ crypto.
- Không có circuit breaker khi data quality degradation liên tục.

### C. Leakage / overfitting / hidden assumption?
⚠️ **Hidden assumption**: Binance feed latency ~0 → không có network jitter model.

⚠️ **No data feed fallback**: Nếu Binance down, không có fallback feed (Coinbase? Kraken?).

### D. Giữ / sửa / loại bỏ?
**Giữ**, nhưng cần:
- 🛠️ **Fix**: Thêm fallback feed (Coinbase/Kraken) khi Binance lỗi.
- 🛠️ **Fix**: Equity data integration (yfinance) trong live mode.
- 🛠️ **Fix**: Circuit breaker khi data quality fails 3 lần liên tiếp.

---

## 9. LLM INTEGRATION

### A. Code đang làm gì?
- `context_enrichment.py` → `MarketContext` (regime_tags, anomaly_flags, cross_asset_signals, confidence_adjustment).
- `backtest_ask_agent` (temp=0, seed=42, no-cache) cho deterministic mode.
- `ab_test_context.py`: 3-mode A/B test (LLM-on, deterministic, replay).
- `features/llm/` (earnings, news, social) — enrichment features.
- `research_memory.py`: checksum'd bar-indexed replay store — 483 audit entries.

### B. Đúng về quantitative/trading?
✅ **Thiết kế đúng**:
- LLM **advisory-only**: confidence_adjustment dùng để scale exposure của signal deterministic, không generate signal.
- `confidence_adjustment` clamped [0.5, 1.5] — reasonable range.
- Deterministic mode (temp=0, seed=42) → reproducibility.
- A/B test: LLM-on vs LLM-off so sánh Sharpe → chứng minh LLM không gây overfitting.

⚠️ **Tuy nhiên**:
- `ab_test_btc_llm_on_v2` PID 171 — chưa chắc chắn đã chạy đủ bars (≥300). Cần verify.
- LLM enrichment chưa tích hợp vào `AdaptiveStrategyRouter` (diverging → entropy threshold increase) — **đây là P0.2 remaining task**.

### C. Leakage / overfitting / hidden assumption?
⚠️ **Potential overfitting**: LLM confidence được tính từ full market context — nếu trong backtest, LLM đã "seen" future data trong `research_memory.py` → **look-ahead bias**. Cần verify research_memory chỉ truy cập historical data đến thời điểm hiện tại.

⚠️ **Assumption**: LLM confidence_adjustment là stationary qua thời gian — nhưng LLM behavior có thể thay đổi với model updates.

⚠️ **Hidden assumption**: `regime_tags["diverging"]` → `entropy_threshold × 1.2` — ngưỡng 1.2 này được chọn như thế nào? Chưa có backtest để tune.

### D. Giữ / sửa / loại bỏ?
**Giữ** LLM enrichment architecture:
- 🛠️ **Fix**: Verify `research_memory.py` chỉ truy cập historical data ≤ current bar.
- 🛠️ **Fix**: Tune `entropy_threshold × 1.2` multiplier bằng walk-forward optimization.
- 🛠️ **Fix**: Thêm fallback: khi LLM call lỗi, confidence_adjustment = 1.0 (neutral).
- **Loại bỏ**: LLM sentiment trong Trader Agent voting (chuyển sang advisory metadata only — xem section 6).

---

## 10. RESEARCH / EVIDENCE SYSTEM

### A. Code đang làm gì?
- `e2e_system_test.py`: 9 scenarios, 98 checks — kiểm tra system end-to-end.
- `o_trade_1_volatile.py` (531 lines): Confidence-scaling backtest, 4-layer formula, 8 backtests × 6 regimes × 3 assets.
- `o_trade_2_execution_quality.py`: T2A (slippage), T2B (fill rate), T2C (vol×slippage), T2C-1 (equity calibration).
- `o_trade_345_eval.py`: T1B (hit rate), T1C (correlation), T3A (order type), T4A (confidence), T5A (diversification), T5B (kill switch).
- `alpha_research/` (pipeline.py, methodology.py, stats.py, holdout.py, provenance.py): Research pipeline với holdout testing.
- `selection_audit.py`: Immutable decision trail (SQLite + JSONL).

### B. Đúng về quantitative/trading?
✅ **Rất đạt chuẩn**:
- 75 LLM tests + 19 agent tests pass.
- A/B test deterministic LLM mode: 10/10 replay match.
- 8 T1A-style backtests với Bonferroni correction (alpha=0.003, PBO deflation=0.76).
- Shadow E2E produces ≥300 bars (920 bars từ 30-day runs).
- T2C per-symbol slippage calibrated (BTC=9.5bps, ETH=12.8bps, BNB=11.6bps).

⚠️ **Vấn đề**:
- `STR-0001/3/4` chưa hoàn thành — BacktestReportV2 + validator thiếu.
- Golden manifest/report chưa có git SHA binding.
- MAE/MFE metrics chưa computed trong report.
- T8A (2020-2021 bull run validation) chưa chạy.
- Cross-asset correlation stress test chưa thực hiện.

### C. Leakage / overfitting / hidden assumption?
⚠️ **Potential overfitting**: T1B/T1C chạy trên toàn bộ 1500 daily bars (2020-2026) — **không có walk-forward split**. Hit rate = 0.5031 có thể overfit tới recent data.

⚠️ **Hypothesis testing**: Bonferroni correction (alpha=0.003) trong T1A — tốt, nhưng PBO deflation=0.76 cho thấy **nhiều kì hằp**. Cần lưu ý.

⚠️ **No out-of-sample (OOS) holdout**: `holdout.py` tồn tại nhưng chưa dùng trong T1A/T1B/T1C. Tất cả đều in-sample.

⚠️ **Hidden assumption**: `trend_pullback` Sharpe trong T1A được tính từ backtest engine — nhưng engine chỉ hỗ trợ long-only, trong khi `trend_pullback` có short signals → **Sharpe có thể sai lệch**.

### D. Giữ / sửa / loại bỏ?
**Giữ** research infrastructure:
- 🛠️ **Fix**: Thêm OOS holdout split trong T1B/T1C (train: 2020-2023, test: 2024-2026).
- 🛠️ **Fix**: Hoàn thành STR-0001/3/4 (BacktestReportV2 + validator + git SHA binding).
- 🛠️ **Fix**: Thêm MAE/MFE metrics vào report.
- 🛠️ **Fix**: Chạy T8A (1000-bar bull run 2020-2021) và cross-asset stress test.
- **Loại bỏ**: T5A-variant (correlation-adjusted sizing) nếu không có alpha contribution sau fee.

---

## Tổng kết: Gains vs Losses

### Trước khi có LLM + Tournament
```
Data → Strategy (static) → Fixed position sizing → Backtest (long-only) → Execution (market-only)
• Confidence = 1.0 (no vol adjustment)
• Single strategy per regime (no pool)
• No anomaly detection
• No kill switch
• Execution: market orders only
• No cross-asset diversification analysis
```

### Sau khi có LLM + Tournament
```
Data → Regime(HMM+rule) → StrategyTournament(16 strategies, shadow mode)
  → ForecastRiskPolicy (deterministic)
  → ExecutionSimulator (market/limit/TWAP/VWAP)
  → LLM Enrichment (advisory: confidence [0.5,1.5], regime_tags, anomaly_flags)
  → Audit Trail (SQLite + JSONL)
  → Kill Switch (Sharpe < -0.5 → demote)
  → Cross-asset (crypto + 18 equities)
```

| Category | Before | After | Gain/Loss |
|----------|--------|-------|-----------|
| **Strategies** | 1 (static) | 16 (tournament) | ✅ Gain: adaptability |
| **Confidence** | Fixed 1.0 | Adaptive [0.5-1.5] | ✅ Gain: DD -20-38% |
| **Regime awareness** | None | HMM + rule-based | ✅ Gain: regime-switching params |
| **Risk controls** | Fixed pos sizing | Dynamic + kill switch | ✅ Gain: max-DD 30%, Sharpe breaker |
| **Execution** | Market-only | T3A matrix (limit/market) | ✅ Gain: 16-68% cost reduction |
| **Anomaly detection** | None | LLM anomaly_flags + regime_tags | ✅ Gain: advisory monitoring |
| **Cross-asset** | Crypto only | Crypto + 18 equities | ✅ Gain: 0.73 diversification benefit |
| **Audit trail** | Minimal | SQLite + JSONL + dashboard | ✅ Gain: full traceability |
| **Backtest** | Long-only | Still long-only* | ⚠️ Loss: short strategies not tested |
| **Walk-forward** | None | None | ⚠️ Loss: potential overfitting |
| **Reproducibility** | None | temp=0, seed=42, no-cache | ✅ Gain: deterministic replay |
| **E2E testing** | Fragmented | 98 checks, 9 scenarios | ✅ Gain: system confidence |

*Cần fix: thêm short-side support trong backtest engine.
"""
