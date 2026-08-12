# 📚 Khóa học: Bóc tách Trading Agent System

> Học lại toàn bộ hệ thống từ gốc — hiểu **từng phần nhỏ**: đọc code thật, hiểu *vì sao* thiết kế như vậy, chạy demo, rồi tự tay viết lại.

## 🎯 Cách học (4 bước)

1. **Đọc** file gốc được chỉ định (có đường dẫn + số dòng)
2. **Hiểu vì sao** — mỗi thiết kế đều có lý do (chỉ ra trong bài)
3. **Demo** — chạy đoạn code mẫu, quan sát output
4. **Tự kiểm tra** — trả lời câu hỏi cuối bài, tự viết lại mini-version

> Quy tắc vàng: **không đọc trước khi hiểu mục tiêu**. Mỗi bài đều bắt đầu bằng "dùng để làm gì".

## 🗺 Bản đồ hệ thống

```
┌─────────────────────────────────────────────────────────────┐
│  PHASE 0-3 (core loop)                                      │
│  data/pipeline ─→ backtest/engine ─→ agents ─→ execution    │
│   (654 dòng)       (370 dòng)       (4 agents)   (paper)    │
├─────────────────────────────────────────────────────────────┤
│  PHASE 4-5 (ops): monitoring · events · infra · CI/CD    │
├─────────────────────────────────────────────────────────────┤
│  PHASE 6 (scale): exchanges/ (8 sàn + DEX + stocks + forex) │
│                   portfolio/ (7 module) · ml/ · strategies/ │
│                   messaging/ · infrastructure/              │
└─────────────────────────────────────────────────────────────┘
```

## 📋 Lộ trình 10 bài

| # | Bài | File gốc | Trạng thái |
|---|-----|----------|------------|
| [1](01_data_model.md) | Data Model | `trading/exchanges/models.py` | ✅ Đầy đủ |
| [2](02_data_pipeline.md) | Data Pipeline | `trading/data/pipeline.py` (654d) | ✅ Đầy đủ |
| [3](03_backtest_engine.md) | Backtest Engine | `trading/backtest/engine.py` (370d) + `src/trading_agent/backtest/engine.py` | ✅ Đầy đủ |
| [4](04_strategies_plugins.md) | Strategies & Plugins | `trading/strategies/` | 📝 DRAFT |
| [5](05_llm_layer.md) | LLM Layer | `trading/llm/client.py` (255d) | 📝 DRAFT |
| [6](06_agents.md) | Agents | `trading/agents/base.py` (134d) | 📝 DRAFT |
| [7](07_execution_risk.md) | Execution & Risk | `scripts/trade_local.py` | 📝 DRAFT |
| [8](08_portfolio.md) | Portfolio | `trading/portfolio/` (7 file) | 📝 DRAFT |
| [9](09_multi_exchange.md) | Multi-Exchange | `trading/exchanges/order_router.py` | 📝 DRAFT |
| [10](10_ml_infra.md) | ML + Infra | `trading/ml/`, `events/`, `messaging/` | 📝 DRAFT |

## 🖥 Yêu cầu môi trường

```bash
# Từ thư mục gốc của project
cd <repo-root>

# Môi trường Python đã cài sẵn các dependency của project
python3 -c "import trading; print('OK')"

# Chạy demo một bài
python3 -c "..."        # xem từng bài
python3 -m pytest tests/ -q   # chạy toàn bộ test (81 tests)
```

## 📝 Cách cập nhật

- Học xong bài nào → điền nội dung đầy đủ vào file tương ứng, đổi `📝 DRAFT` → `✅ DONE` ở bảng trên
- Commit + push:
```bash
git add COURSE/ && git commit -m "course: add lesson N" && git push origin master
```
