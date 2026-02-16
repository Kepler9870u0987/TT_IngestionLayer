"""
Unit tests for CorrelationContext and CorrelationFilter.
"""
import logging
import unittest
from src.common.correlation import (
    CorrelationContext,
    CorrelationFilter,
    generate_correlation_id,
    set_correlation_id,
    get_correlation_id,
    clear_correlation_id,
    set_component,
    get_component
)


class TestCorrelationId(unittest.TestCase):
    """Tests for correlation ID functions."""

    def setUp(self):
        clear_correlation_id()

    def test_generate_returns_uuid(self):
        cid = generate_correlation_id()
        self.assertIsInstance(cid, str)
        self.assertEqual(len(cid), 36)  # UUID4 format
        self.assertEqual(cid.count("-"), 4)

    def test_set_and_get(self):
        set_correlation_id("test-123")
        self.assertEqual(get_correlation_id(), "test-123")

    def test_clear(self):
        set_correlation_id("test-123")
        clear_correlation_id()
        self.assertIsNone(get_correlation_id())

    def test_default_is_none(self):
        self.assertIsNone(get_correlation_id())

    def test_component_set_get(self):
        set_component("producer")
        self.assertEqual(get_component(), "producer")


class TestCorrelationContext(unittest.TestCase):
    """Tests for CorrelationContext context manager."""

    def setUp(self):
        clear_correlation_id()

    def test_auto_generates_id(self):
        with CorrelationContext() as ctx:
            self.assertIsNotNone(ctx.correlation_id)
            self.assertEqual(get_correlation_id(), ctx.correlation_id)

    def test_custom_id(self):
        with CorrelationContext("my-custom-id") as ctx:
            self.assertEqual(ctx.correlation_id, "my-custom-id")
            self.assertEqual(get_correlation_id(), "my-custom-id")

    def test_restores_previous_id(self):
        set_correlation_id("outer")
        with CorrelationContext("inner"):
            self.assertEqual(get_correlation_id(), "inner")
        self.assertEqual(get_correlation_id(), "outer")

    def test_clears_on_exit_if_no_previous(self):
        with CorrelationContext("temporary"):
            self.assertEqual(get_correlation_id(), "temporary")
        self.assertIsNone(get_correlation_id())

    def test_nested_contexts(self):
        with CorrelationContext("level1") as ctx1:
            self.assertEqual(get_correlation_id(), "level1")
            with CorrelationContext("level2") as ctx2:
                self.assertEqual(get_correlation_id(), "level2")
            self.assertEqual(get_correlation_id(), "level1")
        self.assertIsNone(get_correlation_id())


class TestCorrelationFilter(unittest.TestCase):
    """Tests for CorrelationFilter logging filter."""

    def setUp(self):
        clear_correlation_id()
        self.filter = CorrelationFilter()

    def test_injects_correlation_id(self):
        set_correlation_id("filter-test-id")
        record = logging.LogRecord(
            "test", logging.INFO, "", 0, "msg", (), None
        )
        result = self.filter.filter(record)
        self.assertTrue(result)
        self.assertEqual(record.correlation_id, "filter-test-id")

    def test_empty_string_when_no_id(self):
        record = logging.LogRecord(
            "test", logging.INFO, "", 0, "msg", (), None
        )
        self.filter.filter(record)
        self.assertEqual(record.correlation_id, "")

    def test_injects_component(self):
        set_component("worker")
        record = logging.LogRecord(
            "test", logging.INFO, "", 0, "msg", (), None
        )
        self.filter.filter(record)
        self.assertEqual(record.component, "worker")

    def test_never_filters_out_records(self):
        record = logging.LogRecord(
            "test", logging.INFO, "", 0, "msg", (), None
        )
        self.assertTrue(self.filter.filter(record))


if __name__ == "__main__":
    unittest.main()
