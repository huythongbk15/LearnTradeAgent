# Research Evidence

> Tài liệu này phân biệt rõ từng loại bằng chứng nghiên cứu và mức độ tin cậy của chúng.
> **Backtest không phải production validation.** Một config "tốt trong backtest" chỉ là
> *candidate research configuration* cho tới khi có statistical validation + testnet/paper
> evidence.
>
> Snapshot cũ (2026-08-06): [`docs/archive/research/BACKTEST_SUMMARY_2026-08-06.md`](archive/research/BACKTEST_SUMMARY_2026-08-06.md)

## Phân loại bằng chứng

| Loại | Định nghĩa | Độ tin cậy cho production |
| --- | --- | --- |
| In-sample | Huấn luyện + đánh giá trên cùng dữ liệu | Thấp (overfit risk cao) |
| Out-of-sample (OOS) | Đánh giá trên dữ liệu không dùng để chọn tham số | Trung bình |
| Walk-forward (WFO) | Rolling train/test qua nhiều fold | Trung bình–cao nếu đủ fold & min trades |
| Final untouched holdout | 6 tháng cuối đã freeze trong manifest và chưa được dùng để tune | Cao nhất khi chỉ mở một lần sau khi protocol được khóa |
| Paper / Testnet | Thực thi mô phỏng với dữ liệu thật | Cao (nhưng không = live) |
| Live | Vốn thật | Chỉ sau release gates |

## Bằng chứng hiện có (tổng hợp)

### 1. Multi-symbol multi-timeframe backtest — enhanced_ma (snapshot 2026-08-06)

- **Loại:** In-sample (params tối ưu trên toàn bộ chuỗi) — **KHÔNG phải OOS**.
- **Provenance:** commit lịch sử trước `e7f51ba`; dataset 2023-01-01 → 2026-08-05 (~31,500 hourly bars/symbol).
- **Fee/slippage:** chưa model đầy đủ (transaction costs ghi chú là chưa tính).
- **Kết quả nổi bật:** BTC 4h +164.44% (Sharpe 1.05, PF 2.82, 32 trades); SOL 1h +772.49% (Sharpe 1.27, 147 trades); SOL 1h DD -63.63%.
- **Cảnh báo:** các fold/chuỗi có ít trade (32-40 trades/3.5 năm) có Sharpe không đáng tin;
  win rate/PF của full-system backtest báo 0% / 0.00 (calculation issue — không dùng làm bằng chứng).
- **Kết luận:** `CANDIDATE RESEARCH CONFIGURATION` — không phải production config.

### 2. Walk-forward & multi-symbol benchmark (2026-08-05/06)

- **Loại:** WFO (expanding window) — nhưng **OOS Sharpe ≈ 0** trên nhiều fold: strategy
  MA+RSI **không generalize** ngoài in-sample.
- **Kết luận:** `FAIL` cho mục đích production; chỉ giữ lại làm baseline research.

### 3. Full-system 3-year simulation

- **Loại:** Paper simulation với risk controls (event-driven, không vectorized).
- **Kết quả:** +22.26% → +36.28% (tùy snapshot), PF 1.53 (một snapshot), nhưng **win rate / PF
  có calculation issue** ở snapshot sau; 2025-2026 lợi nhuận ≈ 0 (regime dependence).
- **Kết luận:** `INSUFFICIENT EVIDENCE` cho production.

### 4. Alpaca Paper live (từ 2026-08-08)

- **Loại:** Paper validated (không phải live vốn thật).
- **Evidence:** equity ~$95-100k, position sync BTC/SOL/AVAX, fills real qua Alpaca paper endpoint.
- **Kết luận:** xác nhận execution path chạy được ở paper; không chứng minh edge.

### 5. Cost stress test (2026-08-11) — P2 statistical hardening

- **Loại:** Parametrized evaluation of live strategy evidence pipeline at 1x/2x/3x cost multipliers.
- **Provenance:** commit `69904e2`; `scripts/stress_evidence_costs.py`; data 2023-01-01 → 2026-08-11 (~31,600 hourly bars/symbol).
- **Symbols:** BTC/USDT (50%), SOL/USDT (50%), 6 folds × 180 days.
- **Kết quả:**
  - 1x: median Sharpe -0.44, worst DD 0.1%, trades/fold 15-21
  - 2x: median Sharpe -1.18, worst DD 0.1%, trades/fold 15-21
  - 3x: median Sharpe -2.06, worst DD 0.2%, trades/fold 15-21
- **Kết luận:** **`FAIL`** — strategy có **edge âm tại mọi mức cost**, không pass acceptance gate. Xác nhận `NO-GO` cho mainnet.

## Trạng thái statistical hardening

| Yêu cầu (prompt mục 6) | Trạng thái |
| --- | --- |
| Minimum trades per fold | ✅ **Done** — enforced via `min_trades_check` (tests/test_research_stats.py::TestMinTrades) |
| 6-12 tháng final untouched holdout | ✅ **Frozen** — immutable manifest exists; intentionally not opened for iterative tuning |
| Regime breakdown | Một phần (ghi chú regime dependence) |
| Block bootstrap confidence intervals | ✅ **Done** — `block_bootstrap_sharpe_ci` + tests |
| Probabilistic / Deflated Sharpe | ✅ **Done** — `probabilistic_sharpe_ratio`, `deflated_sharpe_ratio` + tests (DSR accounts for ~8000 explored trials) |
| Fee stress 1x/2x/3x | ✅ **Done** — `scripts/stress_evidence_costs.py`: med Sharpe -0.44/-1.18/-2.06 (1x/2x/3x), **no edge at any level** |
| Spread / slippage stress | ✅ **Done** — included in cost stress multipliers |
| Gap / latency / missing candle / partial fill / outage / correlated drawdown scenarios | ✅ **Done** — property tests in `tests/test_live_safety.py` (fail-closed + hypothesis) |

→ Kết luận chung: **`INSUFFICIENT EVIDENCE`** — không config nào được gọi là
`RECOMMENDED PRODUCTION CONFIG`. Các tham số enhanced_ma (fast 15 / slow 100 / ADX>40 /
ATR SL 2.0 / TP 6.0) là *candidate research configuration* duy nhất đáng đưa vào testnet.

## Provenance template (dùng cho mọi result mới)

Mỗi kết quả research mới phải ghi: commit SHA · dataset hash/manifest · date range ·
timeframe · fees · spread · slippage · trade count · Sharpe · max DD · profit factor ·
benchmark · OOS? · exact command.

## Methodology hardening snapshot (2026-08-17)

The executable methodology now uses actual gross/net returns, target-position
turnover/cost, train-only robust transforms, nested expanding outer/inner folds,
purge/embargo, one-shot outer testing, registry-derived trial counts and full-space
PBO/CSCV. Calibration, conformal, drift, simulator provenance and promotion
evidence are immutable artifacts with explicit source/status.

The deterministic benchmark in `scripts/benchmark_methodology.py` is classified
`SYNTHETIC_DIAGNOSTIC`: selected alpha and oracle-assisted regime mixture win their
fixtures; adaptive experts lose fixed baselines; MPC is not empirically
benchmarkable; Platt calibration improves an independent synthetic test. None of
these results demonstrates a market edge. Details and exact values are in
[`RESEARCH_METHODOLOGY.md`](RESEARCH_METHODOLOGY.md).
