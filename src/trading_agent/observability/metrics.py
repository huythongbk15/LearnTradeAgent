"""OpenTelemetry metrics setup for Prometheus/Tempo."""

import logging
from typing import Optional, Callable
from functools import wraps

from opentelemetry import metrics
from opentelemetry.exporter.prometheus import PrometheusMetricReader
from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource, SERVICE_NAME, SERVICE_VERSION
from prometheus_client import start_http_server, Counter, Histogram, Gauge

logger = logging.getLogger(__name__)

_meter_provider: Optional[MeterProvider] = None
_meter = None

# Prometheus metrics (direct)
PROM_ORDER_TOTAL = Counter(
    'trading_orders_total', 
    'Total orders placed',
    ['symbol', 'side', 'status', 'exchange']
)
PROM_ORDER_LATENCY = Histogram(
    'trading_order_latency_seconds',
    'Order execution latency',
    ['symbol', 'exchange']
)
PROM_POSITION_SIZE = Gauge(
    'trading_position_size',
    'Current position size',
    ['symbol', 'strategy']
)
PROM_PNL = Gauge(
    'trading_pnl_total',
    'Total P&L',
    ['strategy', 'symbol']
)
PROM_DRAWDOWN = Gauge(
    'trading_drawdown_percent',
    'Current drawdown percentage',
    ['strategy', 'portfolio']
)
PROM_SIGNAL_COUNT = Counter(
    'trading_signals_total',
    'Total signals generated',
    ['signal_type', 'symbol', 'strategy']
)
PROM_RISK_CHECK = Counter(
    'trading_risk_checks_total',
    'Risk check results',
    ['check_type', 'result']
)
PROM_BALANCE = Gauge(
    'trading_balance',
    'Account balance',
    ['exchange', 'asset']
)
PROM_STRATEGY_WEIGHT = Gauge(
    'trading_strategy_weight',
    'Strategy allocation weight',
    ['strategy', 'portfolio']
)


def init_metrics(
    service_name: str = "trading-agent",
    service_version: str = "1.0.0",
    prometheus_port: int = 9090,
    otlp_endpoint: Optional[str] = None,
    export_interval_ms: int = 60000,
) -> MeterProvider:
    """Initialize metrics with Prometheus and OTLP exporters."""
    global _meter_provider, _meter

    resource = Resource.create({
        SERVICE_NAME: service_name,
        SERVICE_VERSION: service_version,
    })

    readers = []

    # Prometheus exporter (starts HTTP server)
    if prometheus_port:
        prom_reader = PrometheusMetricReader()
        readers.append(prom_reader)
        start_http_server(prometheus_port)
        logger.info(f"Prometheus metrics server started on port {prometheus_port}")

    # OTLP exporter for Tempo/Grafana
    if otlp_endpoint:
        otlp_reader = PeriodicExportingMetricReader(
            OTLPMetricExporter(endpoint=otlp_endpoint, insecure=True),
            export_interval_millis=export_interval_ms,
        )
        readers.append(otlp_reader)
        logger.info(f"OTLP metrics exporter configured: {otlp_endpoint}")

    _meter_provider = MeterProvider(resource=resource, metric_readers=readers)
    metrics.set_meter_provider(_meter_provider)

    _meter = _meter_provider.get_meter(service_name, service_version)
    logger.info(f"Metrics initialized for {service_name} v{service_version}")

    return _meter_provider


def get_meter(name: Optional[str] = None):
    """Get a meter instance."""
    global _meter
    if _meter is None:
        _meter = metrics.get_meter(name or "trading-agent")
    return _meter


# High-level metric creators
def create_counter(name: str, description: str, unit: str = "1") -> Callable:
    """Create a counter metric."""
    meter = get_meter()
    counter = meter.create_counter(name, description=description, unit=unit)
    return lambda value, attrs: counter.add(value, attrs)


def create_histogram(name: str, description: str, unit: str = "s") -> Callable:
    """Create a histogram metric."""
    meter = get_meter()
    histogram = meter.create_histogram(name, description=description, unit=unit)
    return lambda value, attrs: histogram.record(value, attrs)


def create_gauge(name: str, description: str, unit: str = "1") -> Callable:
    """Create a gauge metric (observable)."""
    meter = get_meter()
    gauge = meter.create_gauge(name, description=description, unit=unit)
    return lambda callback: gauge.set(callback)


def create_updown_counter(name: str, description: str, unit: str = "1") -> Callable:
    """Create an up-down counter."""
    meter = get_meter()
    counter = meter.create_up_down_counter(name, description=description, unit=unit)
    return lambda value, attrs: counter.add(value, attrs)


# Pre-defined metric instruments
class TradingMetrics:
    """Pre-configured trading metrics."""

    def __init__(self, meter_name: str = "trading"):
        self.meter = get_meter(meter_name)
        
        self.orders_total = self.meter.create_counter(
            "trading.orders.total",
            description="Total orders placed",
            unit="1",
        )
        
        self.order_latency = self.meter.create_histogram(
            "trading.order.latency",
            description="Order execution latency",
            unit="ms",
        )
        
        self.position_size = self.meter.create_gauge(
            "trading.position.size",
            description="Current position size",
            unit="contracts",
        )
        
        self.pnl = self.meter.create_gauge(
            "trading.pnl",
            description="Profit and loss",
            unit="USD",
        )
        
        self.drawdown = self.meter.create_gauge(
            "trading.drawdown",
            description="Current drawdown percentage",
            unit="%",
        )
        
        self.signals = self.meter.create_counter(
            "trading.signals.total",
            description="Total signals generated",
            unit="1",
        )
        
        self.risk_checks = self.meter.create_counter(
            "trading.risk.checks",
            description="Risk check results",
            unit="1",
        )
        
        self.balance = self.meter.create_gauge(
            "trading.balance",
            description="Account balance",
            unit="USD",
        )
        
        self.strategy_weight = self.meter.create_gauge(
            "trading.strategy.weight",
            description="Strategy allocation weight",
            unit="%",
        )
        
        self.execution_slippage = self.meter.create_histogram(
            "trading.execution.slippage",
            description="Execution slippage",
            unit="bp",
        )
        
        self.market_data_latency = self.meter.create_histogram(
            "trading.market_data.latency",
            description="Market data latency",
            unit="ms",
        )

    def record_order(
        self, 
        symbol: str, 
        side: str, 
        status: str, 
        exchange: str,
        latency_ms: float = None,
        slippage_bp: float = None,
    ):
        """Record order metrics."""
        attrs = {"symbol": symbol, "side": side, "status": status, "exchange": exchange}
        self.orders_total.add(1, attrs)
        
        if latency_ms is not None:
            self.order_latency.record(latency_ms, attrs)
        
        if slippage_bp is not None:
            self.execution_slippage.record(slippage_bp, attrs)

    def record_position(self, symbol: str, strategy: str, size: float, pnl: float):
        """Record position metrics."""
        attrs = {"symbol": symbol, "strategy": strategy}
        self.position_size.set(size, attrs)
        self.pnl.set(pnl, attrs)

    def record_drawdown(self, strategy: str, portfolio: str, pct: float):
        """Record drawdown."""
        self.drawdown.set(pct, {"strategy": strategy, "portfolio": portfolio})

    def record_signal(self, signal_type: str, symbol: str, strategy: str):
        """Record signal."""
        self.signals.add(1, {"signal_type": signal_type, "symbol": symbol, "strategy": strategy})

    def record_risk_check(self, check_type: str, passed: bool):
        """Record risk check result."""
        self.risk_checks.add(1, {"check_type": check_type, "result": "pass" if passed else "fail"})

    def record_balance(self, exchange: str, asset: str, balance: float):
        """Record balance."""
        self.balance.set(balance, {"exchange": exchange, "asset": asset})

    def record_strategy_weight(self, strategy: str, portfolio: str, weight: float):
        """Record strategy weight."""
        self.strategy_weight.set(weight, {"strategy": strategy, "portfolio": portfolio})

    def record_market_data_latency(self, exchange: str, latency_ms: float):
        """Record market data latency."""
        self.market_data_latency.record(latency_ms, {"exchange": exchange})


# Global instance
_trading_metrics: Optional[TradingMetrics] = None


def get_trading_metrics() -> TradingMetrics:
    """Get global trading metrics instance."""
    global _trading_metrics
    if _trading_metrics is None:
        _trading_metrics = TradingMetrics()
    return _trading_metrics


# Decorators for automatic metrics
def measure_latency(metric_name: str, attrs: Optional[dict] = None):
    """Decorator to measure function latency."""
    def decorator(func):
        import time
        @wraps(func)
        async def async_wrapper(*args, **kwargs):
            start = time.perf_counter()
            try:
                return await func(*args, **kwargs)
            finally:
                latency = (time.perf_counter() - start) * 1000
                get_trading_metrics().market_data_latency.record(
                    latency, attrs or {}
                )
        
        @wraps(func)
        def sync_wrapper(*args, **kwargs):
            start = time.perf_counter()
            try:
                return func(*args, **kwargs)
            finally:
                latency = (time.perf_counter() - start) * 1000
                get_trading_metrics().market_data_latency.record(
                    latency, attrs or {}
                )
        
        import asyncio
        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        return sync_wrapper
    
    return decorator


def count_calls(metric_name: str, attrs: Optional[dict] = None):
    """Decorator to count function calls."""
    def decorator(func):
        @wraps(func)
        async def async_wrapper(*args, **kwargs):
            get_trading_metrics().signals.add(1, attrs or {})
            return await func(*args, **kwargs)
        
        @wraps(func)
        def sync_wrapper(*args, **kwargs):
            get_trading_metrics().signals.add(1, attrs or {})
            return func(*args, **kwargs)
        
        import asyncio
        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        return sync_wrapper
    
    return decorator