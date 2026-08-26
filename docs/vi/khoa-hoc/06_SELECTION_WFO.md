# Bài 06 — Walk-forward, overfitting và statistical selection

> Mức độ: trung cấp–nâng cao · Thời lượng: 5–6 giờ · Trạng thái: **MỤC TIÊU**

Phần này mô tả năng lực cần hoàn thiện theo roadmap. Không được đọc như tuyên bố
rằng selector S3/S4 đã production-ready.

## Mục tiêu

- Phân biệt parameter tuning, model selection và unbiased evaluation.
- Thiết kế nested walk-forward theo thời gian.
- Hiểu multiple testing và selection bias.
- Xây hard gates cho một policy có `NO_SELECTION`.
- Viết selection memo từ artifact mà không overclaim.

## Tài liệu cần đọc

- [Research Methodology](../../RESEARCH_METHODOLOGY.md)
- [Research Holdout](../../RESEARCH_HOLDOUT.md)
- [Research Evidence](../../RESEARCH_EVIDENCE.md)
- [Adaptive Strategy Roadmap — S3/S4](../../ADAPTIVE_STRATEGY_SELECTION_ROADMAP.md)
- `tests/test_holdout_manifest.py`

## 1. Vì sao chọn max Sharpe là sai?

Nếu thử 100 candidate không có edge thật, một vài candidate vẫn có Sharpe cao do
may mắn. Chọn maximum rồi báo chính con số đó gây selection bias.

Bạn cần tách:

```text
inner train/validation: chọn params/candidate
outer test: ước lượng behavior sau selection
locked holdout: kiểm tra cuối theo policy đã khóa
```

## 2. Nested walk-forward

Ví dụ:

```text
Outer fold 1: [train ─────][test]
Outer fold 2:    [train ─────][test]
Outer fold 3:       [train ─────][test]

Trong mỗi outer train:
  inner fold A/B/C chọn params hoặc strategy
```

Không được dùng outer test để điều chỉnh inner selection rule.

## 3. Hard gates trước ranking

| Gate | Ví dụ câu hỏi |
| --- | --- |
| Data integrity | Manifest/window có hợp lệ và không leakage? |
| Sample | Trades/effective observations đủ không? |
| Risk | Drawdown/tail loss có vượt policy? |
| Costs | Net result còn chịu được cost stress? |
| Stability | Các fold/params lân cận có nhất quán? |
| Statistics | Uncertainty/multiple testing có được xử lý? |
| Execution | Reject/fault/reality gap có chấp nhận được? |
| Incumbent | Candidate có thực sự tốt hơn incumbent sau costs? |

Chỉ candidate pass toàn bộ hard gates mới được ranking.

## 4. Multiple-testing worksheet

Giả sử:

- 5 strategies;
- 10 pairs;
- 8 parameter sets mỗi strategy;
- 3 cost scenarios;
- 6 folds.

Trả lời:

1. Có bao nhiêu raw evaluations?
2. “Best cell” đã trải qua bao nhiêu cơ hội may mắn?
3. Nên group hypothesis theo strategy/pair thế nào?
4. Dùng FDR/deflated Sharpe/bootstrap ở stage nào?
5. Holdout được mở bao nhiêu lần?

Không cần một công thức duy nhất; cần policy được khóa trước khi xem kết quả.

## 5. Lab thực hành — Tự xây selection policy trên giấy

Tạo policy trước khi đọc tournament metrics:

```text
Minimum trades per outer fold:
Minimum completed folds:
Maximum drawdown:
Minimum net profit factor:
Cost-stress survival:
Maximum failed-cell ratio:
Parameter stability rule:
Uncertainty rule:
Incumbent improvement rule:
Tie-breaker:
NO_SELECTION condition:
```

Sau đó mới mở `data/backtests/tournament/tournament_index.json` và áp dụng policy.
Không sửa ngưỡng để “cứu” candidate.

## 6. Bài tập tính tay

Cho ba candidate:

| Candidate | Outer returns | Max DD | Trades | Stress PF | Failed folds |
| --- | --- | ---: | ---: | ---: | ---: |
| A | 4%, 5%, -8%, 6% | 22% | 120 | 0.95 | 0 |
| B | 2%, 2%, 1%, 2% | 8% | 80 | 1.08 | 0 |
| C | 10%, -1%, 12%, -9% | 28% | 35 | 1.30 | 1 |

1. Candidate nào ổn định nhất?
2. Candidate nào dễ gây ấn tượng sai nếu chỉ nhìn mean return?
3. Nếu incumbent có 1.8% mỗi fold, bạn chọn gì?
4. Khi nào đáp án nên là `KEEP_INCUMBENT`?

## 7. Parameter stability

Một optimum tốt nên có neighborhood tương đối ổn định. Nếu `(20, 50)` rất tốt
nhưng `(19, 50)`, `(21, 50)`, `(20, 49)` đều sụp, khả năng cao là brittle fit.

Bài tập: thiết kế heatmap/table kiểm tra neighborhood nhưng không dùng holdout để
chọn lại center.

## 8. Selection memo

Dùng mẫu selection memo và bắt buộc có:

- candidate universe bị khóa;
- số trials thực;
- fold/holdout policy;
- hard-gate results;
- uncertainty;
- incumbent comparison;
- lý do `SELECT`, `KEEP_INCUMBENT` hoặc `NO_SELECTION`.

## Lỗi thường gặp

- Tune trên toàn bộ data rồi gọi phần cuối là OOS.
- Mở holdout nhiều lần mà không ghi trials.
- Ranking trước hard gates.
- Gộp folds bằng mean và bỏ qua worst fold.
- Dùng quá ít trades.
- Thay policy sau khi thấy candidate yêu thích fail.

## Exit gate

- [ ] Vẽ đúng nested WFO và data boundary.
- [ ] Hoàn thành multiple-testing worksheet.
- [ ] Viết policy trước khi đọc metrics.
- [ ] Chấm ba candidate và giải thích incumbent decision.
- [ ] Viết selection memo có `NO_SELECTION` hợp lệ.

Tiếp theo: [Bài 07 — Promotion và runtime](07_PROMOTION_RUNTIME.md).

