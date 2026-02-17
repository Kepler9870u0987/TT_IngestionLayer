"""
Unit tests for retry decorators (tenacity wrappers).
"""
import pytest
from unittest.mock import patch

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.common.retry import (
    retry_on_network_error,
    retry_on_redis_error,
    retry_on_imap_error,
    retry_on_oauth_error,
    retry_with_custom_predicate,
)


class TestRetryOnNetworkError:
    """Test retry_on_network_error decorator"""

    def test_retries_on_connection_error(self):
        """Test that ConnectionError triggers retries"""
        call_count = 0

        @retry_on_network_error(max_attempts=3, min_wait=0, max_wait=0)
        def failing():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise ConnectionError("conn refused")
            return "ok"

        result = failing()
        assert result == "ok"
        assert call_count == 3

    def test_retries_on_timeout_error(self):
        """Test that TimeoutError triggers retries"""
        call_count = 0

        @retry_on_network_error(max_attempts=2, min_wait=0, max_wait=0)
        def failing():
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise TimeoutError("timed out")
            return "ok"

        result = failing()
        assert result == "ok"
        assert call_count == 2

    def test_does_not_retry_on_value_error(self):
        """Test that ValueError is NOT retried"""
        @retry_on_network_error(max_attempts=3, min_wait=0, max_wait=0)
        def failing():
            raise ValueError("not a network error")

        with pytest.raises(ValueError):
            failing()

    def test_exhausts_retries(self):
        """Test that ConnectionError is re-raised after max attempts"""
        call_count = 0

        @retry_on_network_error(max_attempts=2, min_wait=0, max_wait=0)
        def failing():
            nonlocal call_count
            call_count += 1
            raise ConnectionError("always fails")

        with pytest.raises(ConnectionError):
            failing()
        assert call_count == 2


class TestRetryOnRedisError:
    """Test retry_on_redis_error decorator"""

    def test_retries_on_redis_error(self):
        """Test that RedisError triggers retries"""
        from redis.exceptions import RedisError

        call_count = 0

        @retry_on_redis_error(max_attempts=2, min_wait=0, max_wait=0)
        def failing():
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise RedisError("redis down")
            return "ok"

        result = failing()
        assert result == "ok"
        assert call_count == 2

    def test_retries_on_redis_connection_error(self):
        """Test that Redis ConnectionError triggers retries"""
        from redis.exceptions import ConnectionError as RedisConnectionError

        call_count = 0

        @retry_on_redis_error(max_attempts=2, min_wait=0, max_wait=0)
        def failing():
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise RedisConnectionError("redis conn error")
            return "ok"

        result = failing()
        assert result == "ok"

    def test_does_not_retry_on_value_error(self):
        """Test that ValueError is NOT retried"""
        @retry_on_redis_error(max_attempts=3, min_wait=0, max_wait=0)
        def failing():
            raise ValueError("not a redis error")

        with pytest.raises(ValueError):
            failing()


class TestRetryOnIMAPError:
    """Test retry_on_imap_error decorator"""

    def test_retries_on_connection_error(self):
        """Test that ConnectionError triggers retries for IMAP"""
        call_count = 0

        @retry_on_imap_error(max_attempts=2, min_wait=0, max_wait=0)
        def failing():
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise ConnectionError("imap conn error")
            return "ok"

        result = failing()
        assert result == "ok"

    def test_does_not_retry_on_value_error(self):
        """Test that ValueError is NOT retried"""
        @retry_on_imap_error(max_attempts=2, min_wait=0, max_wait=0)
        def failing():
            raise ValueError("nope")

        with pytest.raises(ValueError):
            failing()


class TestRetryOnOAuthError:
    """Test retry_on_oauth_error decorator"""

    def test_retries_on_oauth2_error(self):
        """Test that OAuth2AuthenticationError triggers retries"""
        from src.common.exceptions import OAuth2AuthenticationError

        call_count = 0

        @retry_on_oauth_error(max_attempts=2, wait_seconds=0)
        def failing():
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise OAuth2AuthenticationError("auth failed")
            return "ok"

        result = failing()
        assert result == "ok"
        assert call_count == 2

    def test_retries_on_token_refresh_error(self):
        """Test that TokenRefreshError triggers retries"""
        from src.common.exceptions import TokenRefreshError

        call_count = 0

        @retry_on_oauth_error(max_attempts=2, wait_seconds=0)
        def failing():
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise TokenRefreshError("refresh failed")
            return "ok"

        result = failing()
        assert result == "ok"

    def test_does_not_retry_on_runtime_error(self):
        """Test that RuntimeError is NOT retried"""
        @retry_on_oauth_error(max_attempts=3, wait_seconds=0)
        def failing():
            raise RuntimeError("not oauth")

        with pytest.raises(RuntimeError):
            failing()


class TestRetryWithCustomPredicate:
    """Test retry_with_custom_predicate"""

    def test_retries_when_predicate_true(self):
        """Test retry when predicate returns True"""
        from tenacity import wait_none

        call_count = 0

        def should_retry(exc):
            return isinstance(exc, ValueError) and "retry" in str(exc)

        @retry_with_custom_predicate(
            max_attempts=3,
            wait_strategy=wait_none(),
            retry_predicate=should_retry,
        )
        def failing():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise ValueError("please retry")
            return "done"

        result = failing()
        assert result == "done"
        assert call_count == 3

    def test_does_not_retry_when_predicate_false(self):
        """Test no retry when predicate returns False"""
        from tenacity import wait_none

        def should_retry(exc):
            return False

        @retry_with_custom_predicate(
            max_attempts=5,
            wait_strategy=wait_none(),
            retry_predicate=should_retry,
        )
        def failing():
            raise ValueError("don't retry")

        with pytest.raises(ValueError, match="don't retry"):
            failing()
