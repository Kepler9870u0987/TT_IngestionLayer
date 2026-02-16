"""
Configuration management using Pydantic Settings.
Loads configuration from environment variables with validation.
"""
from pathlib import Path
from dotenv import load_dotenv
from pydantic_settings import BaseSettings
from pydantic import Field
from typing import Optional

# Load .env file into os.environ so all nested BaseSettings pick up values
_env_path = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(_env_path)


class RedisSettings(BaseSettings):
    """Redis connection and stream configuration"""
    host: str = Field(default="localhost")
    port: int = Field(default=6379)
    password: Optional[str] = Field(default=None)
    db: int = Field(default=0)
    stream_name: str = Field(default="email_ingestion_stream")
    max_stream_length: int = Field(default=10000)

    class Config:
        env_prefix = "REDIS_"


class IMAPSettings(BaseSettings):
    """IMAP server configuration"""
    host: str = Field(default="imap.gmail.com")
    port: int = Field(default=993)
    mailbox: str = Field(default="INBOX")
    poll_interval_seconds: int = Field(default=60)

    class Config:
        env_prefix = "IMAP_"


class OAuth2Settings(BaseSettings):
    """OAuth2 Google authentication configuration"""
    client_id: str
    client_secret: str
    redirect_uri: str = Field(default="http://localhost:8080")
    token_file: str = Field(default="tokens/gmail_token.json")

    class Config:
        env_prefix = "GOOGLE_"


class WorkerSettings(BaseSettings):
    """Worker and consumer group configuration"""
    consumer_group_name: str = Field(default="email_processor_group")
    consumer_name: str = Field(default="worker_01")
    batch_size: int = Field(default=10)
    block_timeout_ms: int = Field(default=5000)

    class Config:
        env_prefix = ""


class IdempotencySettings(BaseSettings):
    """Idempotency configuration"""
    ttl_seconds: int = Field(default=86400)

    class Config:
        env_prefix = "IDEMPOTENCY_"


class DLQSettings(BaseSettings):
    """Dead Letter Queue configuration"""
    stream_name: str = Field(default="email_ingestion_dlq")
    max_retry_attempts: int = Field(default=3)
    initial_backoff_seconds: int = Field(default=2)
    max_backoff_seconds: int = Field(default=3600)

    class Config:
        env_prefix = ""


class MonitoringSettings(BaseSettings):
    """Monitoring and health check configuration"""
    metrics_port: int = Field(default=9090)
    health_check_port: int = Field(default=8080)

    class Config:
        env_prefix = ""


class CircuitBreakerSettings(BaseSettings):
    """Circuit breaker configuration"""
    failure_threshold: int = Field(default=5)
    recovery_timeout_seconds: float = Field(default=60.0)
    success_threshold: int = Field(default=3)

    class Config:
        env_prefix = "CB_"


class RecoverySettings(BaseSettings):
    """Orphaned message recovery configuration"""
    min_idle_ms: int = Field(default=300000)
    max_claim_count: int = Field(default=50)
    max_delivery_count: int = Field(default=10)
    check_interval_seconds: int = Field(default=60)

    class Config:
        env_prefix = "RECOVERY_"


class LoggingSettings(BaseSettings):
    """Logging configuration"""
    level: str = Field(default="INFO")
    format: str = Field(default="json")

    class Config:
        env_prefix = "LOG_"


class Settings(BaseSettings):
    """Main settings aggregating all configuration sections"""
    redis: RedisSettings = Field(default_factory=RedisSettings)
    imap: IMAPSettings = Field(default_factory=IMAPSettings)
    oauth2: OAuth2Settings = Field(default_factory=OAuth2Settings)
    worker: WorkerSettings = Field(default_factory=WorkerSettings)
    idempotency: IdempotencySettings = Field(default_factory=IdempotencySettings)
    dlq: DLQSettings = Field(default_factory=DLQSettings)
    monitoring: MonitoringSettings = Field(default_factory=MonitoringSettings)
    circuit_breaker: CircuitBreakerSettings = Field(default_factory=CircuitBreakerSettings)
    recovery: RecoverySettings = Field(default_factory=RecoverySettings)
    logging: LoggingSettings = Field(default_factory=LoggingSettings)

    class Config:
        extra = "ignore"


# Singleton instance - import this in other modules
try:
    settings = Settings()
except Exception as e:
    # During testing or initial setup, settings might not be fully configured
    print(f"Warning: Could not load settings: {e}")
    settings = None
