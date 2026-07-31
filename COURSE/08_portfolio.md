# Bài 8: Portfolio — tổng tư lệnh vốn

> **Trạng thái:** 📝 DRAFT (chưa học — điền sau khi học xong)
> **File gốc:** `trading/portfolio/` (7 file)

## 🎯 Mục tiêu

*(điền sau khi học)*

## 📂 File cần đọc

- `portfolio/risk_budgeting.py` — risk parity, ERC
- `portfolio/portfolio_optimizer.py` — mean-variance, HRP, Black-Litterman
- `portfolio/auto_rebalancer.py` — calendar/threshold/CPPI
- `portfolio/capital_allocation/` — Kelly sizing
- `portfolio/attribution/` — Brinson analysis

## 🔑 Khái niệm chính (preview)

- Risk budgeting: chia vốn theo mức rủi ro, không chia đều mù quáng
- Black-Litterman: kết hợp LLM views + dữ liệu lịch sử
- Rebalancer: giữ danh mục đúng tỷ trọng
- Attribution: "tháng này lời nhờ đâu?"

## 🧪 Demo plan

```bash
python3 -m pytest tests/test_phase6_integration.py -k "black_litterman or hrp or rebalanc or attribution"
```

## ❓ Câu hỏi tự kiểm tra

*(điền sau khi học)*
