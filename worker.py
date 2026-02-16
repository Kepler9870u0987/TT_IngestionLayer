#!/usr/bin/env python3
"""
Email Worker - Redis Streams Consumer with Idempotency & DLQ
Consumes emails from Redis Streams and processes them with retry logic.
Integrated with Phase 4: shutdown manager, circuit breaker, health checks,
correlation IDs, orphaned message recovery.
"""
import sys
import time
import argparse
from typing import Optional, Dict, Any
from datetime import datetime

from config.settings import settings
from src.common.redis_client import create_redis_client_from_config, RedisClient
from src.common.logging_config import setup_logging
from src.common.exceptions import (
    RedisConnectionError,
    ProcessingError
)
from src.common.shutdown import ShutdownManager
from src.common.correlation import CorrelationContext, set_component
from src.common.circuit_breaker import CircuitBreakers, CircuitBreakerError
from src.common.health import HealthServer, HealthRegistry, HealthCheck
from src.worker.idempotency import create_idempotency_manager_from_config
from src.worker.backoff import create_backoff_manager_from_config
from src.worker.dlq import create_dlq_manager_from_config
from src.worker.processor import create_processor_from_config
from src.worker.recovery import OrphanedMessageRecovery, ConnectionWatchdog

logger = setup_logging(__name__, level=settings.logging.level)

# Set component name for logging
set_component("worker")


class EmailWorker:
    """
    Main worker class orchestrating email consumption and processing.
    Uses consumer groups for horizontal scalability.
    """

    def __init__(
        self,
        stream_name: str,
        consumer_group: str,
        consumer_name: str,
        batch_size: int = 10,
        block_timeout_ms: int = 5000
    ):
        """
        Initialize email worker.

        Args:
            stream_name: Redis stream name to consume from
            consumer_group: Consumer group name
            consumer_name: This consumer's unique name
            batch_size: Number of messages to fetch per batch
            block_timeout_ms: Timeout for blocking read in milliseconds
        """
        self.stream_name = stream_name
        self.consumer_group = consumer_group
        self.consumer_name = consumer_name
        self.batch_size = batch_size
        self.block_timeout_ms = block_timeout_ms

        # Initialize components
        from config.settings import settings as cfg
        self.redis = RedisClient(
            host=cfg.redis.host,
            port=cfg.redis.port,
            password=cfg.redis.password,
            db=cfg.redis.db
        )
        self.idempotency = create_idempotency_manager_from_config(
            self.redis,
            ttl_hours=settings.idempotency.ttl_seconds // 3600
        )
        self.backoff = create_backoff_manager_from_config(
            initial_delay=float(settings.dlq.initial_backoff_seconds),
            max_delay=float(settings.dlq.max_backoff_seconds),
            max_retries=settings.dlq.max_retry_attempts
        )
        self.dlq = create_dlq_manager_from_config(
            self.redis,
            dlq_stream_name=settings.dlq.stream_name
        )
        self.processor = create_processor_from_config()

        # Circuit breaker
        self.redis_cb = CircuitBreakers.get(
            "redis",
            failure_threshold=settings.circuit_breaker.failure_threshold,
            recovery_timeout=settings.circuit_breaker.recovery_timeout_seconds,
            success_threshold=settings.circuit_breaker.success_threshold
        )

        # Shutdown manager
        self.shutdown = ShutdownManager()

        # Orphaned message recovery
        self.recovery = OrphanedMessageRecovery(
            redis_client=self.redis,
            stream_name=stream_name,
            consumer_group=consumer_group,
            consumer_name=consumer_name,
            min_idle_ms=settings.recovery.min_idle_ms,
            max_claim_count=settings.recovery.max_claim_count,
            max_delivery_count=settings.recovery.max_delivery_count
        )

        # Statistics
        self.messages_processed = 0
        self.messages_skipped = 0
        self.messages_failed = 0
        self.messages_dlq = 0
        self.messages_recovered = 0

        logger.info(
            f"EmailWorker initialized: stream={stream_name}, "
            f"group={consumer_group}, consumer={consumer_name}"
        )

    def ensure_consumer_group(self):
        """
        Ensure consumer group exists, create if not.
        """
        try:
            self.redis.xgroup_create(
                stream=self.stream_name,
                groupname=self.consumer_group,
                id="0",
                mkstream=True
            )
            logger.info(f"Consumer group created: {self.consumer_group}")
        except Exception as e:
            # Group might already exist
            if "BUSYGROUP" in str(e):
                logger.info(f"Consumer group already exists: {self.consumer_group}")
            else:
                logger.error(f"Failed to create consumer group: {e}")
                raise

    def process_message(
        self,
        message_id: str,
        message_data: Dict[str, Any]
    ) -> bool:
        """
        Process a single message with idempotency, retry, and DLQ handling.
        Each message gets a unique correlation ID for tracing.

        Args:
            message_id: Stream message ID
            message_data: Message data dictionary

        Returns:
            True if successfully processed or skipped (idempotent),
            False if should be retried
        """
        email_id = message_data.get("message_id", message_id)

        # Wrap processing in correlation context
        with CorrelationContext() as ctx:
            # Check idempotency
            if self.idempotency.is_duplicate(email_id):
                logger.info(f"Skipping duplicate message: {email_id}")
                self.messages_skipped += 1
                return True

            # Check if should retry (backoff logic)
            if not self.backoff.should_retry(email_id):
                retry_count = self.backoff.get_retry_count(email_id)
                logger.warning(
                    f"Message {email_id} exceeded max retries ({retry_count}), "
                    f"sending to DLQ"
                )
                
                # Send to DLQ
                try:
                    self.dlq.send_to_dlq(
                        message_id=email_id,
                        original_data=message_data,
                        error=Exception(f"Max retries exceeded: {retry_count}"),
                        retry_count=retry_count
                    )
                    self.messages_dlq += 1
                    
                    # Mark as processed to not retry again
                    self.idempotency.mark_processed(email_id)
                    return True
                except Exception as dlq_error:
                    logger.critical(f"Failed to send to DLQ: {dlq_error}")
                    return False

            # Process the message
            try:
                result = self.processor.process(message_data)
                
                # Mark as processed (idempotency)
                self.idempotency.mark_processed(email_id)
                
                # Record success
                self.backoff.record_success(email_id)
                self.messages_processed += 1
                
                logger.info(
                    f"Successfully processed: {email_id} "
                    f"(time: {result.get('processing_time_seconds', 0):.3f}s)"
                )
                return True

            except ProcessingError as e:
                # Processing failed, record for retry
                retry_count = self.backoff.record_failure(email_id)
                self.messages_failed += 1
                
                logger.error(
                    f"Processing failed for {email_id}: {e} "
                    f"(attempt {retry_count}/{settings.dlq.max_retry_attempts})"
                )
                return False

            except Exception as e:
                # Unexpected error
                retry_count = self.backoff.record_failure(email_id)
                self.messages_failed += 1
                
                logger.exception(
                    f"Unexpected error processing {email_id}: {e} "
                    f"(attempt {retry_count}/{settings.dlq.max_retry_attempts})"
                )
                return False

    def run(self):
        """
        Main worker loop - consume and process messages.
        """
        global running

        logger.info("Worker starting...")

        # Ensure consumer group exists
        self.ensure_consumer_group()

        # Main processing loop
        while running:
            try:
                # Read messages from stream using consumer group
                messages = self.redis.xreadgroup(
                    groupname=self.consumer_group,
                    consumername=self.consumer_name,
                    streams={self.stream_name: ">"},
                    count=self.batch_size,
                    block=self.block_timeout_ms
                )

                if not messages:
                    # No messages available, continue waiting
                    continue

                # Process each message in the batch
                for stream_name, stream_messages in messages:
                    for message_id, message_data in stream_messages:
                        logger.debug(f"Received message: {message_id}")

                        # Process message
                        success = self.process_message(message_id, message_data)

                        # Acknowledge message if processed successfully
                        if success:
                            self.redis.xack(
                                self.stream_name,
                                self.consumer_group,
                                message_id
                            )
                            logger.debug(f"Acknowledged message: {message_id}")
                        else:
                            # Don't ACK - will be retried later
                            logger.warning(
                                f"Message not acknowledged (will retry): {message_id}"
                            )

                # Log periodic stats
                if self.messages_processed % 100 == 0 and self.messages_processed > 0:
                    self.log_stats()

            except KeyboardInterrupt:
                logger.info("Keyboard interrupt received")
                break

            except RedisConnectionError as e:
                logger.error(f"Redis connection error: {e}")
                time.sleep(5)  # Wait before reconnecting

            except Exception as e:
                logger.exception(f"Unexpected error in worker loop: {e}")
                time.sleep(1)

        logger.info("Worker shutting down gracefully...")
        self.log_stats()

    def log_stats(self):
        """Log worker statistics."""
        total = (
            self.messages_processed + 
            self.messages_skipped + 
            self.messages_failed
        )
        
        logger.info(
            f"Worker Stats - Total: {total}, "
            f"Processed: {self.messages_processed}, "
            f"Skipped (duplicates): {self.messages_skipped}, "
            f"Failed: {self.messages_failed}, "
            f"DLQ: {self.messages_dlq}"
        )
        
        processor_stats = self.processor.get_stats()
        logger.info(
            f"Processor Stats - Success rate: {processor_stats['success_rate']:.2%}"
        )


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Email Worker - Consume and process emails from Redis Streams"
    )
    parser.add_argument(
        "--stream",
        default=settings.redis.stream_name,
        help=f"Redis stream name (default: {settings.redis.stream_name})"
    )
    parser.add_argument(
        "--group",
        default=settings.worker.consumer_group_name,
        help=f"Consumer group name (default: {settings.worker.consumer_group_name})"
    )
    parser.add_argument(
        "--consumer",
        default=settings.worker.consumer_name,
        help=f"Consumer name (default: {settings.worker.consumer_name})"
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=settings.worker.batch_size,
        help=f"Batch size (default: {settings.worker.batch_size})"
    )
    parser.add_argument(
        "--block-timeout",
        type=int,
        default=settings.worker.block_timeout_ms,
        help=f"Block timeout in ms (default: {settings.worker.block_timeout_ms})"
    )

    args = parser.parse_args()

    # Setup signal handlers
    setup_signal_handlers()

    # Create and run worker
    worker = EmailWorker(
        stream_name=args.stream,
        consumer_group=args.group,
        consumer_name=args.consumer,
        batch_size=args.batch_size,
        block_timeout_ms=args.block_timeout
    )

    try:
        worker.run()
    except Exception as e:
        logger.critical(f"Worker failed: {e}")
        sys.exit(1)

    logger.info("Worker terminated")
    sys.exit(0)


if __name__ == "__main__":
    main()
