# Bài 2: Data Pipeline — nến đi vào hệ thống như thế nào

> **Trạng thái:** ✅ Đầy đủ
> **File gốc:** `trading/data/pipeline.py` (654 dòng)

## 🎯 Mục tiêu

Hiểu hành trình của 1 cây nến: **từ sàn giao dịch → chuẩn hóa → lưu trữ → đọc lại**. Học xong bạn sẽ:
- Biết kiến trúc **3 mảnh ghép pluggable**: `DataSource` · `CandleStore` · `DataPipeline`
- Hiểu cơ chế **incremental update** (vì sao tiết kiệm 95-99% bandwidth)
- Hiểu vì sao store **idempotent** (ghi trùng không tạo bản ghi trùng)
- Tự chạy pipeline với dữ liệu giả (không cần API key)

## 1. Kiến trúc tổng thể — 3 mảnh ghép

```
DataSource (lấy nến từ sàn) ──┐
                              ├──→ DataPipeline (điều phối) ──→ CandleStore (lưu trữ)
DataSource (mock/stocks/...) ─┘          │
                                  normalize → dedupe → write
```

| Mảnh ghép | Trách nhiệm | Các implementation |
|-----------|-------------|--------------------|
| `DataSource` | Fetch nến từ 1 nguồn | `CCXTSource` (crypto) · `AlpacaSource` (stock) · `OANDASource` (forex) · `MockSource` (giả) |
| `CandleStore` | Lưu + đọc nến | `SQLiteCandleStore` (zero-dep) · `TimescaleDBCandleStore` (hypertable) |
| `DataPipeline` | Điều phối fetch→normalize→write | 2 chế độ: `ingest()` (backfill) · `incremental()` (cập nhật) |

💡 **Vì sao pluggable?** — Muốn thêm sàn mới: viết 1 `DataSource`, không đụng store. Muốn đổi database: viết 1 `CandleStore`, không đụng source. **Open/Closed principle** — mở cho mở rộng, đóng cho sửa đổi.

## 2. `DataSource.normalize()` — đồng quy dữ liệu (dòng ~70)

```python
def normalize(self, symbol, timeframe, raw) -> Candle:
    # Chấp nhận 2 định dạng:
    #   tuple: (ts_ms, o, h, l, c, v)      ← định dạng CCXT
    #   dict:  {"timestamp", "open", ...}  ← định dạng Alpaca
    if isinstance(raw, (list, tuple)):
        ts_ms, o, h, low, c = raw[0], raw[1], raw[2], raw[3], raw[4]
        ts = datetime.fromtimestamp(ts_ms / 1000.0, tz=timezone.utc)
    ...

    def dec(x):  # chuyển an toàn sang Decimal
        try:
            return Decimal(str(x))
        except (InvalidOperation, TypeError, ValueError):
            return Decimal(0)
```

💡 **Vì sao quan trọng?** — Binance trả tuple, Alpaca trả object. Nếu hệ thống phụ thuộc định dạng từng sàn → mỗi module lại phải biết sàn nào. `normalize()` **đồng quy về 1 chuẩn `Candle`** — mọi thứ phía sau chỉ làm việc với 1 loại dữ liệu.

## 3. `CCXTSource` — fetch có phân trang (dòng ~110)

```python
async def fetch_candles(self, symbol, timeframe, start, end):
    cursor = since_ms
    while cursor < end_ms:
        batch = await self._fetch(symbol, timeframe, cursor, limit=1000)  # 1000 nến/lần
        if not batch:
            break
        out.extend(batch)
        last_ts = batch[-1].timestamp.timestamp() * 1000
        if last_ts <= cursor:
            break  # chống infinite loop
        cursor = int(last_ts) + 1  # trang kế tiếp: sau nến cuối
    return [c for c in out if c.timestamp.timestamp() * 1000 <= end_ms]
```

💡 **Ba kỹ thuật đáng học:**
1. **Cursor pagination**: sàn giới hạn 1000 nến/lần → fetch nhiều trang, con trỏ nhích dần
2. **Chống infinite loop**: nếu trang mới không tiến (`last_ts <= cursor`) → dừng
3. **`asyncio.to_thread`**: SDK ccxt là sync — gói vào thread để **không chặn event loop** (hệ thống vẫn xử lý lệnh khác trong lúc chờ mạng)

## 4. `SQLiteCandleStore` — lưu trữ idempotent (dòng ~290)

```sql
CREATE TABLE candles (
    exchange, symbol, asset_class, timeframe,
    ts,           -- INTEGER (ms epoch)
    open, high, low, close, volume,  -- TEXT! ← lưu Decimal dạng chuỗi
    PRIMARY KEY (exchange, symbol, timeframe, ts)  -- ← chìa khóa chống trùng
)
```

**Insert: `INSERT OR REPLACE`** — ghi cùng `(exchange, symbol, timeframe, ts)` → thay thế bản cũ, không sinh bản trùng.

💡 **Vì sao lưu Decimal dạng TEXT?** — SQLite không có kiểu Decimal. Lưu `float` mất độ chính xác (0.1+0.2≠0.3). Lưu chuỗi `"60000.5"` → đọc lại `Decimal("60000.5")` **chính xác tuyệt đối**. Đổi lại: tốn chút dung lượng, nhưng tiền không cho phép sai số.

## 5. `DataPipeline` — hai chế độ (dòng ~520)

```python
async def ingest(self, symbols, timeframe, start, end):
    """BACKFILL — kéo toàn bộ lịch sử từ start đến end."""
    # dùng khi: lần đầu, hoặc sửa dữ liệu lịch sử


async def incremental(self, symbols, timeframe, limit=200):
    """INCREMENTAL — chỉ kéo `limit` nến gần nhất."""
    # dùng khi: chạy định kỳ mỗi giờ — 95-99% ít dữ liệu hơn backfill
```

**Fault isolation** — mỗi symbol nằm trong `try/except` riêng:
```python
for symbol in symbols:
    try:
        candles = await source.fetch_candles(...)
        ...
    except Exception as e:
        report.errors[key] = str(e)  # 1 symbol lỗi → KHÔNG chặn symbol khác
```

→ `IngestReport` trả về: `total_written`, `symbols` (mỗi cặp ghi bao nhiêu), `errors` (symbol nào lỗi), `elapsed_seconds`.

## 6. `_source_for` — chọn nguồn theo loại tài sản (dòng ~500)

```python
by_asset = {
    AssetClass.CRYPTO: "ccxt",
    AssetClass.STOCK:  "alpaca",
    AssetClass.FOREX:  "oanda",
    ...
}
```

→ Cùng 1 pipeline, `crypto_symbol("BTC")` tự vào CCXTSource, `stock_symbol("AAPL")` tự vào AlpacaSource. **Multi-asset mà code không cần rẽ nhánh** — đây là thành quả của Bài 1 (unified `Symbol`).

## 🧪 Demo

```bash
cd <repo-root>

# 1. Demo built-in (MockSource + SQLite, không cần API key)
python3 -m trading_agent.data.pipeline
# → report: {'total_written': 48, 'total_read': 48, ...}  (2 ngày × 24h)

# 2. Demo tự viết: backfill + incremental + đọc lại
python3 -c "
import asyncio
from datetime import datetime, timezone
from trading_agent.data.pipeline import DataPipeline, SQLiteCandleStore, MockSource
from trading_agent.exchanges.models import crypto_symbol

async def main():
    store = SQLiteCandleStore(db_path='data/demo_pipeline.db')
    pipe = DataPipeline(store=store, sources={'mock': MockSource(seed=50000)})
    btc = crypto_symbol('BTC', 'USDT', exchange='mock')

    # Backfill 3 ngày
    r1 = await pipe.ingest([btc], '1h', datetime(2026,7,1,tzinfo=timezone.utc), datetime(2026,7,4,tzinfo=timezone.utc))
    print('backfill:', r1.total_written, 'candles')

    # Incremental — ghi lại 200 nến gần nhất, idempotent
    r2 = await pipe.incremental([btc], '1h', limit=200)
    print('incremental written:', r2.total_written)
    print('count sau 2 lần:', await pipe.count(btc, '1h'))   # không bị trùng
    latest = await pipe.latest(btc, '1h')
    print('latest:', latest.timestamp, latest.close)
    store.close()

asyncio.run(main())
"
```

**Kết quả mong đợi:** backfill 72 candles → incremental ghi tiếp → `count` KHÔNG nhân đôi (nhờ PRIMARY KEY + INSERT OR REPLACE) — **tự test tính idempotent được**.

## ❓ Câu hỏi tự kiểm tra

1. Nếu gọi `ingest()` 2 lần cùng khoảng thời gian — `count` tăng gấp đôi không? Vì sao? (đáp án: không, `INSERT OR REPLACE` + PK)
2. Vì sao lưu giá bằng TEXT trong SQLite thay vì REAL? (đáp án: độ chính xác Decimal)
3. `incremental()` tiết kiệm bandwidth thế nào so với `ingest()`?
4. 1 symbol lỗi khi fetch — các symbol khác có bị chặn không? (đáp án: không, try/except per symbol)
5. Muốn thêm sàn Kraken làm nguồn dữ liệu — phải sửa file nào? (đáp án: không cần sửa pipeline — viết thêm `DataSource`)
6. `last_ts <= cursor → break` chống lỗi gì? (đáp án: infinite loop khi sàn không tiến trang)

## 🔗 Liên hệ bài sau

Bài 3 — **Backtest Engine**: 48 nến này được dùng để test chiến lược thế nào (vectorized Polars).
