"""
Integration tests for email ingestion system.
Tests the complete flow: Producer -> Redis Stream -> Worker -> Processing
"""
import pytest
import time
from unittest.mock import Mock, patch

from src.common.redis_client import RedisClient
from src.worker.idempotency import IdempotencyManager
from src.worker.backoff import BackoffManager
from src.worker.dlq import DLQManager
from src.worker.processor import EmailProcessor


class TestEmailIngestionIntegration:
    """
    Integration tests for the complete email ingestion pipeline.
    
    Note: These tests require a running Redis instance.
    Set SKIP_INTEGRATION_TESTS=1 to skip these tests.
    """

    @pytest.fixture
    def redis_client(self):
        """Create Redis client for integration tests"""
        try:
            client = RedisClient(
                host="localhost",
                port=6379,
                db=15  # Use separate test database
            )
            # Test connection
            client.ping()
            yield client
            # Cleanup after tests
            client.client.flushdb()
            client.close()
        except Exception as e:
            pytest.skip(f"Redis not available for integration tests: {e}")

    @pytest.fixture
    def stream_name(self):
        """Test stream name"""
        return "test_email_stream"

    @pytest.fixture
    def consumer_group(self):
        """Test consumer group name"""
        return "test_consumer_group"

    @pytest.fixture
    def sample_emails(self):
        """Sample email data for testing"""
        return [
            {
                "message_id": "email-001",
                "from": "sender1@example.com",
                "to": "recipient@example.com",
                "subject": "Test Email 1",
                "date": "2026-02-16T10:00:00Z",
                "body_preview": "This is test email 1",
                "has_attachments": "false"
            },
            {
                "message_id": "email-002",
                "from": "sender2@example.com",
                "to": "recipient@example.com",
                "subject": "Test Email 2",
                "date": "2026-02-16T10:01:00Z",
                "body_preview": "This is test email 2",
                "has_attachments": "true"
            },
            {
                "message_id": "email-003",
                "from": "sender3@example.com",
                "to": "recipient@example.com",
                "subject": "Test Email 3",
                "date": "2026-02-16T10:02:00Z",
                "body_preview": "This is test email 3",
                "has_attachments": "false"
            }
        ]

    def test_producer_to_stream(self, redis_client, stream_name, sample_emails):
        """Test pushing emails to Redis Stream (simulating producer)"""
        # Simulate producer pushing emails to stream
        message_ids = []
        for email in sample_emails:
            msg_id = redis_client.xadd(
                stream=stream_name,
                fields=email,
                maxlen=1000
            )
            message_ids.append(msg_id)
        
        # Verify messages are in stream
        assert len(message_ids) == 3
        
        # Read messages directly from stream
        messages = redis_client.client.xrange(stream_name, "-", "+")
        assert len(messages) == 3

    def test_worker_consumes_from_stream(
        self,
        redis_client,
        stream_name,
        consumer_group,
        sample_emails
    ):
        """Test worker consuming messages from stream"""
        # Setup: Push messages to stream
        for email in sample_emails:
            redis_client.xadd(stream=stream_name, fields=email)
        
        # Create consumer group
        try:
            redis_client.xgroup_create(
                stream=stream_name,
                groupname=consumer_group,
                id="0"
            )
        except Exception as e:
            if "BUSYGROUP" not in str(e):
                raise
        
        # Read messages as consumer
        messages = redis_client.xreadgroup(
            groupname=consumer_group,
            consumername="test_consumer",
            streams={stream_name: ">"},
            count=10,
            block=1000
        )
        
        assert len(messages) > 0
        stream_data = messages[0]
        assert stream_data[0] == stream_name
        assert len(stream_data[1]) == 3

    def test_idempotency_prevents_duplicates(
        self,
        redis_client,
        stream_name,
        consumer_group,
        sample_emails
    ):
        """Test idempotency manager prevents duplicate processing"""
        idempotency = IdempotencyManager(redis_client, key_prefix="test_processed")
        processor = EmailProcessor()
        
        # Push test email
        email = sample_emails[0]
        redis_client.xadd(stream=stream_name, fields=email)
        
        # Create consumer group
        try:
            redis_client.xgroup_create(
                stream=stream_name,
                groupname=consumer_group,
                id="0"
            )
        except:
            pass
        
        # First processing
        messages = redis_client.xreadgroup(
            groupname=consumer_group,
            consumername="test_consumer",
            streams={stream_name: ">"},
            count=1
        )
        
        msg_id, msg_data = messages[0][1][0]
        email_id = msg_data["message_id"]
        
        # Check not duplicate
        assert not idempotency.is_duplicate(email_id)
        
        # Process and mark
        processor.process(msg_data)
        idempotency.mark_processed(email_id)
        
        # Second attempt should detect duplicate
        assert idempotency.is_duplicate(email_id)

    def test_failed_message_sent_to_dlq(self, redis_client, stream_name):
        """Test failed messages are sent to DLQ after max retries"""
        dlq_manager = DLQManager(
            redis_client,
            dlq_stream_name="test_dlq"
        )
        backoff = BackoffManager(max_retries=2)
        
        email_id = "failed-email-001"
        email_data = {
            "message_id": email_id,
            "from": "sender@example.com",
            "subject": "Test",
            "date": "2026-02-16T10:00:00Z"
        }
        
        # Simulate multiple failures
        for attempt in range(2):
            backoff.record_failure(email_id)
        
        # Check max retries exceeded
        assert backoff.has_exceeded_max_retries(email_id)
        
        # Send to DLQ
        dlq_id = dlq_manager.send_to_dlq(
            message_id=email_id,
            original_data=email_data,
            error=Exception("Processing failed"),
            retry_count=2
        )
        
        assert dlq_id is not None
        
        # Verify in DLQ
        dlq_length = dlq_manager.get_dlq_length()
        assert dlq_length == 1

    def test_complete_pipeline_success(
        self,
        redis_client,
        stream_name,
        consumer_group,
        sample_emails
    ):
        """Test complete pipeline: produce -> consume -> process -> ack"""
        idempotency = IdempotencyManager(redis_client, key_prefix="test_processed")
        processor = EmailProcessor()
        
        # 1. Producer: Push emails to stream
        for email in sample_emails[:2]:  # Use 2 emails for faster test
            redis_client.xadd(stream=stream_name, fields=email)
        
        # 2. Worker: Setup consumer group
        try:
            redis_client.xgroup_create(
                stream=stream_name,
                groupname=consumer_group,
                id="0"
            )
        except:
            pass
        
        # 3. Worker: Consume messages
        messages = redis_client.xreadgroup(
            groupname=consumer_group,
            consumername="test_consumer",
            streams={stream_name: ">"},
            count=10,
            block=1000
        )
        
        processed_count = 0
        for stream_data in messages:
            for msg_id, msg_data in stream_data[1]:
                email_id = msg_data["message_id"]
                
                # Check idempotency
                if not idempotency.is_duplicate(email_id):
                    # Process
                    result = processor.process(msg_data)
                    assert result["status"] == "success"
                    
                    # Mark processed
                    idempotency.mark_processed(email_id)
                    
                    # Acknowledge
                    redis_client.xack(
                        stream=stream_name,
                        groupname=consumer_group,
                        ids=msg_id
                    )
                    
                    processed_count += 1
        
        assert processed_count == 2
        assert processor.processed_count == 2

    def test_reprocess_from_dlq(self, redis_client, stream_name):
        """Test reprocessing message from DLQ back to main stream"""
        dlq_manager = DLQManager(redis_client, dlq_stream_name="test_dlq")
        
        # Send message to DLQ
        original_data = {
            "message_id": "email-dlq-001",
            "from": "sender@example.com",
            "subject": "Failed Email",
            "date": "2026-02-16T10:00:00Z"
        }
        
        dlq_id = dlq_manager.send_to_dlq(
            message_id="email-dlq-001",
            original_data=original_data,
            error=Exception("Test failure"),
            retry_count=3
        )
        
        # Reprocess from DLQ
        new_id = dlq_manager.reprocess_from_dlq(
            dlq_entry_id=dlq_id,
            target_stream=stream_name
        )
        
        assert new_id is not None
        
        # Verify message is back in main stream
        messages = redis_client.client.xrange(stream_name, "-", "+")
        assert len(messages) > 0
        
        # Verify DLQ entry was removed
        dlq_length = dlq_manager.get_dlq_length()
        assert dlq_length == 0

    def test_concurrent_consumers(self, redis_client, stream_name, consumer_group):
        """Test multiple consumers in same group don't process same messages"""
        # Push test messages
        for i in range(10):
            redis_client.xadd(
                stream=stream_name,
                fields={
                    "message_id": f"email-{i:03d}",
                    "from": "sender@example.com",
                    "subject": f"Email {i}",
                    "date": "2026-02-16T10:00:00Z"
                }
            )
        
        # Create consumer group
        try:
            redis_client.xgroup_create(
                stream=stream_name,
                groupname=consumer_group,
                id="0"
            )
        except:
            pass
        
        # Simulate two consumers
        consumer1_messages = redis_client.xreadgroup(
            groupname=consumer_group,
            consumername="consumer1",
            streams={stream_name: ">"},
            count=5
        )
        
        consumer2_messages = redis_client.xreadgroup(
            groupname=consumer_group,
            consumername="consumer2",
            streams={stream_name: ">"},
            count=5
        )
        
        # Each consumer should get different messages
        consumer1_ids = [msg[0] for msg in consumer1_messages[0][1]] if consumer1_messages else []
        consumer2_ids = [msg[0] for msg in consumer2_messages[0][1]] if consumer2_messages else []
        
        # Verify no overlap
        assert len(set(consumer1_ids) & set(consumer2_ids)) == 0
        
        # Total should be 10
        assert len(consumer1_ids) + len(consumer2_ids) == 10


@pytest.mark.slow
class TestEmailIngestionLoadTest:
    """
    Load tests for email ingestion system.
    Mark tests with @pytest.mark.slow to exclude from regular test runs.
    """

    @pytest.fixture
    def redis_client(self):
        """Create Redis client for load tests"""
        try:
            client = RedisClient(host="localhost", port=6379, db=15)
            client.ping()
            yield client
            client.client.flushdb()
            client.close()
        except Exception as e:
            pytest.skip(f"Redis not available: {e}")

    def test_throughput_1000_messages(self, redis_client):
        """Test processing throughput with 1000 messages"""
        stream_name = "load_test_stream"
        processor = EmailProcessor()
        idempotency = IdempotencyManager(redis_client, key_prefix="load_test")
        
        # Generate test data
        num_messages = 1000
        start_time = time.time()
        
        # Push messages
        for i in range(num_messages):
            redis_client.xadd(
                stream=stream_name,
                fields={
                    "message_id": f"load-test-{i:06d}",
                    "from": f"sender{i}@example.com",
                    "subject": f"Load Test {i}",
                    "date": "2026-02-16T10:00:00Z",
                    "body_preview": f"This is load test message {i}"
                }
            )
        
        push_time = time.time() - start_time
        
        # Process messages
        process_start = time.time()
        processed = 0
        
        while processed < num_messages:
            messages = redis_client.client.xrange(
                stream_name,
                min="-",
                max="+",
                count=100
            )
            
            for msg_id, msg_data in messages:
                email_id = msg_data["message_id"]
                if not idempotency.is_duplicate(email_id):
                    processor.process(msg_data)
                    idempotency.mark_processed(email_id)
                    processed += 1
            
            if not messages:
                break
        
        process_time = time.time() - process_start
        
        # Calculate metrics
        push_rate = num_messages / push_time
        process_rate = num_messages / process_time
        
        print(f"\nLoad Test Results:")
        print(f"Messages: {num_messages}")
        print(f"Push rate: {push_rate:.1f} msg/s")
        print(f"Process rate: {process_rate:.1f} msg/s")
        print(f"Total time: {push_time + process_time:.2f}s")
        
        # Assert reasonable performance
        assert push_rate > 100  # Should push >100 msg/s
        assert process_rate > 50  # Should process >50 msg/s
