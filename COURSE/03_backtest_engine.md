# Bài 3: Backtest Engine — đo lường chiến lược trước khi mạo hiểm vốn

> File gốc: `src/trading_agent/backtest/engine.py` (370d) + `trading/backtest/engine.py` (370d)
> Bài này đọc **cả hai engine** — hệ thống có 2 thế hệ, hiểu vì sao là chìa khóa.

---

## 1. Dùng để làm gì?

Trước khi đưa 1 đồng vốn thật vào chiến lược, bạn phải trả lời 3 câu hỏi:

1. Chiến lược này **sinh lời bao nhiêu**? → total return, annualized return
2. **Rủi ro thế nào**? → max drawdown, Sharpe/Sortino, win rate
3. **Có đáng tin không**? → profit factor, số lượng trades, thời gian giữ lệnh

Backtest = chạy lại chiến lược trên **dữ liệu lịch sử** để trả lời 3 câu đó
mà không tốn 1 xu. Đây là bộ phận "phòng thí nghiệm" của hệ thống: mọi
chiến lược mới đều phải qua backtest trước khi được duyệt lên live.

---

## 2. Hai engine — hiểu kiến trúc thật của repo

Hệ thống có **2 thế hệ engine**, đừng nhầm:

| | Legacy (đang dùng) | Mới (Phase 6 marketplace) |
|---|---|---|
| Đường dẫn | `src/trading_agent/backtest/engine.py` | `trading/backtest/engine.py` |
| Cách xử lý | **Vectorized** — cả DataFrame bằng Polars | **Event-driven** — từng bar gọi `on_bar()` |
| Hướng giao dịch | Long-only (mặc định) | Long + Short |
| Strategy interface | `compute_indicators()` + `generate_signals()` | `on_start()` / `on_bar()` / `on_stop()` |
| Số học | `float` | `Decimal` (chính xác tiền tệ) |
| Ai dùng | CLI backtest, param sweep, validate, versioning | Marketplace (chưa nối CLI) |
| Trạng thái | ✅ Chạy tốt | ⚠️ Đã fix bug API, `on_bar` adapter còn stub |

> **Vì sao có 2?** Phase 1-5 xây engine vectorized vì **nhanh** (chạy 26k
> bars chỉ vài giây — hợp cho sweep hàng nghìn tổ hợp tham số). Phase 6
> cần chạy **plugin không tin cậy** (strategy từ marketplace) trong sandbox,
> mỗi bar gọi 1 lần để có thể can thiệp (stop-loss, risk budget) → cần
> event-driven. Hai mục tiêu khác nhau, hai thiết kế khác nhau.

---

## 3. Vòng đời một backtest (7 bước)

Nhìn vào `BacktestEngine.run()` — `src/trading_agent/backtest/engine.py:96-115`:

```python
def run(self, df, symbol=None, timeframe=None):
    df = df.sort("timestamp")  # 0. Sắp xếp
    df = self.strategy.compute_indicators(df)  # 1. Indicators
    signals = self.strategy.generate_signals(df)  # 2. Signals ±1/0
    df = df.with_columns(signals.alias("signal"))
    df = self._compute_positions(df)  # 3. Position
    df = self._compute_returns(df)  # 4. Returns
    df = self._build_equity_curve(df)  # 5. Equity curve
    trades = self._extract_trades(df)  # 6. Trades
    self._compute_metrics(result, df, trades)  # 7. Metrics
```

Đọc theo chiều: **dữ liệu → tín hiệu → vị thế → lời/lỗ từng bar → tài sản
tích lũy → danh sách lệnh → chỉ số tổng kết**. Mỗi bước là một hàm riêng —
đây là cách thiết kế để dễ kiểm tra từng khâu.

### 3.1 Signals (±1/0) → Position (forward-fill)

`src/trading_agent/backtest/engine.py:123-151`. Strategy sinh ra chuỗi
`+1 / 0 / -1` (long / hold / short). Nhưng tín hiệu chỉ bắn ở **thời điểm
crossover** — giữa 2 tín hiệu thì position phải **giữ nguyên**. Kỹ thuật
forward-fill:

```python
position = pl.when(pl.col("signal") == 1).then(1)
               .when(pl.col("signal") == -1).then(0)
               .otherwise(None)          # None = giữ nguyên vị thế trước đó
position = position.forward_fill().fill_null(0)
```

> Vì sao `None`? `forward_fill()` chỉ điền giá trị trước đó vào ô trống —
> nếu tín hiệu là 0 thì sẽ **overwrite** position về 0 mất. `None` là
> "không có quyết định mới" → giữ nguyên trạng thái. Đây là pattern kinh
> điển trong backtest.

### 3.2 Return từng bar

`src/trading_agent/backtest/engine.py:153-176`. 3 cột được tính:

```python
# Lợi nhuận của chính đồng coin (close→close)
price_return = close / close.shift(1) - 1

# Lợi nhuận của chiến lược = position NHẬP TRƯỚC đó × price return
strategy_return = position.shift(1) * price_return

# Phí khi ĐỔI vị thế (position thay đổi so với bar trước)
trade_cost = (position != position.shift(1)) ? (commission + slippage) : 0

net_return = strategy_return - trade_cost
```

> ⚠️ **`position.shift(1)` — điểm dễ sai nhất trong backtest.** Tín hiệu
> sinh ra ở cuối bar hiện tại, nhưng bạn chỉ vào lệnh được ở **bar kế
> tiếp** (giá bar hiện tại đã đóng rồi). Nếu không shift, bạn đang "nhìn
> tương lai" (look-ahead bias) — backtest đẹp ảo, live thua thật.

### 3.3 Equity curve (compounding)

`src/trading_agent/backtest/engine.py:178-203`:

```python
equity = (1 + net_return).cum_prod() * initial_capital  # lãi kép
peak = equity.cum_max()  # đỉnh cao nhất
dd = equity / peak - 1  # drawdown
```

- `cum_prod` = nhân dồn → mỗi bar lời/lỗ đè lên tài sản hiện tại (đúng với
  thực tế nếu tái đầu tư toàn bộ).
- `drawdown` luôn ≤ 0 — đo "đang rớt bao nhiêu % so với đỉnh", max drawdown
  là giá trị **âm nhất** của cột này.

### 3.4 Trades

`src/trading_agent/backtest/engine.py:205-232`. Duyệt mảng position:
`position` chuyển 0→1 là mở lệnh (entry), 1→0 là đóng lệnh (exit). Mỗi
lệnh ghi: entry/exit date + price, `pnl_pct`, `bars_held`.

> Trade ở đây đơn giản (long-only, full size). Engine mới sẽ nâng lên
> long+short + kích thước lệnh theo % equity.

### 3.5 Metrics — công thức từng chỉ số

`src/trading_agent/backtest/engine.py:234-304`:

| Chỉ số | Công thức | Nói lên điều gì |
|---|---|---|
| Total return | `final/initial - 1` | Bao nhiêu % sau cả quãng thời gian |
| Annualized return | `(final/initial)^(1/years) - 1` | Trung bình mỗi năm — so sánh được giữa các chiến lược khác thời gian |
| Sharpe | `avg(net_return)/std(net_return) × √(bars/năm)` | Lời trên mỗi đơn vị rủi ro — >1 khá, >2 tốt |
| Sortino | như Sharpe nhưng std chỉ tính **lệnh lỗ** | Phạt rủi ro xấu, không phạt rủi ro tốt |
| Max drawdown | `min(drawdown)` | Cú sụt tệ nhất từ đỉnh — ăn được không? |
| Win rate | `số lệnh thắng / tổng lệnh` | Đúng bao nhiêu % thời gian |
| Profit factor | `tổng lãi / |tổng lỗ|` | >1 là có lãi, >1.5 mới đáng tin |
| Calmar | `annualized / |max DD|` | Lời năm đó "trả giá" bằng bao nhiêu rủi ro |

> Mẹo đọc kết quả: **đừng nhìn mỗi total return**. Một chiến lược +40% mà
> max DD -60% là "bắt dao rơi" — thua Calmar của chiến lược +15%/-5%.
> Profit factor + số trades quyết định độ tin cậy: 302 trades đáng tin hơn
> 5 trades rất nhiều.

---

## 4. Chi phí giao dịch (commission + slippage)

`BacktestEngine.__init__` — `src/trading_agent/backtest/engine.py:91-94`:

```python
commission: float = (0.001,)  # phí sàn 0.1%
slippage: float = (0.0005,)  # trượt giá 0.05%
```

- **Commission**: phí sàn thật (Binance spot ~0.1%).
- **Slippage**: lệnh market không khớp đúng giá hiển thị — đặc biệt lớn
  với coin nhỏ/thanh khoản thấp.
- Được trừ mỗi lần **đổi vị thế** (không phải mỗi bar). Bỏ qua 2 khoản này
  là cách nhanh nhất để backtest "đẹp ảo".

---

## 5. Engine mới Phase 6 — event-driven

`trading/backtest/engine.py` — thiết kế cho marketplace:

### 5.1 Loop chính `run_backtest()` — `trading/backtest/engine.py:160-319`

Khác hẳn legacy: duyệt **từng row**, tạo `Bar` + `StrategyContext`, gọi
`strategy.on_bar(context)`:

```python
for row in rows:
    bar = Bar(symbol=sym_obj, timestamp=..., open=..., close=..., ...)
    context = StrategyContext(symbol=sym_obj, bar=bar, position=None,
                              portfolio_value=..., available_balance=..., ...)
    signals = strategy.on_bar(context)      # ← strategy tự quyết định
    for sig in signals:
        if sig.side.name == "BUY" and position <= 0:   # vào long / đóng short
        elif sig.side.name == "SELL" and position >= 0: # vào short / đóng long
```

Điểm mạnh: strategy plugin **có thể nhìn thấy** portfolio_value, bar, thời
gian hiện tại → viết logic phức tạp (stop-loss, sizing theo equity) ngay
trong strategy. Giá mua/sell đã cộng sẵn chi phí: `entry_price = price *
(1 + commission + slippage)` — engine mới tính phí **trên giá entry**, còn
legacy trừ trực tiếp vào return.

### 5.2 Kiểu dữ liệu chính xác bằng Decimal

`Trade` — `trading/backtest/engine.py:35-52` dùng `Decimal` cho mọi số tiền.
Float dễ sai ở chữ số thứ 16 (0.1 + 0.2 ≠ 0.3) — với tiền thì không chấp
nhận. Đây là bài học thiết kế: **số tiền → Decimal, tỷ lệ/tỷ số → float**.

### 5.3 Hash để xác thực backtest — `verify_backtest_hash()` (engine.py:321-355)

```python
data = {
    strategy,
    symbol,
    timeframe,
    params,
    total_return_pct,
    sharpe_ratio,
    max_drawdown_pct,
    win_rate,
    total_trades,
    trades[...],
}
hash = sha256(json.dumps(data, sort_keys=True))[:16]
```

Vì sao hash? Marketplace cho phép ai đó nộp strategy — **làm sao tin bản
backtest họ kèm theo?** Registry lưu `backtest_hash` trong metadata, và
`validate_backtest()` (`trading/strategies/plugins/strategy_plugin.py:560-571`)
so sánh hash lúc chạy với hash khai báo. Tham số/số liệu đổi → hash đổi →
từ chối. Chống "kê khai gian" kết quả backtest.

### 5.4 Bug đã fix trong bài này

`trading/backtest/engine.py:176` gọi `registry.get_strategy(strategy_name)`
— **method không tồn tại** trong `StrategyRegistry` (API thật là
`registry.get(name, version=None)` lấy class mới nhất, hoặc
`registry.create_instance(name, config)` — `strategy_plugin.py:513-537`).
Backtest bất kỳ qua engine mới đều crash `AttributeError`. Đã sửa thành
`registry.get(strategy_name)` — giờ engine chạy được, dựng đủ equity curve.

> Giới hạn trung thực: adapter của các strategy legacy hiện trả `[]` từ
> `on_bar()` (stub — `adapters.py:120-123`). Nên engine mới chạy được
> nhưng sinh 0 lệnh. Tín hiệu thật đang nằm ở legacy vectorized. Khi nào
> marketplace cần, viết `on_bar()` thật cho adapter là xong — interface
> đã đúng.

---

## 6. Demo — chạy thật và đọc kết quả

```bash
cd <repo-root>
python3 -c "
from trading_agent.backtest.engine import run_backtest
r = run_backtest('ma_crossover', 'BTC/USDT', '1h', initial_capital=10000)
print(r)
"
```

Kết quả thật (dữ liệu BTC/USDT 1h từ 2023, default params MA 20/50):

```
── ma_crossover on BTC/USDT 1h ──
  Total Return:      -41.00%     ← lỗ!
  Ann. Return:       -16.11%
  Sharpe:             -0.40
  Max DD:            -63.67%     ← đáy sâu
  Win Rate:           38.4%      ← đúng < 50%
  Profit Factor:       1.19      ← lãi gộp vẫn > lỗ gộp
  Trades:               302      ← đủ mẫu, kết luận đáng tin
  Avg Hold:            46.0 bars
```

**Đọc kết quả như dân chuyên:**
1. **Default MA(20,50) long-only trên BTC 1h = THUA -41%.** Bình thường!
   BTC 1h không đi theo MA ngắn một chiều.
2. Win rate 38.4% nhưng PF 1.19 → chiến lược **thua nhiều hơn thắng về số
   lần, nhưng thắng lớn hơn thua** (trend-following điển hình).
3. Đây chính là lý do Phase 1 phải **sweep tham số**: tối ưu được MA
   crossover +71.96%, RSI +66.75%, BBands +12.43% (chạy trên 5k candles).
   Cùng engine, khác params → khác trời vực.
4. ⚠️ Nhưng walk-forward + OOS cho thấy **cả 3 đều overfit nặng** — params
   đẹp trên quá khứ không hứa hẹn tương lai. Backtest cho bạn **bằng chứng
   để nghi ngờ**, không phải sự chắc chắn.

---

## 7. Bài tập tự kiểm tra

1. **Đọc trade list**: chạy demo, in `r.trades[:5]` — quan sát entry/exit
   price, `bars_held`. Tìm 1 lệnh thắng và 1 lệnh thua lớn nhất, lý giải
   bằng kiến thức MA crossover.
2. **Chạy chiến lược khác**: `run_backtest('rsi', 'BTC/USDT', '1h')` — so
   sánh total return, max DD, win rate với ma_crossover. Chiến lược nào
   "ngủ ngon" hơn?
3. **Sweep mini**: chạy ma_crossover với 3 bộ params `{fast, slow} = {5,20}`,
   `{20,50}`, `{50,200}`. Quan sát total return thay đổi. Kết luận về độ
   nhạy tham số (đây là mầm mống của overfitting).

---

## 8. TL;DR

- **Backtest = phòng thí nghiệm**: trả lời "lời bao nhiêu / rủi ro gì /
  có tin được không" trước khi đưa vốn thật.
- **Vòng đời 7 bước**: sort → indicators → signals → positions →
  returns → equity → trades → metrics.
- **2 cạm bẫy kinh điển**: quên `position.shift(1)` (nhìn tương lai) và
  quên phí/slippage (lời ảo).
- **Metrics đọc theo cặp**: return phải đi kèm max DD; win rate phải đi
  kèm profit factor; số trades ít = kết luận kém tin.
- **2 engine**: legacy vectorized (nhanh, sweep) vs mới event-driven
  (plugin marketplace, Decimal, hash xác thực). Engine mới đã fix bug
  `registry.get_strategy` → `registry.get`, nhưng `on_bar` adapter còn stub.
- **Thái độ đúng**: backtest đẹp chỉ là *giả thuyết*, tham số tối ưu trên
  quá khứ rất dễ overfit — luôn kiểm chứng walk-forward/OOS.
