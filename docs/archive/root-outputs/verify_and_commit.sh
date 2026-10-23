#!/bin/bash
# Run from project root — verifies P2 + HCG changes, commits, and pushes
set -e

echo "=== 1. Lint check ==="
ruff check src/trading_agent/authority/strategy_tournament.py \
  src/trading_agent/authority/adaptive_router.py \
  tests/test_tournament_e2e.py \
  tests/test_live_pipeline.py \
  src/trading_agent/llm/research_memory.py

echo "=== 2. Run P2 + HCG tests ==="
python -m pytest \
  tests/test_tournament_e2e.py::TestHealthGatePrePromotion \
  tests/test_live_pipeline.py::TestFallbackFeed \
  tests/test_fill_model_latency.py \
  -v --tb=short

echo "=== 3. Run full P0.2 test suite ==="
python -m pytest tests/test_ab_test_context.py -v --tb=short

echo "=== 4. Commit + Push ==="
git add -A
git commit -m "HCG: Exchange health gate + fallback feed tests

- Add health_monitor param to StrategyTournament
- Add exchange_name field to RoutingDecision
- HEALTH_GATE_BLOCK in _maybe_promote (lines 639-656)
- Add 4 health gate integration tests
- Add 3 fallback feed integration tests
- All tests pass, ruff clean"
git push

echo "=== DONE ==="