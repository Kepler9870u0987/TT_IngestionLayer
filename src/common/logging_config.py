"""
Structured logging configuration using JSON format.
Provides consistent logging across all components with correlation ID support.
"""
import logging
import sys
import json
from datetime import datetime, timezone
from typing import Optional

from src.common.correlation import CorrelationFilter


class JSONFormatter(logging.Formatter):
    """JSON formatter for structured logging with correlation tracking"""

    def format(self, record: logging.LogRecord) -> str:
        """Format log record as JSON with correlation and component fields"""
        log_data = {
            "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno
        }

        # Add correlation ID if present (injected by CorrelationFilter)
        correlation_id = getattr(record, 'correlation_id', None)
        if correlation_id:
            log_data['correlation_id'] = correlation_id

        # Add component if present
        component = getattr(record, 'component', None)
        if component:
            log_data['component'] = component

        # Add email UID if present
        if hasattr(record, 'email_uid'):
            log_data['email_uid'] = record.email_uid

        # Add exception info if present
        if record.exc_info:
            log_data["exception"] = self.formatException(record.exc_info)

        return json.dumps(log_data)


def setup_logging(name: str, level: str = "INFO") -> logging.Logger:
    """
    Configure structured logging for a component.

    Args:
        name: Logger name (usually __name__)
        level: Log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)

    Returns:
        Configured logger instance with correlation filter
    """
    logger = logging.getLogger(name)
    logger.setLevel(getattr(logging, level.upper()))

    # Remove existing handlers to avoid duplicates
    logger.handlers.clear()

    # Console handler with JSON formatting
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JSONFormatter())

    logger.addHandler(handler)

    # Add correlation filter for automatic correlation_id injection
    if not any(isinstance(f, CorrelationFilter) for f in logger.filters):
        logger.addFilter(CorrelationFilter())

    # Prevent propagation to root logger
    logger.propagate = False

    return logger


def get_logger(name: str, level: Optional[str] = None) -> logging.Logger:
    """
    Get or create a logger with optional level override.

    Args:
        name: Logger name
        level: Optional log level override

    Returns:
        Logger instance with correlation filter
    """
    if level:
        return setup_logging(name, level)

    # Return existing logger or create new one with default INFO level
    logger = logging.getLogger(name)
    if not logger.handlers:
        return setup_logging(name, "INFO")

    # Ensure correlation filter is attached
    if not any(isinstance(f, CorrelationFilter) for f in logger.filters):
        logger.addFilter(CorrelationFilter())

    return logger
