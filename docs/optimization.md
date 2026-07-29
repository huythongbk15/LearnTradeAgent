# ⚡ Tối Ưu Hóa Hệ Thống

> Tổng hợp các tối ưu đã thực hiện từ Phase 0 → Phase 3, cách thức và hiệu quả.

---

## 📋 Mục Lục

1. [CLI Startup Time (Lazy Imports)](#1-cli-startup-time-lazy-imports)
2. [Data Pipeline Optimization](#2-data-pipeline-optimization)
3. [Backtest Parameter Optimization](#3-backtest-parameter-optimization)
4. [Walk-Forward Analysis](#4-walk-forward-analysis)
5. [LLM Provider & Cost Optimization](#5-llm-provider--cost-optimization)
6. [Paper Exchange Performance](#6-paper-exchange-performance)

---

## 1. CLI Startup Time (Lazy Imports)

### Vấn đề
Mỗi lệnh CLI (kể cả `status`, `reset` đơn giản) mất **~4 giây** do import toàn bộ
heavy modules (polars, ccxt, backtest engine, LLM...) ngay từ top-level.

### Giải pháp
Chuyển từ **static imports** sang **lazy imports**:

```python
# ❌ Trước đây (top-level — load mọi thứ khi chạy)
from trading_agent.data.collector import Collector
from trading_agent.data.storage import load_ohlcv

# ✅ Sau tối ưu (import trong function — chỉ load khi cần)
class _LazyConfig:
    """Config loaded on first access."""
    _cached = None
    def __getattr__(self, name):
        if self._cached is None:
            from trading_agent.config.loader import config as _cfg
            self.__class__._cached = _cfg
        return getattr(self._cached, name)

config = _LazyConfig()

# Heavy modules được import bên trong từng hàm CLI
def execution_status():
    from trading_agent.execution.engine import ExecutionEngine
    engine = ExecutionEngine()
    ...
```

Environment modules bị lazy-load:
- `polars` (data manipulation)
- `ccxt` (exchange connectivity — 100+ exchange modules)
- `trading_agent.backtest.engine` (backtest runner)
- `trading_agent.agents.*` (LLM agents — DeepSeek, OpenRouter)
- `trading_agent.strategies.*` (strategy registry)

### Kết quả

| Lệnh | Trước | Sau | Tốc độ |
|------|-------|-----|--------|
| `execution status` | ~4.0s | **0.22s** | **18x nhanh hơn** |
| `execution reset` | ~3.8s | **0.06s** | **63x nhanh hơn** |
| `info` | ~3.5s | **0.18s** | **19x nhanh hơn** |

### Cách thức áp dụng cho module mới
Khi thêm CLI command mới, luôn dùng **inline imports** bên trong function,
không import heavy modules ở top-level của `cli.py`.

---

## 2. Data Pipeline Optimization

### Vấn đề
- Fetch OHLCV tuần tự cho 5 symbols × 4 timeframes mất nhiều thời gian
- Lưu Parquet từng file riêng gây nhiều disk I/O nhỏ

### Giải pháp

**Trước — fetch tuần tự:**
```python
for symbol in symbols:
    for tf in timeframes:
        collector.fetch_ohlcv(symbol, tf, since=...)  # 1 lần CCXT call
```

**Sau — batch fetch + pagination:**
```python
# Collector tự động fetch theo trang (paginated) với progress bar
# Mỗi exchange 1 instance (cached), rate-limit tự động
collector.fetch_ohlcv(symbol, tf, since=...)
  ├── Tính số trang cần fetch (dựa trên timeframe + since)
  ├── Fetch từng trang với rate-limit
  ├── Merge + dedup
  └── Save Parquet (append-dedup)
```

**Tối ưu incremental update:**
```python
# update_ohlcv() chỉ fetch candles mới nhất
# Dùng timestamp cuối cùng trong storage làm since
# Giảm 95-99% lượng dữ liệu cần fetch
df = collector.update_ohlcv("BTC/USDT", "1h")
```

### Kết quả

| Metric | Trước | Sau |
|--------|-------|-----|
| Fetch 5 symbols × 4 TFs (3 năm) | ~45s (estimating) | **19s** |
| Incremental update (1 symbol) | ~2s | **0.15s** |
| Data quality check (all datasets) | ~30s | **5.2s** (cached exchange) |

---

## 3. Backtest Parameter Optimization

### Vấn đề
Chiến lược mặc định (MA Crossover với fast=20, slow=50) chưa được tối ưu,
hiệu suất thấp hơn nhiều so với optimized version.

### Giải pháp: Parameter Sweep
Chạy **grid search** trên không gian tham số, dùng Sharpe ratio làm objective:

**MA Crossover — sweep (fast, slow):**
```
Grid: fast ∈ [5, 10, 20, 30, 50], slow ∈ [20, 30, 50, 100, 200]
Kết quả: default (20,50) → Sharpe 0.175
         optimized (5,20) → Sharpe 1.67 → Return +71.96%
```

**RSI — sweep (period, oversold, overbought):**
```
Grid: period ∈ [7, 14, 21], oversold ∈ [20, 25, 30, 35],
      overbought ∈ [65, 70, 75, 80]
Kết quả: default (14,30,70) → Sharpe 0.38
         optimized (7,25,75) → Sharpe 1.06 → Return +66.75%
```

**Bollinger Bands — sweep (period, std):**
```
Grid: period ∈ [10, 20, 30, 40], std ∈ [1.5, 2.0, 2.5, 3.0]
Kết quả: default (20,2) → Sharpe 0.39
         optimized (10,2.5) → Sharpe 1.07 → Return +12.43%
```

### Kết quả tổng hợp

| Strategy | Default Return | Optimized Return | Chênh lệch |
|----------|---------------|------------------|------------|
| MA Crossover | +10.73% | **+71.96%** | **+61.23%** |
| RSI | +4.47% | **+66.75%** | **+62.28%** |
| Bollinger Bands | -0.31% | **+12.43%** | **+12.74%** |

### Cảnh báo Overfitting
Tối ưu tham số trên cùng dataset → overfitting cao. Cần walk-forward analysis
để xác nhận tính ổn định (xem mục 4).

---

## 4. Walk-Forward Analysis

### Vấn đề
Parameter sweep có nguy cơ overfit cao — tham số tối ưu trên historical data
có thể không hiệu quả trong tương lai.

### Giải pháp: Walk-Forward Validation

```
╔══════════════════╦══════════════════╗
║  INSAMPLE       ║   OUT-OF-SAMPLE  ║
║  (2023-07 →     ║   (2025-07 →     ║
║   2025-07)      ║    2026-07)      ║
╚══════════════════╩══════════════════╝
         → Optimize params → test
```

**Quy trình:**
1. Chia dữ liệu: **2 năm in-sample** (train) + **1 năm out-of-sample** (test)
2. Tối ưu params trên in-sample (grid search)
3. Test params tối ưu trên out-of-sample
4. So sánh: default vs optimized vs random walk (benchmark)

### Kết quả Walk-Forward

| Strategy | OOS Return (default) | OOS Return (optimized) | Benchmark | Kết luận |
|----------|---------------------|----------------------|-----------|----------|
| **MA Crossover** | **+4.44%** | **+11.22%** | -3.93% | ✅ Optimized vượt default |
| **RSI** | **+0.08%** | **+3.29%** | -3.93% | ⚠️ Lợi nhuận thấp nhưng vẫn hơn |
| **Bollinger Bands** | **+0.29%** | **+1.70%** | -3.93% | 📉 Lợi nhuận rất thấp |

**Kết luận chính:**
- MA Crossover ổn định nhất, optimized params cho kết quả vượt trội
- RSI và BBands có dấu hiệu overfit dù vẫn hơn default
- Tất cả optimized strategies đều vượt benchmark (buy-and-hold)
- **Khuyến nghị:** MA Crossover (fast=5, slow=20) là chiến lược baseline tốt nhất

### Stability Analysis
Kiểm tra độ nhạy của tham số:
```python
# Thay đổi nhẹ tham số → thay đổi lớn trong kết quả?
# Nếu có → chiến lược không ổn định
stability_score = 1 - std(oss_returns) / mean(oss_returns)
```

---

## 5. LLM Provider & Cost Optimization

### Vấn đề
Sử dụng LLM cho multi-agent analysis có thể tốn kém với các model trả phí.
Cần tìm provider free/giá rẻ với context đủ lớn.

### So sánh providers

| Provider | Free Tier | Context | Giá (trả phí) / 1M tokens | Ghi chú |
|----------|-----------|---------|--------------------------|---------|
| **Groq** | ✅ 1000 req/ngày | 128K | — | Tốt nhất free tier |
| **OpenRouter (DeepSeek V4 Flash)** | ✅ 10 req/ngày | 128K | $0.14/$0.28 | **Rẻ nhất trả phí** |
| **Gemini API** | ✅ 60 req/phút | 1M | — | Context lớn nhất |
| **DeepSeek (direct)** | ✅ Daily limit | 1M | $0.14/$0.28 | Context khủng |
| **OpenAI GPT-4o-mini** | ❌ | 128K | $0.15/$0.60 | Chi phí trung bình |
| **Claude Haiku** | ❌ | 200K | $0.25/$1.25 | Đắt hơn |

### Giải pháp triển khai

**Primary → Secondary → Fallback chain:**
```yaml
# config.yaml
llm:
  provider: openrouter
  model: deepseek/deepseek-chat-v4-flash    # Primary: DeepSeek V4 Flash
  max_tokens: 500
  fallback:
    - provider: openai
      model: gpt-4o-mini                     # Fallback 1
    - provider: deepseek
      model: deepseek-chat                   # Fallback 2
    - provider: ollama
      model: qwen2.5:7b                      # Fallback 3 (local)
```

**DeepSeek V4 Flash (OpenRouter free tier):**
- Giá: ~$0.00003/request (với max_tokens=500)
- Chi phí cho 1 cycle 4 agents: ~$0.00009
- Context: 128K — đủ cho cả chart + indicators
- Stable, response nhanh

### Kết quả
| Metric | GPT-4o-mini | DeepSeek V4 Flash |
|--------|-------------|-------------------|
| Cost per analysis (4 agents) | ~$0.002 | **~$0.00009** |
| Responses / $1 | ~500 | **~11,000** |
| Context window | 128K | 128K |
| Quality | Tốt | Tốt (chuyên trading) |

---

## 6. Paper Exchange Performance

### Vấn đề
Paper exchange lưu state JSON với toàn bộ lịch sử — càng chạy lâu càng chậm.

### Giải pháp
- **Giới hạn equity history:** Chỉ giữ 5,000 snapshot gần nhất
  ```python
  self.equity_history = data.get("equity_history", [])[-5000:]
  ```
- **Không import polars/ccxt trong paper_exchange:** Lazy import chỉ khi cần update giá từ data
- **State file nhỏ:** Chỉ lưu positions, balances (đủ để khôi phục)

### Kết quả
| Operation | Time |
|-----------|------|
| Load state (1000 trades) | ~15ms |
| Save state | ~5ms |
| Place order | ~0.3ms |
| Update prices + check 5 positions | ~0.5ms |

---

## 🏆 Tổng Kết Tối Ưu

| Tối ưu | Tác động | Effort | Priority |
|--------|----------|--------|----------|
| Lazy imports CLI | 19-63x startup time | Thấp | **P0** |
| Incremental data fetch | 95-99% less data | Thấp | **P0** |
| Parameter sweep | +61-62% return | Trung bình | **P1** |
| Walk-forward validation | Phát hiện overfit | Trung bình | **P1** |
| LLM cost optimization | 22x cheaper than GPT-4o-mini | Thấp | **P3** |
| Paper exchange state limit | Giữ speed ổn định | Thấp | **P3** |
| Exchange caching | Tái sử dụng connection | Thấp | **P0** |

---

> 📖 Quay lại [tài liệu chính](README.md)
