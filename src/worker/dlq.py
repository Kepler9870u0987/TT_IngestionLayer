"""
Dead Letter Queue (DLQ) manager for handling failed messages.
Routes messages that exceed retry limits to a separate stream for manual review.
"""
from typing import Dict, Any, Optional
from datetime import datetime
import json

from src.common.redis_client import RedisClient
from src.common.logging_config import get_logger
from src.common.exceptions import RedisConnectionError

logger = get_logger(__name__)


class DLQManager:
    """
    Manages Dead Letter Queue for failed messages.
    Routes messages that exceed retry limits to DLQ stream.
    """

    def __init__(
        self,
        redis_client: RedisClient,
        dlq_stream_name: str = "email_ingestion_dlq",
        max_length: int = 10000
    ):
        """
        Initialize DLQ manager.

        Args:
            redis_client: Redis client instance
            dlq_stream_name: Name of DLQ stream (default: "email_ingestion_dlq")
            max_length: Maximum DLQ stream length (default: 10000)
        """
        self.redis = redis_client
        self.dlq_stream_name = dlq_stream_name
        self.max_length = max_length
        logger.info(
            f"DLQManager initialized: stream={dlq_stream_name}, "
            f"maxlen={max_length}"
        )

    def send_to_dlq(
        self,
        message_id: str,
        original_data: Dict[str, Any],
        error: Exception,
        retry_count: int,
        metadata: Optional[Dict[str, Any]] = None
    ) -> str:
        """
        Send a failed message to the Dead Letter Queue.

        Args:
            message_id: Original message identifier
            original_data: Original message data from stream
            error: The exception that caused the failure
            retry_count: Number of retry attempts made
            metadata: Optional additional metadata

        Returns:
            DLQ message ID (stream entry ID)

        Raises:
            RedisConnectionError: If DLQ push fails
        """
        try:
            # Construct DLQ entry with failure details
            dlq_entry = {
                "original_message_id": message_id,
                "failed_at": datetime.now().isoformat(),
                "error_type": type(error).__name__,
                "error_message": str(error),
                "retry_count": str(retry_count),
                "original_data": json.dumps(original_data),
            }
            
            # Add optional metadata
            if metadata:
                dlq_entry["metadata"] = json.dumps(metadata)
            
            # Push to DLQ stream with maxlen trimming
            dlq_id = self.redis.xadd(
                stream=self.dlq_stream_name,
                fields=dlq_entry,
                maxlen=self.max_length,
                approximate=True
            )
            
            logger.error(
                f"Message sent to DLQ: {message_id} "
                f"(error: {type(error).__name__}, retries: {retry_count})"
            )
            logger.info(f"DLQ entry created: {dlq_id}")
            
            return dlq_id
            
        except Exception as e:
            logger.critical(f"Failed to send message to DLQ: {e}")
            raise RedisConnectionError(f"DLQ push failed: {e}")

    def get_dlq_length(self) -> int:
        """
        Get current length of DLQ stream.

        Returns:
            Number of messages in DLQ

        Raises:
            RedisConnectionError: If Redis operation fails
        """
        try:
            length = self.redis.client.xlen(self.dlq_stream_name)
            logger.debug(f"DLQ length: {length}")
            return length
        except Exception as e:
            logger.error(f"Failed to get DLQ length: {e}")
            raise RedisConnectionError(f"DLQ length check failed: {e}")

    def peek_dlq(self, count: int = 10) -> list:
        """
        Peek at the oldest messages in DLQ without removing them.

        Args:
            count: Number of messages to retrieve (default: 10)

        Returns:
            List of DLQ messages with their IDs

        Raises:
            RedisConnectionError: If Redis operation fails
        """
        try:
            messages = self.redis.client.xrange(
                self.dlq_stream_name,
                min="-",
                max="+",
                count=count
            )
            logger.debug(f"Peeked {len(messages)} messages from DLQ")
            return messages
        except Exception as e:
            logger.error(f"Failed to peek DLQ: {e}")
            raise RedisConnectionError(f"DLQ peek failed: {e}")

    def remove_from_dlq(self, dlq_entry_id: str) -> bool:
        """
        Remove a specific entry from DLQ (after manual resolution).

        Args:
            dlq_entry_id: Stream entry ID in DLQ

        Returns:
            True if removed, False if not found

        Raises:
            RedisConnectionError: If Redis operation fails
        """
        try:
            result = self.redis.client.xdel(self.dlq_stream_name, dlq_entry_id)
            if result > 0:
                logger.info(f"Removed entry from DLQ: {dlq_entry_id}")
                return True
            else:
                logger.warning(f"DLQ entry not found: {dlq_entry_id}")
                return False
        except Exception as e:
            logger.error(f"Failed to remove from DLQ: {e}")
            raise RedisConnectionError(f"DLQ removal failed: {e}")

    def reprocess_from_dlq(
        self,
        dlq_entry_id: str,
        target_stream: str = "email_ingestion_stream"
    ) -> Optional[str]:
        """
        Reprocess a message from DLQ by sending it back to main stream.

        Args:
            dlq_entry_id: Stream entry ID in DLQ
            target_stream: Target stream to reprocess into

        Returns:
            New stream entry ID if successful, None if DLQ entry not found

        Raises:
            RedisConnectionError: If Redis operations fail
        """
        try:
            # Fetch the DLQ entry
            entries = self.redis.client.xrange(
                self.dlq_stream_name,
                min=dlq_entry_id,
                max=dlq_entry_id,
                count=1
            )
            
            if not entries:
                logger.warning(f"DLQ entry not found for reprocessing: {dlq_entry_id}")
                return None
            
            entry_id, entry_data = entries[0]
            
            # Parse original data
            original_data = json.loads(entry_data.get("original_data", "{}"))
            
            # Add reprocessing metadata
            original_data["reprocessed_from_dlq"] = "true"
            original_data["reprocessed_at"] = datetime.now().isoformat()
            original_data["original_dlq_id"] = dlq_entry_id
            
            # Push back to main stream
            new_id = self.redis.xadd(
                stream=target_stream,
                fields=original_data
            )
            
            # Remove from DLQ
            self.remove_from_dlq(dlq_entry_id)
            
            logger.info(
                f"Reprocessed message from DLQ: {dlq_entry_id} -> {new_id}"
            )
            return new_id
            
        except Exception as e:
            logger.error(f"Failed to reprocess from DLQ: {e}")
            raise RedisConnectionError(f"DLQ reprocessing failed: {e}")

    def clear_dlq(self) -> int:
        """
        Clear entire DLQ (use with extreme caution).

        Returns:
            Number of entries removed

        Raises:
            RedisConnectionError: If Redis operation fails
        """
        try:
            # Get all entry IDs
            entries = self.redis.client.xrange(
                self.dlq_stream_name,
                min="-",
                max="+"
            )
            
            count = 0
            for entry_id, _ in entries:
                self.redis.client.xdel(self.dlq_stream_name, entry_id)
                count += 1
            
            logger.warning(f"Cleared DLQ: {count} entries removed")
            return count
            
        except Exception as e:
            logger.error(f"Failed to clear DLQ: {e}")
            raise RedisConnectionError(f"DLQ clear failed: {e}")


def create_dlq_manager_from_config(
    redis_client: RedisClient,
    dlq_stream_name: str = "email_ingestion_dlq",
    max_length: int = 10000
) -> DLQManager:
    """
    Factory function to create DLQManager from configuration.

    Args:
        redis_client: Redis client instance
        dlq_stream_name: DLQ stream name
        max_length: Maximum DLQ stream length

    Returns:
        Configured DLQManager instance
    """
    return DLQManager(
        redis_client=redis_client,
        dlq_stream_name=dlq_stream_name,
        max_length=max_length
    )
