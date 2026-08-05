# Bài 4: Strategies & Plugins — chiến lược như app store

> **Trạng thái:** 📝 DRAFT (chưa học — điền sau khi học xong)
> **File gốc:** `trading/strategies/` + `trading/strategies/plugins/adapters.py` (269 dòng)

## 🎯 Mục tiêu

*(điền sau khi học)*

## 📂 File cần đọc

- `trading/strategies/plugins/adapters.py`
- `trading/strategies/plugins/strategy_plugin.py`
- `trading/strategies/online_learning_strategy.py`

## 🔑 Khái niệm chính (preview)

- Plugin architecture với pluggy — chiến lược mới = 1 file đăng ký, không sửa core
- Strategy interface: `init`, `on_bar`, `on_signal`, `on_fill`, `get_params`
- Sandbox (`strategies/sandbox.py`) — chạy code bên thứ 3 an toàn
- Versioning (git-based, rollback, A/B test)

## 🧪 Demo plan

```bash
python3 -c "from trading_agent.strategies.plugins.adapters import ...; ..."
python3 -m pytest tests/test_phase6_integration.py -k "registry or sandbox or validate"
```

## ❓ Câu hỏi tự kiểm tra

*(điền sau khi học)*
