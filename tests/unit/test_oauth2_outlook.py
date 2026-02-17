"""
Unit tests for OAuth2Outlook authentication manager.
"""
import pytest
import json
from unittest.mock import patch, MagicMock, PropertyMock
from pathlib import Path
from datetime import datetime, timedelta, timezone

import sys
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.auth.oauth2_outlook import OAuth2Outlook, create_outlook_oauth2_from_config, OUTLOOK_SCOPES
from src.common.exceptions import OAuth2AuthenticationError, TokenRefreshError


@pytest.fixture
def oauth2(tmp_path):
    """Create OAuth2Outlook instance with temp token path"""
    token_file = str(tmp_path / "test_outlook_token.json")
    with patch("src.auth.oauth2_outlook.msal") as mock_msal:
        mock_app = MagicMock()
        mock_app.get_accounts.return_value = []
        mock_msal.ConfidentialClientApplication.return_value = mock_app
        mock_msal.SerializableTokenCache.return_value = MagicMock()

        instance = OAuth2Outlook(
            client_id="test-client-id",
            client_secret="test-client-secret",
            tenant_id="test-tenant",
            token_file=token_file,
            redirect_uri="http://localhost:8080",
        )
    return instance


@pytest.fixture
def public_oauth2(tmp_path):
    """Create OAuth2Outlook as public client (no client secret)"""
    token_file = str(tmp_path / "test_outlook_token_public.json")
    with patch("src.auth.oauth2_outlook.msal") as mock_msal:
        mock_app = MagicMock()
        mock_app.get_accounts.return_value = []
        mock_msal.PublicClientApplication.return_value = mock_app
        mock_msal.SerializableTokenCache.return_value = MagicMock()

        instance = OAuth2Outlook(
            client_id="test-client-id",
            client_secret="",
            tenant_id="common",
            token_file=token_file,
        )
    return instance


class TestOAuth2OutlookInit:
    """Test OAuth2Outlook initialization"""

    def test_init_stores_params(self, oauth2):
        assert oauth2.client_id == "test-client-id"
        assert oauth2.client_secret == "test-client-secret"
        assert oauth2.tenant_id == "test-tenant"
        assert oauth2._access_token is None
        assert oauth2._token_expiry is None

    def test_init_creates_token_directory(self, tmp_path):
        token_dir = tmp_path / "subdir" / "tokens"
        token_file = str(token_dir / "token.json")
        with patch("src.auth.oauth2_outlook.msal") as mock_msal:
            mock_msal.ConfidentialClientApplication.return_value = MagicMock()
            mock_msal.SerializableTokenCache.return_value = MagicMock()
            OAuth2Outlook("cid", "csecret", token_file=token_file)
        assert token_dir.exists()

    def test_authority_url(self, oauth2):
        assert oauth2.authority == "https://login.microsoftonline.com/test-tenant"

    def test_builds_confidential_app_when_secret_provided(self, tmp_path):
        token_file = str(tmp_path / "t.json")
        with patch("src.auth.oauth2_outlook.msal") as mock_msal:
            mock_msal.SerializableTokenCache.return_value = MagicMock()
            mock_msal.ConfidentialClientApplication.return_value = MagicMock()
            OAuth2Outlook("cid", "secret", token_file=token_file)
            mock_msal.ConfidentialClientApplication.assert_called_once()

    def test_builds_public_app_when_no_secret(self, public_oauth2):
        public_oauth2._mock_msal.PublicClientApplication.assert_called_once()


class TestLoadCredentials:
    """Test load_credentials method"""

    def test_returns_false_when_file_missing(self, oauth2):
        result = oauth2.load_credentials()
        assert result is False

    def test_returns_true_when_cache_has_accounts(self, oauth2):
        """Test loading credentials from existing file with accounts"""
        Path(oauth2.token_file).write_text('{"AccessToken": {}}')

        # Mock deserialization success and accounts found
        # Also mock _build_msal_app to avoid real MSAL authority lookup
        with patch.object(oauth2, '_build_msal_app') as mock_build:
            mock_app = MagicMock()
            mock_app.get_accounts.return_value = [{"username": "user@outlook.com"}]
            mock_build.return_value = mock_app

            result = oauth2.load_credentials()
            assert result is True

    def test_returns_false_when_cache_empty(self, oauth2):
        """Test loading credentials with no cached accounts"""
        Path(oauth2.token_file).write_text('{"AccessToken": {}}')
        oauth2._app.get_accounts.return_value = []

        result = oauth2.load_credentials()
        assert result is False

    def test_returns_false_on_corrupt_file(self, oauth2):
        """Test handling corrupt token file"""
        Path(oauth2.token_file).write_text("not valid json")
        oauth2._cache.deserialize.side_effect = Exception("parse error")

        result = oauth2.load_credentials()
        assert result is False


class TestSaveCredentials:
    """Test save_credentials method"""

    def test_save_when_no_state_change(self, oauth2):
        """save_credentials when cache has no state change should not write"""
        oauth2._cache.has_state_changed = False
        oauth2.save_credentials()  # Should return early without writing

    def test_save_writes_cache(self, oauth2):
        """Test that credentials cache is saved"""
        oauth2._cache.has_state_changed = True
        oauth2._cache.serialize.return_value = '{"AccessToken": {"key": "value"}}'

        oauth2.save_credentials()

        assert Path(oauth2.token_file).exists()
        content = Path(oauth2.token_file).read_text(encoding="utf-8")
        assert "AccessToken" in content

    def test_save_raises_on_write_error(self, oauth2):
        """Test that save raises OAuth2AuthenticationError on failure"""
        oauth2._cache.has_state_changed = True
        oauth2._cache.serialize.side_effect = Exception("serialize error")

        with pytest.raises(OAuth2AuthenticationError):
            oauth2.save_credentials()


class TestAuthenticate:
    """Test authenticate method"""

    def test_uses_cached_token_silently(self, oauth2):
        """Test authenticate acquires token silently when cached"""
        oauth2._app.get_accounts.return_value = [{"username": "user@outlook.com"}]
        oauth2._app.acquire_token_silent.return_value = {
            "access_token": "cached-token",
            "expires_in": 3600,
        }
        # Simulate file exists for load_credentials
        Path(oauth2.token_file).write_text("{}")

        # Mock _build_msal_app to avoid real MSAL authority resolution
        with patch.object(oauth2, '_build_msal_app', return_value=oauth2._app):
            # Mock save to avoid MagicMock serialization error
            oauth2._cache.has_state_changed = False
            result = oauth2.authenticate()

        assert result == "cached-token"
        assert oauth2._access_token == "cached-token"
        oauth2._app.acquire_token_silent.assert_called_once()

    def test_falls_back_to_device_flow(self, oauth2):
        """Test authenticate falls back to device flow when no cached token"""
        oauth2._app.get_accounts.return_value = []
        oauth2._app.initiate_device_flow.return_value = {
            "user_code": "ABCDEF",
            "message": "Go to https://microsoft.com/devicelogin and enter ABCDEF",
        }
        oauth2._app.acquire_token_by_device_flow.return_value = {
            "access_token": "new-token",
            "expires_in": 3600,
        }
        oauth2._cache.has_state_changed = False

        result = oauth2.authenticate()
        assert result == "new-token"
        assert oauth2._access_token == "new-token"

    def test_force_reauth_skips_cache(self, oauth2):
        """Test force_reauth triggers device flow even with cached tokens"""
        oauth2._app.initiate_device_flow.return_value = {
            "user_code": "XYZ123",
            "message": "Go to ...",
        }
        oauth2._app.acquire_token_by_device_flow.return_value = {
            "access_token": "fresh-token",
            "expires_in": 3600,
        }
        oauth2._cache.has_state_changed = False

        result = oauth2.authenticate(force_reauth=True)
        assert result == "fresh-token"
        # Should NOT have called acquire_token_silent
        oauth2._app.acquire_token_silent.assert_not_called()

    def test_raises_on_device_flow_failure(self, oauth2):
        """Test raises OAuth2AuthenticationError when device flow fails"""
        oauth2._app.get_accounts.return_value = []
        oauth2._app.initiate_device_flow.return_value = {
            "error_description": "Device flow not supported",
        }

        with pytest.raises(OAuth2AuthenticationError, match="Device flow"):
            oauth2.authenticate()

    def test_raises_on_token_acquisition_failure(self, oauth2):
        """Test raises when token acquisition returns error"""
        oauth2._app.get_accounts.return_value = []
        oauth2._app.initiate_device_flow.return_value = {
            "user_code": "ABC",
            "message": "Go to ...",
        }
        oauth2._app.acquire_token_by_device_flow.return_value = {
            "error": "authorization_declined",
            "error_description": "User declined",
        }

        with pytest.raises(OAuth2AuthenticationError, match="User declined"):
            oauth2.authenticate()


class TestGetAccessToken:
    """Test get_access_token method"""

    def test_returns_valid_cached_token(self, oauth2):
        """Test returns cached token when valid"""
        oauth2._access_token = "valid-token"
        oauth2._token_expiry = datetime.now(timezone.utc) + timedelta(hours=1)

        result = oauth2.get_access_token()
        assert result == "valid-token"

    def test_refreshes_expired_token_silently(self, oauth2):
        """Test refreshes token via silent acquisition when expired"""
        oauth2._access_token = "old-token"
        oauth2._token_expiry = datetime.now(timezone.utc) - timedelta(hours=1)

        oauth2._app.get_accounts.return_value = [{"username": "user@outlook.com"}]
        oauth2._app.acquire_token_silent.return_value = {
            "access_token": "refreshed-token",
            "expires_in": 3600,
        }
        oauth2._cache.has_state_changed = False

        result = oauth2.get_access_token()
        assert result == "refreshed-token"
        assert oauth2._access_token == "refreshed-token"

    def test_raises_when_no_token_and_no_accounts(self, oauth2):
        """Test raises TokenRefreshError when unable to refresh"""
        oauth2._access_token = None
        oauth2._app.get_accounts.return_value = []

        with pytest.raises(TokenRefreshError):
            oauth2.get_access_token()

    def test_raises_when_silent_acquisition_fails(self, oauth2):
        """Test raises when silent acquire returns None"""
        oauth2._access_token = "old"
        oauth2._token_expiry = datetime.now(timezone.utc) - timedelta(hours=1)

        oauth2._app.get_accounts.return_value = [{"username": "user@outlook.com"}]
        oauth2._app.acquire_token_silent.return_value = None

        with pytest.raises(TokenRefreshError):
            oauth2.get_access_token()


class TestGenerateXOAuth2String:
    """Test XOAUTH2 string generation"""

    def test_generates_base64_string(self, oauth2):
        """Test that XOAUTH2 string is generated correctly"""
        oauth2._access_token = "test-access-token"
        oauth2._token_expiry = datetime.now(timezone.utc) + timedelta(hours=1)

        result = oauth2.generate_xoauth2_string("user@outlook.com")

        import base64
        decoded = base64.b64decode(result).decode()
        assert "user=user@outlook.com" in decoded
        assert "auth=Bearer test-access-token" in decoded

    def test_xoauth2_format_matches_rfc(self, oauth2):
        """Test XOAUTH2 format is RFC-compliant with \\x01 separators"""
        oauth2._access_token = "tok"
        oauth2._token_expiry = datetime.now(timezone.utc) + timedelta(hours=1)

        result = oauth2.generate_xoauth2_string("u@example.com")

        import base64
        decoded = base64.b64decode(result).decode()
        # RFC 7628: user=<user>\x01auth=Bearer <token>\x01\x01
        assert decoded == "user=u@example.com\x01auth=Bearer tok\x01\x01"


class TestIsTokenValid:
    """Test is_token_valid method"""

    def test_false_when_no_token(self, oauth2):
        assert oauth2.is_token_valid() is False

    def test_false_when_no_expiry(self, oauth2):
        oauth2._access_token = "tok"
        oauth2._token_expiry = None
        assert oauth2.is_token_valid() is False

    def test_true_when_valid(self, oauth2):
        oauth2._access_token = "tok"
        oauth2._token_expiry = datetime.now(timezone.utc) + timedelta(hours=1)
        assert oauth2.is_token_valid() is True

    def test_false_when_expiring_soon(self, oauth2):
        """Test returns False when token expires within 5 minutes"""
        oauth2._access_token = "tok"
        oauth2._token_expiry = datetime.now(timezone.utc) + timedelta(minutes=2)
        assert oauth2.is_token_valid() is False


class TestRevokeToken:
    """Test revoke_token method"""

    def test_revoke_clears_state(self, oauth2):
        """Test that revoke clears token and removes accounts"""
        oauth2._access_token = "tok"
        oauth2._token_expiry = datetime.now(timezone.utc)
        oauth2._app.get_accounts.return_value = [{"username": "u@o.com"}]

        Path(oauth2.token_file).write_text("{}")

        oauth2.revoke_token()

        assert oauth2._access_token is None
        assert oauth2._token_expiry is None
        assert not Path(oauth2.token_file).exists()
        oauth2._app.remove_account.assert_called_once()

    def test_revoke_with_no_accounts(self, oauth2):
        """Revoke when no accounts should not raise"""
        oauth2._app.get_accounts.return_value = []
        oauth2.revoke_token()  # Should not raise

    def test_revoke_raises_on_error(self, oauth2):
        """Test raises OAuth2AuthenticationError on failure"""
        oauth2._access_token = "tok"
        oauth2._app.get_accounts.side_effect = Exception("msal error")

        with pytest.raises(OAuth2AuthenticationError):
            oauth2.revoke_token()


class TestGetTokenInfo:
    """Test get_token_info method"""

    def test_invalid_when_no_token(self, oauth2):
        info = oauth2.get_token_info()
        assert info["status"] == "invalid"
        assert info["provider"] == "outlook"
        assert info["has_token"] is False

    def test_with_valid_token(self, oauth2):
        oauth2._access_token = "tok"
        oauth2._token_expiry = datetime.now(timezone.utc) + timedelta(hours=1)
        oauth2._app.get_accounts.return_value = [
            {"username": "user@outlook.com", "home_account_id": "id123"}
        ]

        info = oauth2.get_token_info()
        assert info["status"] == "valid"
        assert info["provider"] == "outlook"
        assert info["has_token"] is True
        assert info["tenant_id"] == "test-tenant"
        assert len(info["accounts"]) == 1
        assert info["accounts"][0]["username"] == "user@outlook.com"
        assert "expiry" in info
        assert "expires_in_seconds" in info


class TestCreateOutlookOAuth2FromConfig:
    """Test factory function"""

    def test_creates_instance(self, tmp_path):
        mock_config = MagicMock()
        mock_config.outlook_oauth2.client_id = "cid"
        mock_config.outlook_oauth2.client_secret = "csecret"
        mock_config.outlook_oauth2.tenant_id = "tenant1"
        mock_config.outlook_oauth2.token_file = str(tmp_path / "t.json")
        mock_config.outlook_oauth2.redirect_uri = "http://localhost:8080"

        with patch("src.auth.oauth2_outlook.msal") as mock_msal:
            mock_msal.ConfidentialClientApplication.return_value = MagicMock()
            mock_msal.SerializableTokenCache.return_value = MagicMock()

            result = create_outlook_oauth2_from_config(mock_config)

        assert isinstance(result, OAuth2Outlook)
        assert result.client_id == "cid"
        assert result.tenant_id == "tenant1"


class TestSetTokenFromResult:
    """Test _set_token_from_result internal method"""

    def test_sets_token_and_expiry(self, oauth2):
        result = {"access_token": "new-tok", "expires_in": 7200}
        oauth2._set_token_from_result(result)

        assert oauth2._access_token == "new-tok"
        assert oauth2._token_expiry is not None
        # Should expire in roughly 2 hours
        delta = oauth2._token_expiry - datetime.now(timezone.utc)
        assert 7100 < delta.total_seconds() < 7300

    def test_defaults_expires_in(self, oauth2):
        """Test defaults to 3600 when expires_in not provided"""
        result = {"access_token": "tok"}
        oauth2._set_token_from_result(result)

        delta = oauth2._token_expiry - datetime.now(timezone.utc)
        assert 3500 < delta.total_seconds() < 3700
