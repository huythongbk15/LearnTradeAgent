# Development

## Environment

- **Python:** `>=3.12,<3.13` (thống nhất: pyproject, CI, Dockerfile).
- **Install:** `pip install -e ".[dev,web,infra]"` (hoặc `poetry install --with dev,web,infra`).
- Dependency groups: `core` (default), `web` (Streamlit/FastAPI/Alpaca), `infra` (Kubernetes),
  `dev` (pytest, ruff, pytest-cov).

## Test

```bash
python -m pytest tests/ -q          # full suite
python -m pytest tests/test_live_safety.py -v   # critical live-path
```

- Coverage gate (CI): critical live-path modules ≥ 75% (`--cov-fail-under=75`).
- Test phân loại: unit, integration, critical live-path, dry-run. **Dry-run test không phải
  production validation.**

## Lint

```bash
ruff check .
```

Rule hiện tại: `E4/E7/E9/F` toàn repo. Safety-critical modules (execution, exchanges, risk,
live runners) được enforce chặt hơn theo từng bước — không suppress hàng loạt.

## CI

- `ci.yml`: PR + master — lint, unit tests, critical live-path gate, compose validate, Docker
  build + smoke test, Trivy scan; master thêm push image + SBOM + cosign sign.
- `phase6-ci.yml`: integration (multi-region/chaos dry-run), load test, manifest validate.
- `cd-staging.yml` / `cd-production.yml`: manual, digest-pinned, verify signature + SBOM.

## Generated docs

`scripts/generate_project_map.py` tạo project tree — chạy lại khi cây thư mục thay đổi,
CI kiểm tra không stale (xem `ci.yml`).

## Quy tắc

- Không thêm feature trading mới khi các release gates chưa xong (feature freeze).
- Không sửa code chỉ để khớp claim cũ trong docs — sửa docs theo code/evidence.
- Mọi thay đổi live path phải có test (characterization test trước khi refactor).
