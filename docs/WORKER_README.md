# Worker - Email Consumer & Processor

Worker component that consumes emails from Redis Streams and processes them with idempotency, retry logic, and Dead Letter Queue (DLQ) support.

## Features

- **Consumer Groups**: Horizontal scaling with multiple worker instances
- **Idempotency**: Redis Sets-based deduplication prevents duplicate processing
- **Exponential Backoff**: Configurable retry logic with increasing delays
- **Dead Letter Queue**: Failed messages after max retries sent to DLQ for manual review
- **Extensible Processing**: Custom business logic via processor hooks
- **Graceful Shutdown**: Clean shutdown on SIGINT/SIGTERM
- **Comprehensive Logging**: Structured JSON logs with statistics

## Quick Start

### 1. Start Redis

```bash
redis-server
```

### 2. Configure Environment

Ensure `.env` is configured (see `.env.example`):

```env
# Redis Configuration
REDIS_HOST=localhost
REDIS_PORT=6379
REDIS_STREAM_NAME=email_ingestion_stream

# Worker Configuration
CONSUMER_GROUP_NAME=email_processor_group
CONSUMER_NAME=worker_01
BATCH_SIZE=10
BLOCK_TIMEOUT_MS=5000

# DLQ Configuration
DLQ_STREAM_NAME=email_ingestion_dlq
MAX_RETRY_ATTEMPTS=3
INITIAL_BACKOFF_SECONDS=2
MAX_BACKOFF_SECONDS=3600

# Idempotency Configuration
IDEMPOTENCY_TTL_SECONDS=604800  # 7 days
```

### 3. Run Worker

```bash
python worker.py
```

### 4. Run with Custom Options

```bash
# Custom consumer name for scaling
python worker.py --consumer worker_02

# Different batch size
python worker.py --batch-size 50

# Custom stream
python worker.py --stream my_email_stream --group my_group
```

## CLI Options

```
--stream         Redis stream name (default: from settings)
--group          Consumer group name (default: from settings)
--consumer       Consumer name (default: from settings)
--batch-size     Number of messages per batch (default: 10)
--block-timeout  Block timeout in milliseconds (default: 5000)
```

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                      Redis Streams                          │
│                 (email_ingestion_stream)                    │
└──────────────────────┬──────────────────────────────────────┘
                       │
                       ↓ XREADGROUP
          ┌────────────────────────────┐
          │    Consumer Group          │
          │  (email_processor_group)   │
          └────────────────────────────┘
                       │
           ┌───────────┴───────────┐
           ↓                       ↓
    ┌──────────┐            ┌──────────┐
    │ Worker 1 │            │ Worker 2 │
    │ (scaled) │            │ (scaled) │
    └──────────┘            └──────────┘
           │
           ↓
    ┌───────────────────┐
    │ Idempotency Check │  → Skip if duplicate
    │  (Redis Sets)     │
    └───────────────────┘
           │
           ↓
    ┌───────────────────┐
    │  Process Email    │  → Business Logic
    │   (Processor)     │
    └───────────────────┘
           │
      ┌────┴─────┐
      ↓          ↓
   Success    Failure
      │          │
      ↓          ↓
   XACK      Backoff
   Done      Retry
              │
         Max Retries?
              │
              ↓
         ┌─────────┐
         │   DLQ   │  → Manual Review
         └─────────┘
```

## Components

### IdempotencyManager (`src/worker/idempotency.py`)

Prevents duplicate processing using Redis Sets:

```python
from src.worker.idempotency import IdempotencyManager

idempotency = IdempotencyManager(redis_client, ttl_hours=168)

if not idempotency.is_duplicate(message_id):
    # Process message
    processor.process(message_data)
    idempotency.mark_processed(message_id)
```

### BackoffManager (`src/worker/backoff.py`)

Exponential backoff for retries:

```python
from src.worker.backoff import BackoffManager

backoff = BackoffManager(initial_delay=2.0, max_retries=5)

if backoff.should_retry(message_id):
    try:
        process_message()
        backoff.record_success(message_id)
    except Exception:
        backoff.record_failure(message_id)
```

### DLQManager (`src/worker/dlq.py`)

Dead Letter Queue for failed messages:

```python
from src.worker.dlq import DLQManager

dlq = DLQManager(redis_client, dlq_stream_name="email_ingestion_dlq")

# Send failed message to DLQ
dlq.send_to_dlq(
    message_id=message_id,
    original_data=message_data,
    error=exception,
    retry_count=attempts
)

# Peek at DLQ
messages = dlq.peek_dlq(count=10)

# Reprocess from DLQ
dlq.reprocess_from_dlq(dlq_entry_id, target_stream="email_ingestion_stream")
```

### EmailProcessor (`src/worker/processor.py`)

Extensible email processor:

```python
from src.worker.processor import EmailProcessor

# With custom handler
def custom_handler(message_data):
    # Your business logic here
    return {"processed": True, "data": message_data}

processor = EmailProcessor(custom_handler=custom_handler)
result = processor.process(message_data)
```

## Horizontal Scaling

Run multiple workers with different consumer names:

```bash
# Terminal 1
python worker.py --consumer worker_01

# Terminal 2
python worker.py --consumer worker_02

# Terminal 3
python worker.py --consumer worker_03
```

Consumer groups ensure each message is processed by only one worker.

## Monitoring

### Check Consumer Group

```bash
redis-cli XINFO GROUPS email_ingestion_stream
```

### Check DLQ Length

```bash
redis-cli XLEN email_ingestion_dlq
```

### View DLQ Messages

```bash
redis-cli XRANGE email_ingestion_dlq - + COUNT 10
```

### Worker Statistics

Workers log statistics periodically:

```json
{
  "message": "Worker Stats - Total: 1000, Processed: 950, Skipped: 45, Failed: 5, DLQ: 5",
  "level": "INFO",
  "timestamp": "2026-02-16T10:00:00Z"
}
```

## Testing

### Unit Tests

```bash
pytest tests/unit/test_idempotency.py -v
pytest tests/unit/test_backoff.py -v
pytest tests/unit/test_dlq.py -v
pytest tests/unit/test_processor.py -v
```

### Integration Tests

Requires running Redis instance:

```bash
pytest tests/integration/test_end_to_end.py -v
```

### Coverage

```bash
pytest tests/unit/ --cov=src/worker --cov-report=html
```

## Troubleshooting

### Worker Not Processing Messages

1. Check Redis connection:
   ```bash
   redis-cli PING
   ```

2. Verify consumer group exists:
   ```bash
   redis-cli XINFO GROUPS email_ingestion_stream
   ```

3. Check stream has messages:
   ```bash
   redis-cli XLEN email_ingestion_stream
   ```

### Messages Going to DLQ

1. Check worker logs for error details
2. Inspect DLQ messages:
   ```bash
   redis-cli XRANGE email_ingestion_dlq - + COUNT 1
   ```
3. Common causes:
   - Missing required fields in message
   - Processing logic errors
   - External service failures

### High Memory Usage

1. Check processed messages set size:
   ```bash
   redis-cli SCARD processed_messages:set
   ```
2. Enable TTL for idempotency in `.env`:
   ```
   IDEMPOTENCY_TTL_SECONDS=604800  # 7 days
   ```

## Performance Tuning

- **Batch Size**: Increase for higher throughput (trade-off: latency)
- **Block Timeout**: Lower for faster response, higher for less Redis load
- **Max Retries**: Adjust based on failure patterns
- **Consumer Count**: Scale horizontally for higher throughput

Target Performance:
- **Throughput**: >100 messages/second per worker
- **Latency**: <1 second average processing time
- **Availability**: Graceful degradation with DLQ

## Next Steps

- Implement custom business logic in `EmailProcessor`
- Set up monitoring (Phase 5: Prometheus + Grafana)
- Configure alerting for DLQ length
- Implement health checks (Phase 4)
- Load testing (Phase 4)

## Related Documentation

- [Producer Documentation](README.md)
- [OAuth2 Setup](docs/OAUTH2_SETUP.md)
- [Progress Tracker](PROGRESS.md)
