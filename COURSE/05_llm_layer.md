# Bài 5: LLM Layer — 4 agents suy nghĩ bằng AI

> ⚠️ **HISTORICAL DRAFT:** Được giữ để truy vết, không mô tả authority chain hiện hành. Xem [Course V2](../docs/tutorials/README.md).

> **Trạng thái:** 📝 DRAFT (chưa học — điền sau khi học xong)
> **File gốc:** `trading/llm/client.py` (255 dòng)

## 🎯 Mục tiêu

*(điền sau khi học)*

## 📂 File cần đọc

- `trading/llm/client.py`

## 🔑 Khái niệm chính (preview)

- Fallback chain: OpenCode (deepseek-v4-flash-free, $0) → OpenAI → DeepSeek → Ollama
- `chat()` và `ask_agent()` — prompt có hệ thống role riêng
- max_tokens=500 phù hợp free tier, chi phí ~$0.00009/cycle
- USE_LLM=false fallback khi không có network

## 🧪 Demo plan

```bash
python3 -c "from trading_agent.llm.client import LLMClient; ..."
python3 -m pytest tests/test_phase2.py -q
```

## ❓ Câu hỏi tự kiểm tra

*(điền sau khi học)*
