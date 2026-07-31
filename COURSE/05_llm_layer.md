# Bài 5: LLM Layer — 4 agents suy nghĩ bằng AI

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
python3 -c "from trading.llm.client import LLMClient; ..."
python3 scripts/test_phase2.py
```

## ❓ Câu hỏi tự kiểm tra

*(điền sau khi học)*
