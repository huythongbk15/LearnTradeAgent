"""Structured logging with OpenTelemetry integration."""

import json
import logging
import sys
from datetime import datetime
from typing import Any, Optional

from opentelemetry import trace


class JSONFormatter(logging.Formatter):
    """JSON log formatter with trace context."""

    def format(self, record: logging.LogRecord) -> str:
        # Get trace context
        span = trace.get_current_span()
        trace_id = None
        span_id = None
        if span and span.get_span_context():
            ctx = span.get_span_context()
            trace_id = format(ctx.trace_id, '032x')
            span_id = format(ctx.span_id, '016x')

        log_obj = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
        }

        if trace_id:
            log_obj["trace_id"] = trace_id
        if span_id:
            log_obj["span_id"] = span_id

        # Add extra fields
        for key, value in record.__dict__.items():
            if key not in {
                'name', 'msg', 'args', 'levelname', 'levelno', 'pathname',
                'filename', 'module', 'lineno', 'funcName', 'created',
                'msecs', 'relativeCreated', 'thread', 'threadName',
                'processName', 'process', 'message', 'exc_info', 'exc_text',
                'stack_info', 'trace_id', 'span_id'
            }:
                log_obj[key] = value

        if record.exc_info:
            log_obj["exception"] = self.formatException(record.exc_info)

        return json.dumps(log_obj)


class TradingLogger:
    """Enhanced logger for trading operations."""

    def __init__(self, name: str):
        self.logger = logging.getLogger(name)
        self._trade_logger = logging.getLogger(f"{name}.trades")
        self._signal_logger = logging.getLogger(f"{name}.signals")
        self._risk_logger = logging.getLogger(f"{name}.risk")

    def trade(self, action: str, symbol: str, size: float, price: float, **kwargs) -> None:
        """Log a trade execution."""
        self._trade_logger.info(
            f"Trade: {action} {size} {symbol} @ {price}",
            extra={
                "trade_action": action,
                "symbol": symbol,
                "size": size,
                "price": price,
                "timestamp": datetime.utcnow().isoformat(),
                **kwargs,
            }
        )

    def signal(self, strategy: str, symbol: str, signal: str, confidence: float, **kwargs) -> None:
        """Log a trading signal."""
        self._signal_logger.info(
            f"Signal: {strategy} -> {signal} {symbol} (conf: {confidence})",
            extra={
                "strategy": strategy,
                "symbol": symbol,
                "signal": signal,
                "confidence": confidence,
                "timestamp": datetime.utcnow().isoformat(),
                **kwargs,
            }
        )

    def risk(self, event: str, metric: str, value: float, threshold: float, **kwargs) -> None:
        """Log a risk event."""
        self._risk_logger.warning(
            f"Risk: {event} - {metric}={value} (threshold={threshold})",
            extra={
                "risk_event": event,
                "metric": metric,
                "value": value,
                "threshold": threshold,
                "timestamp": datetime.utcnow().isoformat(),
                **kwargs,
            }
        )

    def position(self, symbol: str, size: float, entry: float, current: float, pnl: float, **kwargs) -> None:
        """Log position update."""
        self.logger.info(
            f"Position: {symbol} size={size} entry={entry} current={current} pnl={pnl}",
            extra={
                "symbol": symbol,
                "size": size,
                "entry_price": entry,
                "current_price": current,
                "unrealized_pnl": pnl,
                "timestamp": datetime.utcnow().isoformat(),
                **kwargs,
            }
        )

    def order(self, order_id: str, symbol: str, side: str, type: str, size: float, price: float, status: str, **kwargs) -> None:
        """Log order event."""
        self.logger.info(
            f"Order: {order_id} {side} {size} {symbol} @ {price} [{status}]",
            extra={
                "order_id": order_id,
                "symbol": symbol,
                "side": side,
                "type": type,
                "size": size,
                "price": price,
                "status": status,
                "timestamp": datetime.utcnow().isoformat(),
                **kwargs,
            }
        )

    def debug(self, msg: str, **kwargs) -> None:
        self.logger.debug(msg, extra=kwargs)

    def info(self, msg: str, **kwargs) -> None:
        self.logger.info(msg, extra=kwargs)

    def warning(self, msg: str, **kwargs) -> None:
        self.logger.warning(msg, extra=kwargs)

    def error(self, msg: str, **kwargs) -> None:
        self.logger.error(msg, extra=kwargs)

    def exception(self, msg: str, **kwargs) -> None:
        self.logger.exception(msg, extra=kwargs)


def init_logging(
    level: str = "INFO",
    json_format: bool = False,
    log_file: Optional[str] = None,
    service_name: str = "trading-agent",
) -> None:
    """Initialize structured logging."""
    log_level = getattr(logging, level.upper(), logging.INFO)
    
    # Root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)
    
    # Clear existing handlers
    root_logger.handlers.clear()

    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(log_level)
    
    if json_format:
        console_handler.setFormatter(JSONFormatter())
    else:
        console_handler.setFormatter(
            logging.Formatter(
                "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S"
            )
        )
    
    root_logger.addHandler(console_handler)

    # File handler if specified
    if log_file:
        file_handler = logging.FileHandler(log_file)
        file_handler.setLevel(log_level)
        file_handler.setFormatter(JSONFormatter())
        root_logger.addHandler(file_handler)

    # Set specific loggers
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("asyncio").setLevel(logging.WARNING)
    logging.getLogger("aiohttp").setLevel(logging.WARNING)

    logging.info(f"Logging initialized (level={level}, json={json_format}, file={log_file})")


def get_logger(name: str) -> TradingLogger:
    """Get a trading logger instance."""
    return TradingLogger(name)


class LogContext:
    """Context manager for adding context to logs."""

    def __init__(self, logger: TradingLogger, **context):
        self.logger = logger
        self.context = context
        self.old_factory = logging.getLogRecordFactory()

    def __enter__(self):
        def record_factory(*args, **kwargs):
            record = self.old_factory(*args, **kwargs)
            for k, v in self.context.items():
                setattr(record, k, v)
            return record
        
        logging.setLogRecordFactory(record_factory)
        return self

    def __exit__(self, *args):
        logging.setLogRecordFactory(self.old_factory)


# Convenience function
def log_trade(logger: TradingLogger, action: str, symbol: str, size: float, price: float, **kwargs):
    """Quick trade logging."""
    logger.trade(action, symbol, size, price, **kwargs)


def log_signal(logger: TradingLogger, strategy: str, symbol: str, signal: str, confidence: float, **kwargs):
    """Quick signal logging."""
    logger.signal(strategy, symbol, signal, confidence, **kwargs)


def log_risk(logger: TradingLogger, event: str, metric: str, value: float, threshold: float, **kwargs):
    """Quick risk logging."""
    logger.risk(event, metric, value, threshold, **kwargs)