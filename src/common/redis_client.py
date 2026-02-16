"""
Redis client wrapper with connection pooling and retry logic.
Provides high-level abstractions for Redis Streams operations.
"""
import redis
from redis.connection import ConnectionPool
from redis.exceptions import RedisError, ConnectionError as RedisConnectionError
from typing import Optional, Dict, List, Any, Tuple
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type
)

from src.common.logging_config import get_logger
from src.common.exceptions import RedisConnectionError as CustomRedisConnectionError

logger = get_logger(__name__)


class RedisClient:
    """
    Redis client wrapper with connection pooling and retry logic.
    Thread-safe implementation using connection pooling.
    """

    def __init__(
        self,
        host: str = "localhost",
        port: int = 6379,
        password: Optional[str] = None,
        db: int = 0,
        max_connections: int = 20
    ):
        """
        Initialize Redis client with connection pool.

        Args:
            host: Redis server host
            port: Redis server port
            password: Optional password for authentication
            db: Database number
            max_connections: Maximum connections in pool
        """
        self.pool = ConnectionPool(
            host=host,
            port=port,
            password=password,
            db=db,
            max_connections=max_connections,
            socket_keepalive=True,
            socket_keepalive_options={},
            decode_responses=True,
            socket_connect_timeout=5,
            socket_timeout=5
        )
        self.client = redis.Redis(connection_pool=self.pool)
        logger.info(f"Redis client initialized: {host}:{port}, db={db}")

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type((RedisError, RedisConnectionError))
    )
    def ping(self) -> bool:
        """
        Health check - test Redis connectivity.

        Returns:
            True if connection successful

        Raises:
            CustomRedisConnectionError: If connection fails after retries
        """
        try:
            return self.client.ping()
        except Exception as e:
            logger.error(f"Redis ping failed: {e}")
            raise CustomRedisConnectionError(f"Redis connection failed: {e}")

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type((RedisError,))
    )
    def xadd(
        self,
        stream: str,
        fields: Dict[str, Any],
        maxlen: Optional[int] = None,
        approximate: bool = True
    ) -> str:
        """
        Add message to Redis Stream with optional max length.

        Args:
            stream: Stream name
            fields: Dictionary of field-value pairs
            maxlen: Optional maximum stream length (trimming)
            approximate: Use approximate trimming (~) for better performance

        Returns:
            Message ID (e.g., "1234567890123-0")

        Raises:
            CustomRedisConnectionError: If operation fails
        """
        try:
            msg_id = self.client.xadd(
                stream,
                fields,
                maxlen=maxlen,
                approximate=approximate
            )
            logger.debug(f"XADD to {stream}: {msg_id}")
            return msg_id
        except Exception as e:
            logger.error(f"XADD failed for stream {stream}: {e}")
            raise CustomRedisConnectionError(f"Failed to add message to stream: {e}")

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type((RedisError,))
    )
    def xreadgroup(
        self,
        groupname: str,
        consumername: str,
        streams: Dict[str, str],
        count: Optional[int] = None,
        block: Optional[int] = None
    ) -> List[Tuple[str, List[Tuple[str, Dict[str, Any]]]]]:
        """
        Read messages from stream using consumer group.

        Args:
            groupname: Consumer group name
            consumername: Consumer name within group
            streams: Dict of {stream_name: last_id} (use '>' for new messages)
            count: Maximum number of messages to return
            block: Block for N milliseconds if no messages (0 = forever)

        Returns:
            List of tuples: [(stream_name, [(msg_id, fields), ...]), ...]

        Raises:
            CustomRedisConnectionError: If operation fails
        """
        try:
            result = self.client.xreadgroup(
                groupname,
                consumername,
                streams,
                count=count,
                block=block
            )
            if result:
                logger.debug(f"XREADGROUP: {len(result)} streams, {sum(len(msgs) for _, msgs in result)} messages")
            return result or []
        except Exception as e:
            logger.error(f"XREADGROUP failed: {e}")
            raise CustomRedisConnectionError(f"Failed to read from consumer group: {e}")

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type((RedisError,))
    )
    def xack(self, stream: str, groupname: str, *ids: str) -> int:
        """
        Acknowledge messages in consumer group.

        Args:
            stream: Stream name
            groupname: Consumer group name
            ids: Message IDs to acknowledge

        Returns:
            Number of messages successfully acknowledged

        Raises:
            CustomRedisConnectionError: If operation fails
        """
        try:
            count = self.client.xack(stream, groupname, *ids)
            logger.debug(f"XACK {stream}/{groupname}: {count} messages")
            return count
        except Exception as e:
            logger.error(f"XACK failed: {e}")
            raise CustomRedisConnectionError(f"Failed to acknowledge messages: {e}")

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type((RedisError,))
    )
    def xgroup_create(
        self,
        stream: str,
        groupname: str,
        id: str = '0',
        mkstream: bool = True
    ) -> bool:
        """
        Create consumer group (idempotent).

        Args:
            stream: Stream name
            groupname: Consumer group name
            id: Start reading from this ID ('0' = beginning, '$' = end)
            mkstream: Create stream if it doesn't exist

        Returns:
            True if group created, False if already exists

        Raises:
            CustomRedisConnectionError: If operation fails (other than BUSYGROUP)
        """
        try:
            self.client.xgroup_create(stream, groupname, id=id, mkstream=mkstream)
            logger.info(f"Created consumer group: {stream}/{groupname}")
            return True
        except redis.ResponseError as e:
            if 'BUSYGROUP' in str(e):
                logger.debug(f"Consumer group already exists: {stream}/{groupname}")
                return False
            logger.error(f"XGROUP CREATE failed: {e}")
            raise CustomRedisConnectionError(f"Failed to create consumer group: {e}")

    def set(self, key: str, value: Any, ex: Optional[int] = None) -> bool:
        """
        Set key-value with optional expiration.

        Args:
            key: Key name
            value: Value to store
            ex: Expiration in seconds

        Returns:
            True if successful
        """
        try:
            return self.client.set(key, value, ex=ex)
        except Exception as e:
            logger.error(f"SET failed for key {key}: {e}")
            raise CustomRedisConnectionError(f"Failed to set key: {e}")

    def get(self, key: str) -> Optional[str]:
        """
        Get value by key.

        Args:
            key: Key name

        Returns:
            Value or None if key doesn't exist
        """
        try:
            return self.client.get(key)
        except Exception as e:
            logger.error(f"GET failed for key {key}: {e}")
            raise CustomRedisConnectionError(f"Failed to get key: {e}")

    def sadd(self, key: str, *values: Any) -> int:
        """
        Add values to set.

        Args:
            key: Set name
            values: Values to add

        Returns:
            Number of elements added
        """
        try:
            return self.client.sadd(key, *values)
        except Exception as e:
            logger.error(f"SADD failed for key {key}: {e}")
            raise CustomRedisConnectionError(f"Failed to add to set: {e}")

    def sismember(self, key: str, value: Any) -> bool:
        """
        Check if value is in set.

        Args:
            key: Set name
            value: Value to check

        Returns:
            True if value exists in set
        """
        try:
            return self.client.sismember(key, value)
        except Exception as e:
            logger.error(f"SISMEMBER failed for key {key}: {e}")
            raise CustomRedisConnectionError(f"Failed to check set membership: {e}")

    def expire(self, key: str, seconds: int) -> bool:
        """
        Set key expiration.

        Args:
            key: Key name
            seconds: Expiration time in seconds

        Returns:
            True if expiration set
        """
        try:
            return self.client.expire(key, seconds)
        except Exception as e:
            logger.error(f"EXPIRE failed for key {key}: {e}")
            raise CustomRedisConnectionError(f"Failed to set expiration: {e}")

    def xlen(self, stream: str) -> int:
        """
        Get stream length.

        Args:
            stream: Stream name

        Returns:
            Number of messages in stream
        """
        try:
            return self.client.xlen(stream)
        except Exception as e:
            logger.error(f"XLEN failed for stream {stream}: {e}")
            return 0

    def xpending_range(
        self,
        stream: str,
        groupname: str,
        min_id: str = "-",
        max_id: str = "+",
        count: int = 100,
        consumername: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Get pending messages details (messages read but not acknowledged).

        Args:
            stream: Stream name
            groupname: Consumer group name
            min_id: Minimum message ID
            max_id: Maximum message ID
            count: Maximum results to return
            consumername: Filter by consumer (optional)

        Returns:
            List of pending message entries with id, consumer, idle time, delivery count
        """
        try:
            result = self.client.xpending_range(
                stream, groupname,
                min=min_id, max=max_id, count=count,
                consumername=consumername
            )
            return result or []
        except Exception as e:
            logger.error(f"XPENDING RANGE failed: {e}")
            raise CustomRedisConnectionError(f"Failed to get pending messages: {e}")

    def xclaim(
        self,
        stream: str,
        groupname: str,
        consumername: str,
        min_idle_time: int,
        message_ids: List[str]
    ) -> List[Tuple[str, Dict[str, Any]]]:
        """
        Claim ownership of pending messages that have been idle too long.

        Args:
            stream: Stream name
            groupname: Consumer group name
            consumername: Consumer to claim messages for
            min_idle_time: Minimum idle time in milliseconds
            message_ids: Message IDs to claim

        Returns:
            List of (message_id, fields) tuples for claimed messages
        """
        try:
            result = self.client.xclaim(
                stream, groupname, consumername,
                min_idle_time, message_ids
            )
            if result:
                logger.info(f"XCLAIM: Claimed {len(result)} messages for {consumername}")
            return result or []
        except Exception as e:
            logger.error(f"XCLAIM failed: {e}")
            raise CustomRedisConnectionError(f"Failed to claim messages: {e}")

    def pipeline(self):
        """
        Create a Redis pipeline for batching commands.

        Returns:
            Redis pipeline object
        """
        return self.client.pipeline()

    def __enter__(self):
        """Context manager entry"""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit"""
        self.close()

    def close(self):
        """Close connection pool"""
        try:
            self.pool.disconnect()
            logger.info("Redis connection pool closed")
        except Exception as e:
            logger.warning(f"Error closing Redis connection pool: {e}")


# Factory function for easier usage with config
def create_redis_client_from_config(config) -> RedisClient:
    """
    Create Redis client from configuration object.

    Args:
        config: Configuration object with redis settings

    Returns:
        Configured RedisClient instance
    """
    return RedisClient(
        host=config.redis.host,
        port=config.redis.port,
        password=config.redis.password,
        db=config.redis.db
    )
