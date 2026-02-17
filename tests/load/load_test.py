#!/usr/bin/env python3
"""
Load test script for email ingestion pipeline.
Benchmarks throughput and latency for producer → Redis → worker pipeline.

Usage:
    python -m tests.load.load_test --emails 1000
    python -m tests.load.load_test --emails 10000 --workers 4
    python -m tests.load.load_test --emails 500 --batch-size 50
"""
import sys
import os
import time
import json
import uuid
import argparse
import threading
import statistics
from typing import List, Dict, Any, Optional
from datetime import datetime, timezone
from dataclasses import dataclass, field

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from src.common.redis_client import RedisClient
from src.common.logging_config import get_logger

logger = get_logger(__name__, level="INFO")


@dataclass
class LoadTestMetrics:
    """Collects and computes load test metrics."""
    start_time: float = 0.0
    end_time: float = 0.0
    total_produced: int = 0
    total_consumed: int = 0
    produce_latencies: List[float] = field(default_factory=list)
    consume_latencies: List[float] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)

    @property
    def duration_seconds(self) -> float:
        return self.end_time - self.start_time if self.end_time else 0

    @property
    def produce_rate(self) -> float:
        """Messages produced per second."""
        if self.duration_seconds <= 0:
            return 0
        return self.total_produced / self.duration_seconds

    @property
    def consume_rate(self) -> float:
        """Messages consumed per second."""
        if self.duration_seconds <= 0:
            return 0
        return self.total_consumed / self.duration_seconds

    def compute_percentiles(self, latencies: List[float]) -> Dict[str, float]:
        """Compute p50, p95, p99 latencies in milliseconds."""
        if not latencies:
            return {"p50": 0, "p95": 0, "p99": 0, "avg": 0, "min": 0, "max": 0}

        sorted_lat = sorted(latencies)
        n = len(sorted_lat)
        return {
            "p50": sorted_lat[int(n * 0.50)] * 1000,
            "p95": sorted_lat[int(n * 0.95)] * 1000,
            "p99": sorted_lat[int(n * 0.99)] * 1000,
            "avg": statistics.mean(sorted_lat) * 1000,
            "min": min(sorted_lat) * 1000,
            "max": max(sorted_lat) * 1000,
        }

    def summary(self) -> Dict[str, Any]:
        """Generate metrics summary."""
        return {
            "total_emails": self.total_produced,
            "total_consumed": self.total_consumed,
            "duration_seconds": round(self.duration_seconds, 2),
            "produce_rate_msgs_per_sec": round(self.produce_rate, 2),
            "consume_rate_msgs_per_sec": round(self.consume_rate, 2),
            "produce_latency_ms": self.compute_percentiles(self.produce_latencies),
            "consume_latency_ms": self.compute_percentiles(self.consume_latencies),
            "errors": len(self.errors),
        }

    def print_report(self) -> None:
        """Print a formatted report."""
        s = self.summary()
        print("\n" + "=" * 60)
        print("  LOAD TEST REPORT")
        print("=" * 60)
        print(f"  Total emails produced:   {s['total_emails']}")
        print(f"  Total emails consumed:   {s['total_consumed']}")
        print(f"  Duration:                {s['duration_seconds']}s")
        print(f"  Produce rate:            {s['produce_rate_msgs_per_sec']} msg/s")
        print(f"  Consume rate:            {s['consume_rate_msgs_per_sec']} msg/s")
        print()
        
        pl = s["produce_latency_ms"]
        print("  Produce Latency (XADD):")
        print(f"    avg: {pl['avg']:.2f}ms  p50: {pl['p50']:.2f}ms  "
              f"p95: {pl['p95']:.2f}ms  p99: {pl['p99']:.2f}ms")
        print(f"    min: {pl['min']:.2f}ms  max: {pl['max']:.2f}ms")
        print()

        cl = s["consume_latency_ms"]
        print("  Consume Latency (XREADGROUP + ACK):")
        print(f"    avg: {cl['avg']:.2f}ms  p50: {cl['p50']:.2f}ms  "
              f"p95: {cl['p95']:.2f}ms  p99: {cl['p99']:.2f}ms")
        print(f"    min: {cl['min']:.2f}ms  max: {cl['max']:.2f}ms")

        if s["errors"]:
            print(f"\n  Errors: {s['errors']}")

        print("=" * 60 + "\n")


def generate_fake_email(index: int) -> Dict[str, str]:
    """Generate a fake email payload for testing."""
    return {
        "message_id": f"load_test_{uuid.uuid4().hex[:12]}_{index}",
        "uid": str(index + 1),
        "from": f"sender_{index}@test.com",
        "to": "recipient@test.com",
        "subject": f"Load Test Email #{index}",
        "date": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "payload": json.dumps({
            "body_preview": f"This is test email body #{index}",
            "has_attachments": str(index % 10 == 0),
            "size_bytes": str(500 + (index * 7) % 10000),
        }),
        "produced_at": str(time.time()),
    }


class LoadTestProducer:
    """Produces N fake emails to Redis Stream."""

    def __init__(
        self,
        redis: RedisClient,
        stream_name: str,
        total_emails: int,
        batch_size: int = 100,
        max_stream_len: int = 50000
    ):
        self.redis = redis
        self.stream_name = stream_name
        self.total_emails = total_emails
        self.batch_size = batch_size
        self.max_stream_len = max_stream_len
        self.metrics = LoadTestMetrics()

    def run(self) -> LoadTestMetrics:
        """Produce all emails and return metrics."""
        logger.info(
            f"Producing {self.total_emails} emails to '{self.stream_name}' "
            f"(batch_size={self.batch_size})..."
        )

        self.metrics.start_time = time.time()

        for i in range(self.total_emails):
            email = generate_fake_email(i)
            try:
                t0 = time.time()
                self.redis.xadd(
                    self.stream_name,
                    email,
                    maxlen=self.max_stream_len
                )
                latency = time.time() - t0
                self.metrics.produce_latencies.append(latency)
                self.metrics.total_produced += 1

                if (i + 1) % 1000 == 0:
                    rate = self.metrics.total_produced / (time.time() - self.metrics.start_time)
                    logger.info(
                        f"  Produced {i + 1}/{self.total_emails} "
                        f"({rate:.0f} msg/s)"
                    )

            except Exception as e:
                self.metrics.errors.append(f"produce[{i}]: {e}")

        self.metrics.end_time = time.time()
        logger.info(
            f"Production complete: {self.metrics.total_produced} emails "
            f"in {self.metrics.duration_seconds:.2f}s "
            f"({self.metrics.produce_rate:.0f} msg/s)"
        )
        return self.metrics


class LoadTestConsumer:
    """Consumes emails from Redis Stream measuring throughput."""

    def __init__(
        self,
        redis: RedisClient,
        stream_name: str,
        consumer_group: str,
        consumer_name: str,
        expected_count: int,
        batch_size: int = 100,
        timeout_seconds: float = 60.0
    ):
        self.redis = redis
        self.stream_name = stream_name
        self.consumer_group = consumer_group
        self.consumer_name = consumer_name
        self.expected_count = expected_count
        self.batch_size = batch_size
        self.timeout_seconds = timeout_seconds
        self.metrics = LoadTestMetrics()

    def ensure_group(self) -> None:
        """Create consumer group if not exists."""
        try:
            self.redis.xgroup_create(
                stream=self.stream_name,
                groupname=self.consumer_group,
                id="0",
                mkstream=True
            )
        except Exception as e:
            if "BUSYGROUP" not in str(e):
                raise

    def run(self) -> LoadTestMetrics:
        """Consume all expected messages and return metrics."""
        self.ensure_group()

        logger.info(
            f"Consuming from '{self.stream_name}' "
            f"(expected={self.expected_count}, batch={self.batch_size})..."
        )

        self.metrics.start_time = time.time()
        deadline = self.metrics.start_time + self.timeout_seconds

        while (
            self.metrics.total_consumed < self.expected_count
            and time.time() < deadline
        ):
            try:
                t0 = time.time()
                messages = self.redis.xreadgroup(
                    groupname=self.consumer_group,
                    consumername=self.consumer_name,
                    streams={self.stream_name: ">"},
                    count=self.batch_size,
                    block=1000  # 1s block
                )

                if not messages:
                    continue

                for stream_name, stream_messages in messages:
                    for msg_id, msg_data in stream_messages:
                        # Simulate minimal processing
                        _ = msg_data.get("message_id")

                        # ACK
                        self.redis.xack(
                            self.stream_name,
                            self.consumer_group,
                            msg_id
                        )
                        self.metrics.total_consumed += 1

                batch_latency = time.time() - t0
                # Average latency per message in batch
                batch_msg_count = sum(
                    len(msgs) for _, msgs in messages
                )
                if batch_msg_count > 0:
                    per_msg = batch_latency / batch_msg_count
                    self.metrics.consume_latencies.extend(
                        [per_msg] * batch_msg_count
                    )

                if self.metrics.total_consumed % 1000 == 0:
                    elapsed = time.time() - self.metrics.start_time
                    rate = self.metrics.total_consumed / elapsed
                    logger.info(
                        f"  Consumed {self.metrics.total_consumed}/"
                        f"{self.expected_count} ({rate:.0f} msg/s)"
                    )

            except Exception as e:
                self.metrics.errors.append(f"consume: {e}")
                time.sleep(0.5)

        self.metrics.end_time = time.time()

        if self.metrics.total_consumed < self.expected_count:
            logger.warning(
                f"Timeout: consumed only {self.metrics.total_consumed}/"
                f"{self.expected_count}"
            )
        else:
            logger.info(
                f"Consumption complete: {self.metrics.total_consumed} emails "
                f"in {self.metrics.duration_seconds:.2f}s "
                f"({self.metrics.consume_rate:.0f} msg/s)"
            )

        return self.metrics


def run_load_test(
    redis_host: str = "localhost",
    redis_port: int = 6379,
    redis_db: int = 14,
    total_emails: int = 1000,
    batch_size: int = 100,
    num_workers: int = 1,
    timeout: float = 120.0
) -> Dict[str, Any]:
    """
    Run a complete produce → consume load test.

    Args:
        redis_host: Redis host
        redis_port: Redis port
        redis_db: Redis DB (uses 14 by default to avoid conflicts)
        total_emails: Number of emails to produce
        batch_size: Batch size for produce/consume
        num_workers: Number of consumer threads
        timeout: Consumer timeout in seconds

    Returns:
        Combined metrics dictionary
    """
    stream_name = "load_test_stream"
    group_name = "load_test_group"

    redis = RedisClient(host=redis_host, port=redis_port, db=redis_db)

    # Clean up previous test data
    try:
        redis.client.delete(stream_name)
    except Exception:
        pass

    # Phase 1: Produce
    producer = LoadTestProducer(
        redis, stream_name, total_emails, batch_size
    )
    produce_metrics = producer.run()

    # Phase 2: Consume
    emails_per_worker = total_emails // num_workers
    consumers = []
    consumer_threads = []

    for i in range(num_workers):
        expected = emails_per_worker if i < num_workers - 1 else (
            total_emails - emails_per_worker * (num_workers - 1)
        )
        consumer = LoadTestConsumer(
            redis=redis,
            stream_name=stream_name,
            consumer_group=group_name,
            consumer_name=f"load_worker_{i}",
            expected_count=expected,
            batch_size=batch_size,
            timeout_seconds=timeout
        )
        consumers.append(consumer)

    if num_workers == 1:
        consume_metrics = consumers[0].run()
    else:
        # Run consumers in parallel threads
        results = [None] * num_workers
        def run_consumer(idx):
            results[idx] = consumers[idx].run()

        for i in range(num_workers):
            t = threading.Thread(target=run_consumer, args=(i,))
            consumer_threads.append(t)
            t.start()

        for t in consumer_threads:
            t.join(timeout=timeout + 10)

        # Merge metrics
        consume_metrics = LoadTestMetrics()
        consume_metrics.start_time = min(r.start_time for r in results if r)
        consume_metrics.end_time = max(r.end_time for r in results if r)
        for r in results:
            if r:
                consume_metrics.total_consumed += r.total_consumed
                consume_metrics.consume_latencies.extend(r.consume_latencies)
                consume_metrics.errors.extend(r.errors)

    # Clean up
    try:
        redis.client.delete(stream_name)
    except Exception:
        pass
    redis.close()

    # Combined report
    combined = LoadTestMetrics()
    combined.start_time = produce_metrics.start_time
    combined.end_time = consume_metrics.end_time
    combined.total_produced = produce_metrics.total_produced
    combined.total_consumed = consume_metrics.total_consumed
    combined.produce_latencies = produce_metrics.produce_latencies
    combined.consume_latencies = consume_metrics.consume_latencies
    combined.errors = produce_metrics.errors + consume_metrics.errors

    combined.print_report()

    return combined.summary()


def main():
    parser = argparse.ArgumentParser(
        description="Load test for email ingestion pipeline"
    )
    parser.add_argument(
        "--emails", type=int, default=1000,
        help="Number of emails to produce (default: 1000)"
    )
    parser.add_argument(
        "--batch-size", type=int, default=100,
        help="Batch size for produce/consume (default: 100)"
    )
    parser.add_argument(
        "--workers", type=int, default=1,
        help="Number of consumer workers (default: 1)"
    )
    parser.add_argument(
        "--redis-host", default="localhost",
        help="Redis host (default: localhost)"
    )
    parser.add_argument(
        "--redis-port", type=int, default=6379,
        help="Redis port (default: 6379)"
    )
    parser.add_argument(
        "--redis-db", type=int, default=14,
        help="Redis DB for load test (default: 14)"
    )
    parser.add_argument(
        "--timeout", type=float, default=120.0,
        help="Consumer timeout in seconds (default: 120)"
    )
    parser.add_argument(
        "--json", action="store_true",
        help="Output results as JSON"
    )

    args = parser.parse_args()

    results = run_load_test(
        redis_host=args.redis_host,
        redis_port=args.redis_port,
        redis_db=args.redis_db,
        total_emails=args.emails,
        batch_size=args.batch_size,
        num_workers=args.workers,
        timeout=args.timeout
    )

    if args.json:
        print(json.dumps(results, indent=2))

    sys.exit(0 if results.get("errors", 0) == 0 else 1)


if __name__ == "__main__":
    main()
