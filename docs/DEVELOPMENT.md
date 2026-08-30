# Development

## Environment

- **Python:** `>=3.12,<3.13` (thống nhất: pyproject, CI, Dockerfile).
- **Install:** `pip install -e ".[dev,web,infra]"` (hoặc `poetry install --with dev,web,infra`).
- Dependency groups: `core` (default), `web` (Streamlit/FastAPI/Alpaca), `infra` (Kubernetes),
  `dev` (pytest, pytest-xdist, ruff, pytest-cov).

## Test

```bash
make test                 # edit-time suite: all tests except slow/full-fidelity cells
make test-p0             # execution/risk/authority/provenance gate
make test-slow           # WFO, portfolio backtest, fault injection, concurrency
make test-full           # every test: parallel-safe lane, isolated fault lane, serial lane
make test-profile        # full suite + 50 slowest durations

# Equivalent direct runner; override with PYTEST_WORKERS or --workers.
python scripts/run_test_suite.py fast -- -q --tb=short
python scripts/run_test_suite.py slow -- -q --tb=short

python -m pytest tests/test_live_safety.py -v   # critical live-path
python -m pytest tests/test_execution_simulator.py tests/test_execution_simulator_property.py   # simulator + property-based
python -m pytest tests/test_research_governance.py   # research governance (Wave B)
```

Profiles are evidence-based rather than filename-based:

- `fast`: normal edit loop; currently covers 1,225/1,266 collected tests and took about four
  minutes on the reference WSL workspace.
- `slow`: deterministic full-fidelity WFO/backtest/concurrency cells. WFO runs default to
  three workers (CI pins two); fault cells run afterward with two workers so resource
  pressure cannot change their safety outcome.
- `p0`: critical safety marker, including slow P0 cells. Use before handing off execution,
  authority, risk, promotion, or provenance changes.
- `full`: release/local audit profile. CI covers the same population with three disjoint
  shards: `ci-p0`, `ci-fast`, and `slow`.

Marker rules: use `slow` only for deterministic tests too expensive for the edit loop;
combine it with `wfo`, `backtest`, or `fault` where applicable. Use `serial` only when a
test cannot be isolated with `tmp_path`. Never hide a flaky test behind `slow` or `serial`.

- Test pyramid: unit, integration, property-based (hypothesis: fills, rounding,
  fees, partial fills, replay determinism), state-machine, critical live-path.
  **Property-based tests bắt buộc cho order lifecycle/fee/rounding/replay**
  (prompt §27) — không chỉ happy path.

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

- `ci.yml`: PR + master — quality gates and three non-overlapping test shards run in
  parallel; image build waits for all shards, then smoke test/Trivy/attestations continue.
- `phase6-ci.yml`: only Phase 6 integration, multi-region/chaos dry-run, load test, and
  manifest validation; it no longer duplicates the repository-wide suite.
- `cd-staging.yml` / `cd-production.yml`: manual, digest-pinned, verify signature + SBOM.

## Generated docs

`scripts/generate_project_map.py` tạo project tree — chạy lại khi cây thư mục thay đổi,
CI kiểm tra không stale (xem `ci.yml`).

## Quy tắc

- Không thêm feature trading mới khi các release gates chưa xong (feature freeze).
- Không sửa code chỉ để khớp claim cũ trong docs — sửa docs theo code/evidence.
- Mọi thay đổi live path phải có test (characterization test trước khi refactor).
