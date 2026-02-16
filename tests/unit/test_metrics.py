"""
Unit tests for src/monitoring/metrics.py

Tests the MetricsCollector, BackgroundMetricsUpdater, and module helpers.
"""
import time
import threading
import pytest
from unittest.mock import MagicMock, patch, PropertyMock

from src.monitoring.metrics import (
    MetricsCollector,
    BackgroundMetricsUpdater,
    get_metrics_collector,
    start_metrics_server,
    reset_metrics,
    EMAILS_PRODUCED_TOTAL,
    EMAILS_PROCESSED_TOTAL,
    EMAILS_FAILED_TOTAL,
    DLQ_MESSAGES_TOTAL,
    BACKOFF_RETRIES_TOTAL,
    IDEMPOTENCY_DUPLICATES_TOTAL,
    ORPHAN_MESSAGES_CLAIMED_TOTAL,
    IMAP_POLLS_TOTAL,
    STREAM_DEPTH,
    DLQ_DEPTH,
    CIRCUIT_BREAKER_STATE,
    UPTIME_SECONDS,
    ACTIVE_WORKERS,
    PROCESSING_LATENCY,
    IMAP_POLL_DURATION,
)


@pytest.fixture(autouse=True)
def _reset():
    """Reset all metrics before each test for isolation."""
    reset_metrics()
    yield
    reset_metrics()


# -----------------------------------------------------------------------
# MetricsCollector – counter helpers
# -----------------------------------------------------------------------

class TestMetricsCollectorCounters:
    """Tests for counter increment methods."""

    def test_inc_produced_default(self):
        mc = MetricsCollector()
        mc.inc_produced()
        assert mc.get_produced_total() == 1.0

    def test_inc_produced_batch(self):
        mc = MetricsCollector()
        mc.inc_produced(5)
        assert mc.get_produced_total() == 5.0

    def test_inc_processed(self):
        mc = MetricsCollector()
        mc.inc_processed(3)
        assert mc.get_processed_total() == 3.0

    def test_inc_failed(self):
        mc = MetricsCollector()
        mc.inc_failed(2)
        assert mc.get_failed_total() == 2.0

    def test_inc_dlq(self):
        mc = MetricsCollector()
        mc.inc_dlq()
        assert mc.get_dlq_total() == 1.0

    def test_inc_retries(self):
        mc = MetricsCollector()
        mc.inc_retries(4)
        assert BACKOFF_RETRIES_TOTAL._value.get() == 4.0

    def test_inc_duplicates(self):
        mc = MetricsCollector()
        mc.inc_duplicates(2)
        assert IDEMPOTENCY_DUPLICATES_TOTAL._value.get() == 2.0

    def test_inc_orphans_claimed(self):
        mc = MetricsCollector()
        mc.inc_orphans_claimed(3)
        assert ORPHAN_MESSAGES_CLAIMED_TOTAL._value.get() == 3.0

    def test_inc_imap_polls(self):
        mc = MetricsCollector()
        mc.inc_imap_polls()
        mc.inc_imap_polls()
        assert IMAP_POLLS_TOTAL._value.get() == 2.0

    def test_counters_accumulate(self):
        mc = MetricsCollector()
        mc.inc_produced(10)
        mc.inc_produced(5)
        assert mc.get_produced_total() == 15.0


# -----------------------------------------------------------------------
# MetricsCollector – histogram helpers
# -----------------------------------------------------------------------

class TestMetricsCollectorHistograms:
    """Tests for histogram observation methods."""

    def test_observe_processing_latency(self):
        mc = MetricsCollector()
        mc.observe_processing_latency(0.05)
        mc.observe_processing_latency(0.12)
        # Histogram _sum tracks total observed values
        assert PROCESSING_LATENCY._sum.get() == pytest.approx(0.17, abs=1e-6)

    def test_observe_poll_duration(self):
        mc = MetricsCollector()
        mc.observe_poll_duration(1.5)
        assert IMAP_POLL_DURATION._sum.get() == pytest.approx(1.5, abs=1e-6)

    def test_processing_latency_timer(self):
        mc = MetricsCollector()
        with mc.processing_latency_timer():
            time.sleep(0.01)
        assert PROCESSING_LATENCY._sum.get() > 0

    def test_poll_duration_timer(self):
        mc = MetricsCollector()
        with mc.poll_duration_timer():
            time.sleep(0.01)
        assert IMAP_POLL_DURATION._sum.get() > 0


# -----------------------------------------------------------------------
# MetricsCollector – gauge helpers
# -----------------------------------------------------------------------

class TestMetricsCollectorGauges:
    """Tests for gauge set methods."""

    def test_set_stream_depth(self):
        mc = MetricsCollector()
        mc.set_stream_depth(42)
        assert mc.get_stream_depth() == 42.0

    def test_set_dlq_depth(self):
        mc = MetricsCollector()
        mc.set_dlq_depth(7)
        assert mc.get_dlq_depth() == 7.0

    def test_set_circuit_breaker_state(self):
        mc = MetricsCollector()
        mc.set_circuit_breaker_state("redis", "closed")
        assert CIRCUIT_BREAKER_STATE.labels(breaker_name="redis")._value.get() == 0

        mc.set_circuit_breaker_state("redis", "open")
        assert CIRCUIT_BREAKER_STATE.labels(breaker_name="redis")._value.get() == 1

        mc.set_circuit_breaker_state("imap", "half_open")
        assert CIRCUIT_BREAKER_STATE.labels(breaker_name="imap")._value.get() == 2

    def test_set_active_workers(self):
        mc = MetricsCollector()
        mc.set_active_workers(3)
        assert ACTIVE_WORKERS._value.get() == 3.0

    def test_update_uptime(self):
        mc = MetricsCollector()
        time.sleep(0.05)
        mc.update_uptime()
        assert UPTIME_SECONDS._value.get() > 0

    def test_update_circuit_breakers_bulk(self):
        mc = MetricsCollector()
        stats = [
            {"name": "redis", "state": "closed"},
            {"name": "imap", "state": "open"},
        ]
        mc.update_circuit_breakers(stats)
        assert CIRCUIT_BREAKER_STATE.labels(breaker_name="redis")._value.get() == 0
        assert CIRCUIT_BREAKER_STATE.labels(breaker_name="imap")._value.get() == 1


# -----------------------------------------------------------------------
# BackgroundMetricsUpdater
# -----------------------------------------------------------------------

class TestBackgroundMetricsUpdater:
    """Tests for the daemon metrics updater thread."""

    def test_start_and_stop(self):
        mc = MetricsCollector()
        redis_mock = MagicMock()
        redis_mock.xlen.return_value = 0

        updater = BackgroundMetricsUpdater(
            collector=mc,
            redis_client=redis_mock,
            interval=0.1,
        )
        updater.start()
        assert updater.is_running
        time.sleep(0.3)
        updater.stop()
        assert not updater.is_running

    def test_updates_stream_depth(self):
        mc = MetricsCollector()
        redis_mock = MagicMock()
        redis_mock.xlen.side_effect = lambda s: 100 if s == "email_ingestion_stream" else 5

        updater = BackgroundMetricsUpdater(
            collector=mc,
            redis_client=redis_mock,
            interval=0.05,
        )
        updater.start()
        time.sleep(0.2)
        updater.stop()

        assert mc.get_stream_depth() == 100.0
        assert mc.get_dlq_depth() == 5.0

    def test_handles_redis_error_gracefully(self):
        mc = MetricsCollector()
        redis_mock = MagicMock()
        redis_mock.xlen.side_effect = Exception("conn refused")

        updater = BackgroundMetricsUpdater(
            collector=mc,
            redis_client=redis_mock,
            interval=0.05,
        )
        updater.start()
        time.sleep(0.2)
        updater.stop()
        # Should not crash – gauges stay at 0
        assert mc.get_stream_depth() == 0.0

    def test_updates_uptime(self):
        mc = MetricsCollector()
        redis_mock = MagicMock()
        redis_mock.xlen.return_value = 0

        updater = BackgroundMetricsUpdater(
            collector=mc,
            redis_client=redis_mock,
            interval=0.05,
        )
        updater.start()
        time.sleep(0.2)
        updater.stop()

        assert UPTIME_SECONDS._value.get() > 0

    def test_is_not_running_before_start(self):
        mc = MetricsCollector()
        redis_mock = MagicMock()
        updater = BackgroundMetricsUpdater(
            collector=mc, redis_client=redis_mock, interval=1
        )
        assert not updater.is_running


# -----------------------------------------------------------------------
# Module helpers
# -----------------------------------------------------------------------

class TestModuleHelpers:
    """Tests for get_metrics_collector, start_metrics_server, reset_metrics."""

    def test_get_metrics_collector_singleton(self):
        a = get_metrics_collector()
        b = get_metrics_collector()
        assert a is b

    def test_reset_metrics_clears_counters(self):
        mc = get_metrics_collector()
        mc.inc_produced(99)
        assert mc.get_produced_total() == 99.0

        reset_metrics()
        # After reset a fresh collector is created
        mc2 = get_metrics_collector()
        assert mc2.get_produced_total() == 0.0

    @patch("src.monitoring.metrics.start_http_server")
    def test_start_metrics_server_calls_prom(self, mock_start):
        start_metrics_server(port=9999)
        mock_start.assert_called_once_with(9999)

    @patch("src.monitoring.metrics.start_http_server", side_effect=OSError("in use"))
    def test_start_metrics_server_handles_oserror(self, mock_start):
        # Should not raise
        start_metrics_server(port=9999)
        mock_start.assert_called_once()
