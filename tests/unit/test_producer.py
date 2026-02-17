"""
Unit tests for EmailProducer main orchestration class.
Tests focus on the orchestration methods, not the main loop.
"""
import pytest
from unittest.mock import patch, MagicMock, PropertyMock

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.common.exceptions import (
    OAuth2AuthenticationError,
    IMAPConnectionError,
    RedisConnectionError,
    StateManagementError,
)


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
    mock.redis.max_stream_length = 1000
    mock.oauth2.is_configured = True
    mock.oauth2.client_id = "cid"
    mock.oauth2.client_secret = "csecret"
    mock.oauth2.token_file = "tokens/t.json"
    mock.oauth2.redirect_uri = "http://localhost:8080"
    mock.imap.host = "imap.gmail.com"
    mock.imap.port = 993
    mock.imap.poll_interval_seconds = 60
    mock.circuit_breaker.failure_threshold = 5
    mock.circuit_breaker.recovery_timeout_seconds = 60.0
    mock.circuit_breaker.success_threshold = 3
    mock.monitoring.producer_health_port = 8080
    mock.monitoring.producer_metrics_port = 9090
    mock.dlq.stream_name = "test_dlq"
    return mock


@pytest.fixture
def producer(mock_settings):
    """Create an EmailProducer with all dependencies mocked"""
    with patch("producer.settings", mock_settings), \
         patch("producer.create_redis_client_from_config") as mock_redis_factory, \
         patch("producer.create_oauth2_from_config") as mock_oauth_factory, \
         patch("producer.ProducerStateManager") as mock_sm_cls, \
         patch("producer.CircuitBreakers") as mock_cb, \
         patch("producer.ShutdownManager") as mock_shutdown, \
         patch("producer.setup_logging") as mock_log, \
         patch("producer.set_component"):

        mock_redis = MagicMock()
        mock_redis_factory.return_value = mock_redis

        mock_oauth = MagicMock()
        mock_oauth_factory.return_value = mock_oauth

        mock_state = MagicMock()
        mock_sm_cls.return_value = mock_state

        mock_cb_instance = MagicMock()
        mock_cb_instance.is_open = False
        mock_cb.get.return_value = mock_cb_instance

        mock_shutdown_inst = MagicMock()
        mock_shutdown_inst.is_running = True
        mock_shutdown.return_value = mock_shutdown_inst

        from producer import EmailProducer
        p = EmailProducer(
            username="test@gmail.com",
            mailbox="INBOX",
            batch_size=50,
            poll_interval=60
        )
        p._mock_redis = mock_redis  # type: ignore[attr-defined]
        p._mock_oauth = mock_oauth  # type: ignore[attr-defined]
        p._mock_state = mock_state  # type: ignore[attr-defined]
        yield p


class TestEmailProducerInit:
    """Test EmailProducer initialization"""

    def test_init_stores_params(self, producer):
        assert producer.username == "test@gmail.com"
        assert producer.mailbox == "INBOX"
        assert producer.batch_size == 50

    def test_init_raises_if_oauth_not_configured(self, mock_settings):
        """Test that producer raises if OAuth2 is not configured"""
        mock_settings.oauth2.is_configured = False

        with patch("producer.settings", mock_settings), \
             patch("producer.create_redis_client_from_config"), \
             patch("producer.CircuitBreakers"), \
             patch("producer.ShutdownManager"), \
             patch("producer.setup_logging"), \
             patch("producer.set_component"):

            from producer import EmailProducer
            with pytest.raises(OAuth2AuthenticationError, match="OAuth2 not configured"):
                EmailProducer("test@gmail.com")


class TestVerifyConnectivity:
    """Test verify_connectivity method"""

    def test_verify_success(self, producer):
        """Test successful connectivity check"""
        producer._mock_redis.ping.return_value = True
        producer._mock_oauth.is_token_valid.return_value = True

        producer.verify_connectivity()  # Should not raise

    def test_verify_redis_failure(self, producer):
        """Test Redis connectivity failure"""
        producer._mock_redis.ping.return_value = False

        with pytest.raises(RedisConnectionError):
            producer.verify_connectivity()

    def test_verify_triggers_auth_if_token_invalid(self, producer):
        """Test OAuth2 authentication is triggered when token invalid"""
        producer._mock_redis.ping.return_value = True
        producer._mock_oauth.is_token_valid.return_value = False

        producer.verify_connectivity()
        producer._mock_oauth.authenticate.assert_called_once()


class TestFetchAndPushEmails:
    """Test fetch_and_push_emails method"""

    def test_no_new_emails(self, producer, mock_settings):
        """Test when no new emails found"""
        with patch("producer.create_imap_client_from_config") as mock_imap_factory, \
             patch("producer.settings", mock_settings):
            mock_imap = MagicMock()
            mock_imap.select_mailbox.return_value = (12345, 10)
            mock_imap.fetch_uids_since.return_value = []
            mock_imap_factory.return_value = mock_imap

            producer._mock_state.check_uidvalidity_change.return_value = False
            producer._mock_state.get_last_uid.return_value = 100

            count = producer.fetch_and_push_emails()
            assert count == 0

    def test_push_new_emails(self, producer, mock_settings):
        """Test pushing new emails to stream via BatchProducer"""
        with patch("producer.create_imap_client_from_config") as mock_imap_factory, \
             patch("producer.settings", mock_settings), \
             patch("producer.BatchProducer") as mock_batch_cls:
            mock_imap = MagicMock()
            mock_imap.select_mailbox.return_value = (12345, 10)
            mock_imap.fetch_uids_since.return_value = [101, 102]

            mock_msg1 = MagicMock()
            mock_msg1.uid = 101
            mock_msg1.to_json.return_value = '{"uid": 101}'
            mock_msg2 = MagicMock()
            mock_msg2.uid = 102
            mock_msg2.to_json.return_value = '{"uid": 102}'
            mock_imap.fetch_messages.return_value = [mock_msg1, mock_msg2]
            mock_imap_factory.return_value = mock_imap

            mock_batch = MagicMock()
            mock_batch.flush.return_value = ["id-1", "id-2"]
            mock_batch_cls.return_value = mock_batch

            producer._mock_state.check_uidvalidity_change.return_value = False
            producer._mock_state.get_last_uid.return_value = 100

            count = producer.fetch_and_push_emails()
            assert count == 2
            assert mock_batch.add.call_count == 2
            mock_batch.flush.assert_called_once()

    def test_uidvalidity_change_resets_state(self, producer, mock_settings):
        """Test state reset when UIDVALIDITY changes"""
        with patch("producer.create_imap_client_from_config") as mock_imap_factory, \
             patch("producer.settings", mock_settings):
            mock_imap = MagicMock()
            mock_imap.select_mailbox.return_value = (99999, 10)
            mock_imap.fetch_uids_since.return_value = []
            mock_imap_factory.return_value = mock_imap

            producer._mock_state.check_uidvalidity_change.return_value = True
            producer._mock_state.get_last_uid.return_value = 0

            producer.fetch_and_push_emails()
            producer._mock_state.reset_mailbox_state.assert_called_once_with("INBOX")


class TestCleanup:
    """Test cleanup method"""

    def test_cleanup_disconnects_imap(self, producer):
        mock_imap = MagicMock()
        producer.imap_client = mock_imap

        producer.cleanup()
        mock_imap.disconnect.assert_called_once()

    def test_cleanup_closes_redis(self, producer):
        producer.cleanup()
        producer._mock_redis.close.assert_called_once()
