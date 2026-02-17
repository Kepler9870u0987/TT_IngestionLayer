"""
Email processor - Business logic for processing emails from the stream.
Extensible processor with hooks for custom business logic.
Implements normalization, validation, classification, and output forwarding.
"""
from typing import Dict, Any, Optional, Callable, List
from datetime import datetime, timezone
import json
import re

from src.common.logging_config import get_logger
from src.common.exceptions import ProcessingError

logger = get_logger(__name__)

# Priority keywords for classification
HIGH_PRIORITY_KEYWORDS = [
    "urgent", "important", "action required", "critical",
    "asap", "immediate", "escalation", "outage", "incident",
]
LOW_PRIORITY_KEYWORDS = [
    "newsletter", "unsubscribe", "no-reply", "noreply",
    "marketing", "promotion", "digest",
]


class EmailProcessor:
    """
    Processes emails from Redis Stream with extensible business logic.
    Provides base processing with hooks for custom implementations.
    """

    def __init__(
        self,
        custom_handler: Optional[Callable[[Dict[str, Any]], Dict[str, Any]]] = None,
        output_stream: Optional[str] = None,
        redis_client: Optional[Any] = None,
        max_email_size: int = 26214400,
    ):
        """
        Initialize email processor.

        Args:
            custom_handler: Optional custom processing function.
                           Takes email data dict, returns processed result dict.
            output_stream: Optional Redis stream name to forward processed emails to.
            redis_client: Optional RedisClient for output stream forwarding.
            max_email_size: Maximum email size in bytes (default 25 MB).
        """
        self.custom_handler = custom_handler
        self.output_stream = output_stream
        self.redis_client = redis_client
        self.max_email_size = max_email_size
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
        Default processing: normalize, validate size, classify, and optionally
        forward to the output Redis stream.

        Args:
            message_data: Email data dictionary

        Returns:
            Processing result dictionary
        """
        # --- Normalize ---
        normalized = self._normalize_email(message_data)

        # --- Size validation ---
        email_size = int(normalized.get("size", 0))
        if email_size > self.max_email_size:
            raise ProcessingError(
                f"Email {normalized.get('message_id')} exceeds max size: "
                f"{email_size} > {self.max_email_size} bytes"
            )

        # --- Classify ---
        priority = self._classify_priority(normalized)

        result = {
            "message_id": normalized.get("message_id"),
            "from": normalized.get("from"),
            "to": normalized.get("to", []),
            "subject": normalized.get("subject"),
            "date": normalized.get("date"),
            "size": email_size,
            "priority": priority,
            "body_preview": str(normalized.get("body_text", ""))[:200],
            "processed_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        }

        # --- Forward to output stream ---
        if self.output_stream and self.redis_client:
            try:
                payload = json.dumps(result, ensure_ascii=False, default=str)
                self.redis_client.xadd(
                    self.output_stream,
                    {"payload": payload},
                    maxlen=10000,
                )
                logger.debug(
                    f"Forwarded {result['message_id']} to {self.output_stream}"
                )
            except Exception as e:
                logger.error(
                    f"Failed to forward to output stream: {e}"
                )
                # Non-fatal: message was still processed

        logger.info(
            f"Processed {result['message_id']}: "
            f"from={result['from']}, priority={priority}"
        )
        return result

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _normalize_email(data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Normalize email fields: trim whitespace, lowercase addresses.

        Args:
            data: Raw email data dictionary

        Returns:
            Normalized copy of the data
        """
        normalized = dict(data)

        # Lowercase & strip from/to addresses
        if "from" in normalized and isinstance(normalized["from"], str):
            normalized["from"] = normalized["from"].strip().lower()

        if "to" in normalized:
            to_val = normalized["to"]
            if isinstance(to_val, list):
                normalized["to"] = [
                    addr.strip().lower() if isinstance(addr, str) else addr
                    for addr in to_val
                ]
            elif isinstance(to_val, str):
                normalized["to"] = [to_val.strip().lower()]

        # Strip subject
        if "subject" in normalized and isinstance(normalized["subject"], str):
            normalized["subject"] = normalized["subject"].strip()

        return normalized

    @staticmethod
    def _classify_priority(data: Dict[str, Any]) -> str:
        """
        Classify email priority based on subject and sender.

        Args:
            data: Normalized email data

        Returns:
            Priority string: "high", "low", or "normal"
        """
        subject = str(data.get("subject", "")).lower()
        from_addr = str(data.get("from", "")).lower()
        combined = f"{subject} {from_addr}"

        for kw in HIGH_PRIORITY_KEYWORDS:
            if kw in combined:
                return "high"

        for kw in LOW_PRIORITY_KEYWORDS:
            if kw in combined:
                return "low"

        return "normal"

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
    Extended processor with additional keyword detection and
    sender-domain classification.
    """

    def _default_processing(self, message_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Extended processing: base logic + domain extraction, keyword matches.
        """
        result = super()._default_processing(message_data)

        result["processed_by"] = "ExtendedEmailProcessor"

        # Extract sender domain
        from_addr = str(message_data.get("from", ""))
        domain_match = re.search(r"@([\w.-]+)", from_addr)
        result["sender_domain"] = domain_match.group(1) if domain_match else "unknown"

        # Keyword matching in body
        body = str(message_data.get("body_text", ""))
        keywords = ["urgent", "important", "action required", "invoice", "payment"]
        result["keyword_matches"] = [
            kw for kw in keywords if kw.lower() in body.lower()
        ]

        logger.info(
            f"Extended processing: {result['message_id']}, "
            f"priority={result['priority']}, domain={result['sender_domain']}"
        )
        return result


def create_processor_from_config(
    custom_handler: Optional[Callable] = None,
    redis_client: Optional[Any] = None,
) -> EmailProcessor:
    """
    Factory function to create EmailProcessor from configuration.

    Args:
        custom_handler: Optional custom processing function
        redis_client: Optional RedisClient for output stream forwarding

    Returns:
        Configured EmailProcessor instance
    """
    try:
        from config.settings import settings
        output_stream = settings.processor.output_stream_name
        max_size = settings.processor.max_email_size_bytes
    except Exception:
        output_stream = None
        max_size = 26214400

    return EmailProcessor(
        custom_handler=custom_handler,
        output_stream=output_stream,
        redis_client=redis_client,
        max_email_size=max_size,
    )
