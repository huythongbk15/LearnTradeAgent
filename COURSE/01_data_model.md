# Bài 1: Data Model — "Bảng chữ cái" của hệ thống

> **Trạng thái:** ✅ Đầy đủ
> **File gốc:** `trading/exchanges/models.py` (483 dòng)

## 🎯 Mục tiêu

Hiểu **ngôn ngữ chung** mà mọi module (data, backtest, agents, execution, portfolio) dùng để trao đổi. Học xong bài này bạn sẽ:
- Phân biệt được `Symbol` của BTC spot vs BTC perpetual vs option
- Biết vì sao dùng `Decimal` thay vì `float`
- Tự tạo được `Bar`, `OrderBook`, `Position`, `Order` để demo

## 1. Enums — các "trạng thái" chuẩn hóa

```python
class AssetClass(str, Enum):  # loại tài sản
    CRYPTO, STOCK, FOREX, FUTURES, OPTIONS, ETF, BOND, COMMODITY, INDEX


class MarketType(str, Enum):  # kiểu thị trường
    SPOT, MARGIN, FUTURES, OPTIONS, PERPETUAL, SPOT_MARGIN


class OrderSide(str, Enum):
    BUY / SELL


class OrderType(str, Enum):
    MARKET, LIMIT, STOP, STOP_LIMIT, TRAILING_STOP, POST_ONLY, FOK, IOC


class OrderStatus(str, Enum):
    OPEN, PARTIAL, FILLED, CANCELLED, REJECTED, EXPIRED


class TimeInForce(str, Enum):
    GTC, IOC, FOK, GTD
```

💡 **Vì sao dùng `str, Enum`?** — Vừa có tên rõ nghĩa (`OrderStatus.FILLED`) vừa serializable ra string (`"filled"`) để lưu database / gửi qua JSON.

## 2. `Symbol` — danh tính của 1 tài sản

```python
@dataclass(frozen=True, slots=True)
class Symbol:
    base: str  # "BTC"
    quote: str  # "USDT"
    asset_class: AssetClass
    market_type: MarketType
    exchange: str  # "binance" / "alpaca" / "oanda"
    expiry: Optional[str] = None  # futures/options: "2026-09-25"
    strike: Optional[Decimal] = None  # options
    option_type: Optional[str] = None  # "call" / "put"
```

**3 điểm thiết kế đáng chú ý:**

1. **`frozen=True`** → immutable. Một `Symbol` không bao giờ đổi sau khi tạo — an toàn làm khóa dict/set.
2. **`__post_init__` tự chuẩn hóa**: base/quote → UPPER, exchange → lower. `symbol("btc", "usdt", ..., "BINANCE")` tự thành `BTC/USDT/binance` — khỏi lo người dùng viết hoa thường lệch.
3. **`slots=True`** → nhẹ bộ nhớ, truy cập nhanh (quan trọng khi tạo hàng triệu `Bar` trong backtest).

**Các thuộc tính tiện ích:**

```python
pair  # "BTC/USDT"           — ký hiệu chuẩn
ccxt_symbol  # "BTC/USDT:USDT"      — định dạng CCXT (futures có ":settle")
alpaca_symbol  # "AAPL"               — định dạng Alpaca (stock chỉ cần ticker)
oanda_instrument  # "EUR_USD"            — định dạng OANDA
unified_id  # "binance:crypto:spot:BTC:USDT" — danh tính DUY NHẤT xuyên sàn
hash  # md5(unified_id)[:12] — khóa ngắn cho database
```

💡 **Vì sao cần `unified_id`?** — Cùng "BTC" nhưng `binance:crypto:spot:BTC:USDT` ≠ `deribit:options:BTC:USDT:2026-09-25:60000:C`. Hai tài sản khác nhau hoàn toàn. `unified_id` chống nhầm lẫn khi hệ thống chạy 8 sàn + 5 asset class.

## 3. `Bar` — cây nến OHLCV

```python
@dataclass(slots=True)
class Bar:
    symbol: Symbol
    timestamp: datetime
    open/high/low/close/volume: Decimal
    timeframe: str                  # "1m", "5m", "1h", "1d"
    trades: Optional[int] = None
    vwap: Optional[Decimal] = None

    @property
    def typical_price(self) -> Decimal:  # (H+L+C)/3
    @property
    def range_pct(self) -> Decimal:      # (H-L)/O*100 — biến động %
```

💡 **Vì sao `Decimal` mà không `float`?** — Tiền không cho phép sai số floating point (`0.1 + 0.2 != 0.3` trong float). Trong trading, `1.1000000000000001` làm lệch PnL và có thể gây lỗi tính margin/liquidation.

## 4. `OrderBook` — sổ lệnh

```python
bids: list[OrderBookLevel]  # giá mua, giảm dần
asks: list[OrderBookLevel]  # giá bán, tăng dần
sequence: Optional[int]  # số thứ tự snapshot (chống out-of-order)

# Thuộc tính: best_bid, best_ask, spread, spread_pct, mid_price
```

→ Nguồn của **spread** — chi phí ẩn khi vào lệnh. `spread_pct` càng nhỏ, thị trường càng thanh khoản.

## 5. `Position` & `Order` — trạng thái

```python
# Position: size > 0 = LONG, size < 0 = SHORT  ← quy ước quan trọng
#   is_long / is_short / notional = |size| * mark
#   pnl_pct:
#     long:  (mark - entry)/entry * 100
#     short: (entry - mark)/entry * 100

# Order lifecycle: OPEN → PARTIAL → FILLED | CANCELLED | REJECTED | EXPIRED
#   remaining_size = size - filled_size
#   is_active = OPEN hoặc PARTIAL     (đang treo trên sàn)
#   is_done   = FILLED/CANCELLED/...  (đã kết thúc)
```

## 6. Factory functions + Registry — API tiện dụng

```python
crypto_symbol("BTC", "USDT", exchange="binance")
stock_symbol("AAPL")  # → Symbol("AAPL","USD",STOCK,SPOT,"alpaca")
forex_symbol("EUR", "USD")  # → OANDA forex
futures_symbol("BTC", "USDT", expiry="2026-09-25")
option_symbol("BTC", "USDT", "2026-09-25", Decimal("60000"), "C")

COMMON_CRYPTO  # {"BTC/USDT": ..., "BTC/USDT:USDT": ...}
COMMON_STOCKS  # AAPL, MSFT, GOOGL, TSLA, NVDA, SPY, QQQ
COMMON_FOREX  # EUR/USD, GBP/USD, USD/JPY, USD/CHF, AUD/USD, USD/CAD
```

→ Khỏi nhớ thứ tự 8 tham số — gọi hàm có tên rõ ràng.

## 🧪 Demo

```bash
cd <repo-root>
python3 -c "
from trading_agent.exchanges.models import *
from datetime import datetime
from decimal import Decimal

# 1. Hai 'BTC' khác nhau
spot = crypto_symbol('BTC', 'USDT')
perp = COMMON_CRYPTO['BTC/USDT:USDT']
print(spot.unified_id)   # binance:crypto:spot:BTC:USDT
print(perp.unified_id)   # binance:crypto:perpetual:BTC:USDT
print(spot == perp)      # False — dù cùng tên!

# 2. Chuẩn hóa tự động
print(crypto_symbol('btc', 'usdt', exchange='BINANCE').unified_id)

# 3. Bar
bar = Bar(spot, datetime.now(), Decimal('60000'), Decimal('61000'),
          Decimal('59500'), Decimal('60500'), Decimal('12.5'), '1h')
print(bar.typical_price, bar.range_pct)   # 60333.33, 2.5

# 4. OrderBook + spread
book = OrderBook(spot, datetime.now(),
                 bids=[OrderBookLevel(Decimal('60400'), Decimal('2'))],
                 asks=[OrderBookLevel(Decimal('60500'), Decimal('3'))])
print(book.spread_pct)   # ≈ 0.165%

# 5. Position long vs short
pos = Position(spot, Decimal('2'), Decimal('60000'), Decimal('63000'))
print(pos.pnl_pct)       # +5.0 (long)
pos2 = Position(spot, Decimal('-2'), Decimal('60000'), Decimal('63000'))
print(pos2.pnl_pct)      # -5.0 (short)
"
```

## ❓ Câu hỏi tự kiểm tra

1. `Symbol("BTC","USDT",CRYPTO,SPOT,"binance")` và `Symbol("BTC","USDT",CRYPTO,PERPETUAL,"binance")` có bằng nhau không? `unified_id` của chúng khác nhau thế nào?
2. Vì sao `Position` dùng số âm để biểu diễn short?
3. `Order.remaining_size` của lệnh FILLED là bao nhiêu?
4. Nếu một sàn trả về `"btc"`, `"usdt"` viết thường — hệ thống xử lý ra sao? (đáp án: `__post_init__`)
5. `spread_pct` của orderbook có bids/asks rỗng là bao nhiêu? (đáp án: `None` — vì `best_bid` là `None`)

## 🔗 Liên hệ bài sau

Bài 2 — **Data Pipeline**: `Bar` được fetch từ sàn, validate, lưu Parquet thế nào (654 dòng).
