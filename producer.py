#!/usr/bin/env python3
"""
Email Producer - IMAP to Redis Streams
Fetches emails from Gmail via IMAP and pushes to Redis Streams.
"""
import sys
import time
import signal
import argparse
from typing import Optional
from datetime import datetime

from config.settings import settings
from src.auth.oauth2_gmail import create_oauth2_from_config
from src.imap.imap_client import create_imap_client_from_config, GmailIMAPClient
from src.producer.state_manager import ProducerStateManager
from src.common.redis_client import create_redis_client_from_config
from src.common.logging_config import setup_logging
from src.common.exceptions import (
    OAuth2AuthenticationError,
    IMAPConnectionError,
    StateManagementError,
    RedisConnectionError
)

logger = setup_logging(__name__, level=settings.logging.level)

# Global flag for graceful shutdown
running = True


def signal_handler(signum, frame):
    """Handle shutdown signals (SIGINT, SIGTERM)"""
    global running
    logger.info(f"Received signal {signum}, initiating graceful shutdown...")
    running = False


def setup_signal_handlers():
    """Register signal handlers for graceful shutdown"""
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    logger.info("Signal handlers registered")


class EmailProducer:
    """Main producer class orchestrating email ingestion"""

    def __init__(
        self,
        username: str,
        mailbox: str = "INBOX",
        batch_size: int = 50,
        poll_interval: int = 60
    ):
        """
        Initialize email producer.

        Args:
            username: Gmail email address
            mailbox: Mailbox to monitor
            batch_size: Max emails to fetch per poll
            poll_interval: Seconds between polls
        """
        self.username = username
        self.mailbox = mailbox
        self.batch_size = batch_size
        self.poll_interval = poll_interval

        # Initialize components
        logger.info("Initializing producer components...")

        self.redis_client = create_redis_client_from_config(settings)
        logger.info("✓ Redis client initialized")

        self.oauth2 = create_oauth2_from_config(settings)
        logger.info("✓ OAuth2 manager initialized")

        self.imap_client: Optional[GmailIMAPClient] = None
        self.state_manager = ProducerStateManager(self.redis_client, username)
        logger.info("✓ State manager initialized")

        self.stream_name = settings.redis.stream_name
        self.max_stream_length = settings.redis.max_stream_length

        logger.info(f"Producer initialized for {username}/{mailbox}")

    def verify_connectivity(self):
        """Verify Redis and OAuth2 connectivity before starting"""
        logger.info("Verifying connectivity...")

        # Test Redis
        if not self.redis_client.ping():
            raise RedisConnectionError("Redis ping failed")
        logger.info("✓ Redis connection verified")

        # Test OAuth2
        if not self.oauth2.is_token_valid():
            logger.info("Token invalid or expired, authenticating...")
            self.oauth2.authenticate()
        logger.info("✓ OAuth2 token valid")

    def fetch_and_push_emails(self) -> int:
        """
        Fetch new emails and push to Redis Stream.

        Returns:
            Number of emails processed

        Raises:
            Various exceptions if operations fail
        """
        # Connect IMAP if needed
        if not self.imap_client:
            self.imap_client = create_imap_client_from_config(settings, self.oauth2)
            self.imap_client.connect()

        # Select mailbox and get UIDVALIDITY
        current_uidvalidity, total_messages = self.imap_client.select_mailbox(self.mailbox)

        # Check for UIDVALIDITY change
        if self.state_manager.check_uidvalidity_change(self.mailbox, current_uidvalidity):
            logger.warning(
                f"UIDVALIDITY changed for {self.mailbox}! "
                "Mailbox was reset. Starting from beginning."
            )
            self.state_manager.reset_mailbox_state(self.mailbox)

        # Get last processed UID
        last_uid = self.state_manager.get_last_uid(self.mailbox)
        logger.info(f"Mailbox: {self.mailbox}, UIDVALIDITY: {current_uidvalidity}, Last UID: {last_uid}, Total: {total_messages}")

        # Fetch new UIDs
        new_uids = self.imap_client.fetch_uids_since(last_uid, self.batch_size)

        if not new_uids:
            logger.debug("No new emails found")
            self.state_manager.update_last_poll_time(self.mailbox)
            return 0

        # Fetch and parse messages
        logger.info(f"Fetching {len(new_uids)} new emails...")
        messages = self.imap_client.fetch_messages(new_uids)

        # Push to Redis Stream
        pushed_count = 0
        for message in messages:
            try:
                # Serialize message to JSON
                payload = message.to_json()

                # Push to stream
                msg_id = self.redis_client.xadd(
                    self.stream_name,
                    {'payload': payload},
                    maxlen=self.max_stream_length
                )

                logger.debug(f"Pushed email UID {message.uid} to stream: {msg_id}")
                pushed_count += 1

            except Exception as e:
                logger.error(f"Failed to push email UID {message.uid}: {e}")
                # Continue with other messages

        # Update state atomically with last successfully pushed UID
        if pushed_count > 0:
            last_pushed_uid = messages[pushed_count - 1].uid
            self.state_manager.atomic_update_state(
                self.mailbox,
                current_uidvalidity,
                last_pushed_uid
            )
            self.state_manager.increment_email_count(self.mailbox, pushed_count)

            logger.info(
                f"✓ Successfully processed {pushed_count}/{len(messages)} emails. "
                f"Last UID: {last_pushed_uid}"
            )

        return pushed_count

    def run(self, dry_run: bool = False):
        """
        Main producer loop.

        Args:
            dry_run: If True, fetch emails but don't push to Redis
        """
        logger.info("=" * 60)
        logger.info(f"Email Producer Starting")
        logger.info(f"Username: {self.username}")
        logger.info(f"Mailbox: {self.mailbox}")
        logger.info(f"Poll interval: {self.poll_interval}s")
        logger.info(f"Batch size: {self.batch_size}")
        logger.info(f"Stream: {self.stream_name}")
        logger.info(f"Dry run: {dry_run}")
        logger.info("=" * 60)

        try:
            # Verify connectivity
            self.verify_connectivity()

            # Get initial state
            state = self.state_manager.get_state_summary(self.mailbox)
            logger.info(f"Initial state: {state}")

            # Main loop
            poll_count = 0
            total_processed = 0

            while running:
                poll_count += 1
                logger.info(f"\n--- Poll #{poll_count} at {datetime.utcnow().isoformat()}Z ---")

                try:
                    if dry_run:
                        logger.info("DRY RUN: Would fetch and push emails")
                        time.sleep(self.poll_interval)
                        continue

                    # Fetch and push emails
                    count = self.fetch_and_push_emails()
                    total_processed += count

                    if count > 0:
                        logger.info(f"Processed {count} emails (total: {total_processed})")

                except IMAPConnectionError as e:
                    logger.error(f"IMAP error: {e}. Reconnecting on next poll...")
                    if self.imap_client:
                        self.imap_client.disconnect()
                        self.imap_client = None

                except StateManagementError as e:
                    logger.error(f"State management error: {e}")

                except RedisConnectionError as e:
                    logger.error(f"Redis error: {e}. Will retry...")

                except Exception as e:
                    logger.error(f"Unexpected error: {e}", exc_info=True)

                # Sleep until next poll
                logger.debug(f"Sleeping for {self.poll_interval}s...")
                for _ in range(self.poll_interval):
                    if not running:
                        break
                    time.sleep(1)

        except KeyboardInterrupt:
            logger.info("Interrupted by user")

        finally:
            self.cleanup()

        logger.info(f"Producer stopped. Total emails processed: {total_processed}")

    def cleanup(self):
        """Clean up resources"""
        logger.info("Cleaning up resources...")

        if self.imap_client:
            self.imap_client.disconnect()

        if self.redis_client:
            self.redis_client.close()

        logger.info("Cleanup complete")


def main():
    """Main entry point"""
    parser = argparse.ArgumentParser(description="Email Producer - IMAP to Redis Streams")
    parser.add_argument(
        "--username",
        help="Gmail email address (default: from config or IMAP_USER env var)"
    )
    parser.add_argument(
        "--mailbox",
        default="INBOX",
        help="Mailbox to monitor (default: INBOX)"
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=50,
        help="Max emails to fetch per poll (default: 50)"
    )
    parser.add_argument(
        "--poll-interval",
        type=int,
        help=f"Seconds between polls (default: {settings.imap.poll_interval_seconds})"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Fetch emails but don't push to Redis"
    )
    parser.add_argument(
        "--auth-setup",
        action="store_true",
        help="Run OAuth2 setup flow and exit"
    )

    args = parser.parse_args()

    # OAuth2 setup mode
    if args.auth_setup:
        logger.info("Running OAuth2 setup...")
        try:
            oauth = create_oauth2_from_config(settings)
            oauth.authenticate(force_reauth=True)
            logger.info("✓ OAuth2 setup complete!")
            logger.info(f"Token saved to: {oauth.token_file}")
            return 0
        except Exception as e:
            logger.error(f"OAuth2 setup failed: {e}")
            return 1

    # Determine username
    username = args.username
    if not username:
        # Try to get from IMAP_USER env var or extract from client_id
        import os
        username = os.getenv('IMAP_USER')
        if not username:
            # Try to derive from OAuth2 client_id if it looks like an email
            if '@' in settings.oauth2.client_id:
                username = settings.oauth2.client_id
            else:
                logger.error("Username required. Use --username or set IMAP_USER env var")
                return 1

    # Setup signal handlers
    setup_signal_handlers()

    # Create and run producer
    try:
        producer = EmailProducer(
            username=username,
            mailbox=args.mailbox,
            batch_size=args.batch_size,
            poll_interval=args.poll_interval or settings.imap.poll_interval_seconds
        )

        producer.run(dry_run=args.dry_run)
        return 0

    except OAuth2AuthenticationError as e:
        logger.error(f"OAuth2 authentication failed: {e}")
        logger.error("Run with --auth-setup to authenticate")
        return 1

    except Exception as e:
        logger.error(f"Producer failed: {e}", exc_info=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
