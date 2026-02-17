"""
Unit tests for OutlookIMAPClient.
"""
import pytest
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.imap.outlook_imap_client import OutlookIMAPClient, create_outlook_imap_client_from_config
from src.imap.imap_client import EmailMessage
from src.common.exceptions import IMAPConnectionError


@pytest.fixture
def mock_outlook_oauth2():
    """Mock OAuth2Outlook instance"""
    oauth = MagicMock()
    oauth.generate_xoauth2_string.return_value = "base64encodedstring"
    return oauth


@pytest.fixture
def outlook_client(mock_outlook_oauth2):
    """Create OutlookIMAPClient with mocked dependencies"""
    return OutlookIMAPClient(
        oauth2=mock_outlook_oauth2,
        username="test@outlook.com",
        host="outlook.office365.com",
        port=993,
    )


class TestOutlookIMAPClientInit:
    """Test OutlookIMAPClient initialization"""

    def test_default_host(self, mock_outlook_oauth2):
        """Test default host is outlook.office365.com"""
        client = OutlookIMAPClient(
            oauth2=mock_outlook_oauth2,
            username="user@outlook.com",
        )
        assert client.host == "outlook.office365.com"
        assert client.port == 993

    def test_custom_host_port(self, mock_outlook_oauth2):
        """Test custom host/port"""
        client = OutlookIMAPClient(
            oauth2=mock_outlook_oauth2,
            username="user@example.com",
            host="custom.imap.server.com",
            port=1993,
        )
        assert client.host == "custom.imap.server.com"
        assert client.port == 1993

    def test_stores_oauth2(self, outlook_client, mock_outlook_oauth2):
        assert outlook_client.oauth2 == mock_outlook_oauth2

    def test_initial_state(self, outlook_client):
        assert outlook_client.client is None
        assert outlook_client.current_mailbox is None
        assert outlook_client.current_uidvalidity is None
        assert outlook_client.username == "test@outlook.com"


class TestOutlookIMAPClientConnect:
    """Test connect method"""

    @patch("src.imap.outlook_imap_client.IMAPClient")
    def test_connect_success(self, mock_imap_cls, outlook_client):
        """Test successful connection"""
        mock_imap_instance = MagicMock()
        mock_imap_cls.return_value = mock_imap_instance

        outlook_client.connect()

        mock_imap_cls.assert_called_once_with(
            "outlook.office365.com", port=993, ssl=True, use_uid=True
        )
        mock_imap_instance.oauth2_login.assert_called_once_with(
            "test@outlook.com", "base64encodedstring"
        )
        assert outlook_client.client == mock_imap_instance

    @patch("src.imap.outlook_imap_client.IMAPClient")
    def test_connect_failure_raises(self, mock_imap_cls, outlook_client):
        """Test connection failure raises IMAPConnectionError"""
        mock_imap_cls.side_effect = Exception("Connection refused")

        with pytest.raises(IMAPConnectionError, match="Failed to connect to Outlook IMAP"):
            outlook_client.connect()

    @patch("src.imap.outlook_imap_client.IMAPClient")
    def test_connect_auth_failure(self, mock_imap_cls, outlook_client):
        """Test OAuth2 login failure raises IMAPConnectionError"""
        mock_imap_instance = MagicMock()
        mock_imap_instance.oauth2_login.side_effect = Exception("AUTHENTICATE failed")
        mock_imap_cls.return_value = mock_imap_instance

        with pytest.raises(IMAPConnectionError, match="AUTHENTICATE failed"):
            outlook_client.connect()


class TestOutlookIMAPClientInherited:
    """Test that inherited methods from GmailIMAPClient work correctly"""

    def test_disconnect(self, outlook_client):
        """Test disconnect cleans up state"""
        mock_imap = MagicMock()
        outlook_client.client = mock_imap
        outlook_client.current_mailbox = "INBOX"
        outlook_client.current_uidvalidity = 12345

        outlook_client.disconnect()

        mock_imap.logout.assert_called_once()
        assert outlook_client.client is None
        assert outlook_client.current_mailbox is None
        assert outlook_client.current_uidvalidity is None

    def test_select_mailbox(self, outlook_client):
        """Test select_mailbox works via inheritance"""
        mock_imap = MagicMock()
        mock_imap.select_folder.return_value = {
            b"UIDVALIDITY": 67890,
            b"EXISTS": 42,
        }
        outlook_client.client = mock_imap

        uidvalidity, count = outlook_client.select_mailbox("INBOX")

        assert uidvalidity == 67890
        assert count == 42
        assert outlook_client.current_mailbox == "INBOX"
        assert outlook_client.current_uidvalidity == 67890

    def test_fetch_uids_since(self, outlook_client):
        """Test fetch_uids_since works via inheritance"""
        mock_imap = MagicMock()
        mock_imap.search.return_value = [101, 102, 103]
        outlook_client.client = mock_imap
        outlook_client.current_mailbox = "INBOX"

        uids = outlook_client.fetch_uids_since(100, batch_size=50)

        assert uids == [101, 102, 103]

    def test_context_manager(self, mock_outlook_oauth2):
        """Test context manager support"""
        client = OutlookIMAPClient(
            oauth2=mock_outlook_oauth2,
            username="user@outlook.com",
        )
        with patch.object(client, "connect") as mock_connect:
            with patch.object(client, "disconnect") as mock_disconnect:
                with client as c:
                    assert c is client
                    mock_connect.assert_called_once()
                mock_disconnect.assert_called_once()


class TestOutlookIMAPClientFactory:
    """Test factory function"""

    def test_creates_instance(self, mock_outlook_oauth2):
        mock_config = MagicMock()
        mock_config.imap.user = "user@outlook.com"
        mock_config.imap.host = "outlook.office365.com"
        mock_config.imap.port = 993

        result = create_outlook_imap_client_from_config(mock_config, mock_outlook_oauth2)

        assert isinstance(result, OutlookIMAPClient)
        assert result.username == "user@outlook.com"
        assert result.host == "outlook.office365.com"
        assert result.port == 993

    def test_uses_config_values(self, mock_outlook_oauth2):
        """Test factory uses config override values"""
        mock_config = MagicMock()
        mock_config.imap.user = "admin@company.com"
        mock_config.imap.host = "custom-imap.company.com"
        mock_config.imap.port = 1993

        result = create_outlook_imap_client_from_config(mock_config, mock_outlook_oauth2)

        assert result.username == "admin@company.com"
        assert result.host == "custom-imap.company.com"
        assert result.port == 1993


class TestOutlookIMAPClientEmailMessageCompat:
    """Test that OutlookIMAPClient produces standard EmailMessage objects"""

    def test_email_message_from_outlook_is_generic(self):
        """Verify EmailMessage is provider-agnostic"""
        msg = EmailMessage(
            uid=200,
            uidvalidity=99999,
            mailbox="INBOX",
            from_addr="sender@outlook.com",
            to_addrs=["recipient@company.com"],
            subject="Outlook Test",
            date=datetime(2026, 2, 17, 14, 0, 0, tzinfo=timezone.utc),
            body_text="Test body from Outlook",
            body_html="<p>Test</p>",
            size=2500,
            headers={"Message-ID": "<outlook-msg-1@outlook.com>"},
            message_id="<outlook-msg-1@outlook.com>",
        )

        d = msg.to_dict()
        assert d["from"] == "sender@outlook.com"
        assert d["subject"] == "Outlook Test"
        assert d["message_id"] == "<outlook-msg-1@outlook.com>"
        assert "fetched_at" in d

        # JSON serialization works
        j = msg.to_json()
        assert "Outlook Test" in j
