"""Prometheus metrics exporter for the email ingestion pipeline."""
import argparse
import time
from typing import Optional

from prometheus_client import Counter, Gauge, Histogram, start_http_server

from config.settings import settings, Settings
from src.common.logging_config import get_logger

logger = get_logger(__name__)

EMAILS_PRODUCED = Counter(
    "email_ingestion_emails_produced_total",
    "Total emails produced into the Redis stream",
)
EMAILS_PROCESSED = Counter(
    "email_ingestion_emails_processed_total",
    "Total emails processed successfully by workers",
)
EMAILS_FAILED = Counter(
    "email_ingestion_emails_failed_total",
    "Total emails that failed processing",
)
DLQ_MESSAGES = Counter(
    "email_ingestion_dlq_messages_total",
    "Messages routed to the Dead Letter Queue",
)
BACKOFF_RETRIES = Counter(
    "email_ingestion_backoff_retries_total",
    "Retry attempts due to transient errors",
)
PROCESSING_LATENCY = Histogram(
    "email_ingestion_processing_latency_seconds",
    "End-to-end processing latency",
    buckets=(0.05, 0.1, 0.2, 0.5, 1, 2, 5),
)
IMAP_POLL_DURATION = Histogram(
    "email_ingestion_imap_poll_duration_seconds",
    "IMAP polling duration per batch",
    buckets=(0.1, 0.25, 0.5, 1, 2, 5),
)
QUEUE_DEPTH = Gauge(
    "email_ingestion_stream_depth",
    "Current Redis stream length",
)

_server_started = False


def start_metrics_server(port: Optional[int] = None) -> int:
    """Start the Prometheus metrics HTTP server if not already running."""
    global _server_started
    if _server_started:
        return port or (settings.monitoring.metrics_port if settings else 9090)

    resolved_port = port
    if resolved_port is None:
        try:
            resolved_port = settings.monitoring.metrics_port  # type: ignore[attr-defined]
        except Exception:
            resolved_port = 9090

    start_http_server(resolved_port)
    _server_started = True
    logger.info(f"Metrics server started on port {resolved_port}")
    return resolved_port


def observe_processing_latency(seconds: float) -> None:
    PROCESSING_LATENCY.observe(seconds)


def set_queue_depth(length: int) -> None:
    QUEUE_DEPTH.set(length)


def main() -> None:
    parser = argparse.ArgumentParser(description="Start Prometheus metrics server")
    parser.add_argument("--port", type=int, help="Port to expose metrics on")
    args = parser.parse_args()
    start_metrics_server(port=args.port)

    # Keep process alive so Prometheus can scrape metrics
    try:
        while True:
            time.sleep(5)
    except KeyboardInterrupt:
        logger.info("Metrics server stopped")


if __name__ == "__main__":
    main()
