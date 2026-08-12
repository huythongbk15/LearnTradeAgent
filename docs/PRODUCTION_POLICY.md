# Production Policy

Policy tóm tắt cho production deployment. Chi tiết: `docs/DEPLOYMENT.md`,
`.github/BRANCH_PROTECTION.md`, `.github/CODEOWNERS`, `docs/SECURITY.md`.

## 1. Branch protection (master)

Thiết lập **branch ruleset** cho `master` trên GitHub (server-side, không thể
encode trong repo):

- Require pull requests — cấm push trực tiếp, cấm force push.
- ≥ 1 approval **và** approval từ Code Owners.
- Dismiss stale approvals khi diff thay đổi.
- Require conversation resolution trước merge.
- Require branch up-to-date trước merge.
- Required status checks: `Lint & Test`, `Build, Test & Security Scan`,
  `Provenance Gate` (nếu deploy).
- Cấm bypass cho admin; cấm xóa `master`; signed commits nếu khả thi.

## 2. Approvals

- Mọi thay đổi vào `master` qua PR + approval (xem §1).
- Mọi thay đổi execution/risk/CI (`CODEOWNERS`) yêu cầu approval của owner
  (`@huythongbk15`).
- Production deploy qua environment `production`:
  - ≥ 1 required reviewer **không phải** người trigger deploy.
  - Prevent self-review; restrict deploy tới `master`.
  - Secrets production chỉ nằm trong environment, không trong repo secrets.

## 3. CODEOWNERS

`.github/CODEOWNERS` gán owner bắt buộc cho:

- `scripts/live_enhanced_ma_binance.py`, `scripts/generate_live_strategy_evidence.py`
- `src/trading_agent/execution/`, `src/trading_agent/exchanges/`
- `config/live_strategy_evidence.example.json`
- `/.github/`, `/Dockerfile`, `docker-compose*.yml`, `pyproject.toml`, lockfiles

## 4. Deploy policy

- **Single execution leader** — nhiều replicas cấm tới khi có distributed
  lease + fencing + proven failover.
- Image luôn pin **immutable digest** (`@sha256:...`); deploy qua digest verify:
  1. `cd-staging.yml`: chạy tự động sau CI — verify revision label, cosign
     signature, SBOM attestation → pull digest → health check.
  2. `cd-production.yml`: **manual** `workflow_dispatch` (environment approval)
     — verify digest, signature, SBOM → deploy → health check → rollback path.
- **Provenance gate** (`.github/workflows/provenance-gate.yml` +
  `scripts/verify_provenance.py`): deploy bị chặn nếu thiếu SBOM/signature/
  provenance hoặc lệch repo/commit/workflow/branch.
- Deploy thành công **không** set `mainnet_enabled=true` — mainnet là quyết
  định operator riêng (fail-closed mặc định).

## 5. Verify

```bash
# Branch protection / deployment branch policy (cần GITHUB_TOKEN)
GITHUB_TOKEN=<token> python scripts/verify_github_controls.py --repo huythongbk15/LearnTradeAgent

# Provenance gate trước khi deploy
python scripts/verify_provenance.py \
  --image ghcr.io/huythongbk15/learntradeagent@sha256:<digest> \
  --repo huythongbk15/LearnTradeAgent --commit <sha> \
  --workflow .github/workflows/ci.yml --ref refs/heads/master

# Local policy lint (ruff + tests)
ruff check src/ tests/ scripts/ && python -m pytest -q
```

## 6. Config fail-closed (schema v1)

`config/config.yaml` có `schema_version: 1` và section `deployment`:

- `mode: paper | testnet | mainnet-canary | mainnet-normal`
- Non-paper yêu cầu bắt buộc: `position_limit_pct`, `max_slippage_pct`,
  `stale_data_max_age_s`, và Telegram alerting — thiếu là loader **từ chối**
  load (fail-closed).
- `execution_algorithm: market | twap | pov`.
