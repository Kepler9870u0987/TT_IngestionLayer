"""
Edge case handling and recovery for the worker.

Handles:
    - Orphaned messages (XPENDING/XCLAIM): messages stuck in pending
      state because a consumer crashed before acknowledging them.
    - Connection watchdog: monitors Redis/IMAP health and triggers
      reconnection when issues are detected.
    - UIDVALIDITY change detection and state reset.
"""
import time
import threading
from typing import Optional, Dict, Any, List, Tuple

from src.common.redis_client import RedisClient
from src.common.logging_config import get_logger
from src.common.circuit_breaker import CircuitBreakers, CircuitBreakerError

logger = get_logger(__name__)


class OrphanedMessageRecovery:
    """
    Recovers orphaned messages from the pending entries list (PEL).

    When a consumer crashes or disconnects without ACKing messages,
    those messages remain in the PEL indefinitely. This class detects
    such messages and re-claims them for processing.

    Usage:
        recovery = OrphanedMessageRecovery(
            redis, stream="emails", group="workers",
            consumer="worker_01", min_idle_ms=300000
        )
        # On startup or periodically:
        claimed = recovery.claim_orphaned_messages()
        for msg_id, data in claimed:
            process(msg_id, data)
    """

    def __init__(
        self,
        redis_client: RedisClient,
        stream_name: str,
        consumer_group: str,
        consumer_name: str,
        min_idle_ms: int = 300_000,
        max_claim_count: int = 50,
        max_delivery_count: int = 10
    ):
        """
        Initialize orphaned message recovery.

        Args:
            redis_client: Redis client instance
            stream_name: Stream to recover from
            consumer_group: Consumer group name
            consumer_name: This consumer's name (claimer)
            min_idle_ms: Minimum idle time before claiming (default 5min)
            max_claim_count: Max messages to claim per sweep
            max_delivery_count: Max deliveries before sending to DLQ
        """
        self.redis = redis_client
        self.stream_name = stream_name
        self.consumer_group = consumer_group
        self.consumer_name = consumer_name
        self.min_idle_ms = min_idle_ms
        self.max_claim_count = max_claim_count
        self.max_delivery_count = max_delivery_count

        # Stats
        self.total_claimed = 0
        self.total_expired = 0

        logger.info(
            f"OrphanedMessageRecovery initialized: stream={stream_name}, "
            f"min_idle={min_idle_ms}ms, max_claim={max_claim_count}"
        )

    def get_pending_messages(self) -> List[Dict[str, Any]]:
        """
        Get list of pending messages that have been idle too long.

        Returns:
            List of pending message info dicts with keys:
            message_id, consumer, time_since_delivered, times_delivered
        """
        try:
            pending = self.redis.xpending_range(
                stream=self.stream_name,
                groupname=self.consumer_group,
                count=self.max_claim_count
            )
            # Filter by idle time
            orphaned = [
                msg for msg in pending
                if msg.get("time_since_delivered", 0) >= self.min_idle_ms
            ]
            if orphaned:
                logger.info(
                    f"Found {len(orphaned)} orphaned messages "
                    f"(idle >= {self.min_idle_ms}ms)"
                )
            return orphaned
        except Exception as e:
            logger.error(f"Failed to get pending messages: {e}")
            return []

    def claim_orphaned_messages(
        self
    ) -> Tuple[List[Tuple[str, Dict[str, Any]]], List[str]]:
        """
        Claim orphaned messages for this consumer.

        Returns:
            Tuple of:
            - List of (message_id, data) for messages to process
            - List of message_ids that exceeded max delivery count (for DLQ)
        """
        pending = self.get_pending_messages()
        if not pending:
            return [], []

        # Separate messages by delivery count
        to_claim = []
        expired = []

        for msg in pending:
            msg_id = msg.get("message_id", "")
            deliveries = msg.get("times_delivered", 0)

            if deliveries >= self.max_delivery_count:
                expired.append(msg_id)
                self.total_expired += 1
                logger.warning(
                    f"Message {msg_id} exceeded max deliveries "
                    f"({deliveries}/{self.max_delivery_count}), "
                    f"marking for DLQ"
                )
            else:
                to_claim.append(msg_id)

        # Claim messages
        claimed = []
        if to_claim:
            try:
                claimed = self.redis.xclaim(
                    stream=self.stream_name,
                    groupname=self.consumer_group,
                    consumername=self.consumer_name,
                    min_idle_time=self.min_idle_ms,
                    message_ids=to_claim
                )
                self.total_claimed += len(claimed)
                logger.info(
                    f"Claimed {len(claimed)} orphaned messages for {self.consumer_name}"
                )
            except Exception as e:
                logger.error(f"Failed to claim messages: {e}")

        return claimed, expired

    def get_stats(self) -> Dict[str, Any]:
        """Get recovery statistics."""
        return {
            "total_claimed": self.total_claimed,
            "total_expired": self.total_expired
        }


class ConnectionWatchdog:
    """
    Monitors connection health and triggers reconnection.
    Runs periodic health checks in a background thread.

    Usage:
        watchdog = ConnectionWatchdog(check_interval=30)
        watchdog.add_check("redis", redis_client.ping)
        watchdog.start()
        # ...
        watchdog.stop()
    """

    def __init__(
        self,
        check_interval: float = 30.0,
        max_consecutive_failures: int = 3
    ):
        """
        Initialize connection watchdog.

        Args:
            check_interval: Seconds between health checks
            max_consecutive_failures: Failures before triggering reconnect
        """
        self.check_interval = check_interval
        self.max_consecutive_failures = max_consecutive_failures

        self._checks: Dict[str, Dict[str, Any]] = {}
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._lock = threading.Lock()

        logger.info(
            f"ConnectionWatchdog initialized: "
            f"interval={check_interval}s, "
            f"max_failures={max_consecutive_failures}"
        )

    def add_check(
        self,
        name: str,
        check_fn,
        reconnect_fn=None
    ) -> None:
        """
        Add a connection check.

        Args:
            name: Check name (e.g., "redis", "imap")
            check_fn: Function returning True if healthy
            reconnect_fn: Function to call on failure (optional)
        """
        self._checks[name] = {
            "check_fn": check_fn,
            "reconnect_fn": reconnect_fn,
            "consecutive_failures": 0,
            "last_check": None,
            "last_success": None,
            "healthy": True
        }
        logger.debug(f"Added watchdog check: {name}")

    def start(self) -> None:
        """Start the watchdog thread."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(
            target=self._run_loop,
            name="connection-watchdog",
            daemon=True
        )
        self._thread.start()
        logger.info("ConnectionWatchdog started")

    def stop(self) -> None:
        """Stop the watchdog thread."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=5.0)
        logger.info("ConnectionWatchdog stopped")

    def _run_loop(self) -> None:
        """Main watchdog loop."""
        while self._running:
            self._check_all()
            # Sleep in small increments to allow quick shutdown
            for _ in range(int(self.check_interval)):
                if not self._running:
                    break
                time.sleep(1)

    def _check_all(self) -> None:
        """Run all registered checks."""
        for name, check_info in self._checks.items():
            self._run_check(name, check_info)

    def _run_check(self, name: str, check_info: Dict[str, Any]) -> None:
        """Run a single health check."""
        check_info["last_check"] = time.time()

        try:
            result = check_info["check_fn"]()
            if result:
                check_info["consecutive_failures"] = 0
                check_info["last_success"] = time.time()
                if not check_info["healthy"]:
                    check_info["healthy"] = True
                    logger.info(f"Watchdog: {name} connection restored")

                    # Update circuit breaker if exists
                    try:
                        cb = CircuitBreakers.get(name)
                        cb.record_success()
                    except Exception:
                        pass
            else:
                self._handle_failure(name, check_info, "Check returned False")

        except Exception as e:
            self._handle_failure(name, check_info, str(e))

    def _handle_failure(
        self, name: str, check_info: Dict[str, Any], error: str
    ) -> None:
        """Handle a failed health check."""
        check_info["consecutive_failures"] += 1
        failures = check_info["consecutive_failures"]

        logger.warning(
            f"Watchdog: {name} check failed ({failures}/"
            f"{self.max_consecutive_failures}): {error}"
        )

        # Update circuit breaker
        try:
            cb = CircuitBreakers.get(name)
            cb.record_failure()
        except Exception:
            pass

        if failures >= self.max_consecutive_failures:
            check_info["healthy"] = False
            logger.error(
                f"Watchdog: {name} marked unhealthy after "
                f"{failures} consecutive failures"
            )

            # Trigger reconnection if handler provided
            reconnect_fn = check_info.get("reconnect_fn")
            if reconnect_fn:
                try:
                    logger.info(f"Watchdog: Attempting {name} reconnection...")
                    reconnect_fn()
                    logger.info(f"Watchdog: {name} reconnection triggered")
                except Exception as e:
                    logger.error(f"Watchdog: {name} reconnection failed: {e}")

    def get_status(self) -> Dict[str, Any]:
        """Get watchdog status for all checks."""
        with self._lock:
            return {
                name: {
                    "healthy": info["healthy"],
                    "consecutive_failures": info["consecutive_failures"],
                    "last_check": info["last_check"],
                    "last_success": info["last_success"]
                }
                for name, info in self._checks.items()
            }

    @property
    def all_healthy(self) -> bool:
        """Check if all monitored connections are healthy."""
        return all(
            info["healthy"] for info in self._checks.values()
        )
