# ⚡ Quick Start

> 10 lệnh để trải nghiệm toàn bộ hệ thống (Phase 0 → 3).

```bash
# 1. Cài đặt
poetry install

# 2. Fetch dữ liệu Bitcoin
poetry run trading-agent data fetch BTC/USDT --since 2026-07-01

# 3. Kiểm tra dữ liệu
poetry run trading-agent data inspect BTC/USDT

# 4. Chạy backtest
poetry run trading-agent backtest run ma_crossover BTC/USDT

# 5. Phân tích multi-agent
poetry run trading-agent agents analyze BTC/USDT

# 6. Check portfolio
poetry run trading-agent execution status

# 7. Full cycle: agents → trade
poetry run trading-agent execution run BTC/USDT

# 8. Xem trade history
poetry run trading-agent execution trades

# 9. Xem risk status
poetry run trading-agent execution risk

# 10. Thông tin hệ thống
poetry run trading-agent info
```

📍 **Mô hình và luồng chuẩn:** [Core System](CORE_SYSTEM.md) · [Main-flow Validation](operations/MAIN_FLOW_VALIDATION.md)

---

## Cấu hình nhanh

Mở `config/config.yaml` và chỉnh sửa:

```yaml
symbols:
  binance:
    - "BTC/USDT"
    - "ETH/USDT"
    - "SOL/USDT"

llm:
  provider: openrouter
  model: deepseek/deepseek-chat-v4-flash
  api_key: "sk-or-v1-..."    # Lấy từ https://openrouter.ai/keys
```

---

## CLI Reference (tất cả commands)

```bash
# ── System ──
trading-agent info
trading-agent config validate

# ── Data Pipeline (Phase 0) ──
trading-agent data fetch <symbol>              # Fetch OHLCV
trading-agent data update <symbol>             # Incremental update
trading-agent data inspect <symbol>            # Inspect stored data
trading-agent data validate [--symbol]         # Data quality check
trading-agent data list-datasets               # List all datasets
trading-agent data download-all                # Download all config'd
trading-agent data export <symbol>             # Export CSV/JSON

# ── Backtest (Phase 1) ──
trading-agent backtest list                    # List strategies
trading-agent backtest run <strategy> <symbol> # Run backtest

# ── AI Agents (Phase 2) ──
trading-agent agents list                      # List agents
trading-agent agents analyze <symbol>          # Multi-agent analysis

# ── Execution (Phase 3) ──
trading-agent execution status                 # Portfolio summary
trading-agent execution run <symbol>           # Agents → trade
trading-agent execution trades                 # Trade history
trading-agent execution close [sym]            # Close position(s)
trading-agent execution close --all            # Kill switch
trading-agent execution risk                   # Risk controller
trading-agent execution reset                  # Reset to fresh
```

---

## Shortcuts (Makefile)

```bash
make info                 # trading-agent info
make fetch S=BTC/USDT     # Fetch data
make inspect S=BTC/USDT   # Inspect data
make datasets             # List datasets
make backtest             # Run backtest
make analyze S=BTC/USDT   # Multi-agent analysis
make status               # Execution status
make shell                # Python shell
```

---

## Tài liệu liên quan

| File | Nội dung |
|------|---------|
| [🏛 Kiến trúc](ARCHITECTURE.md) | Sơ đồ tổng quan, các layer |
| [🧭 Core System](CORE_SYSTEM.md) | Luồng cốt lõi và invariant |
| [🗺 Documentation Map](DOCUMENTATION_MAP.md) | Taxonomy và thứ tự đọc |
| [🏛 Kiến trúc](ARCHITECTURE.md) | Các plane và boundary |
| [✅ Main-flow Validation](operations/MAIN_FLOW_VALIDATION.md) | Kiểm tra reproducible |
