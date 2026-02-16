"""
Unit tests for HealthCheck, HealthRegistry, and HealthServer.
"""
import json
import time
import unittest
import urllib.request
from unittest.mock import MagicMock, patch

from src.common.health import (
    HealthCheck,
    HealthRegistry,
    HealthServer
)
from src.common.circuit_breaker import CircuitBreakers


class TestHealthCheck(unittest.TestCase):
    """Tests for HealthCheck class."""

    def test_healthy_check(self):
        check = HealthCheck("redis", lambda: True)
        result = check.run()

        self.assertEqual(result["name"], "redis")
        self.assertEqual(result["status"], "healthy")
        self.assertTrue(result["critical"])
        self.assertEqual(result["consecutive_failures"], 0)
        self.assertIsNone(result["error"])

    def test_unhealthy_check_returns_false(self):
        check = HealthCheck("redis", lambda: False)
        result = check.run()

        self.assertEqual(result["status"], "unhealthy")
        self.assertEqual(result["consecutive_failures"], 1)

    def test_unhealthy_check_raises(self):
        def failing():
            raise ConnectionError("Connection refused")

        check = HealthCheck("redis", failing)
        result = check.run()

        self.assertEqual(result["status"], "unhealthy")
        self.assertIn("Connection refused", result["error"])
        self.assertEqual(result["consecutive_failures"], 1)

    def test_consecutive_failures_count(self):
        check = HealthCheck("redis", lambda: False)
        check.run()
        check.run()
        result = check.run()
        self.assertEqual(result["consecutive_failures"], 3)

    def test_success_resets_failures(self):
        counter = {"val": 0}
        def toggle():
            counter["val"] += 1
            return counter["val"] > 2

        check = HealthCheck("redis", toggle)
        check.run()  # fail
        check.run()  # fail
        result = check.run()  # success
        self.assertEqual(result["consecutive_failures"], 0)

    def test_non_critical_check(self):
        check = HealthCheck("cache", lambda: True, critical=False)
        result = check.run()
        self.assertFalse(result["critical"])

    def test_response_time_tracked(self):
        def slow():
            time.sleep(0.01)
            return True

        check = HealthCheck("slow", slow)
        result = check.run()
        self.assertGreater(result["response_time_ms"], 0)


class TestHealthRegistry(unittest.TestCase):
    """Tests for HealthRegistry."""

    def setUp(self):
        CircuitBreakers.reset_all()
        self.registry = HealthRegistry("test")

    def test_liveness(self):
        result = self.registry.get_liveness()
        self.assertEqual(result["status"], "alive")
        self.assertEqual(result["component"], "test")
        self.assertIn("uptime_seconds", result)

    def test_readiness_with_healthy_checks(self):
        self.registry.register_check(
            HealthCheck("redis", lambda: True, critical=True)
        )
        result = self.registry.get_readiness()
        self.assertEqual(result["status"], "ready")

    def test_readiness_with_failing_critical(self):
        self.registry.register_check(
            HealthCheck("redis", lambda: False, critical=True)
        )
        result = self.registry.get_readiness()
        self.assertEqual(result["status"], "not_ready")

    def test_readiness_non_critical_failure_still_ready(self):
        self.registry.register_check(
            HealthCheck("redis", lambda: True, critical=True)
        )
        self.registry.register_check(
            HealthCheck("cache", lambda: False, critical=False)
        )
        result = self.registry.get_readiness()
        self.assertEqual(result["status"], "ready")

    def test_stats_provider(self):
        self.registry.register_stats_provider(
            "worker",
            lambda: {"processed": 100, "failed": 5}
        )
        result = self.registry.get_status()
        self.assertIn("worker", result["statistics"])
        self.assertEqual(result["statistics"]["worker"]["processed"], 100)

    def test_status_includes_circuit_breakers(self):
        CircuitBreakers.get("redis")
        result = self.registry.get_status()
        self.assertIn("circuit_breakers", result)
        self.assertIn("redis", result["circuit_breakers"])


class TestHealthServer(unittest.TestCase):
    """Tests for HealthServer HTTP endpoints."""

    @classmethod
    def setUpClass(cls):
        """Start health server for testing."""
        CircuitBreakers.reset_all()
        cls.registry = HealthRegistry("test_server")
        cls.registry.register_check(
            HealthCheck("redis", lambda: True, critical=True)
        )
        cls.server = HealthServer(cls.registry, port=18080)
        cls.server.start()
        time.sleep(0.3)  # Wait for server to start

    @classmethod
    def tearDownClass(cls):
        cls.server.stop()
        CircuitBreakers.reset_all()

    def _get(self, path: str) -> tuple:
        """Make GET request and return (status_code, data)."""
        url = f"http://127.0.0.1:18080{path}"
        try:
            resp = urllib.request.urlopen(url, timeout=2)
            data = json.loads(resp.read().decode())
            return resp.status, data
        except urllib.error.HTTPError as e:
            data = json.loads(e.read().decode())
            return e.code, data

    def test_health_endpoint(self):
        status, data = self._get("/health")
        self.assertEqual(status, 200)
        self.assertEqual(data["status"], "alive")

    def test_ready_endpoint(self):
        status, data = self._get("/ready")
        self.assertEqual(status, 200)
        self.assertEqual(data["status"], "ready")

    def test_status_endpoint(self):
        status, data = self._get("/status")
        self.assertEqual(status, 200)
        self.assertIn("health_checks", data)
        self.assertIn("circuit_breakers", data)

    def test_unknown_endpoint(self):
        status, data = self._get("/unknown")
        self.assertEqual(status, 404)

    def test_server_is_running(self):
        self.assertTrue(self.server.is_running)


if __name__ == "__main__":
    unittest.main()
