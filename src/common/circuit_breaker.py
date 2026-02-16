"""
Circuit breaker pattern implementation for fault tolerance.
Protects against cascading failures on Redis, IMAP, and external services.

States:
    CLOSED   - Normal operation, requests pass through
    OPEN     - Failures exceeded threshold, requests rejected immediately
    HALF_OPEN - Recovery test, limited requests allowed

Transitions:
    CLOSED -> OPEN:      failure_count >= failure_threshold
    OPEN -> HALF_OPEN:   recovery_timeout elapsed
    HALF_OPEN -> CLOSED: success_count >= success_threshold
    HALF_OPEN -> OPEN:   any failure
"""
import time
import threading
import functools
from typing import Optional, Callable, Any, Dict
from enum import Enum
from datetime import datetime

from src.common.logging_config import get_logger

logger = get_logger(__name__)


class CircuitState(Enum):
    """Circuit breaker states"""
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreakerError(Exception):
    """Raised when circuit is open and request is rejected"""

    def __init__(self, breaker_name: str, state: CircuitState, retry_after: float):
        self.breaker_name = breaker_name
        self.state = state
        self.retry_after = retry_after
        super().__init__(
            f"Circuit breaker '{breaker_name}' is {state.value}. "
            f"Retry after {retry_after:.1f}s"
        )


class CircuitBreaker:
    """
    Thread-safe circuit breaker implementation.

    Usage:
        cb = CircuitBreaker("redis", failure_threshold=5)

        @cb
        def redis_operation():
            return redis.ping()

        # Or manually:
        if cb.allow_request():
            try:
                result = do_something()
                cb.record_success()
            except Exception as e:
                cb.record_failure()
                raise
    """

    def __init__(
        self,
        name: str,
        failure_threshold: int = 5,
        recovery_timeout: float = 60.0,
        success_threshold: int = 3,
        excluded_exceptions: tuple = ()
    ):
        """
        Initialize circuit breaker.

        Args:
            name: Circuit breaker name (for logging)
            failure_threshold: Failures before opening circuit
            recovery_timeout: Seconds before trying half-open
            success_threshold: Successes in half-open before closing
            excluded_exceptions: Exception types that don't count as failures
        """
        self.name = name
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.success_threshold = success_threshold
        self.excluded_exceptions = excluded_exceptions

        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._success_count = 0
        self._last_failure_time: Optional[float] = None
        self._last_state_change: float = time.time()
        self._lock = threading.Lock()

        # Statistics
        self._total_calls = 0
        self._total_failures = 0
        self._total_rejections = 0
        self._total_successes = 0

        logger.info(
            f"CircuitBreaker '{name}' initialized: "
            f"failure_threshold={failure_threshold}, "
            f"recovery_timeout={recovery_timeout}s, "
            f"success_threshold={success_threshold}"
        )

    @property
    def state(self) -> CircuitState:
        """Get current circuit state (may transition from OPEN to HALF_OPEN)."""
        with self._lock:
            if self._state == CircuitState.OPEN:
                # Check if recovery timeout has elapsed
                if self._last_failure_time and \
                   time.time() - self._last_failure_time >= self.recovery_timeout:
                    self._transition_to(CircuitState.HALF_OPEN)
            return self._state

    @property
    def is_closed(self) -> bool:
        """Check if circuit is closed (normal operation)."""
        return self.state == CircuitState.CLOSED

    @property
    def is_open(self) -> bool:
        """Check if circuit is open (rejecting requests)."""
        return self.state == CircuitState.OPEN

    def allow_request(self) -> bool:
        """
        Check if a request should be allowed.

        Returns:
            True if request is allowed, False if circuit is open
        """
        current_state = self.state

        if current_state == CircuitState.CLOSED:
            return True
        elif current_state == CircuitState.HALF_OPEN:
            return True  # Allow test requests
        else:  # OPEN
            self._total_rejections += 1
            return False

    def record_success(self) -> None:
        """Record a successful operation."""
        with self._lock:
            self._total_successes += 1
            self._total_calls += 1

            if self._state == CircuitState.HALF_OPEN:
                self._success_count += 1
                if self._success_count >= self.success_threshold:
                    self._transition_to(CircuitState.CLOSED)
            elif self._state == CircuitState.CLOSED:
                # Reset failure count on success
                self._failure_count = 0

    def record_failure(self, exception: Optional[Exception] = None) -> None:
        """
        Record a failed operation.

        Args:
            exception: The exception that caused the failure
        """
        # Skip excluded exceptions
        if exception and isinstance(exception, self.excluded_exceptions):
            return

        with self._lock:
            self._total_failures += 1
            self._total_calls += 1
            self._last_failure_time = time.time()

            if self._state == CircuitState.HALF_OPEN:
                # Any failure in half-open goes back to open
                self._transition_to(CircuitState.OPEN)

            elif self._state == CircuitState.CLOSED:
                self._failure_count += 1
                if self._failure_count >= self.failure_threshold:
                    self._transition_to(CircuitState.OPEN)

    def _transition_to(self, new_state: CircuitState) -> None:
        """
        Transition to a new state.

        Args:
            new_state: The target state
        """
        old_state = self._state
        self._state = new_state
        self._last_state_change = time.time()

        if new_state == CircuitState.CLOSED:
            self._failure_count = 0
            self._success_count = 0
        elif new_state == CircuitState.HALF_OPEN:
            self._success_count = 0
        elif new_state == CircuitState.OPEN:
            self._success_count = 0

        logger.warning(
            f"CircuitBreaker '{self.name}': {old_state.value} -> {new_state.value} "
            f"(failures={self._failure_count}, threshold={self.failure_threshold})"
        )

    def get_retry_after(self) -> float:
        """
        Get seconds until circuit might transition to half-open.

        Returns:
            Seconds remaining, or 0 if not open
        """
        if self._state != CircuitState.OPEN or not self._last_failure_time:
            return 0.0
        elapsed = time.time() - self._last_failure_time
        return max(0.0, self.recovery_timeout - elapsed)

    def get_stats(self) -> Dict[str, Any]:
        """
        Get circuit breaker statistics.

        Returns:
            Statistics dictionary
        """
        return {
            "name": self.name,
            "state": self.state.value,
            "failure_count": self._failure_count,
            "failure_threshold": self.failure_threshold,
            "success_count": self._success_count,
            "success_threshold": self.success_threshold,
            "total_calls": self._total_calls,
            "total_failures": self._total_failures,
            "total_successes": self._total_successes,
            "total_rejections": self._total_rejections,
            "retry_after": self.get_retry_after(),
            "last_state_change": datetime.fromtimestamp(
                self._last_state_change
            ).isoformat() if self._last_state_change else None
        }

    def reset(self) -> None:
        """Reset circuit breaker to closed state (for maintenance)."""
        with self._lock:
            self._transition_to(CircuitState.CLOSED)
            self._failure_count = 0
            self._success_count = 0
            self._last_failure_time = None
            logger.info(f"CircuitBreaker '{self.name}' manually reset")

    def __call__(self, func: Callable) -> Callable:
        """
        Decorator usage for circuit breaker.

        Args:
            func: Function to protect

        Returns:
            Wrapped function with circuit breaker logic
        """
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            if not self.allow_request():
                raise CircuitBreakerError(
                    self.name,
                    self._state,
                    self.get_retry_after()
                )

            try:
                result = func(*args, **kwargs)
                self.record_success()
                return result
            except self.excluded_exceptions:
                # Don't count excluded exceptions as failures
                raise
            except Exception as e:
                self.record_failure(e)
                raise

        return wrapper


# Pre-configured circuit breakers for common services
class CircuitBreakers:
    """Registry of circuit breakers for different services."""

    _breakers: Dict[str, CircuitBreaker] = {}
    _lock = threading.Lock()

    @classmethod
    def get(
        cls,
        name: str,
        failure_threshold: int = 5,
        recovery_timeout: float = 60.0,
        success_threshold: int = 3
    ) -> CircuitBreaker:
        """
        Get or create a named circuit breaker.

        Args:
            name: Circuit breaker name
            failure_threshold: Failures before opening
            recovery_timeout: Recovery timeout in seconds
            success_threshold: Successes before closing

        Returns:
            CircuitBreaker instance
        """
        with cls._lock:
            if name not in cls._breakers:
                cls._breakers[name] = CircuitBreaker(
                    name=name,
                    failure_threshold=failure_threshold,
                    recovery_timeout=recovery_timeout,
                    success_threshold=success_threshold
                )
            return cls._breakers[name]

    @classmethod
    def get_all_stats(cls) -> Dict[str, Any]:
        """
        Get stats for all registered circuit breakers.

        Returns:
            Dictionary of circuit breaker stats
        """
        return {
            name: cb.get_stats()
            for name, cb in cls._breakers.items()
        }

    @classmethod
    def reset_all(cls) -> None:
        """Reset all circuit breakers (for testing)."""
        with cls._lock:
            for cb in cls._breakers.values():
                cb.reset()
            cls._breakers.clear()
