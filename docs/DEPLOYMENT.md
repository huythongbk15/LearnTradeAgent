# Deployment

## Topology

- **Single execution leader** (`trading-agent`, `replicas: 1` trong `docker-compose.prod.yml`).
  Nhiều execution replicas KHÔNG được deploy cho tới khi có distributed lease + fencing token +
  proven failover (P1.1 trong `LIVE_TRADING_TODO.md`). Correctness > HA.
- Scheduler service trong compose đã bị xóa (module `trading_agent.scheduler` không tồn tại).
  Cron nên chạy ngoài container (host cron) hoặc qua runner được giám sát.

## Deploy flow (CD)

1. CI build image `ghcr.io/<repo>:sha-<sha>` + push (master) + Trivy + SBOM + cosign sign.
2. `cd-staging.yml`: chạy khi CI success — verify revision label, cosign signature, SBOM
   attestation → pull digest → `up -d` → health check (`cli system health`).
3. `cd-production.yml`: **manual** `workflow_dispatch` (environment approval) — verify digest,
   signature, SBOM → deploy single leader → health check → rollback path (`.env.image.previous`).

## Fail-closed

- Image luôn pin theo **immutable digest** (`TRADING_AGENT_IMAGE=...@sha256:...`).
- Deploy thành công **không** set `mainnet_enabled=true` — mainnet là quyết định operator riêng.
- Production không tự enable mainnet; risk profile không đổi tự động khi deploy.

## Rollback

```bash
# trên host production
cp .env.image.previous .env.image
docker compose --env-file .env.image -f docker-compose.yml -f docker-compose.prod.yml up -d
```

## Local

```bash
docker compose -f docker-compose.yml up -d   # infra (profiles: app/infra)
docker compose run --rm trading-agent --help
```
