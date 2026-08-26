# 🤖 Trading Agent System

**Multi-Agent AI Trading Platform** — kết hợp LLM agents với chiến lược giao dịch systematic,
multi-exchange, multi-asset (Crypto + Stocks + Forex + Futures/Options).

[![CI](https://github.com/huythongbk15/LearnTradeAgent/actions/workflows/ci.yml/badge.svg)](https://github.com/huythongbk15/LearnTradeAgent/actions/workflows/ci.yml)

> ⚠️ **SAFETY STATUS — READ FIRST**
>
> Repository này **có chứa live-trading code** (Binance/Alpaca/OANDA adapters, live runner,
> protective orders). Tuy nhiên:
>
> * **Mainnet trading status: `NO-GO`** — real-money mainnet chưa được chứng nhận production-ready.
> * Có code live **không** đồng nghĩa được phép dùng vốn thật.
> * Việc deploy/chạy hệ thống **không tự động enable mainnet** — hai việc độc lập.
> * Release flow bắt buộc: Backtest → Paper → Testnet → Acceptance → Soak → Drills →
>   Operator approval → Mainnet canary → Controlled scaling.
>
> Chi tiết: [`docs/LIVE_TRADING_TODO.md`](docs/LIVE_TRADING_TODO.md) · [`docs/CAPABILITY_MATRIX.md`](docs/CAPABILITY_MATRIX.md)

---

## Dự án này là gì?

Hệ thống giao dịch tự động theo hướng nghiên cứu (research-oriented):

- **Data pipeline**: CCXT → Polars → Parquet, fetch/validate/incremental update
- **Backtest engine**: vectorized + event-driven, walk-forward, OOS, parameter sweep
- **Execution Simulator V2**: event-driven execution simulator (order book, spread/depth,
  partial fills, latency, impact + decay, maker/taker fee, precision rules) với P&L
  attribution tách alpha khỏi execution cost + RealityGapReport
  (`trading_agent.execution.simulator`, `scripts/simulate_execution.py`)
- **Research governance**: immutable StrategyArtifact, promotion lifecycle, uncertainty
  gate (uncertainty → chỉ giảm risk/abstain), 9 abstention reason codes, drift detection
  + StrategyHealthState, multiple-testing trials tracking (`trading_agent.research`)
- **AI agents**: Technical · Sentiment · Risk · Trader (LLM weighted voting, fallback chain)
- **Execution**: paper exchange, risk controller, circuit breaker, kill switch, live broker
  facade (Alpaca paper, Binance testnet), trusted-time market-data checks, protective orders
- **Ops**: logging, SQLite, metrics, Streamlit/Web UI, Telegram alerts, Docker, CI/CD, Trivy

## Current maturity

| Mặt | Trạng thái |
| --- | --- |
| Research / backtest | Hoạt động, nhiều strategy + WFO — xem [`docs/RESEARCH_EVIDENCE.md`](docs/RESEARCH_EVIDENCE.md) |
| Paper trading | Hoạt động (Alpaca paper validated) |
| Testnet (Binance) | P0.1–P0.3 acceptance pass (opt-in, testnet.binance.vision) — chờ P3 soak 30 ngày |
| Mainnet (vốn thật) | **NO-GO** — xem [`docs/LIVE_TRADING_TODO.md`](docs/LIVE_TRADING_TODO.md) |
| CI/CD | Xem GitHub Actions badge — không claim "xanh" cố định trong docs |
| Production validated | Chưa — xem [`docs/CAPABILITY_MATRIX.md`](docs/CAPABILITY_MATRIX.md) |

## Quick start

```bash
# 1. Cài đặt (Python 3.12)
pip install -e ".[dev,web,infra]"

# 2. Fetch dữ liệu
trading-agent data fetch BTC/USDT --since 2026-07-01

# 3. Backtest
trading-agent backtest run ma_crossover BTC/USDT

# 4. Phân tích multi-agent
trading-agent agents analyze BTC/USDT

# 5. Xem hệ thống
trading-agent system health
```

Yêu cầu: **Python >=3.12,<3.13**. Credentials template: [`.env.example`](.env.example)
(không chứa secret thật).

## Kiến trúc tổng quan

```
RESEARCH PLANE   Data → Features → Strategies → Backtest → Statistical Validation → Evidence
DECISION PLANE   Strategies/Agents → Portfolio → Risk → Order Intent
EXECUTION PLANE  Order Planner → Broker Adapter → Order Lifecycle → Fill Ledger → Reconciliation → Protective Orders
CONTROL PLANE    Configuration → Release Gates → Kill Switch → Leader/Fencing → Audit
OBSERVABILITY    Logs → Metrics → Alerts → Incident Response
```

Chi tiết: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)

## Tài liệu chính thức (authoritative)

| Tài liệu | Nội dung |
| --- | --- |
| [`docs/README.md`](docs/README.md) | Cổng tài liệu chuẩn, phân biệt CURRENT / TARGET / HISTORICAL |
| [`docs/vi/README.md`](docs/vi/README.md) | Trung tâm tài liệu tiếng Việt và thuật ngữ song ngữ |
| [`docs/tutorials/README.md`](docs/tutorials/README.md) | Course V2 theo luồng evidence-first |
| [`docs/guides/RESEARCH_TO_PRODUCTION.md`](docs/guides/RESEARCH_TO_PRODUCTION.md) | Luồng strategy từ research đến production gate |
| [`docs/LIVE_TRADING_TODO.md`](docs/LIVE_TRADING_TODO.md) | Live readiness — gates P0-P3, mainnet NO-GO |
| [`docs/CAPABILITY_MATRIX.md`](docs/CAPABILITY_MATRIX.md) | Mức độ trưởng thành từng capability |
| [`docs/RESEARCH_EVIDENCE.md`](docs/RESEARCH_EVIDENCE.md) | Bằng chứng research (in-sample/OOS/WF/holdout) |
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | Kiến trúc theo code thực tế |
| [`docs/DEVELOPMENT.md`](docs/DEVELOPMENT.md) | Hướng dẫn phát triển, test, lint |
| [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md) | Deploy topology, fail-closed, rollback |
| [`docs/SECURITY.md`](docs/SECURITY.md) | Supply chain, credentials, hardening |
| [`docs/LIVE_TRADING_RUNBOOK.md`](docs/LIVE_TRADING_RUNBOOK.md) · [`docs/RUNBOOK.md`](docs/RUNBOOK.md) | Vận hành |

## Stack

| Layer | Công nghệ |
| --- | --- |
| CLI | Click + Rich |
| Data | CCXT → Polars → Parquet, SQLite/TimescaleDB |
| Backtest | Custom engine vectorized (Polars) + event-driven |
| AI Agents | DeepSeek V4 Flash (primary) → OpenAI → DeepSeek → Ollama (fallback) |
| Exchanges | CCXT (8 CEX) · Web3.py (DEX) · Alpaca (stocks) · OANDA (forex) |
| Portfolio | Risk parity · HRP · Black-Litterman · Kelly |
| Infra | Docker · GitHub Actions · K8s (multi-region) |

---

## Disclaimer

**Chỉ dành cho mục đích nghiên cứu và giáo dục.** Giao dịch tiền mã hóa tiềm ẩn rủi ro lớn.
Không sử dụng số tiền bạn không thể mất. Luôn bắt đầu với paper trading trước khi giao dịch thật.
**Mainnet status: NO-GO.**
