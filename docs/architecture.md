# 🏛 Kiến Trúc Hệ Thống

> File này giải thích kiến trúc tổng thể của Trading Agent System — các layer,
> luồng dữ liệu, cách các module giao tiếp với nhau.

---

## 📐 Kiến trúc tổng thể (Layer Diagram)

```mermaid
graph TB
    subgraph DATA["📡 DATA LAYER"]
        CCXT[CCXT<br/>100+ exchanges]
        PARQ[Parquet Files]
        TSDB[(TimescaleDB)]
        PL[Polars DataFrames]
        CCXT -->|fetch OHLCV| PARQ
        PARQ -->|load| PL
        TSDB -.->|future| PL
    end

    subgraph BACKTEST["🧪 BACKTEST LAYER"]
        VB[VectorBT<br/>Fast research]
        NT[NautilusTrader<br/>Production backtest]
        SM[Strategy Manager]
        PM[Performance Metrics]
        SM --> VB
        SM --> NT
        VB --> PM
        NT --> PM
    end

    subgraph AGENTS["🤖 AI AGENT LAYER"]
        TA[Technical Analyst<br/>Indicators, Patterns]
        SA[Sentiment Analyst<br/>News, Social]
        FA[Fundamental Analyst<br/>On-chain, Valuations]
        MA[Macro Analyst<br/>Macro trends]
        TR[Trader Agent<br/>Decision synthesis]
        RM[Risk Manager<br/>Exposure, VaR]
        PMGR[Portfolio Manager<br/>Capital allocation]

        TA --> TR
        SA --> TR
        FA --> TR
        MA --> TR
        TR --> RM
        RM --> PMGR
    end

    subgraph EXEC["⚡ EXECUTION LAYER"]
        ORD[Order Manager]
        POS[Position Tracker]
        CB[Circuit Breaker]
        EXCHANGE[Exchange API<br/>CCXT / Alpaca]
        ORD --> EXCHANGE
        POS --> CB
        CB --> ORD
    end

    subgraph MONITOR["📊 MONITORING"]
        GRAF[Grafana Dashboard]
        TEL[Telegram Alerts]
        LOG[Structured Logs]
    end

    %% Cross-layer connections
    PL --> SM
    PM --> AGENTS
    PMGR --> ORD
    EXCHANGE -->|order status| POS
    EXCHANGE -->|market data| CCXT
    ORD --> LOG
    POS --> LOG
    LOG --> GRAF
    LOG --> TEL
```

---

## 🔄 Luồng dữ liệu & Ra quyết định

```mermaid
sequenceDiagram
    participant S as Strategy (Backtest)
    participant D as Data Pipeline
    participant A as AI Agents
    participant E as Execution
    participant M as Monitor

    Note over S,D: PHASE 1: Backtest
    D->>D: Fetch OHLCV từ CCXT
    D->>D: Lưu Parquet
    S->>D: Load data
    S->>S: Run backtest
    S->>S: Tính metrics

    Note over A,E: PHASE 2-3: Live Trading
    loop Every N minutes
        D->>D: Fetch latest candles
        D->>A: Gửi market data
        par Agent analysis
            A->>A: Technical analysis
            A->>A: Sentiment analysis
            A->>A: Macro analysis
        end
        A->>A: Agents debate & vote
        A->>A: Risk check (VaR, drawdown)
        A->>E: Signal: BUY/SELL/HOLD
        E->>E: Validate order (size, limits)
        E->>E: Place order (CCXT)
        E->>M: Log trade + P&L
    end
```

---

## 🧱 Chi tiết từng Layer

### 📡 Data Layer (`src/trading_agent/data/`)

```
collector.py    ← CCXT fetch + pagination + retry
storage.py      ← Parquet save/load
types.py        ← OHLCV dataclass + Timeframe enum
```

| Thành phần | Input | Output | Ghi chú |
|-----------|-------|--------|---------|
| Collector | symbol + timeframe + since | Polars DataFrame | Paginated fetch, auto rate-limit |
| Storage | DataFrame | `.parquet` file | Append + dedup, theo cấu trúc `exchange/symbol/tf.parquet` |
| CLI | User command | Console output | `trading-agent data fetch/inspect/list-datasets` |

**Cấu trúc thư mục data:**
```
data/raw/
├── binance/
│   ├── BTC_USDT/
│   │   ├── 1h.parquet
│   │   └── 1d.parquet
│   └── ETH_USDT/
│       └── 1h.parquet
└── bybit/
    └── ...
```

---

### 🧪 Backtest Layer (Phase 1 — đang xây dựng)

```mermaid
graph LR
    A[Data] --> B[Strategy]
    B --> C[Backtest Engine]
    C --> D[Trade Log]
    D --> E[Performance Metrics]
    E --> F[Optimization]
    F -.->|tune params| B
```

**2 engine song song:**
| Engine | Khi nào dùng | Ưu điểm |
|--------|-------------|---------|
| **VectorBT** | Parameter sweep, quick research | 167x nhanh hơn Backtrader |
| **NautilusTrader** | Production backtest, fill modeling | Rust core, event-driven, sát live nhất |

---

### 🤖 AI Agent Layer (Phase 2 — đang thiết kế)

```mermaid
graph TB
    subgraph ANALYSTS["Phân Tích"]
        TEC[Technical<br/>Price Action, Indicators]
        SEN[Sentiment<br/>News, Twitter, Reddit]
        FUN[Fundamental<br/>On-chain, TVL, Fees]
        MAC[Macro<br/>Interest Rates, CPI]
    end

    subgraph DEBATE["Debate & Consensus"]
        TRADER[Trader Agent<br/>Tổng hợp tín hiệu]
    end

    subgraph RISK["Risk"]
        RISK_MGR[Risk Manager<br/>Kiểm tra VaR, Drawdown, Size]
    end

    subgraph DECISION["Decision"]
        PM[Portfolio Manager<br/>Quyết định cuối cùng]
        SIGNAL[BUY / SELL / HOLD]
    end

    TEC --> TRADER
    SEN --> TRADER
    FUN --> TRADER
    MAC --> TRADER
    TRADER --> RISK_MGR
    RISK_MGR --> PM
    PM --> SIGNAL
```

**Các agent:**
| Agent | Nguồn dữ liệu | Công cụ |
|-------|--------------|---------|
| Technical Analyst | OHLCV, indicators | TA-Lib, pandas_ta |
| Sentiment Analyst | News API, Twitter, Reddit | LLM + Web search |
| Fundamental Analyst | On-chain data (CoinGecko, Dune) | API + LLM |
| Macro Analyst | Economic calendar, Fed | Web fetch + LLM |
| Trader Agent | Signals từ analysts | LLM debate + voting |
| Risk Manager | Current positions, volatility | Tính VaR, max drawdown |
| Portfolio Manager | All signals + risk | LLM + rules |

---

### ⚡ Execution Layer (Phase 3 — đang thiết kế)

```mermaid
graph LR
    SIGNAL[Signal từ AI] --> VALIDATE{Validate}
    VALIDATE -->|OK| SIZE[Position Sizing]
    VALIDATE -->|REJECT| LOG[Log + Alert]
    SIZE --> ORDER[Create Order]
    ORDER --> PLACE[Place via CCXT]
    PLACE --> STATUS{Status}
    STATUS -->|Filled| TRACK[Track Position]
    STATUS -->|Partial| ADJUST[Adjust]
    STATUS -->|Rejected| RETRY[Retry Logic]
    TRACK --> MONITOR[Monitor P&L]
```

**Safety checks trước khi execute:**
1. ✅ Symbol có khớp lệnh không?
2. ✅ Số lượng có nằm trong giới hạn không?
3. ✅ Có đủ balance không?
4. ✅ Drawdown hiện tại có vượt ngưỡng không?
5. ✅ Giá hiện tại có outlier không?

---

## 📁 Cấu trúc thư mục

```
trading-agent/
├── config/                  # Configuration (YAML)
│   └── config.yaml
├── src/
│   └── trading_agent/
│       ├── __init__.py
│       ├── cli.py           # CLI entry point
│       ├── config/
│       │   └── loader.py    # Config parser
│       ├── data/
│       │   ├── collector.py # CCXT fetcher
│       │   ├── storage.py   # Parquet IO
│       │   └── types.py     # OHLCV types
│       ├── strategies/      # Strategy zoo (Phase 1)
│       ├── backtest/        # Backtest runner (Phase 1)
│       └── agents/          # AI agents (Phase 2)
├── data/                    # Market data
│   ├── raw/                 # Raw OHLCV parquet
│   └── processed/           # Feature-engineered data
├── tests/                   # Unit tests
├── notebooks/               # Jupyter notebooks
├── docs/                    # 📘 BẠN ĐANG Ở ĐÂY
├── docker-compose.yml       # Infra stack
├── Dockerfile               # App container
├── Makefile                 # Command shortcuts
├── pyproject.toml           # Python project
└── README.md                # Project overview
```

> 📖 Xem chi tiết từng file trong [project-structure.md](project-structure.md)
> 🧠 Xem quy trình suy luận trong [reasoning.md](reasoning.md)
> 🎮 Xem demo từng bước trong [demo.md](demo.md)
