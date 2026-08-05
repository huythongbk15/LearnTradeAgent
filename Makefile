# =============================================================================
# Trading Agent System — Makefile
# =============================================================================

.PHONY: help install clean format lint test run info fetch

help:           ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

install:        ## Install dependencies
	python -m poetry install

update:         ## Update dependencies
	python -m poetry update

clean:          ## Clean cache and temp files
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete
	rm -rf .pytest_cache .ruff_cache

format:         ## Format code
	python -m poetry run ruff format src/

lint:           ## Lint code
	python -m poetry run ruff check src/

test:           ## Run tests
	python -m poetry run pytest tests/ -v

info:           ## Show system info
	python -m poetry run trading-agent info

fetch:          ## Fetch OHLCV data (usage: make fetch S=BTC/USDT T=1h)
	python -m poetry run trading-agent data fetch $(S) --timeframe $(T) --save

fetch-all:      ## Download all configured symbols
	python -m poetry run trading-agent data download-all

datasets:       ## List stored datasets
	python -m poetry run trading-agent data list-datasets

inspect:        ## Inspect dataset (usage: make inspect S=BTC/USDT T=1h)
	python -m poetry run trading-agent data inspect $(S) --timeframe $(T)

shell:          ## Open Python shell in project context
	python -m poetry run python

# ── Backtest (Phase 1) ──────────────────────────────────────────────────

backtest:       ## Run backtest (usage: make backtest S=BTC/USDT T=1h STRAT=ma_crossover)
	python -m poetry run trading-agent backtest run $(STRAT) $(S) --timeframe $(T)

strategies:     ## List strategies
	python -m poetry run trading-agent backtest list

# ── AI Agents (Phase 2) ─────────────────────────────────────────────────

analyze:        ## Run multi-agent analysis (usage: make analyze S=BTC/USDT T=1h)
	python -m poetry run trading-agent agents analyze $(S) -t $(T)

# ── Execution (Phase 3) ──────────────────────────────────────────────────

status:         ## Show execution portfolio status
	python -m poetry run trading-agent execution status

trade:          ## Full cycle: agents → trade (usage: make trade S=BTC/USDT)
	python -m poetry run trading-agent execution run $(S)

trades:         ## Show trade history
	python -m poetry run trading-agent execution trades

risk:           ## Show risk controller status
	python -m poetry run trading-agent execution risk

close-all:      ## Kill switch — close all positions
	python -m poetry run trading-agent execution close --all

reset:          ## Reset paper exchange state
	python -m poetry run trading-agent execution reset

# ── Monitoring & Dashboard (Phase 4) ─────────────────────────────────────

dashboard:      ## Start Streamlit dashboard
	python -m poetry run streamlit run dashboard/app.py

db-stats:       ## Show database statistics
	python -m poetry run python scripts/db_stats.py

docker-up:      ## Start infrastructure (TimescaleDB + Redis + Grafana)
	docker compose --profile infra up -d

docker-down:    ## Stop infrastructure
	docker compose --profile infra down

docker-build:   ## Build app image
	docker compose --profile app build

docker-logs:    ## View logs
	docker compose logs -f

benchmark:      ## Run Phase 6 performance benchmarks
	python scripts/benchmark_phase6.py

loadtest:       ## Run Phase 6 load tests (usage: make loadtest QUICK=1 for quick mode)
	python scripts/load_test_phase6.py $(if $(QUICK),--quick,)

chaos-dryrun:   ## Run chaos experiments in dry-run (no cluster)
	python scripts/chaos_dryrun.py

region-dryrun:  ## Run multi-region sync controller in dry-run (no cluster)
	python -m trading_agent.infrastructure.multi_region.sync_controller dryrun

integration:    ## Run Phase 6 integration tests
	python -m pytest tests/test_phase6_integration.py -v

# ── Real-Time Data (Phase 6 P1) ──────────────────────────────────────────

ws-demo:        ## WebSocket Manager demo (mock provider)
	python -m trading_agent.exchanges.websocket_manager

health-demo:    ## Exchange Health Monitor demo
	python -m trading_agent.exchanges.health_monitor

pipeline-demo:  ## Unified Data Pipeline demo (mock source → SQLite)
	python -m trading_agent.data.pipeline

realtime-test:  ## Run real-time data module tests
	python -m pytest tests/test_realtime_data.py -v
