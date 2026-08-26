# 🏛 Kiến Trúc Hệ Thống

> File này giải thích kiến trúc tổng thể của Trading Agent System — các layer,
> luồng dữ liệu, cách các module giao tiếp với nhau.
>
> **Phiên bản hiện tại:** v0.3.0 (Phase 0–3 đã hoàn thành)

---

## 📐 Kiến trúc tổng thể (Layer Diagram)

```mermaid
graph TB
    subgraph DATA["📡 DATA LAYER (Phase 0)"]
        CCXT[CCXT<br/>100+ exchanges]
        PARQ[Parquet Files<br/>data/raw/]
        PL[Polars DataFrames]
        CCXT -->|fetch OHLCV| PARQ
        PARQ -->|load| PL
    end

    subgraph BACKTEST["🧪 BACKTEST LAYER (Phase 1)"]
        SM[Strategy Manager<br/>4 strategies<br/>MA · RSI · BBands · MACD]
        BE[Backtest Engine<br/>Vectorized Polars]
        PM[Performance Metrics<br/>Sharpe · Return · DD]
        OPT[Optimizer<br/>Grid Search · Walk-Forward]
        SM --> BE
        BE --> PM
        BE --> OPT
        OPT -.->|tune params| SM
    end

    subgraph AGENTS["🤖 AI AGENT LAYER (Phase 2)"]
        TA[Technical Analyst<br/>Price Action · Indicators]
        SA[Sentiment Analyst<br/>RSI extremes · Volume]
        RM[Risk Manager<br/>Volatility · Position Size]
        TR[Trader Agent<br/>Weighted voting · Decision]

        TA --> TR
        SA --> TR
        RM --> TR
    end

    subgraph EXEC["⚡ EXECUTION LAYER (Phase 3)"]
        PEX[Paper Exchange<br/>Simulated fills<br/>Slippage · Commission]
        PMGR[Portfolio Manager<br/>Balance · P&L · Equity]
        RC[Risk Controller<br/>Max DD · Daily Loss<br/>Circuit Breaker]
        STATE[(JSON State<br/>data/execution/)]
        PEX --> STATE
        PMGR --> RC
    end

    %% Cross-layer connections
    PL --> SM
    PL --> AGENTS
    PM --> AGENTS
    TR --> PEX
    PEX --> PMGR
```

---

## 🔄 Luồng dữ liệu & Ra quyết định

```mermaid
sequenceDiagram
    participant D as Data Pipeline
    participant S as Backtest
    participant A as AI Agents
    participant E as Execution
    participant R as Risk Controller

    Note over D,A: PHASE 0: Data Collection
    D->>D: Fetch OHLCV từ CCXT (696K candles)
    D->>D: Validate gaps, outliers
    D->>D: Save Parquet

    Note over S,A: PHASE 1: Strategy Optimization
    S->>D: Load historical data
    S->>S: Run parameter sweep
    S->>S: Walk-forward validation
    S->>S: Select best params

    Note over A,E: PHASE 2-3: Trading Cycle
    loop Mỗi candle mới
        A->>D: Load latest data
        A->>A: Technical Analyst → signal
        A->>A: Sentiment Analyst → signal
        A->>A: Risk Manager → position size
        A->>A: Trader Agent → weighted vote
        A->>E: Signal: BUY / SELL / HOLD
        E->>E: Calculate position size
        E->>E: Place order (paper simulated)
        E->>R: Update prices
        R->>R: Check risk limits
        R->>R: Circuit breaker if needed
        E->>E: Log trade + P&L
    end
```

---

## 🧱 Chi tiết từng Layer

### 📡 Data Layer — Phase 0 ✅

```
src/trading_agent/data/
├── collector.py    ← CCXT fetch + pagination + retry + validate
├── storage.py      ← Parquet save/load (append + dedup)
└── types.py        ← OHLCV types + timeframe enum
```

| Thành phần | Input | Output | Ghi chú |
|-----------|-------|--------|---------|
| Collector | symbol + timeframe + since | Polars DataFrame | Paginated fetch, auto rate-limit |
| Storage | DataFrame | `.parquet` file | Append + dedup, cấu trúc `exchange/symbol/tf.parquet` |
| validate_data | DataFrame | Gap/outlier/null report | Data quality assurance |

**Dữ liệu hiện tại:** 5 symbols × 4 timeframes = 20 datasets, 696K candles, 0 gaps.

---

### 🧪 Backtest Layer — Phase 1 ✅

```
src/trading_agent/
├── strategies/
│   ├── base.py       ← Abstract Strategy class + registry
│   ├── ma_crossover.py  ← MA Crossover (fast=5, slow=20)
│   ├── rsi.py        ← RSI Mean Reversion (period=7, oversold=25)
│   └── bbands.py     ← Bollinger Bands (period=10, std=2.5)
└── backtest/
    ├── engine.py     ← Vectorized backtest runner (Polars)
    └── metrics.py    ← Performance metrics
```

**4 strategies implemented:**
| Strategy | Params tối ưu | OOS Return | Sharpe |
|----------|--------------|------------|--------|
| MA Crossover | fast=5, slow=20 | **+11.22%** | 1.67 |
| RSI | period=7, oversold=25, overbought=75 | **+3.29%** | 1.06 |
| Bollinger Bands | period=10, std=2.5 | **+1.70%** | 1.07 |
| MACD | default | baseline | — |

**Optimization:** Grid search + Walk-forward validation (2y train / 1y test).

---

### 🤖 AI Agent Layer — Phase 2 ✅

```
src/trading_agent/agents/
├── base.py         ← AgentMessage dataclass + Agent interface
├── llm.py          ← LLM client (OpenRouter/DeepSeek/OpenAI/Ollama)
├── technical.py    ← Technical Analyst agent
├── sentiment.py    ← Sentiment Analyst agent
├── risk.py         ← Risk Manager agent
├── trader.py       ← Trader Agent (decision synthesis)
└── orchestrator.py ← Orchestrator (runs full analysis cycle)
```

**4 agents hoạt động end-to-end:**

```mermaid
graph LR
    A[Data + Indicators] --> T[Technical Analyst<br/>trend, momentum, volatility]
    A --> S[Sentiment Analyst<br/>RSI extremes, volume]
    T --> TR[Trader Agent<br/>Weighted voting]
    S --> TR
    R[Risk Manager<br/>Volatility, sizing] --> TR
    TR --> SIGNAL[BUY / SELL / HOLD]
```

**Luồng xử lý mỗi agent:**
1. Nhận context: giá hiện tại, indicators, vị thế
2. LLM phân tích với prompt chuyên biệt
3. Output: signal + confidence + reasoning
4. Trader Agent tổng hợp bằng weighted voting

**LLM Provider Chain:**
```
Primary:   DeepSeek V4 Flash (OpenRouter free)  → ~$0.00003/req
Fallback:  GPT-4o-mini (OpenAI)                  → ~$0.0004/req
Local:     Qwen2.5:7b (Ollama)                   → Free
```

---

### ⚡ Execution Layer — Phase 3 ✅

```
src/trading_agent/execution/
├── types.py            ← Order, Trade, Position dataclasses
├── paper_exchange.py   ← Simulated exchange (no real API)
├── engine.py           ← Unified execution interface
└── risk_controller.py  ← Risk limits + circuit breaker
```

**Paper Exchange:**
```python
engine = ExecutionEngine(initial_capital=10_000.0)
engine.exchange.place_order("BTC/USDT", "buy", "market", amount=0.039)
engine.update_prices({"BTC/USDT": 66000.0})
print(engine.get_summary())
# Equity: $10,082.64  |  Unrealized P&L: +$85.15  |  Return: +0.83%
```

| Feature | Mô tả |
|---------|-------|
| Market orders | Fill ngay tại current price + slippage |
| Limit orders | Fill khi price cross limit |
| Stop-loss | Trigger + market fill khi price chạm stop |
| Slippage | 0.05% (cấu hình được) |
| Commission | 0.1% (Binance spot rate) |
| State persistence | JSON file, survive restart |

**Risk Controller:**
| Check | Limit | Hành vi khi vi phạm |
|-------|-------|---------------------|
| Max Drawdown | 15% | Circuit breaker → close all |
| Daily Loss | 8% | Circuit breaker → close all |
| Position Concentration | 50% | Warning + prevent new entry |
| Cooldown | 24h | Ngăn trade mới sau stop-loss |

---

## 📁 Cấu trúc thư mục

```
trading-agent/
├── config/                  # Configuration (YAML)
│   └── config.yaml
├── src/
│   └── trading_agent/
│       ├── cli.py           # CLI entry point (Click + Rich)
│       ├── config/
│       │   └── loader.py    # Config parser
│       ├── data/            # Phase 0: Data Pipeline
│       │   ├── collector.py # CCXT fetcher
│       │   ├── storage.py   # Parquet IO
│       │   └── types.py     # OHLCV types
│       ├── strategies/      # Phase 1: Strategy Library
│       │   ├── base.py
│       │   ├── ma_crossover.py
│       │   ├── rsi.py
│       │   └── bbands.py
│       ├── backtest/        # Phase 1: Backtest Engine
│       │   ├── engine.py
│       │   └── metrics.py
│       ├── agents/          # Phase 2: AI Multi-Agent
│       │   ├── base.py
│       │   ├── llm.py
│       │   ├── technical.py
│       │   ├── sentiment.py
│       │   ├── risk.py
│       │   ├── trader.py
│       │   └── orchestrator.py
│       └── execution/       # Phase 3: Execution & Risk
│           ├── types.py
│           ├── paper_exchange.py
│           ├── engine.py
│           └── risk_controller.py
├── data/                    # Market data
│   ├── raw/                 # OHLCV parquet files
│   └── execution/           # Paper exchange state
├── docs/                    # Documentation
├── docker-compose.yml       # Infra stack
├── Makefile                 # Command shortcuts
├── pyproject.toml           # Python project
└── README.md                # Project overview
```

---

> 📖 Xem chi tiết từng file trong [project-structure.md](../project-structure.md)
> 🧠 Xem quy trình suy luận trong [reasoning.md](../reasoning.md)
> 🎮 Xem demo từng bước trong [demo.md](../demo.md)
> ⚡ Xem tối ưu hóa trong [optimization.md](../optimization.md)
