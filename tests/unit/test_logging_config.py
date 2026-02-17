"""
Unit tests for structured JSON logging configuration.
"""
import pytest
import logging
import json
from unittest.mock import patch, MagicMock

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.common.logging_config import JSONFormatter, setup_logging, get_logger


class TestJSONFormatter:
    """Test JSONFormatter output"""

    def setup_method(self):
        self.formatter = JSONFormatter()

    def test_format_basic_fields(self):
        """Test that basic log fields are present in JSON output"""
        record = logging.LogRecord(
            name="test.logger",
            level=logging.INFO,
            pathname="test.py",
            lineno=42,
            msg="Hello %s",
            args=("world",),
            exc_info=None
        )
        output = self.formatter.format(record)
        data = json.loads(output)

        assert data["level"] == "INFO"
        assert data["logger"] == "test.logger"
        assert data["message"] == "Hello world"
        assert data["line"] == 42
        assert "timestamp" in data
        assert data["timestamp"].endswith("Z")

    def test_format_includes_correlation_id(self):
        """Test correlation_id is included when present on the record"""
        record = logging.LogRecord(
            name="test", level=logging.INFO,
            pathname="", lineno=1, msg="msg", args=(), exc_info=None
        )
        record.correlation_id = "abc-123"
        output = self.formatter.format(record)
        data = json.loads(output)

        assert data["correlation_id"] == "abc-123"

    def test_format_excludes_correlation_id_when_absent(self):
        """Test correlation_id is absent when not set"""
        record = logging.LogRecord(
            name="test", level=logging.INFO,
            pathname="", lineno=1, msg="msg", args=(), exc_info=None
        )
        output = self.formatter.format(record)
        data = json.loads(output)

        assert "correlation_id" not in data

    def test_format_includes_component(self):
        """Test component field is included when present"""
        record = logging.LogRecord(
            name="test", level=logging.INFO,
            pathname="", lineno=1, msg="msg", args=(), exc_info=None
        )
        record.component = "producer"
        output = self.formatter.format(record)
        data = json.loads(output)

        assert data["component"] == "producer"

    def test_format_includes_email_uid(self):
        """Test email_uid is included when present"""
        record = logging.LogRecord(
            name="test", level=logging.INFO,
            pathname="", lineno=1, msg="msg", args=(), exc_info=None
        )
        record.email_uid = 12345
        output = self.formatter.format(record)
        data = json.loads(output)

        assert data["email_uid"] == 12345

    def test_format_includes_exception(self):
        """Test exception info is included"""
        try:
            raise ValueError("boom")
        except ValueError:
            import sys
            exc_info = sys.exc_info()

        record = logging.LogRecord(
            name="test", level=logging.ERROR,
            pathname="", lineno=1, msg="error", args=(), exc_info=exc_info
        )
        output = self.formatter.format(record)
        data = json.loads(output)

        assert "exception" in data
        assert "ValueError: boom" in data["exception"]

    def test_format_returns_valid_json(self):
        """Test output is always valid JSON"""
        record = logging.LogRecord(
            name="test", level=logging.WARNING,
            pathname="", lineno=1, msg="special chars: àéîõü 日本語",
            args=(), exc_info=None
        )
        output = self.formatter.format(record)
        data = json.loads(output)  # Should not raise
        assert "special chars" in data["message"]


class TestSetupLogging:
    """Test setup_logging function"""

    def test_returns_logger(self):
        """Test that setup_logging returns a Logger"""
        logger = setup_logging("test.setup", level="DEBUG")
        assert isinstance(logger, logging.Logger)
        assert logger.name == "test.setup"

    def test_sets_level(self):
        """Test that the logger level is set correctly"""
        logger = setup_logging("test.level", level="WARNING")
        assert logger.level == logging.WARNING

    def test_uses_json_formatter(self):
        """Test that the handler uses JSONFormatter"""
        logger = setup_logging("test.formatter")
        assert len(logger.handlers) > 0
        assert isinstance(logger.handlers[0].formatter, JSONFormatter)

    def test_attaches_correlation_filter(self):
        """Test that CorrelationFilter is attached"""
        from src.common.correlation import CorrelationFilter
        logger = setup_logging("test.filter_attach")
        has_filter = any(isinstance(f, CorrelationFilter) for f in logger.filters)
        assert has_filter

    def test_no_duplicate_handlers(self):
        """Test that calling setup_logging twice doesn't duplicate handlers"""
        logger1 = setup_logging("test.dedup")
        logger2 = setup_logging("test.dedup")
        assert len(logger2.handlers) == 1

    def test_propagation_disabled(self):
        """Test that propagation to root logger is disabled"""
        logger = setup_logging("test.propagate")
        assert logger.propagate is False


class TestGetLogger:
    """Test get_logger function"""

    def test_get_logger_with_level(self):
        """Test get_logger with explicit level"""
        logger = get_logger("test.get_level", level="ERROR")
        assert logger.level == logging.ERROR

    def test_get_logger_creates_new_if_no_handlers(self):
        """Test get_logger creates a new logger if none exists"""
        name = "test.get_new_logger_unique_42"
        # Ensure logger has no handlers
        lg = logging.getLogger(name)
        lg.handlers.clear()

        logger = get_logger(name)
        assert len(logger.handlers) > 0

    def test_get_logger_attaches_correlation_filter(self):
        """Test that get_logger always attaches CorrelationFilter"""
        from src.common.correlation import CorrelationFilter
        name = "test.get_corr"
        logger = get_logger(name)
        has_filter = any(isinstance(f, CorrelationFilter) for f in logger.filters)
        assert has_filter
