"""
Unit tests for CircuitBreaker and CircuitBreakers registry.
"""
import time
import unittest
from unittest.mock import MagicMock

from src.common.circuit_breaker import (
    CircuitBreaker,
    CircuitBreakerError,
    CircuitBreakers,
    CircuitState
)


class TestCircuitBreaker(unittest.TestCase):
    """Tests for CircuitBreaker class."""

    def setUp(self):
        self.cb = CircuitBreaker(
            "test",
            failure_threshold=3,
            recovery_timeout=1.0,
            success_threshold=2
        )

    def test_initial_state_is_closed(self):
        self.assertEqual(self.cb.state, CircuitState.CLOSED)
        self.assertTrue(self.cb.is_closed)
        self.assertTrue(self.cb.allow_request())

    def test_opens_after_failure_threshold(self):
        for _ in range(3):
            self.cb.record_failure()

        self.assertEqual(self.cb.state, CircuitState.OPEN)
        self.assertTrue(self.cb.is_open)
        self.assertFalse(self.cb.allow_request())

    def test_stays_closed_below_threshold(self):
        self.cb.record_failure()
        self.cb.record_failure()
        self.assertTrue(self.cb.is_closed)
        self.assertTrue(self.cb.allow_request())

    def test_success_resets_failure_count(self):
        self.cb.record_failure()
        self.cb.record_failure()
        self.cb.record_success()
        # Should reset failure count
        self.cb.record_failure()
        self.cb.record_failure()
        # Still below threshold since reset
        self.assertTrue(self.cb.is_closed)

    def test_transitions_to_half_open(self):
        for _ in range(3):
            self.cb.record_failure()

        self.assertTrue(self.cb.is_open)

        # Wait for recovery timeout
        time.sleep(1.1)

        # Should transition to half-open
        self.assertEqual(self.cb.state, CircuitState.HALF_OPEN)
        self.assertTrue(self.cb.allow_request())

    def test_half_open_to_closed(self):
        for _ in range(3):
            self.cb.record_failure()
        time.sleep(1.1)

        # Access state to trigger OPEN -> HALF_OPEN transition
        self.assertEqual(self.cb.state, CircuitState.HALF_OPEN)

        # In half-open, record successes to close the circuit
        self.cb.record_success()
        self.cb.record_success()

        self.assertEqual(self.cb.state, CircuitState.CLOSED)

    def test_half_open_failure_goes_to_open(self):
        for _ in range(3):
            self.cb.record_failure()
        time.sleep(1.1)

        # Access state to trigger half-open
        _ = self.cb.state
        self.cb.record_failure()

        self.assertEqual(self.cb.state, CircuitState.OPEN)

    def test_excluded_exceptions_not_counted(self):
        cb = CircuitBreaker(
            "test_excluded",
            failure_threshold=2,
            excluded_exceptions=(ValueError,)
        )
        cb.record_failure(ValueError("ignored"))
        cb.record_failure(ValueError("also ignored"))
        self.assertTrue(cb.is_closed)

    def test_decorator_usage(self):
        cb = CircuitBreaker("decorator_test", failure_threshold=2)

        @cb
        def always_fails():
            raise RuntimeError("boom")

        with self.assertRaises(RuntimeError):
            always_fails()
        with self.assertRaises(RuntimeError):
            always_fails()

        # Circuit should be open now
        with self.assertRaises(CircuitBreakerError):
            always_fails()

    def test_decorator_success(self):
        cb = CircuitBreaker("decorator_success", failure_threshold=3)

        @cb
        def succeeds():
            return 42

        result = succeeds()
        self.assertEqual(result, 42)
        self.assertEqual(cb._total_successes, 1)

    def test_get_stats(self):
        self.cb.record_success()
        self.cb.record_failure()

        stats = self.cb.get_stats()
        self.assertEqual(stats["name"], "test")
        self.assertEqual(stats["total_calls"], 2)
        self.assertEqual(stats["total_successes"], 1)
        self.assertEqual(stats["total_failures"], 1)
        self.assertEqual(stats["state"], "closed")

    def test_manual_reset(self):
        for _ in range(3):
            self.cb.record_failure()
        self.assertTrue(self.cb.is_open)

        self.cb.reset()
        self.assertTrue(self.cb.is_closed)

    def test_circuit_breaker_error_attributes(self):
        err = CircuitBreakerError("test_cb", CircuitState.OPEN, 30.0)
        self.assertEqual(err.breaker_name, "test_cb")
        self.assertEqual(err.state, CircuitState.OPEN)
        self.assertEqual(err.retry_after, 30.0)


class TestCircuitBreakers(unittest.TestCase):
    """Tests for CircuitBreakers registry."""

    def setUp(self):
        CircuitBreakers.reset_all()

    def test_get_creates_breaker(self):
        cb = CircuitBreakers.get("redis")
        self.assertIsInstance(cb, CircuitBreaker)
        self.assertEqual(cb.name, "redis")

    def test_get_returns_same_instance(self):
        cb1 = CircuitBreakers.get("redis")
        cb2 = CircuitBreakers.get("redis")
        self.assertIs(cb1, cb2)

    def test_get_all_stats(self):
        CircuitBreakers.get("redis")
        CircuitBreakers.get("imap")
        stats = CircuitBreakers.get_all_stats()
        self.assertIn("redis", stats)
        self.assertIn("imap", stats)

    def test_reset_all(self):
        CircuitBreakers.get("redis")
        CircuitBreakers.reset_all()
        stats = CircuitBreakers.get_all_stats()
        self.assertEqual(len(stats), 0)


if __name__ == "__main__":
    unittest.main()
