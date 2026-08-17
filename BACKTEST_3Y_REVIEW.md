# BACKTEST 3Y REVIEW — 2026-08-17
## Data
- BTC/ETH/XRP 1h/4h từ Binance, 2023-01-01 → 2026-08-05/17
- 30m: ETH/XRP chỉ có từ ~2022-08-10, không đủ 3y liên tục → không chạy

## 1. Walk-Forward Optimization (MA+RSI, vectorized, fast mode)
| Pair | TF | Windows | OOS Sharpe | OOS Return | OOS MaxDD |
|---|---|---|---|---|---|
| BTC | 1h | 9 | -0.92 | -1.9% | 10.0% |
| ETH | 1h | 13 | 0.14 | 1.0% | 11.6% |
| XRP | 1h | 11 | 0.88 | 10.1% | 12.6% |
| BTC | 4h | 5 | 3.08 | 4.1% | 9.7% |
| ETH | 4h | 1 | 1.91 | 2.7% | 17.0% |
| XRP | 4h | 5 | 0.62 | 0.0% | 15.0% |

## 2. Full System Backtest (2023-2026, 4h)
| Pair | Return | Sharpe | Max DD | Trades | Win Rate | Profit Factor |
|---|---|---|---|---|---|---|
| BTC | +22.23% | 1.77 | 21.67% | 14 | 14.3% | 2.51 |
| ETH | +6.37% | 0.67 | 14.97% | 12 | 16.7% | inf |
| XRP | -10.62% | -1.20 | 12.21% | 9 | 11.1% | 1.02 |

Lưu ý: ETH profit factor = inf vì sample này chưa có loss thực tế; cần chạy thêm bars hoặc freq khác để xác nhận.

## 3. Test Suite
- 837 passed, 3 skipped
- Execution/lifecycle/risk/strategy/simulator/research tests đều xanh

## 4. Fixes applied during review
- `scripts/full_system_backtest.py`: `generate_signals` trả Series tên rỗng → đã rename thành `signal`
- `scripts/full_system_backtest.py`: đọc SYMBOL/TIMEFRAME linh hoạt qua constructor thay vì chỉ module-level env
- `scripts/full_system_backtest.py`: sửa trade_log ghi unrealized PnL → realized PnL theo entry/exit

## 5. Hiệu quả & Hạn chế
- BTC 4h có hiệu quả rõ ràng nhất: return +22%, Sharpe 1.77
- ETH 4h có hiệu quả trung bình, profit factor chưa đủ sample
- XRP 4h không hiệu quả trong khoảng 3y này
- 1h hiện tại chưa ổn định; có thể cần filter regime/timing khác
- Full system hiện chỉ execute khi `i % freq == 0`; freq=2 nghĩa quyết định cách 8h

## 6. Next
- Nếu muốn, chạy 1h full system cho BTC representative với freq=4 hoặc freq=8 để tiết kiệm thời gian
- Tối ưu params theo từng symbol/tf thay vì chung
- Thêm phân tích yearly/monthly breakdown có trọng số
