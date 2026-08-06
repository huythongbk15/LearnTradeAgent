# QwenPaw Agent Control - Makefile integration
# Add to your workspace Makefile or use directly

.PHONY: qp-exec qp-subagent qp-registry qp-cleanup qp-health qp-status

# Controlled command execution with timeout/heartbeat/result file
# Usage: make qp-exec CMD="python -m trading_agent.cli backtest run agent_ensemble -s BTC/USDT -t 1h" TIMEOUT=7200 RESULT=backtest_result.json
qp-exec:
	@python scripts/qwenpaw_control/controlled_exec.py \
		--timeout $(or $(TIMEOUT),3600) \
		--heartbeat $(or $(HEARTBEAT),30) \
		--result-file $(or $(RESULT),) \
		--cwd $(or $(CWD),.) \
		$(CMD)

# Spawn tracked subagent
# Usage: make qp-subagent TASK="Analyze BTC/USDT 1h chart" TIMEOUT=600 BACKGROUND=true WAIT=true
qp-subagent:
	@python scripts/qwenpaw_control/tracked_subagent.py \
		"$(TASK)" \
		--timeout $(or $(TIMEOUT),600) \
		$(if $(filter true,$(BACKGROUND)),--background) \
		$(if $(filter true,$(WAIT)),--wait) \
		$(if $(ALLOWED_TOOLS),--allowed-tools $(ALLOWED_TOOLS)) \
		$(if $(SKILLS),--skills $(SKILLS))

# Process registry commands
qp-registry-list:
	@python scripts/qwenpaw_control/process_registry.py list

qp-registry-recent:
	@python scripts/qwenpaw_control/process_registry.py recent

qp-registry-cleanup:
	@python scripts/qwenpaw_control/process_registry.py cleanup

# Manual cleanup cron
qp-cleanup:
	@python scripts/qwenpaw_control/cleanup_cron.py

# Health check
qp-health:
	@python scripts/qwenpaw_control/health_check.py --format json

qp-health-nagios:
	@python scripts/qwenpaw_control/health_check.py --format nagios

qp-health-prometheus:
	@python scripts/qwenpaw_control/health_check.py --format prometheus

# Status dashboard (combines registry + health)
qp-status:
	@echo "=== QWENPAW AGENT STATUS ==="
	@echo ""
	@echo "--- Active Processes ---"
	@python scripts/qwenpaw_control/process_registry.py list
	@echo ""
	@echo "--- Health Check ---"
	@python scripts/qwenpaw_control/health_check.py --format json | python -m json.tool

# Install cron job for auto-cleanup (run every 10 min)
qp-install-cron:
	@(crontab -l 2>/dev/null | grep -v "qwenpaw_control/cleanup_cron"; \
	  echo "*/10 * * * * cd $(PWD) && python scripts/qwenpaw_control/cleanup_cron.py >> logs/qwenpaw_cleanup.log 2>&1") | crontab -
	@echo "Cron installed: cleanup every 10 minutes"

# Remove cron job
qp-remove-cron:
	@(crontab -l 2>/dev/null | grep -v "qwenpaw_control/cleanup_cron") | crontab -
	@echo "Cron removed"

# Quick test: run a controlled sleep command
qp-test:
	@make qp-exec CMD="sleep 5 && echo 'TEST OK'" TIMEOUT=10 HEARTBEAT=2 RESULT=test_result.json

# Shell aliases (add to .bashrc or source this file)
# alias qp-exec='python scripts/qwenpaw_control/controlled_exec.py'
# alias qp-subagent='python scripts/qwenpaw_control/tracked_subagent.py'
# alias qp-registry='python scripts/qwenpaw_control/process_registry.py'
# alias qp-health='python scripts/qwenpaw_control/health_check.py'
# alias qp-status='make -C $(git rev-parse --show-toplevel) qp-status'