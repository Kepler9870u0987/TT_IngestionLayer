"""
Unit tests for DLQManager.
"""
import pytest
import json
from unittest.mock import Mock, MagicMock
from datetime import datetime

from src.worker.dlq import DLQManager, create_dlq_manager_from_config
from src.common.exceptions import RedisConnectionError


class TestDLQManager:
    """Test suite for DLQManager"""

    @pytest.fixture
    def mock_redis(self):
        """Create mock Redis client"""
        redis_mock = Mock()
        redis_mock.xadd = Mock(return_value="dlq-msg-123")
        redis_mock.client = Mock()
        redis_mock.client.xlen = Mock(return_value=5)
        redis_mock.client.xrange = Mock(return_value=[])
        redis_mock.client.xdel = Mock(return_value=1)
        return redis_mock

    @pytest.fixture
    def dlq_manager(self, mock_redis):
        """Create DLQManager instance with mock Redis"""
        return DLQManager(
            redis_client=mock_redis,
            dlq_stream_name="test_dlq",
            max_length=1000
        )

    def test_initialization(self, dlq_manager):
        """Test DLQManager initialization"""
        assert dlq_manager.dlq_stream_name == "test_dlq"
        assert dlq_manager.max_length == 1000

    def test_send_to_dlq_success(self, dlq_manager, mock_redis):
        """Test sending message to DLQ"""
        original_data = {
            "message_id": "email-123",
            "from": "sender@example.com",
            "subject": "Test"
        }
        error = ValueError("Processing failed")
        
        dlq_id = dlq_manager.send_to_dlq(
            message_id="email-123",
            original_data=original_data,
            error=error,
            retry_count=3
        )
        
        assert dlq_id == "dlq-msg-123"
        
        # Verify xadd was called with correct parameters
        call_args = mock_redis.xadd.call_args
        assert call_args[1]["stream"] == "test_dlq"
        
        fields = call_args[1]["fields"]
        assert fields["original_message_id"] == "email-123"
        assert fields["error_type"] == "ValueError"
        assert fields["error_message"] == "Processing failed"
        assert fields["retry_count"] == "3"
        assert json.loads(fields["original_data"]) == original_data

    def test_send_to_dlq_with_metadata(self, dlq_manager, mock_redis):
        """Test sending message with additional metadata"""
        original_data = {"message_id": "email-456"}
        error = Exception("Error")
        metadata = {"worker": "worker-01", "attempt_time": "2026-02-16T10:00:00"}
        
        dlq_manager.send_to_dlq(
            message_id="email-456",
            original_data=original_data,
            error=error,
            retry_count=5,
            metadata=metadata
        )
        
        fields = mock_redis.xadd.call_args[1]["fields"]
        assert "metadata" in fields
        assert json.loads(fields["metadata"]) == metadata

    def test_get_dlq_length(self, dlq_manager, mock_redis):
        """Test getting DLQ length"""
        mock_redis.client.xlen.return_value = 42
        
        length = dlq_manager.get_dlq_length()
        
        assert length == 42
        mock_redis.client.xlen.assert_called_once_with("test_dlq")

    def test_peek_dlq(self, dlq_manager, mock_redis):
        """Test peeking at DLQ messages"""
        mock_redis.client.xrange.return_value = [
            ("dlq-1", {"message_id": "email-1"}),
            ("dlq-2", {"message_id": "email-2"})
        ]
        
        messages = dlq_manager.peek_dlq(count=10)
        
        assert len(messages) == 2
        mock_redis.client.xrange.assert_called_once_with(
            "test_dlq",
            min="-",
            max="+",
            count=10
        )

    def test_remove_from_dlq_success(self, dlq_manager, mock_redis):
        """Test removing entry from DLQ"""
        mock_redis.client.xdel.return_value = 1
        
        result = dlq_manager.remove_from_dlq("dlq-123")
        
        assert result is True
        mock_redis.client.xdel.assert_called_once_with("test_dlq", "dlq-123")

    def test_remove_from_dlq_not_found(self, dlq_manager, mock_redis):
        """Test removing non-existent entry"""
        mock_redis.client.xdel.return_value = 0
        
        result = dlq_manager.remove_from_dlq("dlq-999")
        
        assert result is False

    def test_reprocess_from_dlq_success(self, dlq_manager, mock_redis):
        """Test reprocessing message from DLQ"""
        original_data = {
            "message_id": "email-123",
            "subject": "Test"
        }
        
        # Mock xrange to return DLQ entry
        mock_redis.client.xrange.return_value = [
            ("dlq-123", {"original_data": json.dumps(original_data)})
        ]
        
        # Mock xadd for reprocessing
        mock_redis.xadd.return_value = "stream-456"
        
        # Mock xdel for removal
        mock_redis.client.xdel.return_value = 1
        
        new_id = dlq_manager.reprocess_from_dlq(
            dlq_entry_id="dlq-123",
            target_stream="email_ingestion_stream"
        )
        
        assert new_id == "stream-456"
        
        # Verify message was reprocessed to main stream
        reprocess_call = mock_redis.xadd.call_args
        assert reprocess_call[1]["stream"] == "email_ingestion_stream"
        
        reprocessed_data = reprocess_call[1]["fields"]
        assert reprocessed_data["message_id"] == "email-123"
        assert reprocessed_data["reprocessed_from_dlq"] == "true"

    def test_reprocess_from_dlq_not_found(self, dlq_manager, mock_redis):
        """Test reprocessing non-existent DLQ entry"""
        mock_redis.client.xrange.return_value = []
        
        result = dlq_manager.reprocess_from_dlq("dlq-999")
        
        assert result is None

    def test_clear_dlq(self, dlq_manager, mock_redis):
        """Test clearing entire DLQ"""
        mock_redis.client.xrange.return_value = [
            ("dlq-1", {}),
            ("dlq-2", {}),
            ("dlq-3", {})
        ]
        
        count = dlq_manager.clear_dlq()
        
        assert count == 3
        assert mock_redis.client.xdel.call_count == 3

    def test_redis_error_handling(self, dlq_manager, mock_redis):
        """Test Redis error handling"""
        mock_redis.xadd.side_effect = Exception("Redis error")
        
        with pytest.raises(RedisConnectionError):
            dlq_manager.send_to_dlq(
                message_id="email-123",
                original_data={},
                error=Exception("Test"),
                retry_count=1
            )

    def test_factory_function(self, mock_redis):
        """Test factory function creates DLQManager correctly"""
        manager = create_dlq_manager_from_config(
            redis_client=mock_redis,
            dlq_stream_name="custom_dlq",
            max_length=5000
        )
        
        assert manager.dlq_stream_name == "custom_dlq"
        assert manager.max_length == 5000
