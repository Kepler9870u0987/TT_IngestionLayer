"""
Retry utilities using tenacity library.
Provides reusable retry decorators for common failure scenarios.
"""
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    wait_fixed,
    retry_if_exception_type,
    retry_if_exception,
    before_sleep_log
)
import logging
from typing import Callable, Type, Tuple

logger = logging.getLogger(__name__)


def retry_on_network_error(
    max_attempts: int = 5,
    min_wait: int = 2,
    max_wait: int = 60,
    multiplier: int = 2
):
    """
    Retry decorator for network-related errors with exponential backoff.

    Args:
        max_attempts: Maximum number of retry attempts
        min_wait: Minimum wait time in seconds
        max_wait: Maximum wait time in seconds
        multiplier: Multiplier for exponential backoff

    Returns:
        Retry decorator

    Example:
        @retry_on_network_error(max_attempts=3)
        def fetch_data():
            # Network operation that might fail
            pass
    """
    return retry(
        stop=stop_after_attempt(max_attempts),
        wait=wait_exponential(multiplier=multiplier, min=min_wait, max=max_wait),
        retry=retry_if_exception_type((
            ConnectionError,
            TimeoutError,
            OSError
        )),
        before_sleep=before_sleep_log(logger, logging.WARNING),
        reraise=True
    )


def retry_on_redis_error(
    max_attempts: int = 3,
    min_wait: int = 1,
    max_wait: int = 10
):
    """
    Retry decorator specifically for Redis operations.

    Args:
        max_attempts: Maximum number of retry attempts
        min_wait: Minimum wait time in seconds
        max_wait: Maximum wait time in seconds

    Returns:
        Retry decorator
    """
    from redis.exceptions import RedisError, ConnectionError as RedisConnectionError

    return retry(
        stop=stop_after_attempt(max_attempts),
        wait=wait_exponential(multiplier=1, min=min_wait, max=max_wait),
        retry=retry_if_exception_type((RedisError, RedisConnectionError)),
        before_sleep=before_sleep_log(logger, logging.WARNING),
        reraise=True
    )


def retry_on_imap_error(
    max_attempts: int = 5,
    min_wait: int = 4,
    max_wait: int = 60
):
    """
    Retry decorator for IMAP operations.

    Args:
        max_attempts: Maximum number of retry attempts
        min_wait: Minimum wait time in seconds
        max_wait: Maximum wait time in seconds

    Returns:
        Retry decorator
    """
    try:
        from imapclient import IMAPClient
        from imapclient.exceptions import IMAPClientError

        return retry(
            stop=stop_after_attempt(max_attempts),
            wait=wait_exponential(multiplier=2, min=min_wait, max=max_wait),
            retry=retry_if_exception_type((
                IMAPClientError,
                ConnectionError,
                TimeoutError,
                OSError
            )),
            before_sleep=before_sleep_log(logger, logging.WARNING),
            reraise=True
        )
    except ImportError:
        # Fallback if imapclient not installed
        return retry(
            stop=stop_after_attempt(max_attempts),
            wait=wait_exponential(multiplier=2, min=min_wait, max=max_wait),
            retry=retry_if_exception_type((ConnectionError, TimeoutError, OSError)),
            before_sleep=before_sleep_log(logger, logging.WARNING),
            reraise=True
        )


def retry_on_oauth_error(
    max_attempts: int = 3,
    wait_seconds: int = 5
):
    """
    Retry decorator for OAuth2 authentication errors.
    Uses fixed wait time as OAuth errors typically need consistent retry intervals.

    Args:
        max_attempts: Maximum number of retry attempts
        wait_seconds: Fixed wait time between retries in seconds

    Returns:
        Retry decorator
    """
    from src.common.exceptions import OAuth2AuthenticationError, TokenRefreshError

    return retry(
        stop=stop_after_attempt(max_attempts),
        wait=wait_fixed(wait_seconds),
        retry=retry_if_exception_type((OAuth2AuthenticationError, TokenRefreshError)),
        before_sleep=before_sleep_log(logger, logging.ERROR),
        reraise=True
    )


def retry_with_custom_predicate(
    max_attempts: int,
    wait_strategy,
    retry_predicate: Callable[[Exception], bool],
    exception_types: Tuple[Type[Exception], ...] = (Exception,)
):
    """
    Generic retry decorator with custom retry predicate.

    Args:
        max_attempts: Maximum number of retry attempts
        wait_strategy: Tenacity wait strategy (e.g., wait_exponential(...))
        retry_predicate: Function that returns True if should retry
        exception_types: Tuple of exception types to catch

    Returns:
        Retry decorator

    Example:
        def should_retry(exc):
            return isinstance(exc, ValueError) and "retry" in str(exc)

        @retry_with_custom_predicate(
            max_attempts=3,
            wait_strategy=wait_fixed(2),
            retry_predicate=should_retry
        )
        def operation():
            pass
    """
    return retry(
        stop=stop_after_attempt(max_attempts),
        wait=wait_strategy,
        retry=retry_if_exception(retry_predicate),
        before_sleep=before_sleep_log(logger, logging.WARNING),
        reraise=True
    )
