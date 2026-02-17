"""
Unit tests for ProducerStateManager.
"""
import pytest
from unittest.mock import MagicMock, patch

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.producer.state_manager import ProducerStateManager, create_state_manager_from_config
from src.common.exceptions import StateManagementError


@pytest.fixture
def mock_redis():
    """Create a mock RedisClient"""
    return MagicMock()


@pytest.fixture
def state_manager(mock_redis):
    """Create a ProducerStateManager with mock Redis"""
    return ProducerStateManager(mock_redis, "user@gmail.com")


class TestStateManagerInit:
    """Test ProducerStateManager initialization"""

    def test_init_stores_params(self, mock_redis):
        sm = ProducerStateManager(mock_redis, "test@gmail.com")
        assert sm.username == "test@gmail.com"
        assert sm.key_prefix == "producer_state:test@gmail.com"

    def test_make_key(self, state_manager):
        key = state_manager._make_key("INBOX", "last_uid")
        assert key == "producer_state:user@gmail.com:INBOX:last_uid"


class TestGetSetLastUID:
    """Test get_last_uid and set_last_uid"""

    def test_get_last_uid_returns_stored_value(self, state_manager, mock_redis):
        mock_redis.get.return_value = "12345"
        result = state_manager.get_last_uid("INBOX")
        assert result == 12345

    def test_get_last_uid_returns_zero_when_none(self, state_manager, mock_redis):
        mock_redis.get.return_value = None
        result = state_manager.get_last_uid("INBOX")
        assert result == 0

    def test_get_last_uid_raises_on_error(self, state_manager, mock_redis):
        mock_redis.get.side_effect = Exception("redis down")
        with pytest.raises(StateManagementError):
            state_manager.get_last_uid("INBOX")

    def test_set_last_uid(self, state_manager, mock_redis):
        state_manager.set_last_uid("INBOX", 999)
        mock_redis.set.assert_called_once_with(
            "producer_state:user@gmail.com:INBOX:last_uid", "999"
        )

    def test_set_last_uid_raises_on_error(self, state_manager, mock_redis):
        mock_redis.set.side_effect = Exception("redis down")
        with pytest.raises(StateManagementError):
            state_manager.set_last_uid("INBOX", 1)


class TestGetSetUIDVALIDITY:
    """Test UIDVALIDITY operations"""

    def test_get_uidvalidity_stored(self, state_manager, mock_redis):
        mock_redis.get.return_value = "67890"
        result = state_manager.get_uidvalidity("INBOX")
        assert result == 67890

    def test_get_uidvalidity_none(self, state_manager, mock_redis):
        mock_redis.get.return_value = None
        result = state_manager.get_uidvalidity("INBOX")
        assert result is None

    def test_set_uidvalidity(self, state_manager, mock_redis):
        state_manager.set_uidvalidity("INBOX", 67890)
        mock_redis.set.assert_called_once_with(
            "producer_state:user@gmail.com:INBOX:uidvalidity", "67890"
        )


class TestCheckUIDVALIDITYChange:
    """Test UIDVALIDITY change detection"""

    def test_first_time_no_change(self, state_manager, mock_redis):
        """First time stored UIDVALIDITY is None -> store and return False"""
        mock_redis.get.return_value = None
        result = state_manager.check_uidvalidity_change("INBOX", 12345)
        assert result is False
        # Should have stored the new value
        mock_redis.set.assert_called()

    def test_same_uidvalidity_no_change(self, state_manager, mock_redis):
        mock_redis.get.return_value = "12345"
        result = state_manager.check_uidvalidity_change("INBOX", 12345)
        assert result is False

    def test_different_uidvalidity_changed(self, state_manager, mock_redis):
        mock_redis.get.return_value = "12345"
        result = state_manager.check_uidvalidity_change("INBOX", 99999)
        assert result is True


class TestResetMailboxState:
    """Test reset_mailbox_state"""

    def test_reset_sets_uid_to_zero(self, state_manager, mock_redis):
        state_manager.reset_mailbox_state("INBOX")
        mock_redis.set.assert_called_with(
            "producer_state:user@gmail.com:INBOX:last_uid", "0"
        )


class TestUpdateLastPollTime:
    """Test update_last_poll_time"""

    def test_sets_timestamp(self, state_manager, mock_redis):
        state_manager.update_last_poll_time("INBOX")
        key = "producer_state:user@gmail.com:INBOX:last_poll"
        mock_redis.set.assert_called_once()
        call_args = mock_redis.set.call_args
        assert call_args[0][0] == key
        assert call_args[0][1].endswith("Z")

    def test_does_not_raise_on_error(self, state_manager, mock_redis):
        """Non-critical: should not raise on error"""
        mock_redis.set.side_effect = Exception("redis down")
        state_manager.update_last_poll_time("INBOX")  # Should not raise


class TestGetLastPollTime:
    """Test get_last_poll_time"""

    def test_returns_timestamp(self, state_manager, mock_redis):
        mock_redis.get.return_value = "2026-02-17T10:00:00Z"
        result = state_manager.get_last_poll_time("INBOX")
        assert result == "2026-02-17T10:00:00Z"

    def test_returns_none_on_error(self, state_manager, mock_redis):
        mock_redis.get.side_effect = Exception("redis down")
        result = state_manager.get_last_poll_time("INBOX")
        assert result is None


class TestIncrementEmailCount:
    """Test increment_email_count"""

    def test_increment_from_zero(self, state_manager, mock_redis):
        mock_redis.get.return_value = None
        state_manager.increment_email_count("INBOX", 5)
        mock_redis.set.assert_called_once_with(
            "producer_state:user@gmail.com:INBOX:total_emails", "5"
        )

    def test_increment_existing(self, state_manager, mock_redis):
        mock_redis.get.return_value = "10"
        state_manager.increment_email_count("INBOX", 3)
        mock_redis.set.assert_called_once_with(
            "producer_state:user@gmail.com:INBOX:total_emails", "13"
        )

    def test_does_not_raise_on_error(self, state_manager, mock_redis):
        mock_redis.get.side_effect = Exception("redis down")
        state_manager.increment_email_count("INBOX")  # Should not raise


class TestGetStateSummary:
    """Test get_state_summary"""

    def test_returns_summary(self, state_manager, mock_redis):
        def mock_get(key):
            if "last_uid" in key:
                return "100"
            if "uidvalidity" in key:
                return "67890"
            if "last_poll" in key:
                return "2026-02-17T10:00:00Z"
            if "total_emails" in key:
                return "50"
            return None

        mock_redis.get.side_effect = mock_get

        summary = state_manager.get_state_summary("INBOX")
        assert summary["mailbox"] == "INBOX"
        assert summary["last_uid"] == 100
        assert summary["uidvalidity"] == 67890
        assert summary["total_emails"] == 50


class TestAtomicUpdateState:
    """Test atomic_update_state"""

    def test_successful_update(self, state_manager, mock_redis):
        """Test successful atomic state update"""
        # UIDVALIDITY same (no change)
        def mock_get(key):
            if "uidvalidity" in key:
                return "12345"
            return None
        mock_redis.get.side_effect = mock_get

        result = state_manager.atomic_update_state("INBOX", 12345, 999)
        assert result is True

    def test_raises_on_uidvalidity_mismatch(self, state_manager, mock_redis):
        """Test raises error when UIDVALIDITY changed during update"""
        mock_redis.get.return_value = "11111"  # stored differs from current

        with pytest.raises(StateManagementError):
            state_manager.atomic_update_state("INBOX", 99999, 100)


class TestCreateStateManagerFromConfig:
    """Test factory function"""

    def test_creates_instance(self, mock_redis):
        mock_config = MagicMock()
        result = create_state_manager_from_config(mock_config, mock_redis, "u@g.com")
        assert isinstance(result, ProducerStateManager)
        assert result.username == "u@g.com"
