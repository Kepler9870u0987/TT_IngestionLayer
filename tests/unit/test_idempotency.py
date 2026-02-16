"""
Unit tests for IdempotencyManager.
"""
import pytest
from unittest.mock import Mock, MagicMock, patch

from src.worker.idempotency import IdempotencyManager, create_idempotency_manager_from_config
from src.common.exceptions import RedisConnectionError


class TestIdempotencyManager:
    """Test suite for IdempotencyManager"""

    @pytest.fixture
    def mock_redis(self):
        """Create mock Redis client"""
        redis_mock = Mock()
        redis_mock.sismember = Mock(return_value=False)
        redis_mock.sadd = Mock(return_value=1)
        redis_mock.client = Mock()
        redis_mock.client.scard = Mock(return_value=0)
        redis_mock.client.delete = Mock(return_value=1)
        redis_mock.client.expire = Mock(return_value=True)
        return redis_mock

    @pytest.fixture
    def idempotency_manager(self, mock_redis):
        """Create IdempotencyManager instance with mock Redis"""
        return IdempotencyManager(
            redis_client=mock_redis,
            key_prefix="test_processed",
            ttl_hours=24
        )

    def test_initialization(self, idempotency_manager):
        """Test IdempotencyManager initialization"""
        assert idempotency_manager.key_prefix == "test_processed"
        assert idempotency_manager.ttl_hours == 24

    def test_get_key(self, idempotency_manager):
        """Test Redis key generation"""
        key = idempotency_manager._get_key()
        assert key == "test_processed:set"

    def test_is_processed_false(self, idempotency_manager, mock_redis):
        """Test is_processed returns False for new message"""
        mock_redis.sismember.return_value = False
        
        result = idempotency_manager.is_processed("msg-123")
        
        assert result is False
        mock_redis.sismember.assert_called_once_with("test_processed:set", "msg-123")

    def test_is_processed_true(self, idempotency_manager, mock_redis):
        """Test is_processed returns True for processed message"""
        mock_redis.sismember.return_value = True
        
        result = idempotency_manager.is_processed("msg-123")
        
        assert result is True
        mock_redis.sismember.assert_called_once_with("test_processed:set", "msg-123")

    def test_mark_processed_new_message(self, idempotency_manager, mock_redis):
        """Test marking a new message as processed"""
        mock_redis.sadd.return_value = 1
        
        result = idempotency_manager.mark_processed("msg-456")
        
        assert result is True
        mock_redis.sadd.assert_called_once_with("test_processed:set", "msg-456")

    def test_mark_processed_duplicate(self, idempotency_manager, mock_redis):
        """Test marking already processed message"""
        mock_redis.sadd.return_value = 0
        
        result = idempotency_manager.mark_processed("msg-456")
        
        assert result is False

    def test_mark_processed_with_ttl(self, idempotency_manager, mock_redis):
        """Test TTL is set when marking message as processed"""
        mock_redis.sadd.return_value = 1
        
        idempotency_manager.mark_processed("msg-789")
        
        # Verify expire was called with correct TTL (24 hours = 86400 seconds)
        mock_redis.client.expire.assert_called_once_with("test_processed:set", 86400)

    def test_mark_processed_no_ttl(self, mock_redis):
        """Test no TTL set when ttl_hours is None"""
        manager = IdempotencyManager(
            redis_client=mock_redis,
            ttl_hours=None
        )
        mock_redis.sadd.return_value = 1
        
        manager.mark_processed("msg-999")
        
        # Verify expire was NOT called
        mock_redis.client.expire.assert_not_called()

    def test_is_duplicate(self, idempotency_manager, mock_redis):
        """Test is_duplicate is an alias for is_processed"""
        mock_redis.sismember.return_value = True
        
        result = idempotency_manager.is_duplicate("msg-123")
        
        assert result is True

    def test_get_processed_count(self, idempotency_manager, mock_redis):
        """Test getting count of processed messages"""
        mock_redis.client.scard.return_value = 42
        
        count = idempotency_manager.get_processed_count()
        
        assert count == 42
        mock_redis.client.scard.assert_called_once_with("test_processed:set")

    def test_clear_processed(self, idempotency_manager, mock_redis):
        """Test clearing all processed messages"""
        mock_redis.client.delete.return_value = 1
        
        result = idempotency_manager.clear_processed()
        
        assert result is True
        mock_redis.client.delete.assert_called_once_with("test_processed:set")

    def test_clear_processed_empty(self, idempotency_manager, mock_redis):
        """Test clearing when no processed messages exist"""
        mock_redis.client.delete.return_value = 0
        
        result = idempotency_manager.clear_processed()
        
        assert result is False

    def test_redis_error_handling(self, idempotency_manager, mock_redis):
        """Test Redis error handling"""
        mock_redis.sismember.side_effect = Exception("Redis connection failed")
        
        with pytest.raises(RedisConnectionError):
            idempotency_manager.is_processed("msg-123")

    def test_factory_function(self, mock_redis):
        """Test factory function creates manager correctly"""
        manager = create_idempotency_manager_from_config(
            redis_client=mock_redis,
            ttl_hours=48
        )
        
        assert manager.key_prefix == "processed_messages"
        assert manager.ttl_hours == 48
