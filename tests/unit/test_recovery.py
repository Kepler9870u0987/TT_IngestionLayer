"""
Unit tests for OrphanedMessageRecovery and ConnectionWatchdog.
"""
import time
import unittest
from unittest.mock import MagicMock, patch, PropertyMock

from src.worker.recovery import OrphanedMessageRecovery, ConnectionWatchdog
from src.common.circuit_breaker import CircuitBreakers


class TestOrphanedMessageRecovery(unittest.TestCase):
    """Tests for OrphanedMessageRecovery."""

    def setUp(self):
        self.redis = MagicMock()
        self.recovery = OrphanedMessageRecovery(
            redis_client=self.redis,
            stream_name="test_stream",
            consumer_group="test_group",
            consumer_name="test_consumer",
            min_idle_ms=5000,
            max_claim_count=10,
            max_delivery_count=5
        )

    def test_no_pending_messages(self):
        self.redis.xpending_range.return_value = []
        claimed, expired = self.recovery.claim_orphaned_messages()
        self.assertEqual(claimed, [])
        self.assertEqual(expired, [])

    def test_claims_idle_messages(self):
        self.redis.xpending_range.return_value = [
            {
                "message_id": "msg1",
                "consumer": "dead_worker",
                "time_since_delivered": 10000,
                "times_delivered": 2
            }
        ]
        self.redis.xclaim.return_value = [("msg1", {"data": "test"})]

        claimed, expired = self.recovery.claim_orphaned_messages()

        self.assertEqual(len(claimed), 1)
        self.assertEqual(claimed[0][0], "msg1")
        self.assertEqual(expired, [])
        self.assertEqual(self.recovery.total_claimed, 1)

    def test_expires_over_delivery_count(self):
        self.redis.xpending_range.return_value = [
            {
                "message_id": "msg1",
                "consumer": "dead_worker",
                "time_since_delivered": 10000,
                "times_delivered": 10
            }
        ]

        claimed, expired = self.recovery.claim_orphaned_messages()

        self.assertEqual(claimed, [])
        self.assertEqual(expired, ["msg1"])
        self.assertEqual(self.recovery.total_expired, 1)

    def test_mixed_claim_and_expire(self):
        self.redis.xpending_range.return_value = [
            {
                "message_id": "ok",
                "consumer": "dead",
                "time_since_delivered": 10000,
                "times_delivered": 1
            },
            {
                "message_id": "expired",
                "consumer": "dead",
                "time_since_delivered": 10000,
                "times_delivered": 10
            }
        ]
        self.redis.xclaim.return_value = [("ok", {"data": "test"})]

        claimed, expired = self.recovery.claim_orphaned_messages()

        self.assertEqual(len(claimed), 1)
        self.assertEqual(expired, ["expired"])

    def test_filters_by_idle_time(self):
        self.redis.xpending_range.return_value = [
            {
                "message_id": "recent",
                "consumer": "alive",
                "time_since_delivered": 1000,  # below min_idle
                "times_delivered": 1
            }
        ]

        claimed, expired = self.recovery.claim_orphaned_messages()
        self.assertEqual(claimed, [])
        self.assertEqual(expired, [])

    def test_get_stats(self):
        stats = self.recovery.get_stats()
        self.assertEqual(stats["total_claimed"], 0)
        self.assertEqual(stats["total_expired"], 0)

    def test_xclaim_failure_handled(self):
        self.redis.xpending_range.return_value = [
            {
                "message_id": "msg1",
                "consumer": "dead",
                "time_since_delivered": 10000,
                "times_delivered": 1
            }
        ]
        self.redis.xclaim.side_effect = Exception("Connection lost")

        claimed, expired = self.recovery.claim_orphaned_messages()
        self.assertEqual(claimed, [])

    def test_xpending_failure_handled(self):
        self.redis.xpending_range.side_effect = Exception("Error")
        pending = self.recovery.get_pending_messages()
        self.assertEqual(pending, [])


class TestConnectionWatchdog(unittest.TestCase):
    """Tests for ConnectionWatchdog."""

    def setUp(self):
        CircuitBreakers.reset_all()
        self.watchdog = ConnectionWatchdog(
            check_interval=0.5,
            max_consecutive_failures=2
        )

    def tearDown(self):
        self.watchdog.stop()
        CircuitBreakers.reset_all()

    def test_add_check(self):
        self.watchdog.add_check("redis", lambda: True)
        status = self.watchdog.get_status()
        self.assertIn("redis", status)

    def test_healthy_check(self):
        self.watchdog.add_check("redis", lambda: True)
        self.watchdog._check_all()

        status = self.watchdog.get_status()
        self.assertTrue(status["redis"]["healthy"])
        self.assertEqual(status["redis"]["consecutive_failures"], 0)

    def test_failing_check(self):
        self.watchdog.add_check("redis", lambda: False)
        self.watchdog._check_all()

        status = self.watchdog.get_status()
        self.assertEqual(status["redis"]["consecutive_failures"], 1)

    def test_marked_unhealthy_after_threshold(self):
        self.watchdog.add_check("redis", lambda: False)
        self.watchdog._check_all()
        self.watchdog._check_all()

        status = self.watchdog.get_status()
        self.assertFalse(status["redis"]["healthy"])

    def test_reconnect_called(self):
        reconnect = MagicMock()
        self.watchdog.add_check(
            "redis", lambda: False, reconnect_fn=reconnect
        )
        self.watchdog._check_all()
        self.watchdog._check_all()

        reconnect.assert_called_once()

    def test_all_healthy_property(self):
        self.watchdog.add_check("redis", lambda: True)
        self.watchdog._check_all()
        self.assertTrue(self.watchdog.all_healthy)

    def test_exception_counts_as_failure(self):
        def failing():
            raise ConnectionError("boom")

        self.watchdog.add_check("redis", failing)
        self.watchdog._check_all()

        status = self.watchdog.get_status()
        self.assertEqual(status["redis"]["consecutive_failures"], 1)

    def test_recovery_resets_healthy(self):
        counter = {"val": 0}
        def toggle():
            counter["val"] += 1
            return counter["val"] > 2

        self.watchdog.add_check("redis", toggle)
        self.watchdog._check_all()  # fail
        self.watchdog._check_all()  # fail, marked unhealthy
        self.watchdog._check_all()  # success, restored

        status = self.watchdog.get_status()
        self.assertTrue(status["redis"]["healthy"])

    def test_start_stop(self):
        self.watchdog.add_check("redis", lambda: True)
        self.watchdog.start()
        self.assertTrue(self.watchdog._running)
        time.sleep(0.2)
        self.watchdog.stop()
        self.assertFalse(self.watchdog._running)


if __name__ == "__main__":
    unittest.main()
