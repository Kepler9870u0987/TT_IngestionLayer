"""
Producer state management using Redis.
Tracks last processed UID and UIDVALIDITY per mailbox.
"""
from typing import Optional, Dict, Any
from datetime import datetime, timezone

from src.common.redis_client import RedisClient
from src.common.logging_config import get_logger
from src.common.exceptions import StateManagementError

logger = get_logger(__name__)


class ProducerStateManager:
    """
    Manages producer state persistence in Redis.
    Tracks UID and UIDVALIDITY per mailbox for incremental fetching.
    """

    def __init__(self, redis_client: RedisClient, username: str):
        """
        Initialize state manager.

        Args:
            redis_client: Redis client instance
            username: Email username (for namespacing keys)
        """
        self.redis = redis_client
        self.username = username
        self.key_prefix = f"producer_state:{username}"

        logger.info(f"State manager initialized for {username}")

    def _make_key(self, mailbox: str, key_type: str) -> str:
        """
        Generate Redis key for state storage.

        Args:
            mailbox: Mailbox name
            key_type: Type of key (last_uid, uidvalidity, last_poll, etc.)

        Returns:
            Redis key string
        """
        return f"{self.key_prefix}:{mailbox}:{key_type}"

    def get_last_uid(self, mailbox: str) -> int:
        """
        Get last processed UID for mailbox.

        Args:
            mailbox: Mailbox name

        Returns:
            Last processed UID (0 if none)
        """
        try:
            key = self._make_key(mailbox, "last_uid")
            value = self.redis.get(key)

            if value:
                uid = int(value)
                logger.debug(f"Retrieved last UID for {mailbox}: {uid}")
                return uid

            logger.debug(f"No last UID found for {mailbox}, returning 0")
            return 0

        except Exception as e:
            logger.error(f"Failed to get last UID for {mailbox}: {e}")
            raise StateManagementError(f"Failed to get last UID: {e}")

    def set_last_uid(self, mailbox: str, uid: int):
        """
        Set last processed UID for mailbox.

        Args:
            mailbox: Mailbox name
            uid: UID to store

        Raises:
            StateManagementError: If storage fails
        """
        try:
            key = self._make_key(mailbox, "last_uid")
            self.redis.set(key, str(uid))

            logger.info(f"Stored last UID for {mailbox}: {uid}")

        except Exception as e:
            logger.error(f"Failed to set last UID for {mailbox}: {e}")
            raise StateManagementError(f"Failed to set last UID: {e}")

    def get_uidvalidity(self, mailbox: str) -> Optional[int]:
        """
        Get stored UIDVALIDITY for mailbox.

        Args:
            mailbox: Mailbox name

        Returns:
            UIDVALIDITY or None if not set
        """
        try:
            key = self._make_key(mailbox, "uidvalidity")
            value = self.redis.get(key)

            if value:
                uidvalidity = int(value)
                logger.debug(f"Retrieved UIDVALIDITY for {mailbox}: {uidvalidity}")
                return uidvalidity

            logger.debug(f"No UIDVALIDITY found for {mailbox}")
            return None

        except Exception as e:
            logger.error(f"Failed to get UIDVALIDITY for {mailbox}: {e}")
            raise StateManagementError(f"Failed to get UIDVALIDITY: {e}")

    def set_uidvalidity(self, mailbox: str, uidvalidity: int):
        """
        Set UIDVALIDITY for mailbox.

        Args:
            mailbox: Mailbox name
            uidvalidity: UIDVALIDITY value

        Raises:
            StateManagementError: If storage fails
        """
        try:
            key = self._make_key(mailbox, "uidvalidity")
            self.redis.set(key, str(uidvalidity))

            logger.info(f"Stored UIDVALIDITY for {mailbox}: {uidvalidity}")

        except Exception as e:
            logger.error(f"Failed to set UIDVALIDITY for {mailbox}: {e}")
            raise StateManagementError(f"Failed to set UIDVALIDITY: {e}")

    def check_uidvalidity_change(self, mailbox: str, current_uidvalidity: int) -> bool:
        """
        Check if UIDVALIDITY has changed (indicates mailbox reset).

        Args:
            mailbox: Mailbox name
            current_uidvalidity: Current UIDVALIDITY from IMAP

        Returns:
            True if UIDVALIDITY changed, False otherwise
        """
        stored_uidvalidity = self.get_uidvalidity(mailbox)

        if stored_uidvalidity is None:
            # First time, store it
            self.set_uidvalidity(mailbox, current_uidvalidity)
            return False

        if stored_uidvalidity != current_uidvalidity:
            logger.warning(
                f"UIDVALIDITY changed for {mailbox}: "
                f"{stored_uidvalidity} -> {current_uidvalidity}"
            )
            return True

        return False

    def reset_mailbox_state(self, mailbox: str):
        """
        Reset all state for a mailbox (used after UIDVALIDITY change).

        Args:
            mailbox: Mailbox name
        """
        try:
            logger.warning(f"Resetting state for mailbox: {mailbox}")

            # Reset last UID to 0
            self.set_last_uid(mailbox, 0)

            # Don't reset UIDVALIDITY - it will be updated on next poll

            logger.info(f"State reset complete for {mailbox}")

        except Exception as e:
            logger.error(f"Failed to reset state for {mailbox}: {e}")
            raise StateManagementError(f"Failed to reset mailbox state: {e}")

    def update_last_poll_time(self, mailbox: str):
        """
        Update last poll timestamp for mailbox.

        Args:
            mailbox: Mailbox name
        """
        try:
            key = self._make_key(mailbox, "last_poll")
            timestamp = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
            self.redis.set(key, timestamp)

            logger.debug(f"Updated last poll time for {mailbox}: {timestamp}")

        except Exception as e:
            logger.warning(f"Failed to update last poll time for {mailbox}: {e}")
            # Non-critical, don't raise

    def get_last_poll_time(self, mailbox: str) -> Optional[str]:
        """
        Get last poll timestamp for mailbox.

        Args:
            mailbox: Mailbox name

        Returns:
            ISO timestamp string or None
        """
        try:
            key = self._make_key(mailbox, "last_poll")
            return self.redis.get(key)

        except Exception as e:
            logger.warning(f"Failed to get last poll time for {mailbox}: {e}")
            return None

    def increment_email_count(self, mailbox: str, count: int = 1):
        """
        Increment total email count for mailbox.

        Args:
            mailbox: Mailbox name
            count: Number to increment by
        """
        try:
            key = self._make_key(mailbox, "total_emails")
            current = self.redis.get(key)
            total = int(current) if current else 0
            total += count
            self.redis.set(key, str(total))

            logger.debug(f"Email count for {mailbox}: {total}")

        except Exception as e:
            logger.warning(f"Failed to increment email count for {mailbox}: {e}")
            # Non-critical, don't raise

    def get_state_summary(self, mailbox: str) -> Dict[str, Any]:
        """
        Get summary of current state for mailbox.

        Args:
            mailbox: Mailbox name

        Returns:
            Dictionary with state information
        """
        try:
            return {
                "mailbox": mailbox,
                "last_uid": self.get_last_uid(mailbox),
                "uidvalidity": self.get_uidvalidity(mailbox),
                "last_poll": self.get_last_poll_time(mailbox),
                "total_emails": int(self.redis.get(self._make_key(mailbox, "total_emails")) or 0)
            }

        except Exception as e:
            logger.error(f"Failed to get state summary for {mailbox}: {e}")
            return {"error": str(e)}

    def atomic_update_state(
        self,
        mailbox: str,
        current_uidvalidity: int,
        new_last_uid: int
    ) -> bool:
        """
        Atomically update mailbox state after successful processing.

        Args:
            mailbox: Mailbox name
            current_uidvalidity: Current UIDVALIDITY
            new_last_uid: New last processed UID

        Returns:
            True if update successful

        Raises:
            StateManagementError: If UIDVALIDITY mismatch or update fails
        """
        try:
            # Check UIDVALIDITY first
            if self.check_uidvalidity_change(mailbox, current_uidvalidity):
                logger.error(
                    f"UIDVALIDITY mismatch during update for {mailbox}. "
                    "Mailbox may have been reset."
                )
                raise StateManagementError("UIDVALIDITY mismatch during state update")

            # Update UID
            self.set_last_uid(mailbox, new_last_uid)

            # Update UIDVALIDITY
            self.set_uidvalidity(mailbox, current_uidvalidity)

            # Update timestamps
            self.update_last_poll_time(mailbox)

            logger.info(
                f"State updated for {mailbox}: "
                f"UIDVALIDITY={current_uidvalidity}, last_UID={new_last_uid}"
            )

            return True

        except StateManagementError:
            raise
        except Exception as e:
            logger.error(f"Failed atomic state update for {mailbox}: {e}")
            raise StateManagementError(f"Failed to update state: {e}")


# Factory function
def create_state_manager_from_config(config, redis_client: RedisClient, username: str) -> ProducerStateManager:
    """
    Create ProducerStateManager from configuration.

    Args:
        config: Configuration object
        redis_client: Redis client instance
        username: Email username

    Returns:
        Configured ProducerStateManager
    """
    return ProducerStateManager(redis_client, username)
