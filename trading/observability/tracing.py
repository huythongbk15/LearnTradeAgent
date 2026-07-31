"""OpenTelemetry tracing setup."""

import os
from functools import wraps
from typing import Callable, Any

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.exporter.jaeger.thrift import JaegerExporter
from opentelemetry.sdk.resources import Resource, SERVICE_NAME
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter
from opentelemetry.instrumentation.logging import LoggingInstrumentor
from opentelemetry.propagate import set_global_textmap
from opentelemetry.propagators.b3 import B3MultiFormat


# Global tracer provider
_tracer_provider: TracerProvider | None = None


def init_tracing(
    service_name: str = "trading-agent",
    otlp_endpoint: str | None = None,
    jaeger_endpoint: str | None = None,
    console_export: bool = False,
) -> TracerProvider:
    """Initialize OpenTelemetry tracing."""
    global _tracer_provider
    
    # Create resource
    resource = Resource.create({
        SERVICE_NAME: service_name,
        "deployment.environment": os.getenv("ENVIRONMENT", "development"),
    })
    
    # Create tracer provider
    _tracer_provider = TracerProvider(resource=resource)
    trace.set_tracer_provider(_tracer_provider)
    
    # Configure exporters
    if otlp_endpoint:
        otlp_exporter = OTLPSpanExporter(endpoint=otlp_endpoint, insecure=True)
        _tracer_provider.add_span_processor(BatchSpanProcessor(otlp_exporter))
    
    if jaeger_endpoint:
        jaeger_exporter = JaegerExporter(
            agent_host_name=jaeger_endpoint.split(":")[0] if ":" in jaeger_endpoint else jaeger_endpoint,
            agent_port=int(jaeger_endpoint.split(":")[1]) if ":" in jaeger_endpoint else 6831,
        )
        _tracer_provider.add_span_processor(BatchSpanProcessor(jaeger_exporter))
    
    if console_export:
        console_exporter = ConsoleSpanExporter()
        _tracer_provider.add_span_processor(BatchSpanProcessor(console_exporter))
    
    # Set B3 propagator for distributed tracing
    set_global_textmap(B3MultiFormat())
    
    # Instrument logging
    LoggingInstrumentor().instrument(set_logging_format=True)
    
    return _tracer_provider


def get_tracer(name: str | None = None) -> trace.Tracer:
    """Get a tracer instance."""
    return trace.get_tracer(name or __name__)


def traced(
    name: str | None = None,
    attributes: dict[str, Any] | None = None,
    record_exception: bool = True,
) -> Callable:
    """Decorator to trace a function."""
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def sync_wrapper(*args, **kwargs):
            tracer = get_tracer(func.__module__)
            span_name = name or f"{func.__module__}.{func.__qualname__}"
            with tracer.start_as_current_span(span_name) as span:
                if attributes:
                    for k, v in attributes.items():
                        span.set_attribute(k, v)
                try:
                    result = func(*args, **kwargs)
                    span.set_status(trace.StatusCode.OK)
                    return result
                except Exception as e:
                    if record_exception:
                        span.record_exception(e)
                    span.set_status(trace.StatusCode.ERROR, str(e))
                    raise
        
        @wraps(func)
        async def async_wrapper(*args, **kwargs):
            tracer = get_tracer(func.__module__)
            span_name = name or f"{func.__module__}.{func.__qualname__}"
            with tracer.start_as_current_span(span_name) as span:
                if attributes:
                    for k, v in attributes.items():
                        span.set_attribute(k, v)
                try:
                    result = await func(*args, **kwargs)
                    span.set_status(trace.StatusCode.OK)
                    return result
                except Exception as e:
                    if record_exception:
                        span.record_exception(e)
                    span.set_status(trace.StatusCode.ERROR, str(e))
                    raise
        
        import asyncio
        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        return sync_wrapper
    
    return decorator


# Context managers for manual span creation
class TraceContext:
    """Context manager for manual trace spans."""
    
    def __init__(self, name: str, attributes: dict[str, Any] | None = None):
        self.name = name
        self.attributes = attributes or {}
        self.span = None
    
    def __enter__(self):
        tracer = get_tracer()
        self.span = tracer.start_span(self.name)
        for k, v in self.attributes.items():
            self.span.set_attribute(k, v)
        return self.span
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type:
            self.span.record_exception(exc_val)
            self.span.set_status(trace.StatusCode.ERROR, str(exc_val))
        else:
            self.span.set_status(trace.StatusCode.OK)
        self.span.end()