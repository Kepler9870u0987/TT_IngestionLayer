"""
Correlation ID management for distributed tracing.
Generates and propagates correlation IDs across the email pipeline.
"""
import uuid
import threading
import logging
from typing import Optional
from contextvars import ContextVar

# Context variable for correlation ID (async-safe and thread-safe)
_correlation_id_var: ContextVar[Optional[str]] = ContextVar(
    'correlation_id', default=None
)

# Context variable for component name
_component_var: ContextVar[Optional[str]] = ContextVar(
    'component', default=None
)


def generate_correlation_id() -> str:
    """
    Generate a new unique correlation ID.

    Returns:
        UUID4 string (e.g., "a1b2c3d4-e5f6-7890-abcd-ef1234567890")
    """
    return str(uuid.uuid4())


def set_correlation_id(correlation_id: str) -> None:
    """
    Set the correlation ID for the current context.

    Args:
        correlation_id: Correlation ID to set
    """
    _correlation_id_var.set(correlation_id)


def get_correlation_id() -> Optional[str]:
    """
    Get the correlation ID from the current context.

    Returns:
        Current correlation ID or None if not set
    """
    return _correlation_id_var.get()


def clear_correlation_id() -> None:
    """Clear the correlation ID from the current context."""
    _correlation_id_var.set(None)


def set_component(component: str) -> None:
    """
    Set the component name for the current context.

    Args:
        component: Component name (e.g., "producer", "worker", "health")
    """
    _component_var.set(component)


def get_component() -> Optional[str]:
    """
    Get the component name from the current context.

    Returns:
        Current component name or None if not set
    """
    return _component_var.get()


class CorrelationFilter(logging.Filter):
    """
    Logging filter that injects correlation_id and component into log records.
    Automatically reads from ContextVar so all log statements in the
    current context get the correlation ID without explicit passing.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        """
        Add correlation_id and component to the log record.

        Args:
            record: Log record to enhance

        Returns:
            Always True (never filters out records)
        """
        record.correlation_id = get_correlation_id() or ""
        record.component = get_component() or ""
        return True


class CorrelationContext:
    """
    Context manager for setting correlation ID within a scope.
    Automatically restores previous correlation ID on exit.

    Usage:
        with CorrelationContext("my-correlation-id"):
            logger.info("This log will have the correlation ID")
        # After the block, previous correlation ID is restored

        # Auto-generate:
        with CorrelationContext() as ctx:
            print(ctx.correlation_id)
    """

    def __init__(self, correlation_id: Optional[str] = None):
        """
        Initialize correlation context.

        Args:
            correlation_id: Specific correlation ID.
                           If None, a new UUID4 is generated.
        """
        self.correlation_id = correlation_id or generate_correlation_id()
        self._previous_id: Optional[str] = None

    def __enter__(self) -> 'CorrelationContext':
        """Set correlation ID on context entry."""
        self._previous_id = get_correlation_id()
        set_correlation_id(self.correlation_id)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        """Restore previous correlation ID on context exit."""
        if self._previous_id is not None:
            set_correlation_id(self._previous_id)
        else:
            clear_correlation_id()
