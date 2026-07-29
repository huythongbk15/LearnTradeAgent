# =============================================================================
# Trading Agent System — Makefile
# =============================================================================

.PHONY: help install clean format lint test run info fetch

help:           ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

install:        ## Install dependencies
	poetry install

update:         ## Update dependencies
	poetry update

clean:          ## Clean cache and temp files
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete
	rm -rf .pytest_cache .ruff_cache

format:         ## Format code
	poetry run ruff format src/

lint:           ## Lint code
	poetry run ruff check src/

test:           ## Run tests
	poetry run pytest tests/ -v

info:           ## Show system info
	poetry run trading-agent info

fetch:          ## Fetch OHLCV data (usage: make fetch S=BTC/USDT T=1h)
	poetry run trading-agent data fetch $(S) --timeframe $(T) --save

fetch-all:      ## Download all configured symbols
	poetry run trading-agent data download-all

datasets:       ## List stored datasets
	poetry run trading-agent data list-datasets

inspect:        ## Inspect dataset (usage: make inspect S=BTC/USDT T=1h)
	poetry run trading-agent data inspect $(S) --timeframe $(T)

shell:          ## Open Python shell in project context
	poetry run python

docker-up:      ## Start infrastructure (TimescaleDB + Redis + Grafana)
	docker compose --profile infra up -d

docker-down:    ## Stop infrastructure
	docker compose --profile infra down

docker-build:   ## Build app image
	docker compose --profile app build

docker-logs:    ## View logs
	docker compose logs -f
