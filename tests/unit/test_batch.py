"""
Unit tests for BatchProducer and BatchAcknowledger.
"""
import unittest
from unittest.mock import MagicMock, patch

from src.common.batch import BatchProducer, BatchAcknowledger


class TestBatchProducer(unittest.TestCase):
    """Tests for BatchProducer."""

    def setUp(self):
        self.redis = MagicMock()
        self.pipe = MagicMock()
        self.redis.pipeline.return_value = self.pipe

    def test_add_buffers_messages(self):
        producer = BatchProducer(
            self.redis, "stream", batch_size=5
        )
        result = producer.add({"key": "val1"})
        self.assertIsNone(result)
        self.assertEqual(producer.pending_count, 1)

    def test_auto_flush_on_batch_size(self):
        self.pipe.execute.return_value = ["id1", "id2", "id3"]
        producer = BatchProducer(
            self.redis, "stream", batch_size=3
        )
        producer.add({"k": "1"})
        producer.add({"k": "2"})
        result = producer.add({"k": "3"})

        self.assertIsNotNone(result)
        self.assertEqual(len(result), 3)
        self.assertEqual(producer.pending_count, 0)

    def test_flush_sends_via_pipeline(self):
        self.pipe.execute.return_value = ["id1", "id2"]
        producer = BatchProducer(
            self.redis, "stream", batch_size=10
        )
        producer.add({"k": "1"})
        producer.add({"k": "2"})

        result = producer.flush()

        self.assertEqual(len(result), 2)
        self.assertEqual(self.pipe.xadd.call_count, 2)
        self.pipe.execute.assert_called_once()

    def test_flush_empty_buffer(self):
        producer = BatchProducer(self.redis, "stream")
        result = producer.flush()
        self.assertEqual(result, [])

    def test_stats_tracking(self):
        self.pipe.execute.return_value = ["id1", "id2"]
        producer = BatchProducer(
            self.redis, "stream", batch_size=10
        )
        producer.add({"k": "1"})
        producer.add({"k": "2"})
        producer.flush()

        stats = producer.get_stats()
        self.assertEqual(stats["total_sent"], 2)
        self.assertEqual(stats["total_batches"], 1)
        self.assertEqual(stats["avg_batch_size"], 2.0)
        self.assertEqual(stats["pending"], 0)

    def test_flush_error_keeps_buffer(self):
        self.pipe.execute.side_effect = Exception("Connection lost")
        producer = BatchProducer(self.redis, "stream")
        producer.add({"k": "1"})

        with self.assertRaises(Exception):
            producer.flush()

        # Buffer should be retained for retry
        self.assertEqual(producer.pending_count, 1)


class TestBatchAcknowledger(unittest.TestCase):
    """Tests for BatchAcknowledger."""

    def setUp(self):
        self.redis = MagicMock()
        self.pipe = MagicMock()
        self.redis.pipeline.return_value = self.pipe

    def test_add_buffers_ids(self):
        acker = BatchAcknowledger(
            self.redis, "stream", "group", batch_size=5
        )
        result = acker.add("msg-1")
        self.assertIsNone(result)
        self.assertEqual(acker.pending_count, 1)

    def test_auto_flush_on_batch_size(self):
        self.pipe.execute.return_value = [1, 1, 1]
        acker = BatchAcknowledger(
            self.redis, "stream", "group", batch_size=3
        )
        acker.add("msg-1")
        acker.add("msg-2")
        result = acker.add("msg-3")

        self.assertEqual(result, 3)
        self.assertEqual(acker.pending_count, 0)

    def test_flush_sends_via_pipeline(self):
        self.pipe.execute.return_value = [1, 1]
        acker = BatchAcknowledger(
            self.redis, "stream", "group"
        )
        acker.add("msg-1")
        acker.add("msg-2")
        count = acker.flush()

        self.assertEqual(count, 2)
        self.assertEqual(self.pipe.xack.call_count, 2)

    def test_flush_empty(self):
        acker = BatchAcknowledger(
            self.redis, "stream", "group"
        )
        count = acker.flush()
        self.assertEqual(count, 0)

    def test_stats(self):
        self.pipe.execute.return_value = [1, 1]
        acker = BatchAcknowledger(
            self.redis, "stream", "group"
        )
        acker.add("msg-1")
        acker.add("msg-2")
        acker.flush()

        stats = acker.get_stats()
        self.assertEqual(stats["total_acked"], 2)
        self.assertEqual(stats["total_batches"], 1)


if __name__ == "__main__":
    unittest.main()
