# PortfolioBacktestEngine — Tài liệu chuẩn (Milestone D)

> Cập nhật: 2026-08-26 · Commits: `40c5c29` (engine) · `17efa98` (snapshot injection + phasing + reservations + parity) · `5df8360`/`3a46813` (actual-fill safety guards + permanent rejection drop)
>
> **Nguyên tắc số 1:** Backtest CHỈ thay "broker + clock". KHÔNG BAO GIỜ thay decision authority. Mọi quyết định trong backtest đi qua đúng chuỗi authority như live — đây là điều kiện tiên quyết để kết quả backtest có ý nghĩa với live.

---

## Mục lục

1. [Nguyên tắc thiết kế](#1-nguyên-tắc-thiết-kế)
2. [Kiến trúc & luồng dữ liệu](#2-kiến-trúc--luồng-dữ-liệu)
3. [Bảo đảm (Guarantees)](#3-bảo-đảm-guarantees)
4. [Execution phasing: REDUCTION → INCREASE](#4-execution-phasing-reduction--increase)
5. [Reservation accounting](#5-reservation-accounting)
6. [Actual-fill safety guards & permanent rejection drop](#6-actual-fill-safety-guards--permanent-rejection-drop)
7. [Snapshot injection (`portfolio_snapshot=`)](#7-snapshot-injection-portfolio_snapshot)
8. [Parity testing: pre-broker boundary](#8-parity-testing-pre-broker-boundary)
9. [Sử dụng](#9-sử-dụng)
10. [Ma trận kiểm thử](#10-ma-trận-kiểm-thử)

---

## 1. Nguyên tắc thiết kế

### Đặt vấn đề

Backtest truyền thống (vectorized, per-pair) tự tính signal → position → PnL bằng logic riêng. Hệ quả:

- Kết quả backtest **không thể tái hiện** bằng code live — hai code path khác nhau cho cùng một chiến lược.
- Portfolio-level effects (shared cash, exposure caps, allocation cạnh tranh giữa các pair) bị bỏ qua hoặc mô phỏng sai.
- Cost model của backtest khác cost model mà execution simulator đã calibrate.

### Giải pháp

`PortfolioBacktestEngine` **tái sử dụng toàn bộ chuỗi authority của live**, chỉ thay đúng 2 thành phần I/O:

| Thành phần | Live | Backtest |
|---|---|---|
| Clock | `datetime.now(UTC)` | `HistoricalMarketClock` (timeline = union bar-close times) |
| Broker | Paper/LiveBroker (Alpaca/Binance) | `HistoricalSimulationBroker` (deterministic fills) |
| Resolver | `RuntimeStrategyResolver` | **giống hệt** |
| Strategy runtime | `StrategyRuntime.execute()` | **giống hệt** |
| Decision authority | `DecisionAuthority` | **giống hệt** |
| Allocation | `BatchPortfolioAllocator.allocate_batch()` | **giống hệt** |
| Risk/Finalize | `finalize_prepared_decision()` | **giống hệt** |
| Planner | `plan_pair_order()` | **giống hệt** |
| Preflight | `preflight_batch()` | **giống hệt** |

**Điều này được kiểm chứng bởi parity test thật** (xem §8): cùng market frame, same starting portfolio truth ($100k flat), live cycle và backtest phải cho **cùng authority outputs trước broker boundary**.

---

## 2. Kiến trúc & luồng dữ liệu

```
HistoricalMarketClock (timeline = union of bar-close instants)
        │
        │  mỗi bước t:
        ▼
┌────────────────────────────────────────────────────────────┐
│ 1. settle(now=t): fill mọi order đủ điều kiện (earliest    │
│    t+1). Thứ tự: REDUCTION → INCREASE → symbol FIFO.       │
│    Actual-fill guards áp dụng TRƯỚC mỗi fill.              │
├────────────────────────────────────────────────────────────┤
│ 2. _build_snapshot(t): PortfolioSnapshot từ ledger broker  │
│    − reserved_cash (pending BUYs)                          │
│    − positions là EFFECTIVE qty (held − pending SELLs)     │
│    − equity mark trên FULL holdings                        │
├────────────────────────────────────────────────────────────┤
│ 3. slice_upto(t) per binding → MarketDataInput             │
│    (chỉ bars closed ≤ t — no lookahead)                    │
├────────────────────────────────────────────────────────────┤
│ 4. gate binding (warmup ≥ 2 closed bars, candle closed)    │
├────────────────────────────────────────────────────────────┤
│ 5. engine.prepare_promoted_strategy(...,                   │
│         portfolio_snapshot=snapshot)   ← snapshot inject!  │
│    → Resolver → StrategyOutput → DecisionAuthority         │
│    → PairPreparedDecision (current_exposure/equity/cash    │
│      lấy TỪ SNAPSHOT, không từ broker giả lập nào khác)    │
├────────────────────────────────────────────────────────────┤
│ 6. build_allocation_request(p, snapshot) → allocate_batch()│
│    → PortfolioTargetVector                                 │
├────────────────────────────────────────────────────────────┤
│ 7. finalize_prepared_decision() → plan_pair_order()        │
│    → preflight_batch() → reductions + increases            │
├────────────────────────────────────────────────────────────┤
│ 8. queue(plan) → QueuedOrder(phase=REDUCTION|INCREASE,     │
│    idempotency_key) — duplicate key bị bỏ qua              │
└────────────────────────────────────────────────────────────┘
        │
        │ bước t+1: quay về (1) — fills earliest t+1
        ▼
PortfolioBacktestResult: equity_curve, drawdown_series,
exposure_history, turnover/cost history, trades (SimFill),
per_symbol_contribution, steps, blocked_cycles, expired_orders
```

### Per-symbol contribution (exact cash-flow identity)

```
contribution(sym) = −Σ(buy notional+fee) + Σ(sell notional−fee) + qty_held × final_px

⇒ Σ_sym contribution ≡ final_equity − initial_cash     (được test enforce)
```

Không dùng mark-to-market xấp xỉ — identity này bắt buộc khớp tới float precision.

---

## 3. Bảo đảm (Guarantees)

| # | Guarantee | Cơ chế | Test |
|---|-----------|--------|------|
| G1 | **No lookahead** — signals chỉ dùng bars closed ≤ t | `slice_upto(t)`; gate yêu cầu ≥ 2 closed bars | `test_no_lookahead*` |
| G2 | **Earliest fill t+1** — fill tại OPEN của bar mở SAU decision instant | `clock.fill_for(binding, decision_time, now)`: first bar `open_ts >= dt AND close_ts <= now` | `test_buy_fills_at_next_bar_open` |
| G3 | **Shared capital pool** — N pair dùng chung 1 cash ledger | `HistoricalSimulationBroker.cash` duy nhất | `test_shared_capital_pool` |
| G4 | **Determinism** — chạy lại cho kết quả giống hệt | Không randomness: fee/slip bps cố định, adverse slippage, sort ổn định | `test_permutation_determinism`, `test_replay_identical` |
| G5 | **Idempotent queuing** — duplicate idempotency_key bị bỏ qua | `broker.queue()` check `_seen_keys` | unit test |
| G6 | **Never negative cash** — aggregate actual fills không thể âm cash | Cash guard sequential theo phase order (§6) | `TestBrokerFillSafetyGuards` |
| G7 | **No synthetic proceeds** — SELL không thể bán quá inventory thực | Inventory guard cap at held qty (§6) | `test_oversized_sell_*` |
| G8 | **Rejected = dropped permanently** — order bị guard loại KHÔNG quay lại hàng đợi, không bao giờ fill ở giá tốt hơn sau này | `continue` thay vì `remaining.append` (§6) | `TestBrokerRejectionDrop` |
| G9 | **Parity với live path** — same inputs ⇒ same authority outputs trước broker boundary | Chuỗi authority shared + parity test (§8) | `TestParityLiveCycleVsBacktest` |

---

## 4. Execution phasing: REDUCTION → INCREASE

Mỗi `QueuedOrder` mang `phase` ("reduction" | "increase") set từ `plan.action`. `settle()` sort:

```python
sort_key(order) = (
    0 if order.phase == "reduction" else 1,   # REDUCTION trước
    order.symbol,                              # rồi per-symbol
    order.decision_time,                       # FIFO
)
```

**Tại sao REDUCTION trước INCREASE:**

1. **Risk reduction không bao giờ bị starve** — lệnh giảm rủi ro (SELL bớt exposure) ưu tiên xử lý trước lệnh mở mới.
2. **Cash giải phóng dùng được ngay trong cùng batch** — SELL fill trước ⇒ proceeds vào cash ⇒ BUY sau đó thấy cash đầy đủ hơn. Nếu INCREASE đi trước, BUY có thể bị cash guard reject oan dù tổng tài khoản là đủ.

Test: `test_reduction_settles_before_increase` — BUY queued EARLIER vẫn settle SAU reduction.

---

## 5. Reservation accounting

Trong khoảng từ khi queue đến khi fill (earliest t+1), order "đang chờ" chiếm giữ tài nguyên:

| Loại order | Chiếm giữ | API |
|---|---|---|
| Pending **BUY** | Cash = Σ(qty × ref_price × (1 + fee_bps + slip_bps)) | `broker.reserved_cash()` |
| Pending **SELL** | Inventory = Σ(qty) theo symbol | `broker.reserved_inventory(sym)` |

`_build_snapshot()` áp reservation vào decision truth:

```python
positions[sym]   = max(held_qty − reserved_inventory(sym), 0)   # effective qty
available_cash   = max(cash − reserved_cash(), 0)
equity           = cash_effective? NO — equity mark trên FULL held qty (money value đúng)
reserved_cash / reserved_inventory → ghi vào PortfolioSnapshot để audit
```

**Ý nghĩa:** planner nhìn thấy *post-reservation* state ⇒ không double-spend cash đang chờ BUY, không double-sell inventory đang chờ SELL. Equity vẫn đúng giá trị thị trường vì mark trên full holdings.

**Lưu ý quan trọng:** reservation là *planning-time* concept (ước lượng từ ref_price). Nếu giá gap-up, actual fill cost > reservation — case này do actual-fill guard xử lý (§6).

---

## 6. Actual-fill safety guards & permanent rejection drop

Đây là lớp phòng vệ cuối cùng tại thời điểm fill THỰC — bảo vệ trước mọi trường hợp reservation estimate lệch reality (gap, gộp lệnh, inventory biến mất).

### 6.1 Các guards (áp dụng tuần tự theo phase order)

```python
# Trong settle(), sau khi xác định eligible fill bar:
if side == "sell":
    held = positions[sym].qty
    if held <= 0:          → REJECT & DROP (no synthetic proceeds)
    qty = min(order.quantity, held)   # oversized SELL → partial fill, remainder DISCARDED

if side == "buy":
    total_cost = qty×fill_price + fee
    if cash < total_cost:  → REJECT & DROP (never negative cash)
```

### 6.2 Vì sao sequential check = aggregate safety

Fills xử lý tuần tự trong sorted order. Mỗi fill BUY trừ `self.cash` ngay ⇒ BUY tiếp theo trong cùng batch check trên **cash còn lại**. Hai BUY cùng fill timestamp: cái đầu thắng, cái thứ hai tự động reject nếu không đủ. Không cần batch pre-check riêng.

### 6.3 Permanent rejection drop — 4 guarantees

Order bị guard loại bị **loại vĩnh viễn khỏi hàng đợi** (`continue`, KHÔNG `remaining.append`). Ý nghĩa:

| # | Tình huống | Bảo đảm | Test |
|---|-----------|---------|------|
| R1 | Gap-up BUY rejected | `pending_count == 0`, `reserved_cash == 0` | `test_gap_up_buy_dropped_not_queued` |
| R2 | Rejected BUY + giá drop sâu sau đó | Order cũ **KHÔNG BAO GIỜ** fill ở giá thấp hơn (tránh fill ngoài ý muốn của quyết định đã chết) | `test_rejected_buy_never_fills_on_price_drop` |
| R3 | SELL khi inventory = 0 | `pending_count == 0`, `reserved_inventory == 0` | `test_zero_inventory_sell_dropped` |
| R4 | Old SELL rejected + BUY mới tạo position sau đó | Stale SELL **KHÔNG BAO GIỜ** đụng inventory mới | `test_rejected_sell_never_touches_new_inventory` |

**Rationale cho drop-instead-of-requeue:** một order đã bị reject nghĩa là điều kiện fill tại thời điểm đó không an toàn (thiếu cash/thiếu inventory). Requeue tạo ra (a) sell-pressure vô hạn trên inventory đã biến mất, (b) fill "ma" ở tương lai với điều kiện thị trường hoàn toàn khác so với lúc ra quyết định — cả hai đều vi phạm tính deterministic và auditability. Quyết định đã chết thì để nó chết; planner sẽ ra quyết định mới ở cycle sau với snapshot mới.

### 6.4 Reservation undershoot → deterministic safe outcome

Case đặc biệt quan trọng: planning APPROVE (vì available_cash sau reservation > 0) nhưng actual cost vượt cash do gap:

```
ref=100, qty=1, fee=10bps, slip=5bps
reservation ≈ 100.15 → available = 101 − 100.15 = 0.85 > 0  ✓ planning OK
actual open = 105 (gap) → cost = 105×1.005 + fee ≈ 105.16 > 101  ✗ settle REJECT
Kết quả: fills=[], cash giữ nguyên 101, order dropped, cash ≥ 0 luôn.
```

Test: `test_reservation_undershoot_actual_cost_deterministic`.

---

## 7. Snapshot injection (`portfolio_snapshot=`)

```python
prepared = engine.prepare_promoted_strategy(
    symbol=symbol,
    timeframe=timeframe,
    environment=env,
    market_data_input=mdi,
    portfolio_snapshot=snapshot,  # ← NEW (optional)
)
```

Khi `portfolio_snapshot` được cung cấp, `_prepare_from_runtime` lấy **toàn bộ portfolio truth từ snapshot** thay vì live broker state:

| Trường | Nguồn khi có snapshot |
|---|---|
| `current_qty` | `snapshot.positions.get(symbol, 0.0)` (đã là effective qty sau reservation) |
| `equity` | `snapshot.equity` |
| `available_cash` | `snapshot.available_cash` |
| `current_exposure` | `current_notional / snapshot.equity` |
| `total_portfolio_exposure` | `snapshot.gross_exposure` |

Khi không cung cấp (live trading): hành vi giữ nguyên — đọc trực tiếp từ paper/live exchange.

**Ý nghĩa kiến trúc:** backtest và live batch mode giờ dùng **cùng một đường code, cùng hình dạng truth source** (PortfolioSnapshot). Sự khác biệt duy nhất là ai sản xuất snapshot — historical ledger hay live exchange.

Test: `test_snapshot_injection_changes_decision_inputs` — inject snapshot equity $250k/cash $40k/position ETH 3.0 vào engine paper $100k flat ⇒ prepared phản ánh đúng giá trị snapshot.

---

## 8. Parity testing: pre-broker boundary

### Thiết kế

`TestParityLiveCycleVsBacktest::test_authority_outputs_identical_before_broker` chứng minh **live path và backtest path là một** ở tầng authority:

```
Cùng df (crossover bar cuối) + cùng promoted artifact + cùng initial $100k flat

LIVE side:  MultiPairRuntime(engine_A).run_cycle(provider)
            - submit_planned_order STUBBED (record plan, không gửi broker)
BACKTEST:   PortfolioBacktestEngine(engine_B, bars={binding: df})
            - plan_pair_order WRAPPED (record plan)

So sánh PLAN outputs (pre-broker):
  ✓ current_exposure / equity / available_cash / total_portfolio_exposure / current_price  (rel 1e-12)
  ✓ reduce_only semantics
  ✓ requested_target_exposure                                                              (rel 1e-12)
  ✓ approved_target_exposure (qua allocate_batch)                                          (rel 1e-12)
  ✓ plan quantity                                                                          (rel 1e-9)
```

### Vì sao so sánh PRE-broker

Broker là thành phần cố ý khác nhau (stub vs simulation). Mọi thứ TRƯỚC broker — resolver, strategy, decision authority, allocation, risk, planner — **phải** giống hệt. Đây chính là định nghĩa của "backtest chỉ thay broker + clock".

### Kết quả mong đợi

- Crossover rơi ở bar cuối ⇒ order không bao giờ fill (không có bar sau) ⇒ `expired_orders == 1` ở backtest — parity vẫn assert được vì so sánh plans chứ không phải fills.
- Quantity khớp rel 1e-9 (cùng công thức quantize từ cùng floats; replan_pair_with_live_truth bên live thấy cùng truth nên không đổi số).

---

## 9. Sử dụng

### Chạy portfolio backtest

```python
from trading_agent.backtest.portfolio_backtest import PortfolioBacktestEngine
from trading_agent.execution.engine import ExecutionEngine

# engine đã cấu hình resolver + stores (như live CLI run-promoted)
engine = ExecutionEngine(...)  # xem tests/test_multi_pair_runtime.py::_build_engine

pbt = PortfolioBacktestEngine(
    engine,
    bars={("BTC/USDT", "1h"): df_btc, ("ETH/USDT", "1h"): df_eth},
    initial_cash=100_000.0,
    fee_bps=10.0,  # qua broker defaults
    slippage_bps=5.0,  # adverse
)
result = pbt.run(environment="paper")

result.final_equity  # equity cuối
result.equity_curve  # [(t, equity), ...]
result.drawdown_series  # [(t, dd), ...]
result.per_symbol_contribution  # exact cash-flow per symbol
result.trades  # list[SimFill]
result.blocked_cycles  # số cycle bị preflight/atomic block
result.expired_orders  # order không bao giờ fill (data kết thúc)
```

### Broker độc lập (unit testing)

```python
from trading_agent.backtest.portfolio_backtest import (
    HistoricalSimulationBroker,
    QueuedOrder,
)

broker = HistoricalSimulationBroker(100_000.0, fee_bps=10.0, slippage_bps=5.0)
broker.queue(
    QueuedOrder(
        idempotency_key="plan_abc",  # duplicate key sẽ bị bỏ qua
        symbol="BTC/USDT",
        side="buy",
        quantity=0.5,
        reference_price=79_000.0,
        decision_time=decision_dt,
        phase="increase",  # hoặc "reduction"
    )
)
fills = broker.settle(now, clock, [("BTC/USDT", "1h")])
broker.reserved_cash()
broker.reserved_inventory("BTC/USDT")
```

### Invariants khi viết code mới quanh broker

1. Không bao giờ re-queue order bị guard reject.
2. Mọi mutation cash/positions PHẢI đi sau guard check.
3. Snapshot cho planner LUÔN qua reservation-aware builder — không bao giờ đọc raw broker state.
4. Thêm field vào `SimFill`/`QueuedOrder` phải update parity test assertions.

---

## 10. Ma trận kiểm thử

File: `tests/test_portfolio_backtest.py` (14 tests)

| Class | Tests | Phủ gì |
|---|---|---|
| `TestParityLiveCycleVsBacktest` | 2 | G9 parity pre-broker; snapshot injection |
| `TestBrokerPhaseAndReservations` | 3 | §4 phasing; §5 reservations; snapshot reflection |
| `TestBrokerFillSafetyGuards` | 4 | §6 guards: gap-up, oversized sell, multi-BUY aggregate, reservation undershoot |
| `TestBrokerRejectionDrop` | 4 | §6.3 permanent-drop guarantees R1–R4 |
| *(core engine tests)* | 1+ | no-lookahead, earliest-t+1, shared cap, determinism, replay, per-symbol identity |

Trạng thái suite: **1085 passed / 9 skipped**, ruff clean.

---

## Hạn chế đã biết (không che giấu)

- **Fill model là market-order tại next-bar open** — không mô phỏng partial book depth, maker/taker selection, funding. Với chiến lược low-frequency (≥ 4h) trên liquid pairs, chấp nhận được; high-frequency KHÔNG đáng tin (đã chứng minh bằng execution simulator: 4h RSI bị fee/slippage phá hủy).
- **Slippage adverse cố định bps** — không scale theo size/impact. Cần calibration với testnet fills trước khi tin turnover lớn.
- **Rejected BUY không retry** — by design (R2). Với chiến lược cần retry, phải lên planner level, không phải broker level.
- **Single currency (USDT quote)** — margin/cross-collateral chưa mô hình hóa.

## Lộ trình

- [ ] Calibration fee/slippage với testnet fills thực tế
- [ ] Impact model theo size (√/power-law) từ execution simulator
- [ ] Multi-quote-currency support nếu mở rộng beyond USDT pairs
