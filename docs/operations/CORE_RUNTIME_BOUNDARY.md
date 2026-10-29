# Ranh giới lõi runtime và chia việc tới hệ thống vận hành được

Status: TARGET
Owner: execution + runtime
Verified: 2026-10-29 (đo trên `origin/master` 70c37c47 và sau từng bước R1–R3b)
Evidence: [`tests/test_runtime_import_boundary.py`](../../tests/test_runtime_import_boundary.py); đo import runtime; lệnh tái lập ở §6

Tài liệu này **không** thay [L0–L4](LIVE_READINESS_AGENT_PLAN.md). Nó tách phần
code mà luồng vận hành thật cần ra khỏi phần nghiên cứu/mở rộng. Ranh giới đó là
tiền đề để làm L0 ("hoàn thiện đường thực thi") trên một lõi nhỏ, đo được. Mainnet
vẫn **NO-GO**; không mục nào ở đây cấp quyền chạy thị trường.

## 1. Luồng đang vận hành thật

Theo crontab hiện tại (2026-10-29):

| Lịch | Lệnh | Vai trò |
| --- | --- | --- |
| 2 giờ/lần | `scripts/live_enhanced_ma_binance.py --testnet --execute` | Runner Binance testnet (luồng chính) |
| 2 giờ/lần | `scripts/live_cron_runner.py --execute` → subprocess `scripts/live_enhanced_ma.py` | Alpaca paper |
| 6 giờ/lần | `scripts/testnet_soak_tracker.py --check` | Theo dõi soak |
| hằng ngày | `scripts/live_status_report.py`, `trading-agent system daily` | Báo cáo |

Ngoài các luồng trên, mọi thứ khác là nghiên cứu offline hoặc chưa dùng.

## 2. Đo hiện trạng

| Chỉ số | Giá trị |
| --- | --- |
| Toàn bộ `src/trading_agent` | 298 file, ~114k dòng |
| Bao đóng import tĩnh của runner testnet | 167 module, ~81k dòng |
| Import runtime của runner testnet | **123 module** `trading_agent` (13 `agents`, 6 `llm`, 5 `ml`), nạp cả sklearn/scipy/hmmlearn; ~1,9 s khi cache nóng (ccxt+polars riêng: 0,28 s), 14,8 s ở lần đầu khi cache đĩa lạnh |
| Sau R1–R3b | Testnet **89** module, Alpaca **62** module; không `agents`/`llm`/`ml`/`backtest`/sklearn/scipy/hmmlearn; import ~1,15 s / ~1,0 s khi cache nóng; không cần `config/config.yaml` để import |
| Lõi runner thực sự cần (ước lượng tĩnh, sau khi cắt các cạnh ở §3) | **44 module, ~22k dòng** |

Một chiến lược MA trên testnet đang kéo theo LLM agents, HMM regime và backtest engine
chỉ vì import cấp module. Vấn đề chính không phải tốc độ mà là phụ thuộc: mỗi lần chạy
cron phụ thuộc vào code nghiên cứu không liên quan, nên lỗi import hoặc thay đổi trong
các phần đó có thể làm hỏng luồng testnet.

## 3. Các cạnh phải cắt

Lần đo đầu chỉ tìm thấy bốn cạnh. Khi làm R2, lần đo lại cho thấy còn có các cạnh đi qua
`__init__.py` của package, vốn import sẵn mọi thứ. Các cạnh này mới là đường chính:

| Importer | Kéo vào | Trạng thái |
| --- | --- | --- |
| `strategies/__init__.py` | mọi strategy: `agent_ensemble` → `agents` → `agents.swarm.specialized` → `llm`; `online_learning_strategy`, `regime_switching` → `ml` → sklearn/hmmlearn | **Đã cắt (R3)**: `agent_ensemble`, `regime_switching` đăng ký khi được tra theo tên (`strategies.base._LAZY_STRATEGY_MODULES`); `online_learning_strategy` không đăng ký gì nên không cần import |
| `authority/__init__.py` | `adaptive_router` → `llm.context_enrichment`, `ml.regime_detection` | **Đã cắt (R2)**: re-export lười (PEP 562) |
| `execution/canonical/order_planner.py` | `llm.context_enrichment` (chỉ dùng kiểu `MarketContext`) | **Đã cắt (R2)**: `TYPE_CHECKING` |

Bốn cạnh đo lần đầu:

| Importer | Kéo vào | Lý do hiện có | Hướng cắt |
| --- | --- | --- | --- |
| `authority/resolver.py` | `strategies/{ma_crossover,rsi,bbands,online_learning_strategy,regime_switching}` → `ml`, `backtest.engine` | Registry strategy import eager ở cấp module | **Đã làm (R3)**: `online_learning`, `regime_switching` là `LazyStrategyClass`; `get_strategy_class*` vẫn trả về lớp thật |
| `execution/canonical/order_planner.py` | `llm/context_enrichment` → `llm/research_memory`, `agents/llm` | Chỉ dùng kiểu `MarketContext` cho tham số tùy chọn | **Đã làm (R2)**: import dưới `TYPE_CHECKING` |
| `execution/canonical/risk_decision.py` | `agents/risk_decision` | Import trong hàm (đường legacy) | Giữ lười; thêm vào allowlist có lý do, hoặc tách hàm chuyển đổi legacy ra module riêng |
| `execution/live_admission.py` | `strategies/canonical` → `candidates.py` → toàn bộ strategies | Gọi `build_default_registry()` ở mỗi lần chạy để so `code_sha` | **Đã làm (R3)**: `regime_switching` đăng ký bằng factory lười, `code_source` là đường dẫn file (cùng file, cùng hash); registry vẫn đủ 20 entry, `descriptor_id`/`code_sha` không đổi |

## 4. Phân tầng module

**Tầng 0 — lõi runtime** (luồng vận hành phụ thuộc, phải ổn định, phải có test):
- `execution`: `permission`, `live_safety`, `data_trust`, `live_admission`, `application`, `correlation`, `batch_models`, `position_cycle`, `types`, `lifecycle/*`, `canonical/*`
- `exchanges`: `ccxt_adapter`, `live_broker`, `models`
- `authority`: `config`, `portfolio`, `resolver`, `promotion_store`, `decision`, `exposure`, `causation`, `loader`
- `research`: `artifact`, `promotion`, `lifecycle`, `forecast`, `calibration`, `drift` (vì `promotion_store` dùng; xem lại ở R3)
- `strategies`: `base`, `enhanced_ma` (chiến lược đang deploy)
- `regime.py`
- Thêm `config`, cảnh báo Telegram và `monitoring` khi nối vào runner (P1.3 trong LIVE_TRADING_TODO)

**Tầng 1 — nghiên cứu offline** (giữ nguyên, không được import từ Tầng 0):
`backtest`, `research` (phần còn lại), `calibration`, `alpha_research`, các strategy khác,
`portfolio`, `ml`, `execution/simulator`, `execution/backtest_sim`

**Tầng 2 — mở rộng, đóng băng** (không phát triển thêm cho tới khi Tầng 0 qua L0; không xóa trong bước này):

| Package | Người import trong `src` | Ghi chú |
| --- | --- | --- |
| `features` | 0 (kể cả tests) | Code chết |
| `enterprise`, `messaging` | 0 | Chỉ có test riêng |
| `infrastructure` (K8s multi-region, chaos) | 0 | Chỉ script/test riêng |
| `online_learning` | 1 | |
| `agents`, `llm` | 7 / 11 | Chỉ được dùng ở runtime qua các cạnh ở §3 |
| `exchanges/{dex,futures}`, `oanda_adapter`, `order_router`, `websocket_manager` | — | Ngoài phạm vi spot Binance/Alpaca |
| `strategies/{options_strategies,stat_arbitrage,plugins,…}` | — | |

## 5. Gói việc

Mỗi gói là một PR nhỏ, có test, không đổi hành vi giao dịch. Thứ tự theo phụ thuộc.

| ID | Nội dung | Hoàn thành khi |
| --- | --- | --- |
| **R1** ✅ | `tests/test_runtime_import_boundary.py`: nạp (không chạy) runner testnet và runner Alpaca trong subprocess sạch; cấm `trading_agent.{agents,llm,ml,backtest,alpha_research}` và sklearn/scipy/hmmlearn. Mục còn mở là `xfail(strict=True)`. Thêm hai kiểm tra trực tiếp cho các cạnh R2 đã cắt | `backtest`/`alpha_research` đã pass; `llm`/`agents`/`ml`/sklearn/scipy/hmmlearn XFAIL chờ R3 |
| **R2** ✅ | Cắt `order_planner → llm` (`TYPE_CHECKING`) và `authority/__init__ → adaptive_router` (re-export lười, API giữ nguyên) | Runner không còn nạp `adaptive_router`; planner một mình không nạp `llm`. Đo: testnet 123 → 121 module, Alpaca 94 → 94, thời gian import không đổi, vì `llm` vẫn đến qua `agents` (R3) |
| **R3** ✅ | Registry strategy lười (`strategies/base.py` `LazyStrategyClass`, `_LAZY_STRATEGY_MODULES`; `authority/resolver.py`; `strategies/canonical/candidates.py`; `canonical/registry.py` chấp nhận `code_source` là `Path`) | Runner thôi nạp `agents`/`llm`/`ml`/hmmlearn; dựng registry admission không nạp `ml`. Đo: testnet 121 → **89** module, Alpaca 94 → **62**; registry canonical và tên registry cũ không đổi; tên lạ vẫn fail-closed. `config/config.yaml` không còn cần cho riêng việc import runner |
| **R3b** ✅ | scipy/sklearn chỉ import trong hàm dùng chúng: `research/drift.py` (`DriftMonitor`, Wasserstein/KS), `research/calibration.py` (fit isotonic/Platt/temperature), `risk/portfolio_risk.py` (`ParametricVaR`), giống mẫu sẵn có ở `research/uncertainty.py` | R1 không còn xfail: cấm cả sklearn/scipy. Đo: testnet 89 module, ~1,15 s (từ ~1,9 s); Alpaca 62 module, ~1,0 s (từ ~1,8 s); không nạp sklearn/scipy/hmmlearn |
| **R4** ✅ | `risk_decision → agents` nằm trong hàm chuyển đổi legacy nên không nạp khi import; mục tiêu "R1 hết xfail" đạt nhờ R3b | Không cần đổi code thêm |
| **R5** ✅ | Tầng 2 được đánh dấu *frozen* trong [CAPABILITY_MATRIX.md](../CAPABILITY_MATRIX.md); `features` giữ nguyên (code chết, không xóa); test ranh giới cấm runner nạp `features`, `enterprise`, `messaging`, `infrastructure`, `online_learning`, `exchanges.dex`, `exchanges.futures` | Đạt. Còn theo dõi bằng xfail strict: `exchanges.{oanda_adapter,order_router,websocket_manager}` (nạp qua `exchanges/__init__`) và `strategies.options_strategies` (qua `strategies/__init__`) |
| **L0** | Làm L0.1–L0.4 trên lõi Tầng 0 theo [LIVE_READINESS_AGENT_PLAN.md](LIVE_READINESS_AGENT_PLAN.md) | Theo tiêu chí L0 sẵn có |

Ngoài phạm vi: viết lại simulator, tách `nested_wfo.py`, xóa package, mở chiến lược
hay sàn mới, thay đổi gate/ngưỡng.

## 6. Tái lập phép đo

Import runtime (in thời gian import và các package `trading_agent` được nạp):

```bash
PYTHONPATH=src:scripts .venv/bin/python -c "import time,sys,importlib.util as u;t=time.perf_counter();s=u.spec_from_file_location('r','scripts/live_enhanced_ma_binance.py');m=u.module_from_spec(s);s.loader.exec_module(m);print(round(time.perf_counter()-t,2),'s',sorted({k.split('.')[1] for k in sys.modules if k.startswith('trading_agent.')}))"
```

Sau R1, test ranh giới là phép đo chuẩn; lệnh trên chỉ để tham khảo.
