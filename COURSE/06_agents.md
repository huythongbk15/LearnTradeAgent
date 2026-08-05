# Bài 6: Agents — Technical · Sentiment · Risk · Trader

> **Trạng thái:** 📝 DRAFT (chưa học — điền sau khi học xong)
> **File gốc:** `trading/agents/base.py` (134 dòng) + `trading/agents/swarm/`

## 🎯 Mục tiêu

*(điền sau khi học)*

## 📂 File cần đọc

- `trading/agents/base.py`
- `trading/agents/swarm/coordinator.py`, `registry.py`, `specialized.py`

## 🔑 Khái niệm chính (preview)

- 4 agent: Technical (HOLD/65%), Sentiment (HOLD/55%), Risk (BUY/60%), Trader (HOLD/24%)
- Weighted voting — kết hợp ý kiến các agent thành 1 quyết định
- Agent chuyên biệt + coordinator (swarm)
- Multi-timeframe: 1h/4h/1d

## 🧪 Demo plan

```bash
python3 -m trading_agent.cli.main agents analyze BTC/USDT
python3 -m pytest tests/ -k "agent"
```

## ❓ Câu hỏi tự kiểm tra

*(điền sau khi học)*
