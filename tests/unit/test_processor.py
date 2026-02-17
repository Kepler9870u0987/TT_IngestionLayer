"""
Unit tests for EmailProcessor.
"""
import pytest
from unittest.mock import Mock
from datetime import datetime

from src.worker.processor import (
    EmailProcessor,
    ExtendedEmailProcessor,
    create_processor_from_config,
    HIGH_PRIORITY_KEYWORDS,
    LOW_PRIORITY_KEYWORDS,
)
from src.common.exceptions import ProcessingError


class TestEmailProcessor:
    """Test suite for EmailProcessor"""

    @pytest.fixture
    def processor(self):
        """Create EmailProcessor instance"""
        return EmailProcessor()

    @pytest.fixture
    def sample_email(self):
        """Sample email data"""
        return {
            "message_id": "email-123",
            "from": "sender@example.com",
            "to": ["recipient@example.com"],
            "subject": "Test Email",
            "date": "2026-02-16T10:00:00Z",
            "body_text": "This is a test email body",
            "size": 1500,
        }

    def test_initialization(self, processor):
        """Test processor initialization"""
        assert processor.custom_handler is None
        assert processor.processed_count == 0
        assert processor.failed_count == 0

    def test_process_valid_email(self, processor, sample_email):
        """Test processing valid email"""
        result = processor.process(sample_email)
        
        assert result["status"] == "success"
        assert result["message_id"] == "email-123"
        assert "processing_time_seconds" in result
        assert "processed_at" in result
        assert "result" in result
        
        assert processor.processed_count == 1
        assert processor.failed_count == 0

    def test_process_missing_fields(self, processor):
        """Test processing email with missing required fields"""
        invalid_email = {
            "message_id": "email-456",
            "from": "sender@example.com"
            # Missing: subject, date
        }
        
        with pytest.raises(ProcessingError) as exc_info:
            processor.process(invalid_email)
        
        assert "Missing required fields" in str(exc_info.value)
        assert processor.failed_count == 1

    def test_validate_email_success(self, processor, sample_email):
        """Test email validation with valid data"""
        # Should not raise exception
        processor._validate_email(sample_email)

    def test_validate_email_failure(self, processor):
        """Test email validation with invalid data"""
        invalid_email = {"message_id": "test"}
        
        with pytest.raises(ProcessingError) as exc_info:
            processor._validate_email(invalid_email)
        
        assert "Missing required fields" in str(exc_info.value)

    def test_default_processing(self, processor, sample_email):
        """Test default processing logic"""
        result = processor._default_processing(sample_email)
        
        assert result["message_id"] == "email-123"
        assert result["from"] == "sender@example.com"
        assert result["subject"] == "Test Email"
        assert result["priority"] in ("high", "normal", "low")
        assert "body_preview" in result
        assert "processed_at" in result

    def test_custom_handler(self, sample_email):
        """Test processor with custom handler"""
        def custom_handler(data):
            return {"custom": True, "original_id": data["message_id"]}
        
        processor = EmailProcessor(custom_handler=custom_handler)
        result = processor.process(sample_email)
        
        assert result["status"] == "success"
        assert result["result"]["custom"] is True
        assert result["result"]["original_id"] == "email-123"

    def test_custom_handler_exception(self, sample_email):
        """Test custom handler raising exception"""
        def failing_handler(data):
            raise ValueError("Custom handler failed")
        
        processor = EmailProcessor(custom_handler=failing_handler)
        
        with pytest.raises(ProcessingError):
            processor.process(sample_email)

    def test_process_batch(self, processor):
        """Test batch processing"""
        messages = [
            {
                "message_id": f"email-{i}",
                "from": "sender@example.com",
                "subject": f"Test {i}",
                "date": "2026-02-16T10:00:00Z"
            }
            for i in range(5)
        ]
        
        result = processor.process_batch(messages)
        
        assert result["total"] == 5
        assert result["successful"] == 5
        assert result["failed"] == 0
        assert "batch_processing_time_seconds" in result
        assert result["messages_per_second"] > 0

    def test_process_batch_with_failures(self, processor):
        """Test batch processing with some failures"""
        messages = [
            {
                "message_id": "email-1",
                "from": "sender@example.com",
                "subject": "Valid",
                "date": "2026-02-16T10:00:00Z",
                "size": 100,
            },
            {
                "message_id": "email-2",
                "from": "sender@example.com"
                # Missing required fields
            },
            {
                "message_id": "email-3",
                "from": "sender@example.com",
                "subject": "Valid",
                "date": "2026-02-16T10:00:00Z",
                "size": 100,
            }
        ]
        
        result = processor.process_batch(messages)
        
        assert result["total"] == 3
        assert result["successful"] == 2
        assert result["failed"] == 1
        assert len(result["errors"]) == 1

    def test_get_stats(self, processor, sample_email):
        """Test getting processor statistics"""
        processor.process(sample_email)
        
        stats = processor.get_stats()
        
        assert stats["processed_count"] == 1
        assert stats["failed_count"] == 0
        assert stats["success_rate"] == 1.0

    def test_get_stats_with_failures(self, processor, sample_email):
        """Test statistics with failures"""
        processor.process(sample_email)  # Success
        
        try:
            processor.process({"message_id": "bad"})  # Failure
        except ProcessingError:
            pass
        
        stats = processor.get_stats()
        
        assert stats["processed_count"] == 1
        assert stats["failed_count"] == 1
        assert stats["success_rate"] == 0.5

    def test_reset_stats(self, processor, sample_email):
        """Test resetting statistics"""
        processor.process(sample_email)
        assert processor.processed_count == 1
        
        processor.reset_stats()
        
        assert processor.processed_count == 0
        assert processor.failed_count == 0

    def test_factory_function(self):
        """Test factory function creates processor correctly"""
        processor = create_processor_from_config()
        
        assert isinstance(processor, EmailProcessor)
        assert processor.custom_handler is None

    def test_normalize_email(self):
        """Test email normalization"""
        data = {
            "from": "  Sender@EXAMPLE.com  ",
            "to": ["  Recip@TEST.com "],
            "subject": "  Spaced  ",
        }
        result = EmailProcessor._normalize_email(data)
        assert result["from"] == "sender@example.com"
        assert result["to"] == ["recip@test.com"]
        assert result["subject"] == "Spaced"

    def test_classify_priority_high(self):
        """Test high priority classification"""
        data = {"subject": "URGENT: Need response", "from": "a@b.com"}
        assert EmailProcessor._classify_priority(data) == "high"

    def test_classify_priority_low(self):
        """Test low priority classification"""
        data = {"subject": "Newsletter Edition 42", "from": "noreply@news.com"}
        assert EmailProcessor._classify_priority(data) == "low"

    def test_classify_priority_normal(self):
        """Test normal priority classification"""
        data = {"subject": "Meeting Tomorrow", "from": "alice@corp.com"}
        assert EmailProcessor._classify_priority(data) == "normal"

    def test_size_validation_rejects_oversized(self):
        """Test that emails exceeding max size are rejected"""
        processor = EmailProcessor(max_email_size=1000)
        data = {
            "message_id": "big",
            "from": "a@b.com",
            "subject": "Big email",
            "date": "2026-01-01",
            "size": 2000,
        }
        with pytest.raises(ProcessingError, match="exceeds max size"):
            processor.process(data)

    def test_output_stream_forwarding(self):
        """Test processed email forwarded to output stream"""
        mock_redis = Mock()
        processor = EmailProcessor(
            output_stream="out_stream", redis_client=mock_redis
        )
        data = {
            "message_id": "fwd-1",
            "from": "a@b.com",
            "subject": "Forward me",
            "date": "2026-01-01",
            "size": 100,
        }
        processor.process(data)
        mock_redis.xadd.assert_called_once()


class TestExtendedEmailProcessor:
    """Test suite for ExtendedEmailProcessor"""

    @pytest.fixture
    def processor(self):
        """Create ExtendedEmailProcessor instance"""
        return ExtendedEmailProcessor()

    @pytest.fixture
    def sample_email(self):
        """Sample email data"""
        return {
            "message_id": "email-123",
            "from": "sender@example.com",
            "to": ["recipient@example.com"],
            "subject": "Urgent Action Required",
            "date": "2026-02-16T10:00:00Z",
            "body_text": "This is an URGENT message that requires immediate action",
            "size": 1500,
        }

    def test_extended_processing(self, processor, sample_email):
        """Test extended processing adds custom fields"""
        result = processor._default_processing(sample_email)
        
        assert result["processed_by"] == "ExtendedEmailProcessor"
        assert "keyword_matches" in result
        assert "priority" in result
        assert "sender_domain" in result

    def test_keyword_detection_high_priority(self, processor, sample_email):
        """Test keyword detection for high priority"""
        result = processor._default_processing(sample_email)
        
        assert result["priority"] == "high"

    def test_keyword_detection_normal_priority(self, processor):
        """Test normal priority email"""
        normal_email = {
            "message_id": "email-456",
            "from": "sender@example.com",
            "subject": "Regular Email",
            "date": "2026-02-16T10:00:00Z",
            "body_text": "Just a regular message",
            "size": 500,
        }
        
        result = processor._default_processing(normal_email)
        
        assert result["priority"] == "normal"
