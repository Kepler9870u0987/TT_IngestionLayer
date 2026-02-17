"""
Exponential backoff manager for retry logic with failure tracking.
Manages retry attempts with increasing delays.
"""
from typing import Dict, Optional
from datetime import datetime, timedelta
import time

from src.common.logging_config import get_logger

logger = get_logger(__name__)


class BackoffManager:
    """
    Manages exponential backoff for message retries.
    Tracks retry attempts and calculates appropriate delays.

    .. note:: **Known limitation** – Retry state is held in-memory only.
       If the worker process restarts, all retry tracking is lost and
       messages resume from attempt 0.  This is acceptable because the
       DLQ safety-net (via ``OrphanedMessageRecovery`` and XPENDING
       delivery counts) prevents infinite retries at the Redis level.
    """

    def __init__(
        self,
        initial_delay: float = 1.0,
        max_delay: float = 300.0,
        multiplier: float = 2.0,
        max_retries: int = 5
    ):
        """
        Initialize backoff manager.

        Args:
            initial_delay: Initial delay in seconds (default: 1.0)
            max_delay: Maximum delay cap in seconds (default: 300 = 5 minutes)
            multiplier: Backoff multiplier (default: 2.0)
            max_retries: Maximum retry attempts (default: 5)
        """
        self.initial_delay = initial_delay
        self.max_delay = max_delay
        self.multiplier = multiplier
        self.max_retries = max_retries
        
        # Track retry attempts per message
        self._retry_counts: Dict[str, int] = {}
        self._next_retry_time: Dict[str, datetime] = {}
        
        logger.info(
            f"BackoffManager initialized: initial={initial_delay}s, "
            f"max={max_delay}s, multiplier={multiplier}, max_retries={max_retries}"
        )

    def calculate_delay(self, attempt: int) -> float:
        """
        Calculate delay for a given retry attempt using exponential backoff.

        Args:
            attempt: Retry attempt number (0-indexed)

        Returns:
            Delay in seconds (capped at max_delay)
        """
        delay = min(
            self.initial_delay * (self.multiplier ** attempt),
            self.max_delay
        )
        return delay

    def should_retry(self, message_id: str) -> bool:
        """
        Check if a message should be retried.

        Args:
            message_id: Message identifier

        Returns:
            True if retry should be attempted, False if max retries exceeded
        """
        retry_count = self._retry_counts.get(message_id, 0)
        
        # Check if max retries exceeded
        if retry_count >= self.max_retries:
            logger.warning(
                f"Max retries ({self.max_retries}) exceeded for message: {message_id}"
            )
            return False
        
        # Check if enough time has passed for retry
        next_retry = self._next_retry_time.get(message_id)
        if next_retry and datetime.now() < next_retry:
            logger.debug(
                f"Too early to retry message {message_id}, "
                f"next retry at {next_retry}"
            )
            return False
        
        return True

    def record_failure(self, message_id: str) -> int:
        """
        Record a processing failure and calculate next retry time.

        Args:
            message_id: Message identifier

        Returns:
            Current retry count for the message
        """
        # Increment retry count
        retry_count = self._retry_counts.get(message_id, 0)
        retry_count += 1
        self._retry_counts[message_id] = retry_count
        
        # Calculate and store next retry time
        delay = self.calculate_delay(retry_count - 1)
        next_retry = datetime.now() + timedelta(seconds=delay)
        self._next_retry_time[message_id] = next_retry
        
        logger.info(
            f"Recorded failure for {message_id}: "
            f"attempt {retry_count}/{self.max_retries}, "
            f"next retry in {delay:.1f}s at {next_retry}"
        )
        
        return retry_count

    def record_success(self, message_id: str):
        """
        Record successful processing and clear retry tracking.

        Args:
            message_id: Message identifier
        """
        if message_id in self._retry_counts:
            retry_count = self._retry_counts.pop(message_id)
            logger.info(
                f"Message {message_id} succeeded after {retry_count} retries"
            )
        if message_id in self._next_retry_time:
            self._next_retry_time.pop(message_id)

    def get_retry_count(self, message_id: str) -> int:
        """
        Get current retry count for a message.

        Args:
            message_id: Message identifier

        Returns:
            Number of retry attempts (0 if none)
        """
        return self._retry_counts.get(message_id, 0)

    def get_next_retry_time(self, message_id: str) -> Optional[datetime]:
        """
        Get next scheduled retry time for a message.

        Args:
            message_id: Message identifier

        Returns:
            Next retry datetime, or None if not scheduled
        """
        return self._next_retry_time.get(message_id)

    def wait_for_retry(self, message_id: str) -> float:
        """
        Block until the message is ready for retry.

        Args:
            message_id: Message identifier

        Returns:
            Actual wait time in seconds
        """
        next_retry = self._next_retry_time.get(message_id)
        if not next_retry:
            return 0.0
        
        now = datetime.now()
        if now >= next_retry:
            return 0.0
        
        wait_time = (next_retry - now).total_seconds()
        logger.info(f"Waiting {wait_time:.1f}s before retry of {message_id}")
        time.sleep(wait_time)
        return wait_time

    def has_exceeded_max_retries(self, message_id: str) -> bool:
        """
        Check if message has exceeded maximum retry attempts.

        Args:
            message_id: Message identifier

        Returns:
            True if max retries exceeded, False otherwise
        """
        return self._retry_counts.get(message_id, 0) >= self.max_retries

    def cleanup_old_entries(self, age_hours: int = 24):
        """
        Clean up tracking for messages that haven't been retried recently.

        Args:
            age_hours: Remove entries older than this many hours
        """
        cutoff = datetime.now() - timedelta(hours=age_hours)
        old_messages = [
            msg_id for msg_id, retry_time in self._next_retry_time.items()
            if retry_time < cutoff
        ]
        
        for msg_id in old_messages:
            self._retry_counts.pop(msg_id, None)
            self._next_retry_time.pop(msg_id, None)
        
        if old_messages:
            logger.info(f"Cleaned up {len(old_messages)} old retry entries")


def create_backoff_manager_from_config(
    initial_delay: float = 2.0,
    max_delay: float = 300.0,
    multiplier: float = 2.0,
    max_retries: int = 5
) -> BackoffManager:
    """
    Factory function to create BackoffManager from configuration.

    Args:
        initial_delay: Initial delay in seconds
        max_delay: Maximum delay in seconds
        multiplier: Backoff multiplier
        max_retries: Maximum retry attempts

    Returns:
        Configured BackoffManager instance
    """
    return BackoffManager(
        initial_delay=initial_delay,
        max_delay=max_delay,
        multiplier=multiplier,
        max_retries=max_retries
    )
