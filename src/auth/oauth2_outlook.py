"""
OAuth2 authentication for Outlook using MSAL (Microsoft Authentication Library).
Handles token storage, refresh, and IMAP XOAUTH2 authentication for
Microsoft 365 / Outlook.com accounts.
"""
import os
import json
import base64
from pathlib import Path
from typing import Optional, Dict, Any
from datetime import datetime, timedelta, timezone

import msal

from src.common.logging_config import get_logger
from src.common.exceptions import OAuth2AuthenticationError, TokenRefreshError

logger = get_logger(__name__)

# Outlook IMAP OAuth2 scope
OUTLOOK_SCOPES = [
    "https://outlook.office365.com/IMAP.AccessAsUser.All",
    "offline_access",
]

# Microsoft identity platform endpoints
AUTHORITY_BASE = "https://login.microsoftonline.com"


class OAuth2Outlook:
    """
    OAuth2 authentication manager for Outlook/Microsoft 365 IMAP access.
    Uses MSAL for the OAuth2 flow with device-code or interactive browser auth.
    """

    def __init__(
        self,
        client_id: str,
        client_secret: str = "",
        tenant_id: str = "common",
        token_file: str = "tokens/outlook_token.json",
        redirect_uri: str = "http://localhost:8080",
    ):
        """
        Initialize OAuth2 manager for Outlook.

        Args:
            client_id: Azure AD application (client) ID
            client_secret: Azure AD client secret (empty for public client apps)
            tenant_id: Azure AD tenant ID or 'common' / 'organizations' / 'consumers'
            token_file: Path to store/load MSAL token cache
            redirect_uri: OAuth2 redirect URI
        """
        self.client_id = client_id
        self.client_secret = client_secret
        self.tenant_id = tenant_id
        self.token_file = Path(token_file)
        self.redirect_uri = redirect_uri
        self.authority = f"{AUTHORITY_BASE}/{tenant_id}"

        # MSAL token cache (serializable to disk)
        self._cache = msal.SerializableTokenCache()

        # Current access token info
        self._access_token: Optional[str] = None
        self._token_expiry: Optional[datetime] = None

        # Ensure token directory exists
        self.token_file.parent.mkdir(parents=True, exist_ok=True)

        # Build the MSAL application
        self._app = self._build_msal_app()

        logger.info(
            f"Outlook OAuth2 manager initialized, "
            f"tenant={tenant_id}, token file: {self.token_file}"
        )

    def _build_msal_app(self) -> msal.ClientApplication:
        """
        Build the MSAL application (confidential or public).

        Returns:
            MSAL ClientApplication instance

        Note:
            Both ConfidentialClientApplication and PublicClientApplication
            support device_flow methods used in _run_auth_flow().
        """
        app: msal.ClientApplication
        if self.client_secret:
            app = msal.ConfidentialClientApplication(
                client_id=self.client_id,
                client_credential=self.client_secret,
                authority=self.authority,
                token_cache=self._cache,
            )
        else:
            app = msal.PublicClientApplication(
                client_id=self.client_id,
                authority=self.authority,
                token_cache=self._cache,
            )
        return app

    def load_credentials(self) -> bool:
        """
        Load token cache from file if it exists.

        Returns:
            True if cache loaded and contains at least one account, False otherwise
        """
        if not self.token_file.exists():
            logger.info("Token file not found, need to authenticate")
            return False

        try:
            cache_data = self.token_file.read_text(encoding="utf-8")
            self._cache.deserialize(cache_data)
            # Rebuild app with loaded cache
            self._app = self._build_msal_app()

            accounts = self._app.get_accounts()
            if accounts:
                logger.info(
                    f"Credentials loaded from token file "
                    f"({len(accounts)} account(s))"
                )
                return True
            else:
                logger.info("Token cache loaded but no accounts found")
                return False

        except Exception as e:
            logger.error(f"Failed to load credentials: {e}")
            return False

    def save_credentials(self):
        """Save token cache to file."""
        try:
            if self._cache.has_state_changed:
                self.token_file.write_text(
                    self._cache.serialize(), encoding="utf-8"
                )
                logger.info(f"Credentials saved to {self.token_file}")
        except Exception as e:
            logger.error(f"Failed to save credentials: {e}")
            raise OAuth2AuthenticationError(f"Failed to save credentials: {e}")

    def authenticate(self, force_reauth: bool = False):
        """
        Authenticate and acquire a valid access token.

        Tries silent acquisition first (from cache), then falls back to
        interactive browser-based or device-code flow.

        Args:
            force_reauth: Force a new interactive authentication flow

        Returns:
            Access token string

        Raises:
            OAuth2AuthenticationError: If authentication fails
        """
        # Try loading cached credentials
        if not force_reauth:
            self.load_credentials()

            # Attempt silent token acquisition from cache
            accounts = self._app.get_accounts()
            if accounts:
                result = self._app.acquire_token_silent(
                    OUTLOOK_SCOPES, account=accounts[0]
                )
                if result and "access_token" in result:
                    self._set_token_from_result(result)
                    logger.info("Token acquired silently from cache")
                    self.save_credentials()
                    return result["access_token"]

        # Need interactive authentication
        logger.info("Starting interactive OAuth2 authentication flow...")
        return self._run_auth_flow()

    def _run_auth_flow(self) -> str:
        """
        Run the interactive OAuth2 authorization flow.

        Uses device-code flow for headless environments, or
        interactive browser flow when available.

        Returns:
            Access token string

        Raises:
            OAuth2AuthenticationError: If flow fails
        """
        try:
            # Try device code flow (works in headless and headed environments)
            flow = self._app.initiate_device_flow(scopes=OUTLOOK_SCOPES)  # type: ignore

            if "user_code" not in flow:
                raise OAuth2AuthenticationError(
                    f"Failed to initiate device flow: {flow.get('error_description', 'Unknown error')}"
                )

            # Display instructions to user
            print("\n" + "=" * 60)
            print("Microsoft Account Authentication")
            print("=" * 60)
            print(f"\n{flow['message']}\n")  # MSAL provides a user-friendly message
            print("=" * 60 + "\n")

            # Wait for user to complete auth in browser
            result = self._app.acquire_token_by_device_flow(flow)  # type: ignore

            if "access_token" not in result:
                error_desc = result.get("error_description", "Unknown error")
                raise OAuth2AuthenticationError(
                    f"Authentication failed: {error_desc}"
                )

            self._set_token_from_result(result)
            self.save_credentials()
            logger.info("OAuth2 authentication completed successfully")
            return result["access_token"]

        except OAuth2AuthenticationError:
            raise
        except Exception as e:
            logger.error(f"OAuth2 flow failed: {e}")
            raise OAuth2AuthenticationError(f"OAuth2 authentication failed: {e}")

    def _set_token_from_result(self, result: Dict[str, Any]):
        """
        Extract and store token data from MSAL result.

        Args:
            result: MSAL token acquisition result dict
        """
        self._access_token = result["access_token"]
        # MSAL returns expires_in (seconds)
        expires_in = result.get("expires_in", 3600)
        self._token_expiry = datetime.now(timezone.utc) + timedelta(
            seconds=int(expires_in)
        )

    def get_access_token(self) -> str:
        """
        Get current access token, refreshing if necessary.

        Returns:
            Valid access token string

        Raises:
            OAuth2AuthenticationError: If unable to get valid token
        """
        # If we have a cached token that isn't expiring soon, use it
        if self._access_token and self.is_token_valid():
            return self._access_token

        # Try silent acquisition (MSAL handles refresh automatically)
        accounts = self._app.get_accounts()
        if accounts:
            result = self._app.acquire_token_silent(
                OUTLOOK_SCOPES, account=accounts[0]
            )
            if result and "access_token" in result:
                self._set_token_from_result(result)
                self.save_credentials()
                logger.info("Access token refreshed silently")
                return result["access_token"]

        # No valid token and can't refresh silently
        raise TokenRefreshError(
            "Failed to refresh Outlook token. "
            "Run with --auth-setup to re-authenticate."
        )

    def generate_xoauth2_string(self, username: str) -> str:
        """
        Generate XOAUTH2 authentication string for IMAP.

        The XOAUTH2 SASL mechanism format is identical across providers
        (Gmail, Outlook, etc.) per RFC 7628.

        Args:
            username: Outlook/Microsoft email address

        Returns:
            Base64-encoded XOAUTH2 string
        """
        access_token = self.get_access_token()
        auth_string = f"user={username}\x01auth=Bearer {access_token}\x01\x01"
        return base64.b64encode(auth_string.encode()).decode()

    def is_token_valid(self) -> bool:
        """
        Check if current token is valid (exists and not expiring soon).

        Returns:
            True if token is valid for at least 5 more minutes
        """
        if not self._access_token:
            return False

        if not self._token_expiry:
            return False

        # 5-minute buffer before expiry
        buffer_time = datetime.now(timezone.utc) + timedelta(minutes=5)
        if self._token_expiry < buffer_time:
            logger.info("Token expiring soon")
            return False

        return True

    def revoke_token(self):
        """
        Clear cached credentials and delete token file.

        Microsoft does not provide a simple token revocation endpoint
        like Google. The recommended approach is to clear the local cache
        and let tokens expire naturally, or revoke via Azure AD portal.
        """
        try:
            # Remove all accounts from MSAL cache
            accounts = self._app.get_accounts()
            for account in accounts:
                self._app.remove_account(account)

            # Clear local state
            self._access_token = None
            self._token_expiry = None

            # Delete token file
            if self.token_file.exists():
                self.token_file.unlink()

            logger.info("Token cache cleared and file deleted")

        except Exception as e:
            logger.error(f"Token revocation failed: {e}")
            raise OAuth2AuthenticationError(f"Failed to revoke token: {e}")

    def get_token_info(self) -> Dict[str, Any]:
        """
        Get information about current token state.

        Returns:
            Dictionary with token info
        """
        accounts = self._app.get_accounts() if self._app else []

        info: Dict[str, Any] = {
            "status": "valid" if self.is_token_valid() else "invalid",
            "provider": "outlook",
            "tenant_id": self.tenant_id,
            "has_token": bool(self._access_token),
            "accounts": [
                {"username": a.get("username", ""), "home_account_id": a.get("home_account_id", "")}
                for a in accounts
            ],
            "scopes": OUTLOOK_SCOPES,
        }

        if self._token_expiry:
            info["expiry"] = self._token_expiry.isoformat()
            info["expires_in_seconds"] = (
                self._token_expiry - datetime.now(timezone.utc)
            ).total_seconds()

        return info


# Factory function for usage with config
def create_outlook_oauth2_from_config(config) -> OAuth2Outlook:
    """
    Create OAuth2Outlook instance from configuration object.

    Args:
        config: Configuration object with outlook_oauth2 settings

    Returns:
        Configured OAuth2Outlook instance
    """
    return OAuth2Outlook(
        client_id=config.outlook_oauth2.client_id,
        client_secret=config.outlook_oauth2.client_secret,
        tenant_id=config.outlook_oauth2.tenant_id,
        token_file=config.outlook_oauth2.token_file,
        redirect_uri=config.outlook_oauth2.redirect_uri,
    )


# CLI utility for initial setup
if __name__ == "__main__":
    import argparse
    from config.settings import settings

    parser = argparse.ArgumentParser(
        description="OAuth2 Outlook Authentication Setup"
    )
    parser.add_argument(
        "--setup", action="store_true", help="Run initial authentication flow"
    )
    parser.add_argument(
        "--info", action="store_true", help="Show token information"
    )
    parser.add_argument(
        "--revoke", action="store_true", help="Revoke current token"
    )
    parser.add_argument(
        "--refresh", action="store_true", help="Refresh current token"
    )

    args = parser.parse_args()

    try:
        oauth = create_outlook_oauth2_from_config(settings)

        if args.setup:
            print("Starting OAuth2 authentication flow for Outlook...")
            print("Follow the instructions to authenticate with your Microsoft account.")
            oauth.authenticate(force_reauth=True)
            print(f"\n✓ Authentication successful!")
            print(f"Token saved to: {oauth.token_file}")

        elif args.info:
            oauth.load_credentials()
            info = oauth.get_token_info()
            print("\nToken Information:")
            print(json.dumps(info, indent=2))

        elif args.revoke:
            print("Revoking token...")
            oauth.load_credentials()
            oauth.revoke_token()
            print("✓ Token revoked")

        elif args.refresh:
            print("Refreshing token...")
            oauth.load_credentials()
            oauth.get_access_token()
            print("✓ Token refreshed")

        else:
            parser.print_help()

    except Exception as e:
        print(f"\n✗ Error: {e}")
        exit(1)
