"""
IMAP client with OAuth2 authentication and UID/UIDVALIDITY tracking.
Handles email fetching with proper state management.
"""
import email
from email.header import decode_header
from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime
import json

from imapclient import IMAPClient
from imapclient.exceptions import IMAPClientError

from src.auth.oauth2_gmail import OAuth2Gmail
from src.common.logging_config import get_logger
from src.common.retry import retry_on_imap_error
from src.common.exceptions import IMAPConnectionError

logger = get_logger(__name__)


class EmailMessage:
    """Represents a parsed email message"""

    def __init__(
        self,
        uid: int,
        uidvalidity: int,
        mailbox: str,
        from_addr: str,
        to_addrs: List[str],
        subject: str,
        date: datetime,
        body_text: str,
        body_html: str,
        size: int,
        headers: Dict[str, str],
        message_id: str
    ):
        self.uid = uid
        self.uidvalidity = uidvalidity
        self.mailbox = mailbox
        self.from_addr = from_addr
        self.to_addrs = to_addrs
        self.subject =subject
        self.date = date
        self.body_text = body_text
        self.body_html = body_html
        self.size = size
        self.headers = headers
        self.message_id = message_id
        self.fetched_at = datetime.utcnow()

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for Redis storage"""
        return {
            "uid": self.uid,
            "uidvalidity": self.uidvalidity,
            "mailbox": self.mailbox,
            "from": self.from_addr,
            "to": self.to_addrs,
            "subject": self.subject,
            "date": self.date.isoformat() if self.date else None,
            "body_text": self.body_text[2000] if len(self.body_text) > 2000 else self.body_text,
            "body_html_preview": self.body_html[:500] if self.body_html else "",
            "size": self.size,
            "headers": self.headers,
            "message_id": self.message_id,
            "fetched_at": self.fetched_at.isoformat() + "Z"
        }

    def to_json(self) -> str:
        """Convert to JSON string"""
        return json.dumps(self.to_dict(), ensure_ascii=False)


class GmailIMAPClient:
    """
    IMAP client for Gmail with OAuth2 authentication.
    Handles UID/UIDVALIDITY tracking and email fetching.
    """

    def __init__(
        self,
        oauth2: OAuth2Gmail,
        username: str,
        host: str = "imap.gmail.com",
        port: int = 993
    ):
        """
        Initialize Gmail IMAP client.

        Args:
            oauth2: OAuth2Gmail instance for authentication
            username: Gmail email address
            host: IMAP server host
            port: IMAP server port
        """
        self.oauth2 = oauth2
        self.username = username
        self.host = host
        self.port = port
        self.client: Optional[IMAPClient] = None
        self.current_mailbox: Optional[str] = None
        self.current_uidvalidity: Optional[int] = None

        logger.info(f"Gmail IMAP client initialized for {username}")

    @retry_on_imap_error(max_attempts=5)
    def connect(self):
        """
        Connect to IMAP server with OAuth2 authentication.

        Raises:
            IMAPConnectionError: If connection fails
        """
        try:
            logger.info(f"Connecting to IMAP: {self.host}:{self.port}")

            self.client = IMAPClient(self.host, port=self.port, ssl=True, use_uid=True)

            # Authenticate with XOAUTH2
            xoauth2_string = self.oauth2.generate_xoauth2_string(self.username)
            self.client.oauth2_login(self.username, xoauth2_string)

            logger.info("IMAP connection established")

        except Exception as e:
            logger.error(f"IMAP connection failed: {e}")
            raise IMAPConnectionError(f"Failed to connect to IMAP: {e}")

    def disconnect(self):
        """Disconnect from IMAP server"""
        if self.client:
            try:
                self.client.logout()
                logger.info("IMAP connection closed")
            except Exception as e:
                logger.warning(f"Error during IMAP logout: {e}")
            finally:
                self.client = None
                self.current_mailbox = None
                self.current_uidvalidity = None

    def select_mailbox(self, mailbox: str = "INBOX") -> Tuple[int, int]:
        """
        Select mailbox and get UIDVALIDITY and message count.

        Args:
            mailbox: Mailbox name (default: INBOX)

        Returns:
            Tuple of (uidvalidity, message_count)

        Raises:
            IMAPConnectionError: If selection fails
        """
        if not self.client:
            self.connect()

        try:
            select_info = self.client.select_folder(mailbox)

            self.current_mailbox = mailbox
            self.current_uidvalidity = select_info[b'UIDVALIDITY']

            message_count = select_info[b'EXISTS']

            logger.info(
                f"Selected mailbox '{mailbox}': "
                f"UIDVALIDITY={self.current_uidvalidity}, "
                f"messages={message_count}"
            )

            return self.current_uidvalidity, message_count

        except Exception as e:
            logger.error(f"Failed to select mailbox '{mailbox}': {e}")
            raise IMAPConnectionError(f"Failed to select mailbox: {e}")

    def fetch_uids_since(self, last_uid: int, batch_size: int = 50) -> List[int]:
        """
        Fetch UIDs of messages since last_uid.

        Args:
            last_uid: Last processed UID
            batch_size: Maximum number of UIDs to return

        Returns:
            List of UIDs (sorted, limited to batch_size)

        Raises:
            IMAPConnectionError: If fetch fails
        """
        if not self.client or not self.current_mailbox:
            raise IMAPConnectionError("No mailbox selected")

        try:
            # Search for messages with UID > last_uid
            search_criteria = [f'UID', f'{last_uid + 1}:*']
            uids = self.client.search(search_criteria)

            if uids:
                # Sort and limit
                uids = sorted(uids)[:batch_size]
                logger.info(f"Found {len(uids)} new messages (UIDs: {uids[0]}-{uids[-1]})")
                return uids

            logger.debug("No new messages found")
            return []

        except Exception as e:
            logger.error(f"Failed to fetch UIDs: {e}")
            raise IMAPConnectionError(f"Failed to fetch UIDs: {e}")

    def fetch_messages(self, uids: List[int]) -> List[EmailMessage]:
        """
        Fetch and parse email messages by UIDs.

        Args:
            uids: List of message UIDs to fetch

        Returns:
            List of EmailMessage objects

        Raises:
            IMAPConnectionError: If fetch fails
        """
        if not self.client or not self.current_mailbox:
            raise IMAPConnectionError("No mailbox selected")

        if not uids:
            return []

        try:
            # Fetch message data
            fetch_data = self.client.fetch(
                uids,
                [
                    'RFC822.SIZE',
                    'ENVELOPE',
                    'BODY.PEEK[HEADER]',
                    'BODY.PEEK[TEXT]<0.5000>'  # First 5KB of body
                ]
            )

            messages = []
            for uid in uids:
                if uid not in fetch_data:
                    logger.warning(f"UID {uid} not in fetch results")
                    continue

                msg_data = fetch_data[uid]
                email_msg = self._parse_message(uid, msg_data)
                messages.append(email_msg)

            logger.info(f"Fetched and parsed {len(messages)} messages")
            return messages

        except Exception as e:
            logger.error(f"Failed to fetch messages: {e}")
            raise IMAPConnectionError(f"Failed to fetch messages: {e}")

    def _parse_message(self, uid: int, msg_data: Dict) -> EmailMessage:
        """
        Parse raw IMAP message data into EmailMessage.

        Args:
            uid: Message UID
            msg_data: Raw message data from IMAP fetch

        Returns:
            EmailMessage object
        """
        envelope = msg_data.get(b'ENVELOPE')
        size = msg_data.get(b'RFC822.SIZE', 0)

        # Parse envelope
        subject = self._decode_header(envelope.subject) if envelope and envelope.subject else ""
        from_addr = self._parse_address(envelope.from_[0]) if envelope and envelope.from_ else ""
        to_addrs = [self._parse_address(addr) for addr in envelope.to] if envelope and envelope.to else []
        message_date = envelope.date if envelope and envelope.date else datetime.utcnow()

        # Parse headers
        header_data = msg_data.get(b'BODY[HEADER]', b'')
        headers = self._parse_headers(header_data)

        # Get message ID
        message_id = headers.get('Message-ID', f"<uid-{uid}@local>")

        # Get body preview
        body_data = msg_data.get(b'BODY[TEXT]<0.5000>', b'')
        body_text = body_data.decode('utf-8', errors='ignore') if body_data else ""

        return EmailMessage(
            uid=uid,
            uidvalidity=self.current_uidvalidity,
            mailbox=self.current_mailbox,
            from_addr=from_addr,
            to_addrs=to_addrs,
            subject=subject,
            date=message_date,
            body_text=body_text,
            body_html='',  # Not fetching full HTML for efficiency
            size=size,
            headers=headers,
            message_id=message_id
        )

    @staticmethod
    def _decode_header(header_bytes) -> str:
        """Decode email header (handles encoding)"""
        if not header_bytes:
            return ""

        if isinstance(header_bytes, bytes):
            decoded = decode_header(header_bytes.decode('utf-8', errors='ignore'))
            parts = []
            for content, encoding in decoded:
                if isinstance(content, bytes):
                    parts.append(content.decode(encoding or 'utf-8', errors='ignore'))
                else:
                    parts.append(str(content))
            return ' '.join(parts)

        return str(header_bytes)

    @staticmethod
    def _parse_address(address_tuple) -> str:
        """Parse address tuple from envelope to email string"""
        if not address_tuple:
            return ""

        name, route, mailbox, host = address_tuple
        email_addr = f"{mailbox.decode() if isinstance(mailbox, bytes) else mailbox}@{host.decode() if isinstance(host, bytes) else host}"

        if name:
            name_str = name.decode('utf-8', errors='ignore') if isinstance(name, bytes) else name
            return f"{name_str} <{email_addr}>"

        return email_addr

    @staticmethod
    def _parse_headers(header_data: bytes) -> Dict[str, str]:
        """Parse email headers into dictionary"""
        if not header_data:
            return {}

        headers = {}
        try:
            msg = email.message_from_bytes(header_data)
            for key, value in msg.items():
                headers[key] = value
        except Exception as e:
            logger.warning(f"Failed to parse headers: {e}")

        return headers

    def __enter__(self):
        """Context manager entry"""
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit"""
        self.disconnect()


# Factory function
def create_imap_client_from_config(config, oauth2: OAuth2Gmail) -> GmailIMAPClient:
    """
    Create GmailIMAPClient from configuration.

    Args:
        config: Configuration object
        oauth2: OAuth2Gmail instance

    Returns:
        Configured GmailIMAPClient
    """
    return GmailIMAPClient(
        oauth2=oauth2,
        username=config.oauth2.client_id.split('@')[0] + '@gmail.com',  # Extract email
        host=config.imap.host,
        port=config.imap.port
    )
