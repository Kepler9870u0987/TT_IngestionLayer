"""
Unit tests for ShutdownManager.
"""
import unittest
import threading
import time
from src.common.shutdown import ShutdownManager, ShutdownState


class TestShutdownManager(unittest.TestCase):
    """Tests for ShutdownManager."""

    def setUp(self):
        ShutdownManager.reset()
        self.shutdown = ShutdownManager(timeout=5)

    def tearDown(self):
        ShutdownManager.reset()

    def test_initial_state_is_running(self):
        self.assertTrue(self.shutdown.is_running)
        self.assertFalse(self.shutdown.is_shutting_down)
        self.assertEqual(self.shutdown.state, ShutdownState.RUNNING)

    def test_singleton_returns_same_instance(self):
        s1 = ShutdownManager()
        s2 = ShutdownManager()
        self.assertIs(s1, s2)

    def test_register_callback(self):
        callback = lambda: None
        self.shutdown.register(callback, priority=10, name="test")
        status = self.shutdown.get_status()
        self.assertIn("test", status["callback_names"])
        self.assertEqual(status["callbacks_registered"], 1)

    def test_unregister_callback(self):
        self.shutdown.register(lambda: None, name="test")
        result = self.shutdown.unregister("test")
        self.assertTrue(result)
        self.assertEqual(self.shutdown.get_status()["callbacks_registered"], 0)

    def test_unregister_nonexistent(self):
        result = self.shutdown.unregister("nonexistent")
        self.assertFalse(result)

    def test_initiate_shutdown(self):
        called = []
        self.shutdown.register(lambda: called.append("a"), priority=10, name="a")
        self.shutdown.register(lambda: called.append("b"), priority=20, name="b")

        self.shutdown.initiate_shutdown()

        self.assertEqual(self.shutdown.state, ShutdownState.STOPPED)
        self.assertFalse(self.shutdown.is_running)
        self.assertEqual(called, ["a", "b"])

    def test_callbacks_execute_in_priority_order(self):
        execution_order = []
        self.shutdown.register(
            lambda: execution_order.append("high"), priority=30, name="high"
        )
        self.shutdown.register(
            lambda: execution_order.append("low"), priority=10, name="low"
        )
        self.shutdown.register(
            lambda: execution_order.append("mid"), priority=20, name="mid"
        )

        self.shutdown.initiate_shutdown()
        self.assertEqual(execution_order, ["low", "mid", "high"])

    def test_callback_error_doesnt_stop_others(self):
        called = []
        self.shutdown.register(
            lambda: (_ for _ in ()).throw(RuntimeError("fail")),
            priority=10, name="failing"
        )
        self.shutdown.register(
            lambda: called.append("ok"), priority=20, name="ok"
        )

        self.shutdown.initiate_shutdown()
        self.assertIn("ok", called)

    def test_double_shutdown_ignored(self):
        self.shutdown.initiate_shutdown()
        # Second call should be ignored
        self.shutdown.initiate_shutdown()
        self.assertEqual(self.shutdown.state, ShutdownState.STOPPED)

    def test_wait_for_shutdown(self):
        result = self.shutdown.wait_for_shutdown(timeout=0.1)
        self.assertFalse(result)  # Should timeout

    def test_wait_for_shutdown_triggered(self):
        def trigger():
            time.sleep(0.1)
            self.shutdown.initiate_shutdown()

        t = threading.Thread(target=trigger)
        t.start()

        result = self.shutdown.wait_for_shutdown(timeout=2.0)
        self.assertTrue(result)
        t.join()

    def test_get_status(self):
        self.shutdown.register(lambda: None, name="test", priority=15)
        status = self.shutdown.get_status()

        self.assertEqual(status["state"], "running")
        self.assertTrue(status["is_running"])
        self.assertEqual(status["callbacks_registered"], 1)
        self.assertEqual(status["timeout"], 5)


if __name__ == "__main__":
    unittest.main()
