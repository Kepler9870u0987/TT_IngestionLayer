"""
Prometheus metrics exporter for the Email Ingestion System.

Exposes counters, histograms, and gauges on a dedicated HTTP server
(default port 9090) for Prometheus scraping.

Usage:
    from src.monitoring.metrics import get_metrics_collector, start_metrics_server

    # Start the metrics HTTP server
    start_metrics_server(port=9090)

    # Get the singleton collector
    metrics = get_metrics_collector()

    # Record events
    metrics.inc_produced(count=5)
    metrics.inc_processed()
    metrics.observe_processing_latency(0.123)
"""
import time
import threading
from typing import Optional, Callable

from prometheus_client import (
    Counter,
    Histogram,
    Gauge,
    Info,
    start_http_server,
    CollectorRegistry,
    REGISTRY,
)

from src.common.logging_config import get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Prometheus metric definitions (module-level singletons)
# ---------------------------------------------------------------------------

# -- Counters --
EMAILS_PRODUCED_TOTAL = Counter(
    "email_ingestion_emails_produced_total",
    "Total number of emails produced to the Redis stream",
)

EMAILS_PROCESSED_TOTAL = Counter(
    "email_ingestion_emails_processed_total",
    "Total number of emails successfully processed by workers",
)

EMAILS_FAILED_TOTAL = Counter(
    "email_ingestion_emails_failed_total",
    "Total number of email processing failures",
)

DLQ_MESSAGES_TOTAL = Counter(
    "email_ingestion_dlq_messages_total",
    "Total number of messages sent to the Dead Letter Queue",
)

BACKOFF_RETRIES_TOTAL = Counter(
    "email_ingestion_backoff_retries_total",
    "Total number of backoff retries attempted",
)

IDEMPOTENCY_DUPLICATES_TOTAL = Counter(
    "email_ingestion_idempotency_duplicates_total",
    "Total number of duplicate messages skipped by idempotency check",
)

ORPHAN_MESSAGES_CLAIMED_TOTAL = Counter(
    "email_ingestion_orphan_messages_claimed_total",
    "Total orphaned messages reclaimed via XPENDING/XCLAIM",
)

IMAP_POLLS_TOTAL = Counter(
    "email_ingestion_imap_polls_total",
    "Total number of IMAP polling cycles executed",
)

# -- Histograms --
PROCESSING_LATENCY = Histogram(
    "email_ingestion_processing_latency_seconds",
    "Email processing latency in seconds",
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
)

IMAP_POLL_DURATION = Histogram(
    "email_ingestion_imap_poll_duration_seconds",
    "Duration of a single IMAP poll cycle in seconds",
    buckets=(0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0),
)

# -- Gauges --
STREAM_DEPTH = Gauge(
    "email_ingestion_stream_depth",
    "Current number of messages in the main Redis stream",
)

DLQ_DEPTH = Gauge(
    "email_ingestion_dlq_depth",
    "Current number of messages in the Dead Letter Queue stream",
)

CIRCUIT_BREAKER_STATE = Gauge(
    "email_ingestion_circuit_breaker_state",
    "Circuit breaker state (0=closed, 1=open, 2=half_open)",
    ["breaker_name"],
)

UPTIME_SECONDS = Gauge(
    "email_ingestion_uptime_seconds",
    "Seconds since the component started",
)

ACTIVE_WORKERS = Gauge(
    "email_ingestion_active_workers",
    "Number of active worker instances (self-reported)",
)

# -- Info --
BUILD_INFO = Info(
    "email_ingestion",
    "Email Ingestion System build / version info",
)

# ---------------------------------------------------------------------------
# MetricsCollector – thin convenience wrapper around the raw metrics
# ---------------------------------------------------------------------------

_CB_STATE_MAP = {"closed": 0, "open": 1, "half_open": 2}


class MetricsCollector:
    """
    Convenience wrapper around Prometheus metrics.

    Provides named helper methods so callers don't need to import the raw
    metric objects.  All methods are thread-safe (Prometheus client handles it).
    """

    def __init__(self) -> None:
        self._start_time = time.time()
        BUILD_INFO.info({
            "version": "1.0.0",
            "phase": "5",
            "component": "email_ingestion",
        })

    # -- Counters -----------------------------------------------------------

    def inc_produced(self, count: int = 1) -> None:
        """Increment emails produced counter."""
        EMAILS_PRODUCED_TOTAL.inc(count)

    def inc_processed(self, count: int = 1) -> None:
        """Increment emails processed counter."""
        EMAILS_PROCESSED_TOTAL.inc(count)

    def inc_failed(self, count: int = 1) -> None:
        """Increment emails failed counter."""
        EMAILS_FAILED_TOTAL.inc(count)

    def inc_dlq(self, count: int = 1) -> None:
        """Increment DLQ messages counter."""
        DLQ_MESSAGES_TOTAL.inc(count)

    def inc_retries(self, count: int = 1) -> None:
        """Increment backoff retries counter."""
        BACKOFF_RETRIES_TOTAL.inc(count)

    def inc_duplicates(self, count: int = 1) -> None:
        """Increment idempotency duplicates counter."""
        IDEMPOTENCY_DUPLICATES_TOTAL.inc(count)

    def inc_orphans_claimed(self, count: int = 1) -> None:
        """Increment orphan messages claimed counter."""
        ORPHAN_MESSAGES_CLAIMED_TOTAL.inc(count)

    def inc_imap_polls(self, count: int = 1) -> None:
        """Increment IMAP polls counter."""
        IMAP_POLLS_TOTAL.inc(count)

    # -- Histograms ---------------------------------------------------------

    def observe_processing_latency(self, seconds: float) -> None:
        """Record an email processing latency observation."""
        PROCESSING_LATENCY.observe(seconds)

    def observe_poll_duration(self, seconds: float) -> None:
        """Record an IMAP poll duration observation."""
        IMAP_POLL_DURATION.observe(seconds)

    def processing_latency_timer(self):
        """
        Return a context-manager / decorator that measures processing latency.

        Usage:
            with metrics.processing_latency_timer():
                process(msg)
        """
        return PROCESSING_LATENCY.time()

    def poll_duration_timer(self):
        """
        Return a context-manager / decorator that measures poll duration.

        Usage:
            with metrics.poll_duration_timer():
                fetch_emails()
        """
        return IMAP_POLL_DURATION.time()

    # -- Gauges -------------------------------------------------------------

    def set_stream_depth(self, depth: int) -> None:
        """Set current stream depth gauge."""
        STREAM_DEPTH.set(depth)

    def set_dlq_depth(self, depth: int) -> None:
        """Set current DLQ depth gauge."""
        DLQ_DEPTH.set(depth)

    def set_circuit_breaker_state(self, name: str, state_str: str) -> None:
        """
        Update circuit breaker gauge.

        Args:
            name: breaker name (e.g. "redis", "imap")
            state_str: one of "closed", "open", "half_open"
        """
        CIRCUIT_BREAKER_STATE.labels(breaker_name=name).set(
            _CB_STATE_MAP.get(state_str, -1)
        )

    def set_active_workers(self, count: int) -> None:
        """Set active workers gauge."""
        ACTIVE_WORKERS.set(count)

    def update_uptime(self) -> None:
        """Set uptime gauge to current elapsed time."""
        UPTIME_SECONDS.set(time.time() - self._start_time)

    # -- Bulk helpers -------------------------------------------------------

    def update_circuit_breakers(self, cb_stats: list) -> None:
        """
        Update all circuit-breaker gauges from ``CircuitBreakers.get_all_stats()``.

        Args:
            cb_stats: list of dicts with at least ``name`` and ``state`` keys
        """
        for cb in cb_stats:
            self.set_circuit_breaker_state(cb["name"], cb["state"])

    # -- Accessors for testing ----------------------------------------------

    @staticmethod
    def get_produced_total() -> float:
        return EMAILS_PRODUCED_TOTAL._value.get()

    @staticmethod
    def get_processed_total() -> float:
        return EMAILS_PROCESSED_TOTAL._value.get()

    @staticmethod
    def get_failed_total() -> float:
        return EMAILS_FAILED_TOTAL._value.get()

    @staticmethod
    def get_dlq_total() -> float:
        return DLQ_MESSAGES_TOTAL._value.get()

    @staticmethod
    def get_stream_depth() -> float:
        return STREAM_DEPTH._value.get()

    @staticmethod
    def get_dlq_depth() -> float:
        return DLQ_DEPTH._value.get()


# ---------------------------------------------------------------------------
# BackgroundMetricsUpdater – periodic gauge refresh
# ---------------------------------------------------------------------------


class BackgroundMetricsUpdater:
    """
    Daemon thread that periodically updates gauges that require a Redis
    round-trip (stream depth, DLQ depth) and circuit breaker states.

    Args:
        collector: ``MetricsCollector`` instance
        redis_client: ``RedisClient`` for XLEN calls
        stream_name: main stream name for depth gauge
        dlq_stream_name: DLQ stream name for depth gauge
        interval: refresh interval in seconds (default 15)
    """

    def __init__(
        self,
        collector: "MetricsCollector",
        redis_client,
        stream_name: str = "email_ingestion_stream",
        dlq_stream_name: str = "email_ingestion_dlq",
        interval: float = 15.0,
    ) -> None:
        self.collector = collector
        self.redis_client = redis_client
        self.stream_name = stream_name
        self.dlq_stream_name = dlq_stream_name
        self.interval = interval
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        """Start background updater daemon thread."""
        self._thread = threading.Thread(
            target=self._run, name="metrics-updater", daemon=True
        )
        self._thread.start()
        logger.info(
            f"BackgroundMetricsUpdater started (interval={self.interval}s)"
        )

    def stop(self) -> None:
        """Signal the updater thread to stop."""
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=5)
        logger.info("BackgroundMetricsUpdater stopped")

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def _run(self) -> None:
        """Main loop executed in the daemon thread."""
        while not self._stop_event.is_set():
            try:
                self._update()
            except Exception as exc:
                logger.warning(f"BackgroundMetricsUpdater error: {exc}")
            self._stop_event.wait(self.interval)

    def _update(self) -> None:
        """Perform a single update cycle."""
        # Stream depths
        try:
            depth = self.redis_client.xlen(self.stream_name)
            self.collector.set_stream_depth(depth)
        except Exception:
            pass  # xlen already returns 0 on error

        try:
            dlq_depth = self.redis_client.xlen(self.dlq_stream_name)
            self.collector.set_dlq_depth(dlq_depth)
        except Exception:
            pass

        # Circuit breakers
        try:
            from src.common.circuit_breaker import CircuitBreakers
            cb_stats = CircuitBreakers.get_all_stats()
            self.collector.update_circuit_breakers(cb_stats)
        except Exception:
            pass

        # Uptime
        self.collector.update_uptime()


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------

_metrics_collector: Optional[MetricsCollector] = None
_metrics_lock = threading.Lock()


def get_metrics_collector() -> MetricsCollector:
    """
    Return the singleton ``MetricsCollector`` instance.
    Creates one on first call (thread-safe).
    """
    global _metrics_collector
    if _metrics_collector is None:
        with _metrics_lock:
            if _metrics_collector is None:
                _metrics_collector = MetricsCollector()
    return _metrics_collector


def start_metrics_server(port: int = 9090) -> None:
    """
    Start the Prometheus metrics HTTP server on *port*.

    This is a thin wrapper around ``prometheus_client.start_http_server``
    that catches ``OSError`` when the port is already in use.
    """
    try:
        start_http_server(port)
        logger.info(
            f"Prometheus metrics server started on port {port}  "
            f"→  http://localhost:{port}/metrics"
        )
    except OSError as exc:
        logger.error(f"Failed to start metrics server on port {port}: {exc}")


def reset_metrics() -> None:
    """
    Reset all counters / gauges to zero.
    Useful in test suites to get deterministic values.
    """
    global _metrics_collector
    for c in (
        EMAILS_PRODUCED_TOTAL,
        EMAILS_PROCESSED_TOTAL,
        EMAILS_FAILED_TOTAL,
        DLQ_MESSAGES_TOTAL,
        BACKOFF_RETRIES_TOTAL,
        IDEMPOTENCY_DUPLICATES_TOTAL,
        ORPHAN_MESSAGES_CLAIMED_TOTAL,
        IMAP_POLLS_TOTAL,
    ):
        c._value.set(0)

    for g in (STREAM_DEPTH, DLQ_DEPTH, UPTIME_SECONDS, ACTIVE_WORKERS):
        g._value.set(0)

    # Reset per-label gauges
    CIRCUIT_BREAKER_STATE._metrics.clear()

    _metrics_collector = None
