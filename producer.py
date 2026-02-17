#!/usr/bin/env python3
"""
Email Producer - IMAP to Redis Streams
Fetches emails from Gmail or Outlook via IMAP and pushes to Redis Streams.
Supports multiple email providers via EMAIL_PROVIDER env var or --provider CLI arg.
Integrated with Phase 4: shutdown manager, circuit breaker, health checks, correlation IDs.
"""
import sys
import time
import argparse
from typing import Optional, Union
from datetime import datetime, timezone

from config.settings import settings
from src.auth.oauth2_gmail import create_oauth2_from_config
from src.auth.oauth2_outlook import create_outlook_oauth2_from_config
from src.imap.imap_client import create_imap_client_from_config, GmailIMAPClient
from src.imap.outlook_imap_client import create_outlook_imap_client_from_config, OutlookIMAPClient
from src.producer.state_manager import ProducerStateManager
from src.common.redis_client import create_redis_client_from_config
from src.common.logging_config import setup_logging
from src.common.exceptions import (
    OAuth2AuthenticationError,
    IMAPConnectionError,
    StateManagementError,
    RedisConnectionError
)
from src.common.shutdown import ShutdownManager
from src.common.correlation import CorrelationContext, set_component
from src.common.circuit_breaker import CircuitBreakers, CircuitBreakerError
from src.common.health import HealthServer, HealthRegistry, HealthCheck
from src.common.batch import BatchProducer
from src.worker.recovery import ConnectionWatchdog
from src.monitoring.metrics import (
    get_metrics_collector,
    start_metrics_server,
    BackgroundMetricsUpdater,
)

SUPPORTED_PROVIDERS = ("gmail", "outlook")

logger = setup_logging(__name__, level=settings.logging.level)

# Set component name for logging
set_component("producer")


class EmailProducer:
    """Main producer class orchestrating email ingestion"""

    def __init__(
        self,
        username: str,
        mailbox: str = "INBOX",
        batch_size: int = 50,
        poll_interval: int = 60,
        provider: str = "gmail",
    ):
        """
        Initialize email producer.

        Args:
            username: Email address (Gmail or Outlook)
            mailbox: Mailbox to monitor
            batch_size: Max emails to fetch per poll
            poll_interval: Seconds between polls
            provider: Email provider ('gmail' or 'outlook')
        """
        self.username = username
        self.mailbox = mailbox
        self.batch_size = batch_size
        self.poll_interval = poll_interval
        self.provider = provider.lower()

        if self.provider not in SUPPORTED_PROVIDERS:
            raise ValueError(
                f"Unsupported email provider '{self.provider}'. "
                f"Supported: {', '.join(SUPPORTED_PROVIDERS)}"
            )

        # Initialize components
        logger.info(f"Initializing producer components (provider={self.provider})...")

        self.redis_client = create_redis_client_from_config(settings)
        logger.info("✓ Redis client initialized")

        # Provider-specific OAuth2 initialization
        if self.provider == "outlook":
            if not settings.outlook_oauth2.is_configured:
                raise OAuth2AuthenticationError(
                    "Outlook OAuth2 not configured. Set MICROSOFT_CLIENT_ID "
                    "in .env file. See docs/OUTLOOK_OAUTH2_SETUP.md for instructions."
                )
            self.oauth2 = create_outlook_oauth2_from_config(settings)
        else:
            if not settings.oauth2.is_configured:
                raise OAuth2AuthenticationError(
                    "OAuth2 not configured. Set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET "
                    "in .env file. See docs/OAUTH2_SETUP.md for instructions."
                )
            self.oauth2 = create_oauth2_from_config(settings)
        logger.info(f"✓ OAuth2 manager initialized ({self.provider})")

        self.imap_client: Optional[Union[GmailIMAPClient, OutlookIMAPClient]] = None
        self.state_manager = ProducerStateManager(self.redis_client, username)
        logger.info("✓ State manager initialized")

        self.stream_name = settings.redis.stream_name
        self.max_stream_length = settings.redis.max_stream_length

        # Circuit breakers
        self.redis_cb = CircuitBreakers.get(
            "redis",
            failure_threshold=settings.circuit_breaker.failure_threshold,
            recovery_timeout=settings.circuit_breaker.recovery_timeout_seconds,
            success_threshold=settings.circuit_breaker.success_threshold
        )
        self.imap_cb = CircuitBreakers.get(
            "imap",
            failure_threshold=settings.circuit_breaker.failure_threshold,
            recovery_timeout=settings.circuit_breaker.recovery_timeout_seconds,
            success_threshold=settings.circuit_breaker.success_threshold
        )

        # Shutdown manager
        self.shutdown = ShutdownManager()

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
        # Connect IMAP if needed (provider-aware)
        if not self.imap_client:
            if self.provider == "outlook":
                self.imap_client = create_outlook_imap_client_from_config(
                    settings, self.oauth2
                )
            else:
                self.imap_client = create_imap_client_from_config(
                    settings, self.oauth2
                )
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

        # Push to Redis Stream using batch pipeline
        batch = BatchProducer(
            redis_client=self.redis_client,
            stream_name=self.stream_name,
            batch_size=len(messages),  # flush all at once
            maxlen=self.max_stream_length
        )

        for message in messages:
            try:
                payload = message.to_json()
                batch.add({'payload': payload})
            except Exception as e:
                logger.error(f"Failed to serialize email UID {message.uid}: {e}")

        try:
            msg_ids = batch.flush()
            pushed_count = len(msg_ids)
            logger.debug(f"Batch pushed {pushed_count} emails to stream")
        except Exception as e:
            logger.error(f"Batch flush failed: {e}")
            pushed_count = 0

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
        logger.info(f"Provider: {self.provider}")
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

            # Setup health checks
            health_registry = HealthRegistry("producer")
            health_registry.register_check(
                HealthCheck("redis", lambda: self.redis_client.ping(), critical=True)
            )
            health_registry.register_stats_provider(
                "producer",
                lambda: {"poll_count": poll_count, "total_processed": total_processed}
            )
            health_server = HealthServer(
                health_registry,
                port=settings.monitoring.producer_health_port
            )
            health_server.start()

            # Setup Prometheus metrics server
            start_metrics_server(port=settings.monitoring.producer_metrics_port)
            metrics_updater = BackgroundMetricsUpdater(
                collector=get_metrics_collector(),
                redis_client=self.redis_client,
                stream_name=self.stream_name,
                dlq_stream_name=settings.dlq.stream_name,
            )
            metrics_updater.start()

            # Setup connection watchdog
            watchdog = ConnectionWatchdog(check_interval=30)
            watchdog.add_check("redis", lambda: self.redis_client.ping())
            watchdog.start()

            # Register shutdown callbacks
            self.shutdown.register(
                lambda: setattr(self, '_stop_flag', True),
                priority=0, name="stop_accepting"
            )
            self.shutdown.register(
                lambda: health_server.stop(),
                priority=5, name="health_server"
            )
            self.shutdown.register(
                lambda: watchdog.stop(),
                priority=5, name="watchdog"
            )
            self.shutdown.register(
                lambda: metrics_updater.stop(),
                priority=5, name="metrics_updater"
            )
            self.shutdown.register(
                lambda: self.cleanup(),
                priority=30, name="cleanup_resources"
            )

            # Main loop
            poll_count = 0
            total_processed = 0

            while self.shutdown.is_running:
                poll_count += 1

                # Each poll gets a unique correlation ID for tracing
                with CorrelationContext() as ctx:
                    logger.info(
                        f"--- Poll #{poll_count} at "
                        f"{datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')} ---"
                    )

                    try:
                        if dry_run:
                            logger.info("DRY RUN: Would fetch and push emails")
                            time.sleep(self.poll_interval)
                            continue

                        # Check circuit breakers before operations
                        if self.redis_cb.is_open:
                            logger.warning(
                                "Redis circuit breaker open, skipping poll"
                            )
                            time.sleep(5)
                            continue

                        if self.imap_cb.is_open:
                            logger.warning(
                                "IMAP circuit breaker open, skipping poll"
                            )
                            time.sleep(5)
                            continue

                        # Fetch and push emails
                        poll_start = time.time()
                        count = self.fetch_and_push_emails()
                        total_processed += count

                        # Record metrics
                        metrics = get_metrics_collector()
                        metrics.observe_poll_duration(time.time() - poll_start)
                        metrics.inc_imap_polls()
                        if count > 0:
                            metrics.inc_produced(count)

                        # Record success on circuit breakers
                        self.redis_cb.record_success()
                        self.imap_cb.record_success()

                        if count > 0:
                            logger.info(
                                f"Processed {count} emails "
                                f"(total: {total_processed})"
                            )

                    except IMAPConnectionError as e:
                        logger.error(f"IMAP error: {e}. Reconnecting on next poll...")
                        self.imap_cb.record_failure(e)
                        if self.imap_client:
                            self.imap_client.disconnect()
                            self.imap_client = None

                    except StateManagementError as e:
                        logger.error(f"State management error: {e}")

                    except RedisConnectionError as e:
                        logger.error(f"Redis error: {e}. Will retry...")
                        self.redis_cb.record_failure(e)

                    except CircuitBreakerError as e:
                        logger.warning(f"Circuit breaker: {e}")
                        time.sleep(e.retry_after)

                    except Exception as e:
                        logger.error(f"Unexpected error: {e}", exc_info=True)

                # Sleep until next poll (interruptible)
                logger.debug(f"Sleeping for {self.poll_interval}s...")
                for _ in range(self.poll_interval):
                    if not self.shutdown.is_running:
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
        help="Email address (default: from config or IMAP_USER env var)"
    )
    parser.add_argument(
        "--provider",
        choices=["gmail", "outlook"],
        help="Email provider (default: from EMAIL_PROVIDER env var or 'gmail')"
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

    # Determine provider
    provider = args.provider or settings.email_provider

    # OAuth2 setup mode
    if args.auth_setup:
        logger.info(f"Running OAuth2 setup for provider '{provider}'...")
        try:
            if provider == "outlook":
                if not settings.outlook_oauth2.is_configured:
                    logger.error(
                        "Outlook OAuth2 not configured. Set MICROSOFT_CLIENT_ID "
                        "in .env file. See docs/OUTLOOK_OAUTH2_SETUP.md."
                    )
                    return 1
                oauth = create_outlook_oauth2_from_config(settings)
            else:
                if not settings.oauth2.is_configured:
                    logger.error(
                        "OAuth2 not configured. Set GOOGLE_CLIENT_ID and "
                        "GOOGLE_CLIENT_SECRET in .env file."
                    )
                    return 1
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
        # Read from settings (IMAP_USER env var)
        username = settings.imap.user
        if not username:
            logger.error("Username required. Use --username or set IMAP_USER env var")
            return 1

    # Setup shutdown manager (replaces old signal handlers)
    shutdown = ShutdownManager()
    shutdown.install_signal_handlers()

    # Create and run producer
    try:
        producer = EmailProducer(
            username=username,
            mailbox=args.mailbox,
            batch_size=args.batch_size,
            poll_interval=args.poll_interval or settings.imap.poll_interval_seconds,
            provider=provider,
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
