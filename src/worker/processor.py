"""
Email processor - Business logic for processing emails from the stream.
Extensible processor with hooks for custom business logic.
"""
from typing import Dict, Any, Optional, Callable
from datetime import datetime
import json

from src.common.logging_config import get_logger
from src.common.exceptions import ProcessingError

logger = get_logger(__name__)


class EmailProcessor:
    """
    Processes emails from Redis Stream with extensible business logic.
    Provides base processing with hooks for custom implementations.
    """

    def __init__(
        self,
        custom_handler: Optional[Callable[[Dict[str, Any]], Dict[str, Any]]] = None
    ):
        """
        Initialize email processor.

        Args:
            custom_handler: Optional custom processing function.
                           Takes email data dict, returns processed result dict.
        """
        self.custom_handler = custom_handler
        self.processed_count = 0
        self.failed_count = 0
        logger.info("EmailProcessor initialized")

    def process(self, message_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Process an email message.

        Args:
            message_data: Email data from Redis stream

        Returns:
            Processing result dictionary with status and metadata

        Raises:
            ProcessingError: If processing fails
        """
        try:
            start_time = datetime.now()
            
            # Extract email details
            message_id = message_data.get("message_id", "unknown")
            email_from = message_data.get("from", "")
            subject = message_data.get("subject", "")
            
            logger.info(
                f"Processing email: {message_id} "
                f"from={email_from}, subject='{subject}'"
            )
            
            # Validate required fields
            self._validate_email(message_data)
            
            # Apply custom business logic if provided
            if self.custom_handler:
                result = self.custom_handler(message_data)
            else:
                result = self._default_processing(message_data)
            
            # Calculate processing time
            processing_time = (datetime.now() - start_time).total_seconds()
            
            # Increment success counter
            self.processed_count += 1
            
            # Return result with metadata
            return {
                "status": "success",
                "message_id": message_id,
                "processing_time_seconds": processing_time,
                "processed_at": datetime.now().isoformat(),
                "result": result
            }
            
        except ProcessingError:
            self.failed_count += 1
            raise
        except Exception as e:
            self.failed_count += 1
            message_id = message_data.get("message_id", "unknown")
            logger.error(f"Email processing failed: {e}")
            raise ProcessingError(f"Processing failed for {message_id}: {e}")

    def _validate_email(self, message_data: Dict[str, Any]):
        """
        Validate email data has required fields.

        Args:
            message_data: Email data dictionary

        Raises:
            ProcessingError: If validation fails
        """
        required_fields = ["message_id", "from", "subject", "date"]
        missing_fields = [
            field for field in required_fields
            if field not in message_data
        ]
        
        if missing_fields:
            raise ProcessingError(
                f"Missing required fields: {', '.join(missing_fields)}"
            )
        
        logger.debug(f"Email validation passed for {message_data.get('message_id')}")

    def _default_processing(self, message_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Default processing logic (placeholder for custom business logic).

        Args:
            message_data: Email data dictionary

        Returns:
            Processing result dictionary
        """
        # Default: extract key fields and log them
        result = {
            "message_id": message_data.get("message_id"),
            "from": message_data.get("from"),
            "to": message_data.get("to", []),
            "subject": message_data.get("subject"),
            "date": message_data.get("date"),
            "has_attachments": message_data.get("has_attachments", "false") == "true",
            "body_preview": message_data.get("body_preview", "")[:100]
        }
        
        logger.info(
            f"Default processing completed for {result['message_id']}: "
            f"from={result['from']}, subject='{result['subject']}'"
        )
        
        # TODO: Add your custom business logic here
        # Examples:
        # - Store in database
        # - Send to analytics pipeline
        # - Trigger notifications
        # - Extract attachments
        # - Classify/categorize emails
        
        return result

    def process_batch(self, messages: list) -> Dict[str, Any]:
        """
        Process multiple messages in batch.

        Args:
            messages: List of message dictionaries

        Returns:
            Batch processing summary with success/failure counts
        """
        batch_start = datetime.now()
        results = {
            "total": len(messages),
            "successful": 0,
            "failed": 0,
            "errors": []
        }
        
        for msg in messages:
            try:
                self.process(msg)
                results["successful"] += 1
            except Exception as e:
                results["failed"] += 1
                results["errors"].append({
                    "message_id": msg.get("message_id", "unknown"),
                    "error": str(e)
                })
        
        batch_time = (datetime.now() - batch_start).total_seconds()
        results["batch_processing_time_seconds"] = batch_time
        results["messages_per_second"] = results["total"] / batch_time if batch_time > 0 else 0
        
        logger.info(
            f"Batch processing complete: {results['successful']}/{results['total']} "
            f"successful, {results['failed']} failed, {batch_time:.2f}s"
        )
        
        return results

    def get_stats(self) -> Dict[str, Any]:
        """
        Get processor statistics.

        Returns:
            Statistics dictionary
        """
        return {
            "processed_count": self.processed_count,
            "failed_count": self.failed_count,
            "success_rate": (
                self.processed_count / (self.processed_count + self.failed_count)
                if (self.processed_count + self.failed_count) > 0
                else 0.0
            )
        }

    def reset_stats(self):
        """Reset processor statistics counters."""
        self.processed_count = 0
        self.failed_count = 0
        logger.info("Processor statistics reset")


class ExtendedEmailProcessor(EmailProcessor):
    """
    Example extended processor with additional functionality.
    Override _default_processing() for custom business logic.
    """

    def _default_processing(self, message_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Extended processing with additional features.

        Args:
            message_data: Email data dictionary

        Returns:
            Extended processing result
        """
        # Call base processing
        result = super()._default_processing(message_data)
        
        # Add extended functionality
        result["processed_by"] = "ExtendedEmailProcessor"
        result["extended"] = True
        
        # Example: Extract and count keywords
        body = message_data.get("body_preview", "")
        keywords = ["urgent", "important", "action required"]
        result["keyword_matches"] = [
            kw for kw in keywords if kw.lower() in body.lower()
        ]
        
        # Example: Classify email priority
        if result["keyword_matches"]:
            result["priority"] = "high"
        else:
            result["priority"] = "normal"
        
        logger.info(
            f"Extended processing: {result['message_id']}, "
            f"priority={result['priority']}"
        )
        
        return result


def create_processor_from_config(
    custom_handler: Optional[Callable] = None
) -> EmailProcessor:
    """
    Factory function to create EmailProcessor from configuration.

    Args:
        custom_handler: Optional custom processing function

    Returns:
        Configured EmailProcessor instance
    """
    return EmailProcessor(custom_handler=custom_handler)
