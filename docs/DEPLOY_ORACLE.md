# Deploy LearnTradeAgent lên Oracle Cloud bằng Docker

Stack này chủ ý chỉ chạy `web` và `caddy`. Dự án hiện lưu trạng thái bằng
SQLite/JSON/Parquet; TimescaleDB và Redis chưa nằm trên đường chạy chính, nên
không được bật chỉ để “đủ kiến trúc”. Cấu hình mặc định chỉ quan sát/phân tích,
không đặt lệnh.

## 1. Chuẩn bị VM và mạng

- Dùng Ubuntu 24.04 trên một VM Oracle Cloud phù hợp quota của tài khoản.
- Trong VCN Security List/NSG, chỉ mở TCP 80 và 443 cho Internet; giới hạn TCP
  22 vào IP quản trị của bạn. Nếu dùng HTTP/3, mở thêm UDP 443.
- Trỏ bản ghi DNS `A` (và `AAAA` nếu dùng IPv6) về public IP của VM trước khi
  bật HTTPS tự động.

Trên VM:

```bash
sudo apt update
sudo apt install -y docker.io docker-compose-v2 git ufw
sudo systemctl enable --now docker
sudo usermod -aG docker "$USER"
sudo ufw default deny incoming
sudo ufw allow OpenSSH
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw allow 443/udp
sudo ufw --force enable
```

Đăng xuất/đăng nhập lại để quyền nhóm `docker` có hiệu lực.

## 2. Clone và tạo secrets

```bash
git clone https://github.com/huythongbk15/LearnTradeAgent.git
cd LearnTradeAgent
cp .env.oracle.example .env.oracle
chmod 600 .env.oracle
mkdir -p runtime/data runtime/logs
chmod 700 runtime
```

Tạo `WEBUI_API_KEY`:

```bash
openssl rand -hex 32
```

Tạo mật khẩu Caddy Basic Auth theo chế độ nhập tương tác để mật khẩu thô không
nằm trong command history:

```bash
docker run --rm -it caddy:2.10.2-alpine caddy hash-password
```

Sửa `.env.oracle`:

- `SITE_ADDRESS=trade.example.com` để Caddy tự cấp TLS;
- đặt bcrypt hash vào `CADDY_PASSWORD_HASH` và giữ dấu nháy đơn;
- đặt chuỗi ngẫu nhiên vào `WEBUI_API_KEY`;
- thêm Alpaca **Paper** key nếu cần xem portfolio Paper;
- giữ `TRADING_EXECUTION_ENABLED=false` trong lần triển khai đầu.

## 3. Kiểm tra và khởi động

```bash
docker compose --env-file .env.oracle -f docker-compose.oracle.yml config --quiet
docker compose --env-file .env.oracle -f docker-compose.oracle.yml build
docker compose --env-file .env.oracle -f docker-compose.oracle.yml up -d
docker compose --env-file .env.oracle -f docker-compose.oracle.yml ps
curl --fail https://trade.example.com/health
```

Xem log:

```bash
docker compose --env-file .env.oracle -f docker-compose.oracle.yml logs -f --tail=200
```

Dashboard có hai lớp bảo vệ: Caddy Basic Auth cho toàn trang và
`WEBUI_API_KEY` cho thao tác quản trị. Nhập `WEBUI_API_KEY` trong tab Alpaca
Paper Trading; khóa chỉ nằm trong `localStorage` của trình duyệt đang dùng.

## 4. Bật Paper execution sau khi xác minh

Chỉ sau khi backtest, dữ liệu và tài khoản Alpaca Paper đều đúng, đổi:

```dotenv
TRADING_EXECUTION_ENABLED=true
TRADING_MODE=paper
```

Sau đó áp dụng lại:

```bash
docker compose --env-file .env.oracle -f docker-compose.oracle.yml up -d --force-recreate web
```

Backend từ chối `live=true`, adapter Web luôn khởi tạo với `paper=True`, và mỗi
chu kỳ vẫn cần khóa quản trị cùng chuỗi xác nhận. Stack này không có scheduler
tự chạy lệnh; người vận hành chủ động chạy từng Paper cycle từ Web UI.

## 5. Sao lưu, cập nhật và khôi phục

Sao lưu trạng thái khi đã dừng Web để có snapshot nhất quán:

```bash
docker compose --env-file .env.oracle -f docker-compose.oracle.yml stop web
tar -czf "learntrade-backup-$(date +%Y%m%d-%H%M%S).tgz" runtime
docker compose --env-file .env.oracle -f docker-compose.oracle.yml start web
```

Cập nhật an toàn:

```bash
git pull --ff-only
docker compose --env-file .env.oracle -f docker-compose.oracle.yml build --pull
docker compose --env-file .env.oracle -f docker-compose.oracle.yml up -d
curl --fail https://trade.example.com/health
```

Định kỳ kiểm tra dung lượng bằng `df -h`, log bằng `docker compose ... logs`, và
không lưu API key thật trong Git, ảnh Docker hay command history.
