"""
Custom exceptions for email ingestion system.
Hierarchical exception structure for better error handling and debugging.
"""


class BaseIngestionException(Exception):
    """Base exception for the email ingestion system"""
    pass


class RedisConnectionError(BaseIngestionException):
    """Error connecting to or communicating with Redis"""
    pass


class IMAPConnectionError(BaseIngestionException):
    """Error connecting to or communicating with IMAP server"""
    pass


class OAuth2AuthenticationError(BaseIngestionException):
    """Error during OAuth2 authentication flow"""
    pass


class TokenRefreshError(OAuth2AuthenticationError):
    """Error refreshing OAuth2 token"""
    pass


class IdempotencyError(BaseIngestionException):
    """Error in the idempotency checking system"""
    pass


class MessageProcessingError(BaseIngestionException):
    """Generic error during message processing"""
    pass


class StateManagementError(BaseIngestionException):
    """Error managing producer state (UID, UIDVALIDITY)"""
    pass


class DLQError(BaseIngestionException):
    """Error routing messages to Dead Letter Queue"""
    pass


class ConfigurationError(BaseIngestionException):
    """Error in configuration loading or validation"""
    pass
