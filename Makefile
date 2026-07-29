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

# ── Backtest (Phase 1) ──────────────────────────────────────────────────

backtest:       ## Run backtest (usage: make backtest S=BTC/USDT T=1h STRAT=ma_crossover)
	poetry run trading-agent backtest run $(STRAT) $(S) --timeframe $(T)

strategies:     ## List strategies
	poetry run trading-agent backtest list

# ── AI Agents (Phase 2) ─────────────────────────────────────────────────

analyze:        ## Run multi-agent analysis (usage: make analyze S=BTC/USDT T=1h)
	poetry run trading-agent agents analyze $(S) -t $(T)

# ── Execution (Phase 3) ──────────────────────────────────────────────────

status:         ## Show execution portfolio status
	poetry run trading-agent execution status

trade:          ## Full cycle: agents → trade (usage: make trade S=BTC/USDT)
	poetry run trading-agent execution run $(S)

trades:         ## Show trade history
	poetry run trading-agent execution trades

risk:           ## Show risk controller status
	poetry run trading-agent execution risk

close-all:      ## Kill switch — close all positions
	poetry run trading-agent execution close --all

reset:          ## Reset paper exchange state
	poetry run trading-agent execution reset

# ── Monitoring & Dashboard (Phase 4) ─────────────────────────────────────

dashboard:      ## Start Streamlit dashboard
	poetry run streamlit run dashboard/app.py

db-stats:       ## Show database statistics
	poetry run python -c "
from trading_agent.monitoring.database import init_db, get_trade_stats, get_agent_decisions, get_equity_curve
init_db()
stats = get_trade_stats()
print(f'Trades: {stats[\"total_trades\"]} | Wins: {stats[\"wins\"]} | Losses: {stats[\"losses\"]}')
print(f'Win Rate: {stats[\"win_rate\"]:.1%} | Total P&L: \${stats[\"total_pnl\"]:+.2f}')
eq = get_equity_curve(limit=2)
print(f'Equity snapshots: {len(get_equity_curve(limit=99999))}')
decisions = get_agent_decisions(limit=99999)
print(f'Agent decisions logged: {len(decisions)}')
"

docker-up:      ## Start infrastructure (TimescaleDB + Redis + Grafana)
	docker compose --profile infra up -d

docker-down:    ## Stop infrastructure
	docker compose --profile infra down

docker-build:   ## Build app image
	docker compose --profile app build

docker-logs:    ## View logs
	docker compose logs -f
