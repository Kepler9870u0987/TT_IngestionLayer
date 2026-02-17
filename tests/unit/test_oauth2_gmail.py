"""
Unit tests for OAuth2Gmail authentication manager.
"""
import pytest
import json
from unittest.mock import patch, MagicMock, mock_open
from pathlib import Path
from datetime import datetime, timedelta, timezone

import sys
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.auth.oauth2_gmail import OAuth2Gmail, create_oauth2_from_config
from src.common.exceptions import OAuth2AuthenticationError, TokenRefreshError


@pytest.fixture
def oauth2(tmp_path):
    """Create OAuth2Gmail instance with temp token path"""
    token_file = str(tmp_path / "test_token.json")
    return OAuth2Gmail(
        client_id="test-client-id",
        client_secret="test-client-secret",
        token_file=token_file,
        redirect_uri="http://localhost:8080"
    )


class TestOAuth2GmailInit:
    """Test OAuth2Gmail initialization"""

    def test_init_stores_params(self, tmp_path):
        token_file = str(tmp_path / "t.json")
        o = OAuth2Gmail("cid", "csecret", token_file=token_file)
        assert o.client_id == "cid"
        assert o.client_secret == "csecret"
        assert o.credentials is None

    def test_init_creates_token_directory(self, tmp_path):
        token_dir = tmp_path / "subdir" / "tokens"
        token_file = str(token_dir / "token.json")
        OAuth2Gmail("cid", "csecret", token_file=token_file)
        assert token_dir.exists()


class TestLoadCredentials:
    """Test load_credentials method"""

    def test_returns_false_when_file_missing(self, oauth2):
        result = oauth2.load_credentials()
        assert result is False

    @patch("src.auth.oauth2_gmail.Credentials")
    def test_returns_true_when_file_exists(self, mock_creds_cls, oauth2):
        """Test loading credentials from existing file"""
        # Create a dummy token file
        Path(oauth2.token_file).write_text('{"token": "fake"}')
        mock_creds_cls.from_authorized_user_file.return_value = MagicMock()

        result = oauth2.load_credentials()
        assert result is True
        assert oauth2.credentials is not None
        mock_creds_cls.from_authorized_user_file.assert_called_once()

    @patch("src.auth.oauth2_gmail.Credentials")
    def test_returns_false_on_corrupt_file(self, mock_creds_cls, oauth2):
        """Test handling corrupt token file"""
        Path(oauth2.token_file).write_text("not json")
        mock_creds_cls.from_authorized_user_file.side_effect = Exception("parse error")

        result = oauth2.load_credentials()
        assert result is False


class TestSaveCredentials:
    """Test save_credentials method"""

    def test_save_when_no_credentials(self, oauth2):
        """save_credentials with no credentials should not raise"""
        oauth2.save_credentials()  # Should return early

    def test_save_writes_json(self, oauth2):
        """Test that credentials are saved as JSON"""
        mock_creds = MagicMock()
        mock_creds.token = "access-token"
        mock_creds.refresh_token = "refresh-token"
        mock_creds.token_uri = "https://oauth2.googleapis.com/token"
        mock_creds.client_id = "cid"
        mock_creds.client_secret = "cs"
        mock_creds.scopes = ["https://mail.google.com/"]
        mock_creds.expiry = datetime(2026, 3, 1, 12, 0, 0)

        oauth2.credentials = mock_creds
        oauth2.save_credentials()

        saved = json.loads(Path(oauth2.token_file).read_text())
        assert saved["token"] == "access-token"
        assert saved["refresh_token"] == "refresh-token"
        assert saved["client_id"] == "cid"

    def test_save_raises_on_write_error(self, oauth2):
        """Test that save raises OAuth2AuthenticationError on failure"""
        mock_creds = MagicMock()
        mock_creds.token = "t"
        mock_creds.refresh_token = "r"
        mock_creds.token_uri = "u"
        mock_creds.client_id = "c"
        mock_creds.client_secret = "s"
        mock_creds.scopes = []
        mock_creds.expiry = None
        oauth2.credentials = mock_creds

        # Make the token file path unwritable
        with patch("builtins.open", side_effect=PermissionError("denied")):
            with pytest.raises(OAuth2AuthenticationError):
                oauth2.save_credentials()


class TestAuthenticate:
    """Test authenticate method"""

    @patch("src.auth.oauth2_gmail.Credentials")
    def test_uses_existing_valid_credentials(self, mock_creds_cls, oauth2):
        """Test authenticate returns existing valid credentials"""
        mock_creds = MagicMock()
        mock_creds.valid = True
        mock_creds.expired = False
        mock_creds_cls.from_authorized_user_file.return_value = mock_creds

        Path(oauth2.token_file).write_text('{"token": "x"}')
        result = oauth2.authenticate()

        assert result == mock_creds

    @patch("src.auth.oauth2_gmail.Request")
    @patch("src.auth.oauth2_gmail.Credentials")
    def test_refreshes_expired_token(self, mock_creds_cls, mock_request, oauth2):
        """Test automatic token refresh"""
        mock_creds = MagicMock()
        mock_creds.valid = False
        mock_creds.expired = True
        mock_creds.refresh_token = "refresh-tok"
        # Make attributes JSON-serializable for save_credentials
        mock_creds.token = "refreshed-token"
        mock_creds.token_uri = "https://oauth2.googleapis.com/token"
        mock_creds.client_id = "cid"
        mock_creds.client_secret = "csecret"
        mock_creds.scopes = ["https://mail.google.com/"]
        mock_creds.expiry = None
        mock_creds_cls.from_authorized_user_file.return_value = mock_creds

        Path(oauth2.token_file).write_text('{"token": "x"}')

        # After refresh, token becomes valid
        def do_refresh(request):
            mock_creds.valid = True
            mock_creds.expired = False

        mock_creds.refresh.side_effect = do_refresh

        result = oauth2.authenticate()
        mock_creds.refresh.assert_called_once()
        assert result == mock_creds

    @patch("src.auth.oauth2_gmail.InstalledAppFlow")
    def test_force_reauth_runs_flow(self, mock_flow_cls, oauth2):
        """Test force_reauth triggers new flow"""
        mock_flow = MagicMock()
        mock_creds = MagicMock()
        mock_creds.token = "new"
        mock_creds.refresh_token = "ref"
        mock_creds.token_uri = "u"
        mock_creds.client_id = "c"
        mock_creds.client_secret = "s"
        mock_creds.scopes = []
        mock_creds.expiry = None
        mock_flow.run_local_server.return_value = mock_creds
        mock_flow_cls.from_client_config.return_value = mock_flow

        result = oauth2.authenticate(force_reauth=True)
        assert result == mock_creds
        mock_flow.run_local_server.assert_called_once()


class TestGetAccessToken:
    """Test get_access_token method"""

    def test_get_token_when_valid(self, oauth2):
        """Test returns token when valid"""
        mock_creds = MagicMock()
        mock_creds.expired = False
        mock_creds.token = "valid-token"
        oauth2.credentials = mock_creds

        result = oauth2.get_access_token()
        assert result == "valid-token"

    @patch("src.auth.oauth2_gmail.Request")
    def test_refreshes_expired_token(self, mock_request, oauth2):
        """Test refreshes token when expired"""
        mock_creds = MagicMock()
        mock_creds.expired = True
        mock_creds.refresh_token = "ref"
        mock_creds.token = "refreshed-token"
        mock_creds.token_uri = "u"
        mock_creds.client_id = "c"
        mock_creds.client_secret = "s"
        mock_creds.scopes = []
        mock_creds.expiry = None
        oauth2.credentials = mock_creds

        result = oauth2.get_access_token()
        mock_creds.refresh.assert_called_once()

    @patch("src.auth.oauth2_gmail.Request")
    def test_raises_token_refresh_error(self, mock_request, oauth2):
        """Test raises TokenRefreshError on refresh failure"""
        from google.auth.exceptions import RefreshError

        mock_creds = MagicMock()
        mock_creds.expired = True
        mock_creds.refresh_token = "ref"
        mock_creds.refresh.side_effect = RefreshError("nope")
        oauth2.credentials = mock_creds

        with pytest.raises(TokenRefreshError):
            oauth2.get_access_token()


class TestGenerateXOAuth2String:
    """Test XOAUTH2 string generation"""

    def test_generates_base64_string(self, oauth2):
        """Test that XOAUTH2 string is generated correctly"""
        mock_creds = MagicMock()
        mock_creds.expired = False
        mock_creds.token = "ya29.abc123"
        oauth2.credentials = mock_creds

        result = oauth2.generate_xoauth2_string("user@gmail.com")

        import base64
        decoded = base64.b64decode(result).decode()
        assert "user=user@gmail.com" in decoded
        assert "auth=Bearer ya29.abc123" in decoded


class TestIsTokenValid:
    """Test is_token_valid method"""

    def test_false_when_no_credentials(self, oauth2):
        assert oauth2.is_token_valid() is False

    def test_false_when_invalid(self, oauth2):
        mock_creds = MagicMock()
        mock_creds.valid = False
        oauth2.credentials = mock_creds
        assert oauth2.is_token_valid() is False

    def test_true_when_valid(self, oauth2):
        mock_creds = MagicMock()
        mock_creds.valid = True
        mock_creds.expiry = datetime.now(timezone.utc) + timedelta(hours=1)
        oauth2.credentials = mock_creds
        assert oauth2.is_token_valid() is True

    def test_false_when_expiring_soon(self, oauth2):
        """Test returns False when token expires within 5 minutes"""
        mock_creds = MagicMock()
        mock_creds.valid = True
        mock_creds.expiry = datetime.now(timezone.utc) + timedelta(minutes=2)
        oauth2.credentials = mock_creds
        assert oauth2.is_token_valid() is False


class TestRevokeToken:
    """Test revoke_token method"""

    def test_revoke_with_no_credentials(self, oauth2):
        """revoke when no credentials should not raise"""
        oauth2.revoke_token()

    @patch("requests.post")
    def test_revoke_deletes_token_file(self, mock_post, oauth2):
        """Test that revoke deletes the token file"""
        mock_creds = MagicMock()
        mock_creds.token = "tok"
        oauth2.credentials = mock_creds

        # Create a token file
        Path(oauth2.token_file).write_text('{"token": "x"}')

        oauth2.revoke_token()

        assert not Path(oauth2.token_file).exists()
        assert oauth2.credentials is None


class TestGetTokenInfo:
    """Test get_token_info method"""

    def test_no_token(self, oauth2):
        info = oauth2.get_token_info()
        assert info["status"] == "no_token"

    def test_with_valid_token(self, oauth2):
        mock_creds = MagicMock()
        mock_creds.valid = True
        mock_creds.token = "tok"
        mock_creds.refresh_token = "ref"
        mock_creds.scopes = ["https://mail.google.com/"]
        mock_creds.expiry = datetime(2026, 6, 1, 12, 0, 0)
        oauth2.credentials = mock_creds

        info = oauth2.get_token_info()
        assert info["status"] == "valid"
        assert info["has_token"] is True
        assert info["has_refresh_token"] is True


class TestCreateOAuth2FromConfig:
    """Test factory function"""

    def test_creates_instance(self):
        mock_config = MagicMock()
        mock_config.oauth2.client_id = "cid"
        mock_config.oauth2.client_secret = "csecret"
        mock_config.oauth2.token_file = "tokens/t.json"
        mock_config.oauth2.redirect_uri = "http://localhost:8080"

        result = create_oauth2_from_config(mock_config)
        assert isinstance(result, OAuth2Gmail)
        assert result.client_id == "cid"
