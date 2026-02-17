"""
IMAP client for Outlook/Microsoft 365 with OAuth2 authentication.
Handles email fetching via IMAP with UID/UIDVALIDITY tracking.
Reuses the same EmailMessage data class as the Gmail client.
"""
from typing import List, Optional, Tuple

from imapclient import IMAPClient

from src.auth.oauth2_outlook import OAuth2Outlook
from src.imap.imap_client import EmailMessage, GmailIMAPClient
from src.common.logging_config import get_logger
from src.common.retry import retry_on_imap_error
from src.common.exceptions import IMAPConnectionError

logger = get_logger(__name__)


class OutlookIMAPClient(GmailIMAPClient):
    """
    IMAP client for Outlook/Microsoft 365 with OAuth2 authentication.

    Inherits from GmailIMAPClient since the IMAP protocol and email parsing
    logic are identical — only the default host and auth provider differ.
    The XOAUTH2 SASL mechanism is the same across Gmail and Outlook.
    """

    def __init__(
        self,
        oauth2: OAuth2Outlook,
        username: str,
        host: str = "outlook.office365.com",
        port: int = 993,
    ):
        """
        Initialize Outlook IMAP client.

        Args:
            oauth2: OAuth2Outlook instance for authentication
            username: Outlook/Microsoft email address
            host: IMAP server host (default: outlook.office365.com)
            port: IMAP server port (default: 993)
        """
        # GmailIMAPClient.__init__ stores oauth2, username, host, port
        # and initializes client, current_mailbox, current_uidvalidity
        self.oauth2 = oauth2
        self.username = username
        self.host = host
        self.port = port
        self.client: Optional[IMAPClient] = None
        self.current_mailbox: Optional[str] = None
        self.current_uidvalidity: Optional[int] = None

        logger.info(f"Outlook IMAP client initialized for {username}")

    @retry_on_imap_error(max_attempts=5)
    def connect(self):
        """
        Connect to Outlook IMAP server with OAuth2 authentication.

        Raises:
            IMAPConnectionError: If connection fails
        """
        try:
            logger.info(f"Connecting to Outlook IMAP: {self.host}:{self.port}")

            self.client = IMAPClient(
                self.host, port=self.port, ssl=True, use_uid=True
            )

            # Authenticate with XOAUTH2 (same SASL mechanism as Gmail)
            xoauth2_string = self.oauth2.generate_xoauth2_string(self.username)
            self.client.oauth2_login(self.username, xoauth2_string)

            logger.info("Outlook IMAP connection established")

        except Exception as e:
            logger.error(f"Outlook IMAP connection failed: {e}")
            raise IMAPConnectionError(
                f"Failed to connect to Outlook IMAP: {e}"
            )


# Factory function
def create_outlook_imap_client_from_config(
    config, oauth2: OAuth2Outlook
) -> OutlookIMAPClient:
    """
    Create OutlookIMAPClient from configuration.

    Args:
        config: Configuration object with imap settings
        oauth2: OAuth2Outlook instance

    Returns:
        Configured OutlookIMAPClient
    """
    return OutlookIMAPClient(
        oauth2=oauth2,
        username=config.imap.user,
        host=config.imap.host,
        port=config.imap.port,
    )
