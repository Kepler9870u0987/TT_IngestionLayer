"""
Idempotency manager using Redis Sets for deduplication.
Ensures each email is processed exactly once.
"""
from typing import Optional
from datetime import datetime, timedelta

from src.common.redis_client import RedisClient
from src.common.logging_config import get_logger
from src.common.exceptions import RedisConnectionError

logger = get_logger(__name__)


class IdempotencyManager:
    """
    Manages message deduplication using Redis Sets.
    Tracks processed message IDs to prevent duplicate processing.
    """

    def __init__(
        self,
        redis_client: RedisClient,
        key_prefix: str = "processed_messages",
        ttl_hours: Optional[int] = None
    ):
        """
        Initialize idempotency manager.

        Args:
            redis_client: Redis client instance
            key_prefix: Prefix for Redis keys (default: "processed_messages")
            ttl_hours: Optional TTL in hours for processed message tracking.
                      If None, messages are tracked indefinitely.
        """
        self.redis = redis_client
        self.key_prefix = key_prefix
        self.ttl_hours = ttl_hours
        logger.info(
            f"IdempotencyManager initialized: prefix={key_prefix}, "
            f"ttl_hours={ttl_hours}"
        )

    def _get_key(self) -> str:
        """
        Generate Redis key for processed messages set.

        Returns:
            Redis key string
        """
        return f"{self.key_prefix}:set"

    def is_processed(self, message_id: str) -> bool:
        """
        Check if a message has already been processed.

        Args:
            message_id: Unique message identifier (e.g., email UID or message-id)

        Returns:
            True if message was already processed, False otherwise

        Raises:
            RedisConnectionError: If Redis operation fails
        """
        try:
            result = self.redis.sismember(self._get_key(), message_id)
            if result:
                logger.debug(f"Message already processed: {message_id}")
            return result
        except Exception as e:
            logger.error(f"Failed to check message processing status: {e}")
            raise RedisConnectionError(f"Idempotency check failed: {e}")

    def mark_processed(self, message_id: str) -> bool:
        """
        Mark a message as processed.

        Args:
            message_id: Unique message identifier

        Returns:
            True if message was newly marked (not already processed),
            False if message was already in the set

        Raises:
            RedisConnectionError: If Redis operation fails
        """
        try:
            key = self._get_key()
            result = self.redis.sadd(key, message_id)
            
            # Set TTL if configured
            if self.ttl_hours and result > 0:
                ttl_seconds = int(timedelta(hours=self.ttl_hours).total_seconds())
                self.redis.client.expire(key, ttl_seconds)
                logger.debug(f"Set TTL for {key}: {ttl_seconds}s")
            
            if result > 0:
                logger.info(f"Marked message as processed: {message_id}")
                return True
            else:
                logger.debug(f"Message already marked: {message_id}")
                return False
        except Exception as e:
            logger.error(f"Failed to mark message as processed: {e}")
            raise RedisConnectionError(f"Mark processed failed: {e}")

    def is_duplicate(self, message_id: str) -> bool:
        """
        Check if message is a duplicate (already processed).
        Alias for is_processed() for clearer API.

        Args:
            message_id: Unique message identifier

        Returns:
            True if duplicate, False if new message
        """
        return self.is_processed(message_id)

    def get_processed_count(self) -> int:
        """
        Get total count of processed messages.

        Returns:
            Number of processed messages in the set

        Raises:
            RedisConnectionError: If Redis operation fails
        """
        try:
            count = self.redis.client.scard(self._get_key())
            logger.debug(f"Processed messages count: {count}")
            return int(count)  # type: ignore
        except Exception as e:
            logger.error(f"Failed to get processed count: {e}")
            raise RedisConnectionError(f"Get count failed: {e}")

    def clear_processed(self) -> bool:
        """
        Clear all processed message tracking (use with caution).
        Useful for testing or maintenance.

        Returns:
            True if set was deleted, False if set didn't exist

        Raises:
            RedisConnectionError: If Redis operation fails
        """
        try:
            result = self.redis.client.delete(self._get_key())
            logger.warning("Cleared all processed message tracking")
            return bool(result > 0)  # type: ignore
        except Exception as e:
            logger.error(f"Failed to clear processed messages: {e}")
            raise RedisConnectionError(f"Clear processed failed: {e}")


def create_idempotency_manager_from_config(
    redis_client: RedisClient,
    ttl_hours: Optional[int] = 168  # 7 days default
) -> IdempotencyManager:
    """
    Factory function to create IdempotencyManager from configuration.

    Args:
        redis_client: Redis client instance
        ttl_hours: TTL in hours (default: 168 = 7 days)

    Returns:
        Configured IdempotencyManager instance
    """
    return IdempotencyManager(
        redis_client=redis_client,
        key_prefix="processed_messages",
        ttl_hours=ttl_hours
    )
