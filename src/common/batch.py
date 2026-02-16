"""
Performance optimization utilities for Redis Streams operations.

Provides batch operations using Redis pipelines for improved throughput:
    - BatchProducer: Batch XADD with pipeline
    - BatchAcknowledger: Batch XACK with pipeline
    - Batch helpers for reducing round-trips
"""
from typing import Dict, Any, List, Optional, Tuple

from src.common.redis_client import RedisClient
from src.common.logging_config import get_logger

logger = get_logger(__name__)


class BatchProducer:
    """
    Batch XADD operations using Redis pipelines.
    Reduces network round-trips for high-throughput producing.

    Usage:
        batch = BatchProducer(redis, "email_stream", batch_size=50)
        for email in emails:
            batch.add(email)
        results = batch.flush()  # Sends all at once
    """

    def __init__(
        self,
        redis_client: RedisClient,
        stream_name: str,
        batch_size: int = 50,
        maxlen: Optional[int] = None,
        approximate: bool = True
    ):
        """
        Initialize batch producer.

        Args:
            redis_client: Redis client instance
            stream_name: Target stream name
            batch_size: Auto-flush after this many messages
            maxlen: Optional max stream length
            approximate: Use approximate trimming
        """
        self.redis = redis_client
        self.stream_name = stream_name
        self.batch_size = batch_size
        self.maxlen = maxlen
        self.approximate = approximate
        self._buffer: List[Dict[str, Any]] = []

        # Stats
        self.total_sent = 0
        self.total_batches = 0

    def add(self, fields: Dict[str, Any]) -> Optional[List[str]]:
        """
        Add a message to the buffer. Auto-flushes when batch_size is reached.

        Args:
            fields: Message fields

        Returns:
            List of message IDs if auto-flushed, None otherwise
        """
        self._buffer.append(fields)
        if len(self._buffer) >= self.batch_size:
            return self.flush()
        return None

    def flush(self) -> List[str]:
        """
        Send all buffered messages via pipeline.

        Returns:
            List of message IDs for sent messages
        """
        if not self._buffer:
            return []

        pipe = self.redis.pipeline()
        for fields in self._buffer:
            pipe.xadd(
                self.stream_name,
                fields,
                maxlen=self.maxlen,
                approximate=self.approximate
            )

        try:
            results = pipe.execute()
            msg_ids = [str(r) for r in results if r]
            count = len(msg_ids)

            self.total_sent += count
            self.total_batches += 1

            logger.debug(
                f"BatchProducer: flushed {count} messages "
                f"(batch #{self.total_batches})"
            )

            self._buffer.clear()
            return msg_ids

        except Exception as e:
            logger.error(f"BatchProducer flush failed: {e}")
            # Keep buffer for retry
            raise

    @property
    def pending_count(self) -> int:
        """Number of messages in buffer waiting to be sent."""
        return len(self._buffer)

    def get_stats(self) -> Dict[str, Any]:
        """Get batch producer statistics."""
        return {
            "total_sent": self.total_sent,
            "total_batches": self.total_batches,
            "avg_batch_size": (
                self.total_sent / self.total_batches
                if self.total_batches > 0 else 0
            ),
            "pending": self.pending_count
        }


class BatchAcknowledger:
    """
    Batch XACK operations using Redis pipelines.
    Reduces round-trips for acknowledging multiple messages.

    Usage:
        acker = BatchAcknowledger(redis, "email_stream", "workers")
        for msg_id in processed_ids:
            acker.add(msg_id)
        count = acker.flush()
    """

    def __init__(
        self,
        redis_client: RedisClient,
        stream_name: str,
        consumer_group: str,
        batch_size: int = 50
    ):
        """
        Initialize batch acknowledger.

        Args:
            redis_client: Redis client instance
            stream_name: Stream name
            consumer_group: Consumer group name
            batch_size: Auto-flush after this many ACKs
        """
        self.redis = redis_client
        self.stream_name = stream_name
        self.consumer_group = consumer_group
        self.batch_size = batch_size
        self._buffer: List[str] = []

        # Stats
        self.total_acked = 0
        self.total_batches = 0

    def add(self, message_id: str) -> Optional[int]:
        """
        Add a message ID for acknowledgment.

        Args:
            message_id: Message ID to acknowledge

        Returns:
            Total ACKed if auto-flushed, None otherwise
        """
        self._buffer.append(message_id)
        if len(self._buffer) >= self.batch_size:
            return self.flush()
        return None

    def flush(self) -> int:
        """
        Send all buffered ACKs via pipeline.

        Returns:
            Number of messages acknowledged
        """
        if not self._buffer:
            return 0

        pipe = self.redis.pipeline()
        for msg_id in self._buffer:
            pipe.xack(self.stream_name, self.consumer_group, msg_id)

        try:
            results = pipe.execute()
            count = sum(1 for r in results if r)

            self.total_acked += count
            self.total_batches += 1

            logger.debug(
                f"BatchAcknowledger: ACKed {count} messages "
                f"(batch #{self.total_batches})"
            )

            self._buffer.clear()
            return count

        except Exception as e:
            logger.error(f"BatchAcknowledger flush failed: {e}")
            raise

    @property
    def pending_count(self) -> int:
        """Number of IDs waiting to be ACKed."""
        return len(self._buffer)

    def get_stats(self) -> Dict[str, Any]:
        """Get acknowledger statistics."""
        return {
            "total_acked": self.total_acked,
            "total_batches": self.total_batches,
            "avg_batch_size": (
                self.total_acked / self.total_batches
                if self.total_batches > 0 else 0
            ),
            "pending": self.pending_count
        }
