# Bài 09 — Shared capital, portfolio constraints và regime router

> Mức độ: nâng cao · Thời lượng: 4–5 giờ · Trạng thái: **HIỆN HÀNH + MỤC TIÊU**

## Mục tiêu

- Phân biệt per-pair strategy score với portfolio utility.
- Hiểu shared capital, correlation, turnover và concentration constraints.
- Thiết kế regime router có uncertainty/hysteresis.
- Giải thích vì sao strategy tốt nhất từng pair không tạo portfolio tốt nhất.

## File/tài liệu cần đọc

- [Production Policy](../../PRODUCTION_POLICY.md)
- [Adaptive Roadmap — S5/S6](../../ADAPTIVE_STRATEGY_SELECTION_ROADMAP.md)
- `src/trading_agent/authority/portfolio.py`
- `src/trading_agent/authority/exposure.py`
- `src/trading_agent/portfolio/`
- `src/trading_agent/execution/correlation.py`
- `src/trading_agent/ml/regime_detection.py`
- `tests/test_portfolio_backtest.py`
- `tests/test_risk_decision.py`

## 1. Local optimum và portfolio optimum

Giả sử MA tốt nhất cho BTC, ETH, SOL nhưng ba return stream tương quan rất cao.
Chọn MA cho cả ba có thể tạo một bet trend duy nhất, không phải ba nguồn alpha.

Portfolio layer tối ưu dưới constraints:

```text
expected utility
- risk
- correlation/concentration
- turnover/execution cost
- uncertainty penalty
```

Strategy ranking chỉ là input, không có authority cuối.

## 2. Shared-capital invariants

- Tổng allocation không vượt available capital/risk budget.
- Gross/net exposure trong limits.
- Pair/strategy/regime concentration có cap.
- Correlated positions không được coi độc lập.
- Turnover/cost được tính trước rebalance.
- Reduction được ưu tiên khi constraint bị vi phạm.
- Infeasible solution phải reduce/reject, không normalize mù.

## 3. Lab tính tay — Chọn dưới capital constraint

Bạn có vốn 100, risk budget 12. Ba candidate:

| Pair/strategy | Expected return | Risk | Corr với BTC | Requested capital |
| --- | ---: | ---: | ---: | ---: |
| BTC/MA | 8 | 6 | 1.00 | 60 |
| ETH/MA | 7 | 5 | 0.88 | 55 |
| SOL/RSI | 5 | 4 | 0.35 | 45 |

Constraints:

- total capital <= 100;
- max pair 50;
- correlated MA cluster <= 65;
- total risk approximation <= 12.

Tạo ít nhất hai allocation khả thi và giải thích trade-off. Không cần optimizer
phức tạp; mục tiêu là thấy per-pair winners không thể cộng trực tiếp.

## 4. Regime routing

Router không nên dùng hard label đơn giản:

```text
if regime == bull: switch immediately
```

Contract an toàn nên xét:

- posterior probabilities;
- entropy/uncertainty;
- minimum confidence;
- hysteresis thresholds;
- minimum dwell/hold time;
- switching cost;
- incumbent health;
- fallback/abstain.

## 5. Bài tập timeline hysteresis

Posterior trend qua 8 kỳ:

```text
0.58, 0.63, 0.69, 0.72, 0.68, 0.75, 0.77, 0.61
```

Policy:

- enter trend strategy khi posterior >= 0.70 trong 2 kỳ liên tiếp;
- exit khi posterior < 0.60 trong 2 kỳ;
- minimum dwell = 3 kỳ;
- entropy cao → reduce 50%, không switch.

Vẽ routing state và exposure từng kỳ. Giải thích tại sao policy tránh churn.

## 6. Handover giữa strategies

Switch strategy không đồng nghĩa đóng/mở toàn bộ ngay. Handover cần xác định:

- ownership của position hiện tại;
- reduce-only transition;
- target exposure net giữa incumbent/challenger;
- outstanding orders;
- protection transfer;
- attribution trước/sau switch;
- cooldown nếu switch thất bại.

## 7. Bài tập thiết kế routing decision

Thiết kế `RoutingDecision` gồm:

```text
decision_id
observed_at
pair/timeframe
regime posterior + entropy
incumbent policy/strategy
candidate policy/strategy
action: KEEP | SWITCH | REDUCE | ABSTAIN
reason codes
hysteresis/dwell state
target risk multiplier
parent policy/promotion IDs
```

Field nào phải content-addressed? Field nào là transient state?

## 8. Lỗi thường gặp

- Chọn strategy tốt nhất từng pair rồi cộng allocation.
- Dùng hard regime label không uncertainty.
- Switch mỗi bar.
- Quên switching cost và outstanding orders.
- Router mở exposure vượt portfolio constraint.
- Attribution trade sau switch về sai strategy.

## Exit gate

- [ ] Tạo hai allocation khả thi và giải thích constraints.
- [ ] Giải đúng timeline hysteresis.
- [ ] Thiết kế handover không tăng exposure trái phép.
- [ ] Viết `RoutingDecision` contract.
- [ ] Giải thích portfolio authority cao hơn router preference.

Tiếp theo: [Bài 10 — Vận hành và release](10_VAN_HANH.md).

