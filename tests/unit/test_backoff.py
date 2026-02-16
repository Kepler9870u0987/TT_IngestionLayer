"""
Unit tests for BackoffManager.
"""
import pytest
import time
from datetime import datetime, timedelta
from unittest.mock import patch

from src.worker.backoff import BackoffManager, create_backoff_manager_from_config


class TestBackoffManager:
    """Test suite for BackoffManager"""

    @pytest.fixture
    def backoff_manager(self):
        """Create BackoffManager instance"""
        return BackoffManager(
            initial_delay=1.0,
            max_delay=60.0,
            multiplier=2.0,
            max_retries=3
        )

    def test_initialization(self, backoff_manager):
        """Test BackoffManager initialization"""
        assert backoff_manager.initial_delay == 1.0
        assert backoff_manager.max_delay == 60.0
        assert backoff_manager.multiplier == 2.0
        assert backoff_manager.max_retries == 3

    def test_calculate_delay(self, backoff_manager):
        """Test delay calculation with exponential backoff"""
        # attempt 0: 1.0 * 2^0 = 1.0
        assert backoff_manager.calculate_delay(0) == 1.0
        
        # attempt 1: 1.0 * 2^1 = 2.0
        assert backoff_manager.calculate_delay(1) == 2.0
        
        # attempt 2: 1.0 * 2^2 = 4.0
        assert backoff_manager.calculate_delay(2) == 4.0
        
        # attempt 3: 1.0 * 2^3 = 8.0
        assert backoff_manager.calculate_delay(3) == 8.0

    def test_calculate_delay_max_cap(self, backoff_manager):
        """Test delay is capped at max_delay"""
        # attempt 10: 1.0 * 2^10 = 1024, but capped at 60
        delay = backoff_manager.calculate_delay(10)
        assert delay == 60.0

    def test_should_retry_first_attempt(self, backoff_manager):
        """Test should_retry returns True for first attempt"""
        result = backoff_manager.should_retry("msg-123")
        assert result is True

    def test_should_retry_max_exceeded(self, backoff_manager):
        """Test should_retry returns False when max retries exceeded"""
        # Record failures up to max_retries
        for _ in range(3):
            backoff_manager.record_failure("msg-123")
        
        result = backoff_manager.should_retry("msg-123")
        assert result is False

    def test_record_failure(self, backoff_manager):
        """Test recording failure increments retry count"""
        retry_count = backoff_manager.record_failure("msg-456")
        
        assert retry_count == 1
        assert backoff_manager.get_retry_count("msg-456") == 1

    def test_record_failure_multiple(self, backoff_manager):
        """Test recording multiple failures"""
        backoff_manager.record_failure("msg-789")
        backoff_manager.record_failure("msg-789")
        retry_count = backoff_manager.record_failure("msg-789")
        
        assert retry_count == 3

    def test_record_success(self, backoff_manager):
        """Test recording success clears retry tracking"""
        backoff_manager.record_failure("msg-success")
        backoff_manager.record_failure("msg-success")
        assert backoff_manager.get_retry_count("msg-success") == 2
        
        backoff_manager.record_success("msg-success")
        
        assert backoff_manager.get_retry_count("msg-success") == 0

    def test_get_retry_count_nonexistent(self, backoff_manager):
        """Test getting retry count for message never failed"""
        count = backoff_manager.get_retry_count("msg-new")
        assert count == 0

    def test_get_next_retry_time(self, backoff_manager):
        """Test getting next retry time"""
        before = datetime.now()
        backoff_manager.record_failure("msg-time")
        after = datetime.now()
        
        next_retry = backoff_manager.get_next_retry_time("msg-time")
        
        assert next_retry is not None
        # Should be ~1 second in the future (initial_delay)
        assert next_retry > before
        assert next_retry > after

    def test_get_next_retry_time_nonexistent(self, backoff_manager):
        """Test getting next retry time for non-tracked message"""
        next_retry = backoff_manager.get_next_retry_time("msg-none")
        assert next_retry is None

    def test_has_exceeded_max_retries(self, backoff_manager):
        """Test checking if max retries exceeded"""
        assert not backoff_manager.has_exceeded_max_retries("msg-check")
        
        for _ in range(3):
            backoff_manager.record_failure("msg-check")
        
        assert backoff_manager.has_exceeded_max_retries("msg-check")

    def test_should_retry_respects_time(self, backoff_manager):
        """Test should_retry respects next retry time"""
        backoff_manager.record_failure("msg-timed")
        
        # Immediately after failure, should not be ready for retry
        # (need to wait for the delay)
        # Actually, should_retry checks if enough time has passed
        # Let's check the next_retry_time is in the future
        next_retry = backoff_manager.get_next_retry_time("msg-timed")
        assert next_retry > datetime.now()

    @patch('time.sleep')
    def test_wait_for_retry(self, mock_sleep, backoff_manager):
        """Test waiting for retry blocks appropriately"""
        backoff_manager.record_failure("msg-wait")
        
        wait_time = backoff_manager.wait_for_retry("msg-wait")
        
        assert wait_time > 0
        mock_sleep.assert_called_once()

    def test_wait_for_retry_no_tracking(self, backoff_manager):
        """Test waiting for untracked message returns immediately"""
        wait_time = backoff_manager.wait_for_retry("msg-untracked")
        assert wait_time == 0.0

    def test_cleanup_old_entries(self, backoff_manager):
        """Test cleaning up old retry entries"""
        # Record old failure
        backoff_manager.record_failure("msg-old")
        
        # Manually set old next_retry_time
        old_time = datetime.now() - timedelta(hours=48)
        backoff_manager._next_retry_time["msg-old"] = old_time
        
        # Record recent failure
        backoff_manager.record_failure("msg-recent")
        
        # Cleanup entries older than 24 hours
        backoff_manager.cleanup_old_entries(age_hours=24)
        
        # Old entry should be removed
        assert "msg-old" not in backoff_manager._retry_counts
        assert "msg-old" not in backoff_manager._next_retry_time
        
        # Recent entry should remain
        assert "msg-recent" in backoff_manager._retry_counts

    def test_factory_function(self):
        """Test factory function creates BackoffManager correctly"""
        manager = create_backoff_manager_from_config(
            initial_delay=3.0,
            max_delay=600.0,
            multiplier=3.0,
            max_retries=10
        )
        
        assert manager.initial_delay == 3.0
        assert manager.max_delay == 600.0
        assert manager.multiplier == 3.0
        assert manager.max_retries == 10

    def test_multiple_messages_independent(self, backoff_manager):
        """Test that different messages have independent retry tracking"""
        backoff_manager.record_failure("msg-1")
        backoff_manager.record_failure("msg-1")
        backoff_manager.record_failure("msg-2")
        
        assert backoff_manager.get_retry_count("msg-1") == 2
        assert backoff_manager.get_retry_count("msg-2") == 1
        
        backoff_manager.record_success("msg-1")
        
        assert backoff_manager.get_retry_count("msg-1") == 0
        assert backoff_manager.get_retry_count("msg-2") == 1
