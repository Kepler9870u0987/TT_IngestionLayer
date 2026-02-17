"""
OAuth2 authentication for Gmail using Google Auth libraries.
Handles token storage, refresh, and IMAP XOAUTH2 authentication.
"""
import os
import json
import base64
from pathlib import Path
from typing import Optional, Dict, Any
from datetime import datetime, timedelta, timezone

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.exceptions import RefreshError

from src.common.logging_config import get_logger
from src.common.exceptions import OAuth2AuthenticationError, TokenRefreshError

logger = get_logger(__name__)

# Gmail IMAP requires this scope
SCOPES = ['https://mail.google.com/']


class OAuth2Gmail:
    """
    OAuth2 authentication manager for Gmail IMAP access.
    Handles the full OAuth2 flow including token storage and refresh.
    """

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        token_file: str = "tokens/gmail_token.json",
        redirect_uri: str = "http://localhost:8080"
    ):
        """
        Initialize OAuth2 manager.

        Args:
            client_id: Google OAuth2 client ID
            client_secret: Google OAuth2 client secret
            token_file: Path to store/load token
            redirect_uri: OAuth2 redirect URI
        """
        self.client_id = client_id
        self.client_secret = client_secret
        self.token_file = Path(token_file)
        self.redirect_uri = redirect_uri
        self.credentials: Optional[Credentials] = None

        # Ensure token directory exists
        self.token_file.parent.mkdir(parents=True, exist_ok=True)

        logger.info(f"OAuth2 manager initialized, token file: {self.token_file}")

    def load_credentials(self) -> bool:
        """
        Load credentials from token file if it exists.

        Returns:
            True if credentials loaded successfully, False otherwise
        """
        if not self.token_file.exists():
            logger.info("Token file not found, need to authenticate")
            return False

        try:
            self.credentials = Credentials.from_authorized_user_file(
                str(self.token_file),
                SCOPES
            )
            logger.info("Credentials loaded from token file")
            return True
        except Exception as e:
            logger.error(f"Failed to load credentials: {e}")
            return False

    def save_credentials(self):
        """Save credentials to token file."""
        if not self.credentials:
            logger.warning("No credentials to save")
            return

        try:
            creds_data = {
                'token': self.credentials.token,
                'refresh_token': self.credentials.refresh_token,
                'token_uri': self.credentials.token_uri,
                'client_id': self.credentials.client_id,
                'client_secret': self.credentials.client_secret,
                'scopes': self.credentials.scopes,
                'expiry': self.credentials.expiry.isoformat() if self.credentials.expiry else None
            }

            with open(self.token_file, 'w') as f:
                json.dump(creds_data, f, indent=2)

            logger.info(f"Credentials saved to {self.token_file}")
        except Exception as e:
            logger.error(f"Failed to save credentials: {e}")
            raise OAuth2AuthenticationError(f"Failed to save credentials: {e}")

    def authenticate(self, force_reauth: bool = False) -> Credentials:
        """
        Authenticate and return valid credentials.
        Handles token refresh automatically.

        Args:
            force_reauth: Force new authentication flow even if token exists

        Returns:
            Valid Credentials object

        Raises:
            OAuth2AuthenticationError: If authentication fails
        """
        # Load existing credentials if not forcing reauth
        if not force_reauth and self.load_credentials():
            # Check if credentials are valid
            if self.credentials and self.credentials.valid:
                logger.info("Using existing valid credentials")
                return self.credentials

            # Try to refresh if expired
            if self.credentials and self.credentials.expired and self.credentials.refresh_token:
                logger.info("Token expired, refreshing...")
                try:
                    self.credentials.refresh(Request())
                    self.save_credentials()
                    logger.info("Token refreshed successfully")
                    return self.credentials
                except RefreshError as e:
                    logger.error(f"Token refresh failed: {e}")
                    # Fall through to reauth flow

        # Need new authentication
        logger.info("Starting OAuth2 authentication flow...")
        return self._run_auth_flow()

    def _run_auth_flow(self) -> Credentials:
        """
        Run the OAuth2 authorization flow.

        Returns:
            New Credentials object

        Raises:
            OAuth2AuthenticationError: If flow fails
        """
        try:
            # Create client config
            client_config = {
                "installed": {
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "redirect_uris": [self.redirect_uri],
                    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                    "token_uri": "https://oauth2.googleapis.com/token"
                }
            }

            flow = InstalledAppFlow.from_client_config(
                client_config,
                SCOPES,
                redirect_uri=self.redirect_uri
            )

            # Run local server flow
            self.credentials = flow.run_local_server(
                port=8080,
                prompt='consent',
                success_message='Authentication successful! You can close this window.'
            )

            self.save_credentials()
            logger.info("OAuth2 authentication completed successfully")
            return self.credentials

        except Exception as e:
            logger.error(f"OAuth2 flow failed: {e}")
            raise OAuth2AuthenticationError(f"OAuth2 authentication failed: {e}")

    def get_access_token(self) -> str:
        """
        Get current access token, refreshing if necessary.

        Returns:
            Valid access token string

        Raises:
            OAuth2AuthenticationError: If unable to get valid token
        """
        if not self.credentials:
            self.authenticate()

        # Refresh if expired
        if self.credentials.expired and self.credentials.refresh_token:
            try:
                logger.info("Access token expired, refreshing...")
                self.credentials.refresh(Request())
                self.save_credentials()
            except RefreshError as e:
                logger.error(f"Token refresh failed: {e}")
                raise TokenRefreshError(f"Failed to refresh token: {e}")

        if not self.credentials or not self.credentials.token:
            raise OAuth2AuthenticationError("No valid token available")

        return self.credentials.token

    def generate_xoauth2_string(self, username: str) -> str:
        """
        Generate XOAUTH2 authentication string for IMAP.

        Args:
            username: Gmail email address

        Returns:
            Base64-encoded XOAUTH2 string

        Example output format:
            user=username@gmail.com\x01auth=Bearer ya29.xxx\x01\x01
        """
        access_token = self.get_access_token()

        auth_string = f"user={username}\x01auth=Bearer {access_token}\x01\x01"
        return base64.b64encode(auth_string.encode()).decode()

    def is_token_valid(self) -> bool:
        """
        Check if current token is valid (exists and not expired).

        Returns:
            True if token is valid
        """
        if not self.credentials:
            return False

        if not self.credentials.valid:
            return False

        # Check expiry with 5-minute buffer
        if self.credentials.expiry:
            buffer_time = datetime.now(timezone.utc) + timedelta(minutes=5)
            if self.credentials.expiry.replace(tzinfo=timezone.utc) < buffer_time:
                logger.info("Token expiring soon")
                return False

        return True

    def revoke_token(self):
        """
        Revoke current token and delete token file.

        Raises:
            OAuth2AuthenticationError: If revocation fails
        """
        if not self.credentials:
            logger.warning("No credentials to revoke")
            return

        try:
            # Revoke token with Google
            import requests
            requests.post(
                'https://oauth2.googleapis.com/revoke',
                params={'token': self.credentials.token},
                headers={'content-type': 'application/x-www-form-urlencoded'}
            )

            # Delete token file
            if self.token_file.exists():
                self.token_file.unlink()

            self.credentials = None
            logger.info("Token revoked and file deleted")

        except Exception as e:
            logger.error(f"Token revocation failed: {e}")
            raise OAuth2AuthenticationError(f"Failed to revoke token: {e}")

    def get_token_info(self) -> Dict[str, Any]:
        """
        Get information about current token.

        Returns:
            Dictionary with token info (expiry, scopes, etc.)
        """
        if not self.credentials:
            return {"status": "no_token"}

        info = {
            "status": "valid" if self.credentials.valid else "invalid",
            "has_token": bool(self.credentials.token),
            "has_refresh_token": bool(self.credentials.refresh_token),
            "scopes": self.credentials.scopes if self.credentials.scopes else [],
        }

        if self.credentials.expiry:
            info["expiry"] = self.credentials.expiry.isoformat()
            info["expires_in_seconds"] = (
                self.credentials.expiry.replace(tzinfo=timezone.utc) - datetime.now(timezone.utc)
            ).total_seconds()

        return info


# Factory function for easier usage with config
def create_oauth2_from_config(config) -> OAuth2Gmail:
    """
    Create OAuth2Gmail instance from configuration object.

    Args:
        config: Configuration object with oauth2 settings

    Returns:
        Configured OAuth2Gmail instance
    """
    return OAuth2Gmail(
        client_id=config.oauth2.client_id,
        client_secret=config.oauth2.client_secret,
        token_file=config.oauth2.token_file,
        redirect_uri=config.oauth2.redirect_uri
    )


# CLI utility for initial setup
if __name__ == "__main__":
    import argparse
    from config.settings import settings

    parser = argparse.ArgumentParser(description="OAuth2 Gmail Authentication Setup")
    parser.add_argument("--setup", action="store_true", help="Run initial authentication flow")
    parser.add_argument("--info", action="store_true", help="Show token information")
    parser.add_argument("--revoke", action="store_true", help="Revoke current token")
    parser.add_argument("--refresh", action="store_true", help="Refresh current token")

    args = parser.parse_args()

    try:
        oauth = create_oauth2_from_config(settings)

        if args.setup:
            print("Starting OAuth2 authentication flow...")
            print("A browser window will open for authentication.")
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
            oauth.get_access_token()  # This will trigger refresh if needed
            print("✓ Token refreshed")

        else:
            parser.print_help()

    except Exception as e:
        print(f"\n✗ Error: {e}")
        exit(1)
