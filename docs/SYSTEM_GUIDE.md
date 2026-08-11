# 📘 Tài liệu tổng hợp hệ thống — Trading Agent System

> Tài liệu chính thức, nguồn duy nhất để hiểu hệ thống: **tính năng, cách dùng cụ thể,
> các phương pháp trade, UI/UX và notification**.
> Ngôn ngữ cấu hình: `config/config.yaml` · Biến bí mật: `.env` · CLI: `python -m trading_agent.cli`

> ⚠️ **SAFETY STATUS — READ FIRST**
>
> * **Mainnet trading status: `NO-GO`.** Code live có tồn tại (Binance/Alpaca/OANDA), nhưng
>   real-money mainnet **chưa được chứng nhận production-ready**.
> * Có code live **không** đồng nghĩa được phép dùng vốn thật; deploy không tự enable mainnet.
> * Thứ tự bắt buộc: Backtest → Paper → Testnet → Acceptance → Soak → Drills → Operator
>   approval → Mainnet canary → Controlled scaling.
> * Chi tiết gates: [`LIVE_TRADING_TODO.md`](LIVE_TRADING_TODO.md) · Maturity:
>   [`CAPABILITY_MATRIX.md`](CAPABILITY_MATRIX.md)

---

## 1. Tổng quan hệ thống

Hệ thống **multi-agent AI crypto trading** kết hợp:
- **LLM agents** (DeepSeek V4 Flash qua OpenCode — miễn phí) phân tích kỹ thuật, sentiment, rủi ro.
- **Chiến lược truyền thống** (MA Crossover, RSI, Bollinger, Enhanced MA, ensemble, regime-switching).
- **Nhiều chế độ giao dịch**: backtest → paper (giả lập nội bộ) → Alpaca paper → Binance testnet → Binance live/futures.
- **Hạ tầng**: TimescaleDB/SQLite, Redis, Grafana, Docker, CI/CD, cron, Telegram alerts.

```
┌──────────────────────────────────────────────────────────────────┐
│  DATA LAYER        ccxt/binance → parquet/duckdb → SQLite DB     │
│  ANALYSIS LAYER    10 strategies + 4 AI agents (TA/Sentiment/    │
│                    Risk/Trader) + meta-learning + options        │
│  EXECUTION LAYER   Paper exchange | Alpaca | Binance testnet/live│
│  RISK LAYER        Risk controller, ATR stop, DD guard, kill     │
│                    switch, position sizing (Kelly/vol-target)    │
│  UI/UX LAYER       CLI (rich tables) | Streamlit | Grafana       │
│  NOTIFY LAYER      Telegram (trades/risk/daily/status) | console │
│  OPS LAYER         Docker, cron, backup, CI/CD, monitoring       │
└──────────────────────────────────────────────────────────────────┘
```

---

## 2. Tính năng chính

| Nhóm | Tính năng | Lệnh |
|---|---|---|
| 📊 **Dữ liệu** | Fetch OHLCV 6 timeframe, 10+ cặp Binance spot/futures; lưu parquet; pre-compute ATR | `data fetch/fetch-all/enrich-at/export`, `make fetch` |
| 🔬 **Backtest** | 10 chiến lược, 8+ metrics (PnL, Sharpe, PF, DD, win rate), walk-forward, đa symbol | `backtest list/run`, `make backtest` |
| 🤖 **AI Agents** | 4 agent (Technical/Sentiment/Risk/Trader) chạy LLM miễn phí, kết hợp tín hiệu | `agents analyze/list`, `make analyze` |
| 💱 **Giao dịch** | Paper exchange nội bộ, live Alpaca paper, Binance testnet/live, futures, options | `execution`, `live`, `options` |
| 🛡 **Rủi ro** | Risk controller, ATR trailing stop, max DD guard, position sizing, kill switch | `execution risk/close`, `live monitor` |
| 📈 **Portfolio** | Black-Litterman, Monte Carlo, efficient frontier, auto-rebalancer | `portfolio` |
| 🧠 **Meta-learning** | MAML/Reptile/Meta-SGD/ANIL, regime adaptation | `meta` |
| 🖥 **UI** | CLI rich tables, Streamlit dashboard, Grafana/Prometheus | `make dashboard`, `make docker-up` |
| 🔔 **Notify** | Telegram: giao dịch, risk breach, daily summary, status; console | config `alerts` |
| ⚙️ **Ops** | Docker compose (TimescaleDB+Redis+Grafana), backup, cron, CI/CD, health check | `system health`, `make docker-*` |

---

## 3. Cài đặt & cấu hình lần đầu

### 3.1 Cài đặt
```bash
cd <repo-root>
make install          # poetry install
make test             # chạy 450+ tests
make info             # kiểm tra cấu hình hệ thống
```

### 3.2 Biến môi trường (`.env`) — các loại bí mật
```bash
# --- LLM (mặc định OpenCode — MIỄN PHÍ, không cần key) ---
# OPENCODE_API_KEY=...        # tùy chọn, nếu có tăng quota

# --- Alpaca (paper/crypto) — https://alpaca.markets ---
ALPACA_API_KEY=...
ALPACA_API_SECRET=...
ALPACA_BASE_URL=https://paper-api.alpaca.markets   # paper; đổi sang live nếu muốn real

# --- Binance (spot + futures) ---
BINANCE_API_KEY=...
BINANCE_API_SECRET=...
# Binance TESTNET — tiền ảo, chạy như live thật (testnet.binance.vision)
BINANCE_TESTNET_API_KEY=...
BINANCE_TESTNET_API_SECRET=...

# --- Telegram notification ---
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...

# --- Backend (có thể bỏ qua nếu không dùng docker) ---
# POSTGRES_* REDIS_URL ...
```

### 3.3 Cấu hình chính (`config/config.yaml`)
- `exchanges`: bật/tắt Binance spot, futures, Bybit, OKX — `enable: true/false`.
- `data`: exchange mặc định, timeframe, storage (parquet/csv/duckdb), batch size.
- `symbols`: danh sách cặp muốn trade/fetch.
- `llm`: `provider: opencode`, `model: deepseek-v4-flash-free`, temperature, max_tokens, fallback chain.
- `alerts`: `console.enabled`, `telegram.enabled` (+ token/chat_id từ env).
- Tham số từng chiến lược nằm trong registry; xem qua `backtest list`.

> ⚠️ **Bảo mật**: không commit `.env`. Template mẫu: `config/credentials.yaml.example`.

---

## 4. Cách sử dụng cụ thể — CLI & Makefile

Mọi lệnh gõ từ thư mục gốc: `<repo-root>`

### 4.1 Dữ liệu
```bash
make fetch S=BTC/USDT T=1h            # fetch 1 cặp, 1 timeframe
make fetch-all                        # fetch toàn bộ symbols/timeframes đã cấu hình
make datasets                         # liệt kê dataset đã lưu
make inspect S=BTC/USDT T=1h          # xem nhanh dữ liệu
python -m trading_agent.cli data export --symbol BTC/USDT --timeframe 1h --format csv
python -m trading_agent.cli data enrich-at --symbol BTC/USDT --timeframe 1h   # pre-compute ATR
```

### 4.2 Backtest
```bash
python -m trading_agent.cli backtest list                                     # xem 10 strategy
make backtest S=BTC/USDT T=1h STRAT=enhanced_ma                               # backtest 1 strategy
python -m trading_agent.cli backtest run enhanced_ma -s BTC/USDT -t 1h        # tương đương
```
Kết quả gồm: total return, Sharpe, Sortino, Profit Factor, max DD, win rate, số lệnh.

### 4.3 AI Agents
```bash
make analyze S=BTC/USDT T=1h                     # 4 agents phân tích 1 symbol
python -m trading_agent.cli agents analyze -s BTC/USDT -t 1h
python -m trading_agent.cli agents list          # danh sách agent
```
Mỗi agent trả **signal + confidence**: Technical/Sentiment/Risk/Trader; Trader tổng hợp → hành động (BUY/SELL/HOLD).

### 4.4 Paper exchange nội bộ (không cần broker)
```bash
make trade S=BTC/USDT T=1h       # full cycle: agents → signal → paper trade
make status                      # portfolio paper hiện tại
make trades                      # lịch sử giao dịch
make risk                        # trạng thái risk controller
make close-all                   # KILL SWITCH — đóng mọi vị thế
make reset                       # reset paper exchange
```

### 4.5 Portfolio
```bash
python -m trading_agent.cli portfolio optimize -s BTC/USDT ETH/USDT SOL/USDT --method black-litterman
python -m trading_agent.cli portfolio frontier --symbols BTC/USDT,ETH/USDT,SOL/USDT --plot
python -m trading_agent.cli portfolio monte-carlo --symbols BTC/USDT,ETH/USDT,SOL/USDT
python -m trading_agent.cli portfolio rebalancer init|run|status
```

### 4.6 Meta-learning & Options
```bash
python -m trading_agent.cli meta train --regimes bull,bear,sideways
python -m trading_agent.cli meta adapt --regime bear
python -m trading_agent.cli options chain --symbol SPY
python -m trading_agent.cli options covered-call --symbol SPY
```

### 4.7 System
```bash
make db-stats                    # thống kê DB
python -m trading_agent.cli system health     # kiểm tra toàn bộ components
python -m trading_agent.cli system daily      # performance daily summary
python -m trading_agent.cli system logs       # log gần nhất (container)
python -m trading_agent.cli llm stats         # cache & cost LLM
```

---

## 5. Phương pháp trade — các option

### 5.1 So sánh các chế độ giao dịch

| Chế độ | Tiền | Rủi ro | Dùng khi | Cách chạy |
|---|---|---|---|---|
| **Backtest** | ảo (lịch sử) | không | Kiểm chứng ý tưởng, tối ưu tham số | `make backtest` |
| **Paper Exchange nội bộ** | ảo ($100k) | không | Test full cycle agents→execution | `make trade` |
| **Live Alpaca Paper** | ảo (Alpaca paper $100k) | không | Chạy như live thật, broker API thật | `python scripts/live_cron_runner.py --execute` |
| **Binance Testnet** | ảo (testnet) | không | Test khớp lệnh sàn Binance thật | `python scripts/live_enhanced_ma_binance.py --testnet` |
| **Binance Live Spot** | thật | có | Chỉ sau testnet, dry-run và checklist vận hành | `python scripts/live_enhanced_ma_binance.py --execute --confirm-live ...` |
| **Binance Futures** | thật | có | Đòn bẩy, cả long/short | CLI `live` + cấu hình futures |
| **Options (US)** | thật | có | Covered call, CSP, vol selling... | `options` (cần broker options) |

> ✅ **Khuyến nghị quy trình**: Backtest → Paper nội bộ → **Alpaca paper / Binance testnet** (validate vài tuần) → Live thật với risk guard.

### 5.2 Các chiến lược (mặc định & khuyến nghị)

| Strategy | Ý tưởng | Ghi chú |
|---|---|---|
| `ma_crossover` | Fast MA cắt lên/cắt xuống slow MA | Cơ bản nhất |
| `rsi` | RSI oversold/overbought | |
| `bbands` | Chạm band dưới/trên | |
| `enhanced_ma` ⭐ | MA(20,80) + ADX>40 filter + ATR trailing stop + risk sizing | **Chạy live hiện tại** (Single 1h) |
| `ma_adx` | MA + lọc ADX đơn giản | |
| `ma_vol_target` | MA + sizing theo volatility | |
| `ensemble_ma_adx` | Ensemble nhiều MA+ADX, weight theo regime | |
| `ma_adx_regime` | MA+ADX tham số động theo regime | |
| `regime_switching` | Chuyển đổi chiến lược theo regime (bull/bear/sideways) | Dùng trong CLI `live run` |
| `agent_ensemble` | Kết hợp tín hiệu 4 AI agents | Có thể bật/tắt LLM |

**Live hiện tại (paper)**: `Enhanced MA (20,80,40)` timeframe **1h**, 4 cặp BTC/ETH/SOL/AVAX, ADX filter > 40, ATR trailing stop, equity target ~$100k.
```bash
# Chạy live paper 1 lần (khuyến nghị qua cron):
python scripts/live_cron_runner.py --execute        # dry-run mặc định
python scripts/live_cron_runner.py --execute --live  # cho phép đặt lệnh (paper Alpaca)
python scripts/live_status_report.py                 # báo cáo trạng thái nhanh
```

### 5.3 Khung quản trị rủi ro
- **Risk controller**: theo dõi equity peak, max drawdown guard, tự chặn giao dịch khi DD vượt ngưỡng.
- **ATR trailing stop**: tự động bảo vệ lợi nhuận (đã pre-compute cho dữ liệu).
- **Position sizing**: đa dạng — fixed, vol-target, Kelly (CLI `live run --sizing`).
- **Kill switch**: `make close-all` hoặc `execution close --all`.
- **Giới hạn tần suất**: giới hạn spread/khối lượng, tránh over-trading (tham số trong config).
- Nhật ký live luôn kiểm tra `🛡 Risk: ... trading allowed` trước khi đặt lệnh.

---

## 6. UI / UX

### 6.1 CLI (mặc định)
- Bảng rich table màu sắc, dấu trạng thái ✅/🚀/🛡 — đọc nhanh trên terminal.
- Khởi động nhanh (~0.2s) nhờ lazy import; tất cả lệnh có `--help`.

### 6.2 Streamlit Dashboard
```bash
make dashboard        # streamlit run dashboard/app.py
# Mở http://localhost:8501 — bảng equity, positions, trade history, metrics
```

### 6.3 Grafana + Prometheus (hạ tầng docker)
```bash
make docker-up        # TimescaleDB + Redis + Grafana
make docker-logs      # log container
# Grafana: http://localhost:3000 (metrics từ /metrics endpoint của app)
```

### 6.4 Web UI (React + FastAPI) — khuyến nghị
```bash
bash scripts/webui.sh start      # http://localhost:8000 — start/stop/status/restart
```
8 tab: **Dashboard** (equity chart realtime, positions, trades) · **AI Agents** · **Backtest** (so sánh nhiều strategy, % tiến độ) · **Dữ liệu** (fetch) · **Portfolio** (weights pie, optimize) · **Live** · **Logs** · **Hệ thống**.
- **📋 Logs realtime**: xem `trading_agent.log`/`server.log` trực tiếp trên web (tự làm mới 2s), lọc theo level (INFO/WARNING/ERROR/DEBUG), tìm kiếm, pause auto-scroll — tiện theo dõi mỗi khi chạy tiến trình.
- Tiến trình chạy (fetch/backtest/live) hiện cả **% + dòng log stream realtime** ngay trong panel.

### 6.5 DB & logs
```bash
make db-stats         # thống kê SQLite/Timescale
python -m trading_agent.cli system logs      # log container app
tail -f logs/*.log                            # log local (scripts/)
```

---

## 7. Notification — Telegram

### 7.1 Cấu hình
1. Tạo bot với **@BotFather** → lấy `TELEGRAM_BOT_TOKEN`.
2. Lấy `chat_id` (gửi tin nhắn cho bot rồi xem update, hoặc dùng `@userinfobot`).
3. Thêm vào `.env`:
```bash
TELEGRAM_BOT_TOKEN=123456:ABC...
TELEGRAM_CHAT_ID=123456789
```
4. Bật trong `config/config.yaml`:
```yaml
alerts:
  console:
    enabled: true
  telegram:
    enabled: true
```

### 7.2 Các loại thông báo
| Loại | Nội dung | Kích hoạt |
|---|---|---|
| **Trade alerts** | Lệnh fill/fail, mở/đóng vị thế | khi có giao dịch trong live |
| **Risk alerts** | Vượt ngưỡng drawdown, risk blocked | risk controller |
| **Daily summary** | Performance hôm nay | `system daily` / cron |
| **Status report** | Equity, positions, ADX, signal hàng ngày | `live_status_report.py` / cron |
| **CI/CD** | Kết quả build/test GitHub Actions | GitHub → Telegram webhook |

> Nếu `telegram.enabled: false` → tin nhắn chỉ in ra console (log). Xem log: `python -m trading_agent.cli system logs`.

---

## 8. Tự động hóa (cron)

Hệ thống có cron để chạy live định kỳ (thường mỗi khung 1h sau khi có candle đóng):
```bash
# Ví dụ: chạy live paper mỗi giờ
0 * * * * cd <repo-root> && python scripts/live_cron_runner.py --execute --live >> logs/live.log 2>&1
```
- Kiểm tra cron daemon đang chạy: `service cron status` (WSL cần `sudo service cron start`).
- Dùng skill `cron` của agent nếu muốn quản lý job qua QwenPaw (`qwenpaw cron create ... --agent-id trading`).

---

## 9. Ops & Troubleshooting nhanh

| Triệu chứng | Kiểm tra | Xử lý |
|---|---|---|
| `ModuleNotFoundError: alpaca` | SDK optional | `pip install -e ".[dev]"` |
| Live không đặt lệnh | `🛡 Risk ... trading allowed`? | Xem DD guard / cấu hình risk |
| Telegram không gửi | `alerts.telegram.enabled`? token? | Bật config, set `.env`, test bot |
| CLI chậm | — | Dùng lazy import đã tối ưu; kiểm tra `make info` |
| DB rác / muốn reset | — | `make reset` (paper), `python -m trading_agent.cli execution reset` |
| Docker lỗi | `make docker-logs` | `make docker-down && make docker-up` |
| Cron daemon tắt (WSL) | `service cron status` | `sudo service cron start` |
| Nghi ngờ overfitting | OOS/WFO | Tham số đã tối ưu chỉ nên dùng cho style tương tự; luôn validate OOS |

**Full runbook**: xem `docs/RUNBOOK.md` (bản production) và `docs/LIVE_TEST_GUIDE.md` (bản live). **Kiến trúc**: `docs/TRAADING_SYSTEM_OVERVIEW.md`, `docs/PROJECT_SUMMARY.md`, `docs/project-structure.md`.

---

*Cập nhật lần cuối: 2026-08-10 · Xem docs/README.md để điều hướng toàn bộ tài liệu.*
