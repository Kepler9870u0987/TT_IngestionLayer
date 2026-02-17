"""
Unit tests for GmailIMAPClient and EmailMessage.
"""
import pytest
from unittest.mock import patch, MagicMock, PropertyMock
from datetime import datetime

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.imap.imap_client import EmailMessage, GmailIMAPClient, create_imap_client_from_config
from src.common.exceptions import IMAPConnectionError


@pytest.fixture
def sample_email_message():
    """Create a sample EmailMessage"""
    return EmailMessage(
        uid=100,
        uidvalidity=12345,
        mailbox="INBOX",
        from_addr="sender@example.com",
        to_addrs=["recipient@example.com"],
        subject="Test Subject",
        date=datetime(2026, 2, 17, 10, 0, 0),
        body_text="Hello, this is a test email body.",
        body_html="<p>Hello</p>",
        size=1500,
        headers={"Message-ID": "<uid-100@example.com>", "Content-Type": "text/plain"},
        message_id="<uid-100@example.com>"
    )


@pytest.fixture
def mock_oauth2():
    """Mock OAuth2Gmail instance"""
    oauth = MagicMock()
    oauth.generate_xoauth2_string.return_value = "base64encodedstring"
    return oauth


@pytest.fixture
def imap_client(mock_oauth2):
    """Create GmailIMAPClient with mocked dependencies"""
    return GmailIMAPClient(
        oauth2=mock_oauth2,
        username="test@gmail.com",
        host="imap.gmail.com",
        port=993
    )


class TestEmailMessage:
    """Test EmailMessage data class"""

    def test_to_dict_basic_fields(self, sample_email_message):
        """Test to_dict includes all basic fields"""
        d = sample_email_message.to_dict()
        assert d["uid"] == 100
        assert d["uidvalidity"] == 12345
        assert d["mailbox"] == "INBOX"
        assert d["from"] == "sender@example.com"
        assert d["to"] == ["recipient@example.com"]
        assert d["subject"] == "Test Subject"
        assert d["message_id"] == "<uid-100@example.com>"
        assert d["size"] == 1500
        assert "fetched_at" in d

    def test_to_dict_truncates_body(self):
        """Test body_text is truncated to 2000 chars"""
        long_body = "x" * 3000
        msg = EmailMessage(
            uid=1, uidvalidity=1, mailbox="INBOX", from_addr="a@b.com",
            to_addrs=[], subject="s", date=datetime.utcnow(),
            body_text=long_body, body_html="", size=0, headers={},
            message_id="<1@local>"
        )
        d = msg.to_dict()
        assert len(d["body_text"]) == 2000

    def test_to_dict_no_truncation_short_body(self):
        """Test short body_text is returned as-is"""
        msg = EmailMessage(
            uid=1, uidvalidity=1, mailbox="INBOX", from_addr="a@b.com",
            to_addrs=[], subject="s", date=datetime.utcnow(),
            body_text="short", body_html="", size=0, headers={},
            message_id="<1@local>"
        )
        d = msg.to_dict()
        assert d["body_text"] == "short"

    def test_to_dict_html_preview(self):
        """Test body_html_preview is limited to 500 chars"""
        html = "<div>" + "a" * 600 + "</div>"
        msg = EmailMessage(
            uid=1, uidvalidity=1, mailbox="INBOX", from_addr="a@b.com",
            to_addrs=[], subject="s", date=datetime.utcnow(),
            body_text="", body_html=html, size=0, headers={},
            message_id="<1@local>"
        )
        d = msg.to_dict()
        assert len(d["body_html_preview"]) == 500

    def test_to_dict_empty_html(self):
        """Test empty html returns empty string"""
        msg = EmailMessage(
            uid=1, uidvalidity=1, mailbox="INBOX", from_addr="a@b.com",
            to_addrs=[], subject="s", date=datetime.utcnow(),
            body_text="", body_html="", size=0, headers={},
            message_id="<1@local>"
        )
        d = msg.to_dict()
        assert d["body_html_preview"] == ""

    def test_to_json_returns_valid_json(self, sample_email_message):
        """Test to_json returns valid JSON string"""
        import json
        j = sample_email_message.to_json()
        data = json.loads(j)
        assert data["uid"] == 100

    def test_to_dict_date_iso_format(self, sample_email_message):
        """Test date is serialized as ISO format"""
        d = sample_email_message.to_dict()
        assert d["date"] == "2026-02-17T10:00:00"

    def test_to_dict_none_date(self):
        """Test None date is handled"""
        msg = EmailMessage(
            uid=1, uidvalidity=1, mailbox="INBOX", from_addr="a@b.com",
            to_addrs=[], subject="s", date=None,
            body_text="", body_html="", size=0, headers={},
            message_id="<1@local>"
        )
        d = msg.to_dict()
        assert d["date"] is None


class TestGmailIMAPClientInit:
    """Test GmailIMAPClient initialization"""

    def test_init_stores_params(self, mock_oauth2):
        client = GmailIMAPClient(mock_oauth2, "user@gmail.com", "imap.test.com", 993)
        assert client.username == "user@gmail.com"
        assert client.host == "imap.test.com"
        assert client.port == 993
        assert client.client is None

    def test_init_defaults(self, mock_oauth2):
        client = GmailIMAPClient(mock_oauth2, "u@gmail.com")
        assert client.host == "imap.gmail.com"
        assert client.port == 993


class TestGmailIMAPClientConnect:
    """Test connect method"""

    @patch("src.imap.imap_client.IMAPClient")
    def test_connect_success(self, mock_imap_cls, imap_client):
        """Test successful connection"""
        mock_imap = MagicMock()
        mock_imap_cls.return_value = mock_imap

        imap_client.connect()

        mock_imap_cls.assert_called_once_with(
            "imap.gmail.com", port=993, ssl=True, use_uid=True
        )
        mock_imap.oauth2_login.assert_called_once()

    @patch("src.imap.imap_client.IMAPClient")
    def test_connect_failure_raises(self, mock_imap_cls, imap_client):
        """Test connection failure raises IMAPConnectionError"""
        mock_imap_cls.side_effect = Exception("conn failed")

        with pytest.raises(IMAPConnectionError):
            imap_client.connect()


class TestGmailIMAPClientDisconnect:
    """Test disconnect method"""

    def test_disconnect_with_active_client(self, imap_client):
        """Test disconnect cleans up"""
        mock_client = MagicMock()
        imap_client.client = mock_client
        imap_client.current_mailbox = "INBOX"
        imap_client.current_uidvalidity = 123

        imap_client.disconnect()

        mock_client.logout.assert_called_once()
        assert imap_client.client is None
        assert imap_client.current_mailbox is None

    def test_disconnect_with_no_client(self, imap_client):
        """Test disconnect with no active client"""
        imap_client.disconnect()  # Should not raise


class TestGmailIMAPClientSelectMailbox:
    """Test select_mailbox method"""

    @patch("src.imap.imap_client.IMAPClient")
    def test_select_mailbox(self, mock_imap_cls, imap_client):
        """Test selecting a mailbox"""
        mock_imap = MagicMock()
        mock_imap.select_folder.return_value = {
            b'UIDVALIDITY': 67890,
            b'EXISTS': 42
        }
        imap_client.client = mock_imap

        uidvalidity, count = imap_client.select_mailbox("INBOX")

        assert uidvalidity == 67890
        assert count == 42
        assert imap_client.current_mailbox == "INBOX"

    def test_select_mailbox_connects_if_needed(self, imap_client):
        """Test auto-connect when selecting mailbox"""
        imap_client.client = None

        with patch.object(imap_client, "connect") as mock_connect:
            # After connect, set the client mock
            def setup_client():
                imap_client.client = MagicMock()
                imap_client.client.select_folder.return_value = {
                    b'UIDVALIDITY': 1, b'EXISTS': 0
                }
            mock_connect.side_effect = setup_client

            imap_client.select_mailbox()
            mock_connect.assert_called_once()


class TestGmailIMAPClientFetchUids:
    """Test fetch_uids_since method"""

    def test_fetch_uids_since(self, imap_client):
        """Test fetching UIDs since a given UID"""
        mock_imap = MagicMock()
        mock_imap.search.return_value = [101, 102, 103, 104, 105]
        imap_client.client = mock_imap
        imap_client.current_mailbox = "INBOX"

        uids = imap_client.fetch_uids_since(100, batch_size=3)

        assert uids == [101, 102, 103]

    def test_fetch_uids_no_results(self, imap_client):
        """Test when no new UIDs found"""
        mock_imap = MagicMock()
        mock_imap.search.return_value = []
        imap_client.client = mock_imap
        imap_client.current_mailbox = "INBOX"

        uids = imap_client.fetch_uids_since(100)
        assert uids == []

    def test_fetch_uids_no_mailbox_raises(self, imap_client):
        """Test raises when no mailbox selected"""
        imap_client.client = MagicMock()
        imap_client.current_mailbox = None

        with pytest.raises(IMAPConnectionError):
            imap_client.fetch_uids_since(0)


class TestGmailIMAPClientFetchMessages:
    """Test fetch_messages method"""

    def test_fetch_empty_uids(self, imap_client):
        """Test fetch with empty UID list"""
        imap_client.client = MagicMock()
        imap_client.current_mailbox = "INBOX"

        result = imap_client.fetch_messages([])
        assert result == []

    def test_fetch_messages_no_mailbox_raises(self, imap_client):
        """Test raises when no mailbox selected"""
        imap_client.client = MagicMock()
        imap_client.current_mailbox = None

        with pytest.raises(IMAPConnectionError):
            imap_client.fetch_messages([1, 2])


class TestGmailIMAPClientContextManager:
    """Test context manager"""

    @patch("src.imap.imap_client.IMAPClient")
    def test_context_manager(self, mock_imap_cls, mock_oauth2):
        """Test context manager connects and disconnects"""
        mock_imap = MagicMock()
        mock_imap_cls.return_value = mock_imap

        with GmailIMAPClient(mock_oauth2, "u@gmail.com") as client:
            assert client is not None

        mock_imap.logout.assert_called_once()


class TestDecodeHeader:
    """Test _decode_header static method"""

    def test_decode_bytes(self):
        result = GmailIMAPClient._decode_header(b"Hello World")
        assert "Hello" in result

    def test_decode_str(self):
        result = GmailIMAPClient._decode_header("Hello World")
        assert result == "Hello World"

    def test_decode_empty(self):
        result = GmailIMAPClient._decode_header(None)
        assert result == ""


class TestParseAddress:
    """Test _parse_address static method"""

    def test_parse_address_with_name(self):
        addr = (b"John Doe", None, b"john", b"example.com")
        result = GmailIMAPClient._parse_address(addr)
        assert "john@example.com" in result
        assert "John Doe" in result

    def test_parse_address_without_name(self):
        addr = (None, None, b"alice", b"test.com")
        result = GmailIMAPClient._parse_address(addr)
        assert result == "alice@test.com"

    def test_parse_address_empty(self):
        result = GmailIMAPClient._parse_address(None)
        assert result == ""


class TestCreateIMAPClientFromConfig:
    """Test factory function"""

    def test_creates_instance(self, mock_oauth2):
        mock_config = MagicMock()
        mock_config.oauth2.client_id = "id@gmail.com"
        mock_config.imap.host = "imap.gmail.com"
        mock_config.imap.port = 993

        result = create_imap_client_from_config(mock_config, mock_oauth2)
        assert isinstance(result, GmailIMAPClient)
