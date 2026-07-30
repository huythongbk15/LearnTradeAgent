"""
Prometheus metrics server — exposes trading metrics on port 8000.

Usage:
    python -m trading_agent.monitoring.metrics_server

Or via CLI:
    trading-agent system serve
"""

from __future__ import annotations

import http.server
import json
import os
from typing import Any

from trading_agent.execution.engine import ExecutionEngine
from trading_agent.log_config import get_logger

logger = get_logger(__name__)

METRICS_PORT = int(os.environ.get("METRICS_PORT", "8000"))


def _collect_metrics() -> dict[str, Any]:
    """Collect all trading metrics for Prometheus."""
    engine = ExecutionEngine()
    summary = engine.get_summary()
    positions = engine.get_positions_summary()

    metrics = {
        "equity": summary.get("equity", 0),
        "cash": summary.get("cash", 0),
        "positions_value": summary.get("positions_value", 0),
        "return_pct": summary.get("return_pct", 0),
        "total_trades": summary.get("total_trades", 0),
        "open_positions": summary.get("open_positions", 0),
        "open_orders": summary.get("open_orders", 0),
        "unrealized_pnl": summary.get("unrealized_pnl", 0),
        "daily_pnl": summary.get("daily_pnl", 0),
    }

    # Also collect from RiskController if available
    try:
        from trading_agent.execution.risk_controller import RiskController
        rc = RiskController(engine)
        status = rc.get_status()
        metrics["drawdown_pct"] = status.get("drawdown_pct", 0)
        metrics["daily_loss_pct"] = status.get("daily_loss_pct", 0)
        metrics["circuit_breaker_active"] = 1 if status.get("circuit_breaker_active") else 0
    except Exception:
        metrics["drawdown_pct"] = 0
        metrics["daily_loss_pct"] = 0
        metrics["circuit_breaker_active"] = 0

    return metrics


def _format_prometheus(metrics: dict) -> str:
    """Convert metrics dict to Prometheus exposition format."""
    lines = [
        "# HELP trading_equity Current portfolio equity",
        "# TYPE trading_equity gauge",
        f'trading_equity{_labels("total")} {metrics.get("equity", 0)}',
        "",
        "# HELP trading_cash Current available cash",
        "# TYPE trading_cash gauge",
        f'trading_cash{_labels()} {metrics.get("cash", 0)}',
        "",
        "# HELP trading_positions_value Value of open positions",
        "# TYPE trading_positions_value gauge",
        f'trading_positions_value{_labels()} {metrics.get("positions_value", 0)}',
        "",
        "# HELP trading_return_pct Total return percentage",
        "# TYPE trading_return_pct gauge",
        f'trading_return_pct{_labels()} {metrics.get("return_pct", 0)}',
        "",
        "# HELP trading_trades_total Total number of trades",
        "# TYPE trading_trades_total counter",
        f'trading_trades_total{_labels()} {metrics.get("total_trades", 0)}',
        "",
        "# HELP trading_open_positions Number of open positions",
        "# TYPE trading_open_positions gauge",
        f'trading_open_positions{_labels()} {metrics.get("open_positions", 0)}',
        "",
        "# HELP trading_unrealized_pnl Unrealized P&L",
        "# TYPE trading_unrealized_pnl gauge",
        f'trading_unrealized_pnl{_labels()} {metrics.get("unrealized_pnl", 0)}',
        "",
        "# HELP trading_drawdown_pct Current drawdown percentage",
        "# TYPE trading_drawdown_pct gauge",
        f'trading_drawdown_pct{_labels()} {metrics.get("drawdown_pct", 0)}',
        "",
        "# HELP trading_daily_pnl Daily P&L",
        "# TYPE trading_daily_pnl gauge",
        f'trading_daily_pnl{_labels()} {metrics.get("daily_pnl", 0)}',
        "",
    ]
    return "\n".join(lines)


def _labels(kind: str = "") -> str:
    """Return Prometheus labels string."""
    if kind:
        return f'{{kind="{kind}"}}'
    return "{}"


class MetricsHandler(http.server.BaseHTTPRequestHandler):
    """HTTP handler serving Prometheus /metrics and /healthz."""

    def do_GET(self) -> None:
        if self.path == "/metrics":
            self._serve_metrics()
        elif self.path in ("/healthz", "/health"):
            self._serve_health()
        else:
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b"Not found")

    def _serve_metrics(self) -> None:
        try:
            metrics = _collect_metrics()
            body = _format_prometheus(metrics).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; version=0.0.4")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except Exception as e:
            logger.error(f"Metrics collection failed: {e}")
            self.send_response(500)
            self.end_headers()
            self.wfile.write(b"Internal error")

    def _serve_health(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        body = json.dumps({"status": "ok", "service": "trading-agent-metrics"}).encode()
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:
        logger.debug(f"Metrics HTTP: {format % args}")


def serve_forever(port: int = METRICS_PORT) -> None:
    """Start the metrics HTTP server (blocking)."""
    server = http.server.HTTPServer(("0.0.0.0", port), MetricsHandler)
    logger.info(f"🚀 Metrics server listening on http://0.0.0.0:{port}/metrics")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Metrics server stopped")
        server.server_close()


if __name__ == "__main__":
    serve_forever()
