"""
Unit tests for EmailWorker main orchestration class.
Tests focus on ensure_consumer_group and process_message methods.
"""
import pytest
import time
from unittest.mock import patch, MagicMock, PropertyMock

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.common.exceptions import ProcessingError


@pytest.fixture
def mock_settings():
    """Mock the settings singleton"""
    mock = MagicMock()
    mock.logging.level = "INFO"
    mock.redis.host = "localhost"
    mock.redis.port = 6379
    mock.redis.username = None
    mock.redis.password = None
    mock.redis.db = 0
    mock.redis.ssl = False
    mock.redis.ssl_ca_certs = None
    mock.redis.stream_name = "test_stream"
    mock.idempotency.ttl_seconds = 86400
    mock.dlq.initial_backoff_seconds = 2
    mock.dlq.max_backoff_seconds = 3600
    mock.dlq.max_retry_attempts = 3
    mock.dlq.stream_name = "test_dlq"
    mock.circuit_breaker.failure_threshold = 5
    mock.circuit_breaker.recovery_timeout_seconds = 60.0
    mock.circuit_breaker.success_threshold = 3
    mock.recovery.min_idle_ms = 300000
    mock.recovery.max_claim_count = 50
    mock.recovery.max_delivery_count = 10
    mock.monitoring.worker_health_port = 8081
    mock.monitoring.worker_metrics_port = 9091
    return mock


@pytest.fixture
def worker(mock_settings):
    """Create an EmailWorker with all dependencies mocked"""
    with patch("worker.settings", mock_settings), \
         patch("worker.RedisClient") as mock_redis_cls, \
         patch("worker.create_idempotency_manager_from_config") as mock_idemp, \
         patch("worker.create_backoff_manager_from_config") as mock_backoff, \
         patch("worker.create_dlq_manager_from_config") as mock_dlq, \
         patch("worker.create_processor_from_config") as mock_proc, \
         patch("worker.CircuitBreakers") as mock_cb, \
         patch("worker.ShutdownManager") as mock_shutdown, \
         patch("worker.OrphanedMessageRecovery") as mock_recovery, \
         patch("worker.setup_logging") as mock_log, \
         patch("worker.set_component"), \
         patch("worker.get_metrics_collector") as mock_metrics:

        mock_redis = MagicMock()
        mock_redis_cls.return_value = mock_redis

        mock_idemp_inst = MagicMock()
        mock_idemp.return_value = mock_idemp_inst

        mock_backoff_inst = MagicMock()
        mock_backoff.return_value = mock_backoff_inst

        mock_dlq_inst = MagicMock()
        mock_dlq.return_value = mock_dlq_inst

        mock_proc_inst = MagicMock()
        mock_proc.return_value = mock_proc_inst

        mock_cb_inst = MagicMock()
        mock_cb_inst.is_open = False
        mock_cb.get.return_value = mock_cb_inst

        mock_shutdown_inst = MagicMock()
        mock_shutdown_inst.is_running = True
        mock_shutdown.return_value = mock_shutdown_inst

        mock_metrics_inst = MagicMock()
        mock_metrics.return_value = mock_metrics_inst

        from worker import EmailWorker
        w = EmailWorker(
            stream_name="test_stream",
            consumer_group="test_group",
            consumer_name="worker_01"
        )
        # Expose mocks for test assertions
        w._mock_redis = mock_redis
        w._mock_idemp = mock_idemp_inst
        w._mock_backoff = mock_backoff_inst
        w._mock_dlq = mock_dlq_inst
        w._mock_proc = mock_proc_inst
        w._mock_metrics = mock_metrics_inst
        yield w


class TestEmailWorkerInit:
    """Test EmailWorker initialization"""

    def test_init_stores_params(self, worker):
        assert worker.stream_name == "test_stream"
        assert worker.consumer_group == "test_group"
        assert worker.consumer_name == "worker_01"
        assert worker.messages_processed == 0
        assert worker.messages_failed == 0


class TestEnsureConsumerGroup:
    """Test ensure_consumer_group method"""

    def test_creates_new_group(self, worker):
        """Test creating a new consumer group"""
        worker.ensure_consumer_group()
        worker._mock_redis.xgroup_create.assert_called_once_with(
            stream="test_stream",
            groupname="test_group",
            id="0",
            mkstream=True
        )

    def test_handles_existing_group(self, worker):
        """Test handling BUSYGROUP error (group already exists)"""
        worker._mock_redis.xgroup_create.side_effect = Exception(
            "BUSYGROUP Consumer Group name already exists"
        )
        worker.ensure_consumer_group()  # Should not raise

    def test_raises_on_other_error(self, worker):
        """Test non-BUSYGROUP errors are re-raised"""
        worker._mock_redis.xgroup_create.side_effect = Exception("unexpected error")
        with pytest.raises(Exception, match="unexpected error"):
            worker.ensure_consumer_group()


class TestProcessMessage:
    """Test process_message method"""

    def test_skip_duplicate(self, worker):
        """Test idempotent skip of duplicate message"""
        worker._mock_idemp.is_duplicate.return_value = True

        result = worker.process_message("msg-1", {"message_id": "email-1"})
        assert result is True
        assert worker.messages_skipped == 1
        worker._mock_proc.process.assert_not_called()

    def test_successful_processing(self, worker):
        """Test successful message processing"""
        worker._mock_idemp.is_duplicate.return_value = False
        worker._mock_backoff.should_retry.return_value = True
        worker._mock_proc.process.return_value = {"status": "success"}

        result = worker.process_message("msg-1", {
            "message_id": "email-1",
            "from": "a@b.com",
            "subject": "test",
            "date": "2026-02-17"
        })

        assert result is True
        assert worker.messages_processed == 1
        worker._mock_idemp.mark_processed.assert_called_once_with("email-1")
        worker._mock_backoff.record_success.assert_called_once_with("email-1")

    def test_processing_error_triggers_retry(self, worker):
        """Test processing failure records retry"""
        worker._mock_idemp.is_duplicate.return_value = False
        worker._mock_backoff.should_retry.return_value = True
        worker._mock_proc.process.side_effect = ProcessingError("parse error")
        worker._mock_backoff.record_failure.return_value = 1

        result = worker.process_message("msg-1", {"message_id": "email-1"})

        assert result is False
        assert worker.messages_failed == 1
        worker._mock_backoff.record_failure.assert_called_once_with("email-1")

    def test_max_retries_sends_to_dlq(self, worker):
        """Test message sent to DLQ after max retries"""
        worker._mock_idemp.is_duplicate.return_value = False
        worker._mock_backoff.should_retry.return_value = False
        worker._mock_backoff.get_retry_count.return_value = 3

        result = worker.process_message("msg-1", {"message_id": "email-1"})

        assert result is True
        assert worker.messages_dlq == 1
        worker._mock_dlq.send_to_dlq.assert_called_once()
        worker._mock_idemp.mark_processed.assert_called_once_with("email-1")

    def test_dlq_failure_returns_false(self, worker):
        """Test DLQ send failure returns False"""
        worker._mock_idemp.is_duplicate.return_value = False
        worker._mock_backoff.should_retry.return_value = False
        worker._mock_backoff.get_retry_count.return_value = 3
        worker._mock_dlq.send_to_dlq.side_effect = Exception("DLQ error")

        result = worker.process_message("msg-1", {"message_id": "email-1"})
        assert result is False

    def test_unexpected_error_records_failure(self, worker):
        """Test unexpected error during processing"""
        worker._mock_idemp.is_duplicate.return_value = False
        worker._mock_backoff.should_retry.return_value = True
        worker._mock_proc.process.side_effect = RuntimeError("boom")
        worker._mock_backoff.record_failure.return_value = 1

        result = worker.process_message("msg-1", {"message_id": "email-1"})

        assert result is False
        assert worker.messages_failed == 1


class TestLogStats:
    """Test log_stats method (read via get stats)"""

    def test_stats_initial(self, worker):
        """Test initial statistics are zero"""
        assert worker.messages_processed == 0
        assert worker.messages_skipped == 0
        assert worker.messages_failed == 0
        assert worker.messages_dlq == 0
        assert worker.messages_recovered == 0
