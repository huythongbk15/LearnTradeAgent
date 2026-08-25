# VPS Lite Deploy — paper trading 24/7 trên VPS nhỏ (1 vCPU / 1GB RAM)

Mô hình: **cron, không daemon**. Container chỉ dậy khi cron gọi mỗi giờ, chạy xong
tắt ngay → idle RAM ≈ 0. Không TimescaleDB/Redis/Grafana — paper loop chỉ cần
Alpaca REST + file state + Telegram.

```
GitHub push master → CI (lint+tests) → build & push ghcr.io
        → CD staging: SSH vào VPS, pin digest vào .env.image, pull, smoke test
        → Host cron (mỗi giờ): docker compose run --rm … live_cron_runner.py --execute
        → Telegram tự báo khi có order/lỗi
```

## One-time setup trên VPS

### 1. Swap 2GB (BẮT BUỘC với 1GB RAM)

```bash
sudo fallocate -l 2G /swapfile && sudo chmod 600 /swapfile \
  && sudo mkswap /swapfile && sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
free -h   # phải thấy Swap: 2.0Gi
```

### 2. Docker + thư mục

```bash
sudo dnf install -y dnf-utils && sudo dnf config-manager --add-repo \
  https://download.docker.com/linux/centos/docker-ce.repo
sudo dnf install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
sudo systemctl enable --now docker
sudo usermod -aG docker $USER   # đăng nhập lại SSH sau lệnh này
sudo mkdir -p /opt/trading-agent-staging/logs /opt/trading-agent-staging/data
sudo chown -R $USER: /opt/trading-agent-staging
```

### 3. Đồng bộ file từ máy local (chạy ở máy bạn)

```bash
VPS=opc@129.150.51.149; KEY=~/.ssh/gha_deploy_ed25519; D=/opt/trading-agent-staging
scp -i $KEY docker-compose.yml docker-compose.lite.yml $VPS:$D/
ssh -i $KEY $VPS "mkdir -p $D/config"
scp -i $KEY scripts/live_cron_runner.py $VPS:$D/scripts/ 2>/dev/null || \
  ssh -i $KEY $VPS "mkdir -p $D/scripts" && scp -i $KEY scripts/live_cron_runner.py $VPS:$D/scripts/
scp -i $KEY config/credentials.yaml $VPS:$D/config/    # Alpaca keys (gitignored)
scp -i $KEY .env.local $VPS:$D/.env.local              # TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
```

`.env.local` tối thiểu:
```bash
TELEGRAM_BOT_TOKEN=123:ABC
TELEGRAM_CHAT_ID=987654321
TRADING_EXECUTION_ENABLED=true
TRADING_MODE=paper
```
*(lần deploy đầu CD sẽ tự tạo `.env.image` chứa digest ảnh)*

### 4. Cron mỗi giờ

```bash
crontab -e
# thêm dòng:
0 * * * * cd /opt/trading-agent-staging && docker compose --env-file .env.image \
  -f docker-compose.yml -f docker-compose.lite.yml run --rm trading-agent \
  python scripts/live_cron_runner.py --execute >> logs/cron.log 2>&1
```

Xem log: `tail -f /opt/trading-agent-staging/logs/cron.log`

## Vận hành

| Việc | Lệnh |
|---|---|
| Deploy bản mới | `git push` → CI/CD tự làm hết |
| Chạy tay ngay | dòng cron bỏ prefix `0 * * * *`, chạy phần còn lại |
| Xem ảnh đang pin | `cat .env.image` |
| Rollback | khôi phục digest cũ trong `.env.image` → chạy tay lại cron command |

## Không làm gì trên VPS này

- Backtest / research / training → máy local
- Grafana/Prometheus/Loki → không cài (Telegram đã đủ cho alert)
- Mainnet → vẫn NO-GO bất kể server nào
