"""
Unit tests for RedisClient wrapper.
Uses mocking to test without requiring actual Redis instance.
"""
import pytest
from unittest.mock import Mock, patch, MagicMock, call
import redis

# Add project root to path for imports
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.common.redis_client import RedisClient
from src.common.exceptions import RedisConnectionError as CustomRedisConnectionError


@pytest.fixture
def mock_redis_pool():
    """Mock Redis connection pool"""
    with patch('src.common.redis_client.ConnectionPool') as mock_pool:
        yield mock_pool


@pytest.fixture
def mock_redis_client():
    """Mock Redis client instance"""
    with patch('src.common.redis_client.redis.Redis') as mock_redis:
        mock_instance = MagicMock()
        mock_redis.return_value = mock_instance
        yield mock_instance


class TestRedisClientInit:
    """Test RedisClient initialization"""

    def test_init_with_defaults(self, mock_redis_pool, mock_redis_client):
        """Test initialization with default parameters"""
        client = RedisClient()

        assert client is not None
        mock_redis_pool.assert_called_once()
        call_kwargs = mock_redis_pool.call_args[1]
        assert call_kwargs['host'] == 'localhost'
        assert call_kwargs['port'] == 6379
        assert call_kwargs['db'] == 0

    def test_init_with_custom_params(self, mock_redis_pool, mock_redis_client):
        """Test initialization with custom parameters"""
        client = RedisClient(
            host='redis.example.com',
            port=6380,
            password='secret',
            db=1,
            max_connections=50
        )

        assert client is not None
        call_kwargs = mock_redis_pool.call_args[1]
        assert call_kwargs['host'] == 'redis.example.com'
        assert call_kwargs['port'] == 6380
        assert call_kwargs['password'] == 'secret'
        assert call_kwargs['db'] == 1
        assert call_kwargs['max_connections'] == 50


class TestRedisClientPing:
    """Test ping/health check"""

    def test_ping_success(self, mock_redis_pool, mock_redis_client):
        """Test successful ping"""
        mock_redis_client.ping.return_value = True

        client = RedisClient()
        result = client.ping()

        assert result is True
        mock_redis_client.ping.assert_called_once()

    def test_ping_failure(self, mock_redis_pool, mock_redis_client):
        """Test ping failure raises exception"""
        mock_redis_client.ping.side_effect = redis.ConnectionError("Connection refused")

        client = RedisClient()

        with pytest.raises(CustomRedisConnectionError):
            client.ping()


class TestRedisClientXADD:
    """Test XADD stream operations"""

    def test_xadd_success(self, mock_redis_pool, mock_redis_client):
        """Test successful XADD"""
        mock_redis_client.xadd.return_value = "1234567890123-0"

        client = RedisClient()
        msg_id = client.xadd("test_stream", {"field": "value"})

        assert msg_id == "1234567890123-0"
        mock_redis_client.xadd.assert_called_once()
        call_args = mock_redis_client.xadd.call_args
        assert call_args[0][0] == "test_stream"
        assert call_args[0][1] == {"field": "value"}

    def test_xadd_with_maxlen(self, mock_redis_pool, mock_redis_client):
        """Test XADD with max length"""
        mock_redis_client.xadd.return_value = "1234567890124-0"

        client = RedisClient()
        msg_id = client.xadd("test_stream", {"key": "val"}, maxlen=1000)

        assert msg_id == "1234567890124-0"
        call_kwargs = mock_redis_client.xadd.call_args[1]
        assert call_kwargs['maxlen'] == 1000

    def test_xadd_failure(self, mock_redis_pool, mock_redis_client):
        """Test XADD failure"""
        mock_redis_client.xadd.side_effect = redis.RedisError("Stream error")

        client = RedisClient()

        with pytest.raises(CustomRedisConnectionError):
            client.xadd("test_stream", {"field": "value"})


class TestRedisClientXREADGROUP:
    """Test XREADGROUP consumer group operations"""

    def test_xreadgroup_success(self, mock_redis_pool, mock_redis_client):
        """Test successful XREADGROUP"""
        mock_result = [
            ('stream1', [
                ('1234567890-0', {'field1': 'value1'}),
                ('1234567891-0', {'field2': 'value2'})
            ])
        ]
        mock_redis_client.xreadgroup.return_value = mock_result

        client = RedisClient()
        result = client.xreadgroup(
            'group1',
            'consumer1',
            {'stream1': '>'},
            count=10,
            block=5000
        )

        assert result == mock_result
        mock_redis_client.xreadgroup.assert_called_once_with(
            'group1',
            'consumer1',
            {'stream1': '>'},
            count=10,
            block=5000
        )

    def test_xreadgroup_empty(self, mock_redis_pool, mock_redis_client):
        """Test XREADGROUP with no messages"""
        mock_redis_client.xreadgroup.return_value = None

        client = RedisClient()
        result = client.xreadgroup('group1', 'consumer1', {'stream1': '>'})

        assert result == []

    def test_xreadgroup_failure(self, mock_redis_pool, mock_redis_client):
        """Test XREADGROUP failure"""
        mock_redis_client.xreadgroup.side_effect = redis.RedisError("Group error")

        client = RedisClient()

        with pytest.raises(CustomRedisConnectionError):
            client.xreadgroup('group1', 'consumer1', {'stream1': '>'})


class TestRedisClientXACK:
    """Test XACK acknowledgment operations"""

    def test_xack_single(self, mock_redis_pool, mock_redis_client):
        """Test acknowledging single message"""
        mock_redis_client.xack.return_value = 1

        client = RedisClient()
        count = client.xack('stream1', 'group1', '1234567890-0')

        assert count == 1
        mock_redis_client.xack.assert_called_once_with(
            'stream1', 'group1', '1234567890-0'
        )

    def test_xack_multiple(self, mock_redis_pool, mock_redis_client):
        """Test acknowledging multiple messages"""
        mock_redis_client.xack.return_value = 3

        client = RedisClient()
        count = client.xack(
            'stream1', 'group1',
            '1234567890-0', '1234567891-0', '1234567892-0'
        )

        assert count == 3

    def test_xack_failure(self, mock_redis_pool, mock_redis_client):
        """Test XACK failure"""
        mock_redis_client.xack.side_effect = redis.RedisError("ACK error")

        client = RedisClient()

        with pytest.raises(CustomRedisConnectionError):
            client.xack('stream1', 'group1', '1234567890-0')


class TestRedisClientXGROUPCREATE:
    """Test XGROUP CREATE operations"""

    def test_xgroup_create_success(self, mock_redis_pool, mock_redis_client):
        """Test successful consumer group creation"""
        mock_redis_client.xgroup_create.return_value = True

        client = RedisClient()
        result = client.xgroup_create('stream1', 'group1', id='0', mkstream=True)

        assert result is True
        mock_redis_client.xgroup_create.assert_called_once_with(
            'stream1', 'group1', id='0', mkstream=True
        )

    def test_xgroup_create_already_exists(self, mock_redis_pool, mock_redis_client):
        """Test consumer group already exists (BUSYGROUP)"""
        mock_redis_client.xgroup_create.side_effect = redis.ResponseError("BUSYGROUP Consumer Group name already exists")

        client = RedisClient()
        result = client.xgroup_create('stream1', 'group1')

        assert result is False

    def test_xgroup_create_failure(self, mock_redis_pool, mock_redis_client):
        """Test XGROUP CREATE failure"""
        mock_redis_client.xgroup_create.side_effect = redis.ResponseError("Unknown error")

        client = RedisClient()

        with pytest.raises(CustomRedisConnectionError):
            client.xgroup_create('stream1', 'group1')


class TestRedisClientSetGet:
    """Test SET/GET operations"""

    def test_set_get(self, mock_redis_pool, mock_redis_client):
        """Test SET and GET operations"""
        mock_redis_client.set.return_value = True
        mock_redis_client.get.return_value = "test_value"

        client = RedisClient()

        # SET
        result = client.set("test_key", "test_value")
        assert result is True

        # GET
        value = client.get("test_key")
        assert value == "test_value"

    def test_set_with_expiration(self, mock_redis_pool, mock_redis_client):
        """Test SET with expiration"""
        mock_redis_client.set.return_value = True

        client = RedisClient()
        result = client.set("test_key", "test_value", ex=3600)

        assert result is True
        call_kwargs = mock_redis_client.set.call_args[1]
        assert call_kwargs['ex'] == 3600


class TestRedisClientSetOperations:
    """Test SET data structure operations"""

    def test_sadd(self, mock_redis_pool, mock_redis_client):
        """Test SADD operation"""
        mock_redis_client.sadd.return_value = 2

        client = RedisClient()
        count = client.sadd("test_set", "value1", "value2")

        assert count == 2
        mock_redis_client.sadd.assert_called_once_with("test_set", "value1", "value2")

    def test_sismember_true(self, mock_redis_pool, mock_redis_client):
        """Test SISMEMBER when element exists"""
        mock_redis_client.sismember.return_value = True

        client = RedisClient()
        result = client.sismember("test_set", "value1")

        assert result is True

    def test_sismember_false(self, mock_redis_pool, mock_redis_client):
        """Test SISMEMBER when element doesn't exist"""
        mock_redis_client.sismember.return_value = False

        client = RedisClient()
        result = client.sismember("test_set", "nonexistent")

        assert result is False


class TestRedisClientContextManager:
    """Test context manager functionality"""

    def test_context_manager(self, mock_redis_pool, mock_redis_client):
        """Test using RedisClient as context manager"""
        mock_pool_instance = MagicMock()
        mock_redis_pool.return_value = mock_pool_instance

        with RedisClient() as client:
            assert client is not None

        # Verify pool.disconnect() was called on exit
        mock_pool_instance.disconnect.assert_called_once()


class TestRedisClientXLEN:
    """Test XLEN stream length operation"""

    def test_xlen(self, mock_redis_pool, mock_redis_client):
        """Test getting stream length"""
        mock_redis_client.xlen.return_value = 42

        client = RedisClient()
        length = client.xlen("test_stream")

        assert length == 42
        mock_redis_client.xlen.assert_called_once_with("test_stream")

    def test_xlen_error_returns_zero(self, mock_redis_pool, mock_redis_client):
        """Test XLEN error handling returns 0"""
        mock_redis_client.xlen.side_effect = redis.RedisError("Stream error")

        client = RedisClient()
        length = client.xlen("test_stream")

        assert length == 0
