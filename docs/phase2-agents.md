# Phase 2 — Multi-Agent Trading System

## Architecture

```
User (CLI: trading-agent agents analyze <symbol> -t <tf>)
  │
  ▼
Orchestrator.analyze()
  │
  ├─ 1. Load OHLCV data from Parquet
  ├─ 2. Compute indicators (MA, RSI, BBands)
  ├─ 3. Build AnalysisContext
  │
  ├─ 4. Technical Analyst    → AgentMessage
  ├─ 5. Sentiment Analyst    → AgentMessage (gets tech output)
  ├─ 6. Risk Manager         → AgentMessage (gets tech + sentiment)
  │
  ├─ 7. Trader (weighted voting + risk override) → Final Decision
  │
  ├─ 8. Print report + save decisions to DB
  │
  └─ [Multi-symbol] PortfolioManager → PortfolioDecision
```

## Agents

### 1. Technical Analyst (`technical.py`)

**Role:** Phân tích trend, momentum, volatility từ indicators.

**Data sử dụng:**
- MAs (MA5 → MA200) — trend direction & slope
- RSI(14) — momentum & overbought/oversold
- Bollinger Bands — volatility & price position within bands
- Price changes (5-bar, 20-bar) — short-term momentum
- Volatility (20-bar std dev) — market activity
- Volume ratio (5/20) — volume confirmation

**LLM Prompt:** Yêu cầu phân tích như prop trader, output JSON với `signal/confidence/reasoning/details`.

**Fallback:** Rule-based dựa trên RSI (oversold → BUY, overbought → SELL, else → HOLD).

### 2. Sentiment Analyst (`sentiment.py`)

**Role:** Suy luận market sentiment từ price action + volume (không có news feed).

**Data sử dụng:**
- RSI — fear/greed
- Volume ratio — accumulation/distribution
- Price changes — momentum strength

**LLM Prompt:** Xác định sentiment (bullish/bearish/fear/greed) và momentum strength.

**Fallback:** Rule-based dựa trên RSI + volume ratio.

### 3. Risk Manager (`risk.py`)

**Role:** Đánh giá rủi ro, position sizing.

**Data sử dụng:**
- Volatility — HIGH → giảm position
- Volume — LOW volume → giảm position
- RSI — neutral zone cho phép entry
- Current position — đã có position thì HOLD an toàn hơn

**LLM Prompt:** Đánh giá risk level (LOW/MEDIUM/HIGH/EXTREME) + max position size.

**Fallback:** Rule-based dựa trên volatility threshold.

### 4. Trader (`trader.py`)

**Role:** Tổng hợp tín hiệu từ 3 agents → quyết định cuối.

**Weighted Voting:**
| Agent | Weight |
|-------|--------|
| Technical Analyst | 40% |
| Sentiment Analyst | 20% |
| Risk Manager | 40% |

**Risk Override:** Nếu Risk Manager báo HIGH/EXTREME → signal bị ép thành HOLD.

**Decision logic:**
- Weighted score > +0.3 → BUY
- Weighted score < -0.3 → SELL
- Else → HOLD

### 5. Portfolio Manager (`portfolio.py`) — MỚI

**Role:** Quản lý danh mục đa symbol. Nhận signals từ Trader của mỗi symbol, tính toán allocation tối ưu.

**Cách hoạt động:**
1. Từng symbol được run `Orchestrator.analyze()` → có `AgentMessage` (final decision)
2. PortfolioManager nhận list `(symbol, AgentMessage)` pairs
3. Chỉ BUY signals mới được allocation; HOLD/SELL → 0%
4. Risk override (HIGH/EXTREME) → không allocation
5. Raw score = confidence × max_position_size_pct
6. Normalize scores → budget (85%) phân bổ theo tỷ lệ
7. Clamp mỗi symbol ≤ 40% portfolio
8. Redistribute leftover từ clamped positions
9. Luôn giữ 15% cash buffer

**Output:** `PortfolioDecision` với allocations cho từng symbol + cash %.

```
💰 Portfolio Allocation ($50,000)
──────────────────────────────────────────────────
  BTC/USDT      40.0% BUY  ████████████
  ETH/USDT      40.0% BUY  ████████████
  CASH          20.0%
──────────────────────────────────────────────────
  Risk Level: MEDIUM
```

### 6. Orchestrator (`orchestrator.py`)

**Role:** Pipeline coordinator. Load data → tính indicators → run agents → report.

Cũng cung cấp `print_report()` để in kết quả dạng rich tree.

## AgentMessage Protocol (`base.py`)

```python
@dataclass
class AgentMessage:
    role: str  # "technical_analyst" | "sentiment_analyst" | "risk_manager" | "trader"
    signal: str  # "BUY" | "SELL" | "HOLD"
    confidence: float  # 0.0 - 1.0
    reasoning: str  # 1-2 câu giải thích
    details: dict  # data phụ thuộc role
    max_position_size_pct: float  # (risk/trader only)
    risk_level: str  # (risk/trader only)
    warnings: list[str]  # (risk/trader only)
```

## LLM Provider (`llm.py`)

**Priority chain:**
1. OpenCode (`deepseek-v4-flash-free`) — **free, $0**
2. OpenAI (`gpt-4o-mini`) — cần API key
3. OpenRouter (`deepseek/deepseek-v4-flash`) — cần key
4. DeepSeek (`deepseek-chat`) — cần key
5. Ollama (`qwen2.5:7b`) — local fallback

**Fixes applied:**
- `reasoning_content` field (OpenCode DeepSeek V4 Flash) — fallback khi content rỗng
- `USE_LLM=false` — raise exception để agent gọi `_rule_based()` thay vì generic HOLD

**Usage:**
- `chat()` — raw chat completion; fallback qua từng provider
- `ask_agent()` — structured JSON output; fallback → rule-based nếu LLM fail

## CLI Usage

```bash
# Phân tích multi-agent
trading-agent agents analyze BTC/USDT -t 1h

# List symbols/timeframes có data
trading-agent agents list

# Rule-based mode (fast, no LLM)
USE_LLM=false trading-agent agents analyze BTC/USDT -t 1h

# Portfolio allocation
trading-agent portfolio allocate --symbols BTC/USDT,ETH/USDT,SOL/USDT

# Backtest với AI ensemble signals
USE_LLM=false trading-agent backtest run agent_ensemble -s BTC/USDT -t 1h -p threshold_buy=0.2 -p max_hold_bars=48
```

## Test Results

### Multi-Timeframe (15m/1h/4h)
| Timeframe | Technical | Sentiment | Risk | Final | LLM Mode |
|-----------|-----------|-----------|------|-------|----------|
| 15m | SELL/65% | HOLD/60% | HOLD/30% | HOLD/26% | LLM ✅ |
| 1h  | HOLD/60% | HOLD/60% | BUY/60% | HOLD/16% | LLM ✅ |
| 4h  | HOLD/30% | HOLD/55% | BUY/40% | HOLD/20% | LLM ✅ (1 RULE) |

### Fallback Test
- Khi LLM fail → cả 4 agents đều fallback sang rule-based
- Rule-based agents generate signals với RSI extremes (oversold → BUY, overbought → SELL)

### Backtest: Agent Ensemble on BTC/USDT 1h (1000 bars)
| Metric | Value |
|--------|-------|
| Return | +2.61% |
| Sharpe | 0.93 |
| Max DD | -7.42% |
| Trades | 11 |
| Win Rate | 72.7% |
| Profit Factor | 2.06 |
| Speed | ~8,000 bars/s (rule-based) |

