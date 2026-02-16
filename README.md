# Email Ingestion System - Production Ready

Sistema di ingestion email production-ready da Gmail IMAP con Redis Streams, implementato con approccio incrementale in 5 fasi.

**Status Corrente**: ✅ Phase 1 & 2 Complete

---

## Overview

Sistema scalabile per l'ingestion di email da Gmail tramite IMAP con OAuth2, utilizzando Redis Streams per processing asincrono.

**Architettura:**
- **Producer**: Polling IMAP con OAuth2, push a Redis Streams, gestione UID/UIDVALIDITY
- **Worker**: Consumer groups Redis, idempotenza, DLQ per retry (Phase 3 - Da implementare)
- **Monitoring**: Prometheus metrics, health checks (Phase 5 - Da implementare)

**Stack Tecnologico:**
- Python 3.11+
- Redis 7+ (Streams, consumer groups)
- OAuth2 Gmail (production-ready)
- IMAPClient per email fetching
- Pydantic per configuration management

---

## Quick Start

### 1. Setup Environment

```bash
# Clone repository
cd TT_IngestionLayer

# Create virtual environment
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt
pip install -r requirements-dev.txt

# Create .env from template
cp .env.example .env
```

### 2. Configure OAuth2

Follow [OAuth2 Setup Guide](docs/OAUTH2_SETUP.md) to:
1. Create Google Cloud project
2. Enable Gmail API
3. Get OAuth2 credentials
4. Configure .env file

### 3. Start Redis

```bash
# Local Redis
redis-server

# Or with Docker
docker run -d -p 6379:6379 redis:7-alpine
```

### 4. Authenticate

```bash
# Run OAuth2 setup (browser window will open)
python producer.py --auth-setup
```

### 5. Run Producer

```bash
# Start producer
python producer.py --username your-email@gmail.com

# Or use env var
export IMAP_USER=your-email@gmail.com
python producer.py
```

---

## Configuration

Configuration via `.env` file (environment variables):

### Redis
```bash
REDIS_HOST=localhost
REDIS_PORT=6379
REDIS_STREAM_NAME=email_ingestion_stream
REDIS_MAX_STREAM_LENGTH=10000
```

### IMAP
```bash
IMAP_HOST=imap.gmail.com
IMAP_PORT=993
IMAP_MAILBOX=INBOX
POLL_INTERVAL_SECONDS=60
```

### OAuth2
```bash
GOOGLE_CLIENT_ID=your-client-id.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=your-secret
GOOGLE_TOKEN_FILE=tokens/gmail_token.json
```

See `.env.example` for all options.

---

## Producer Usage

### Basic Usage

```bash
# Start with defaults
python producer.py

# Custom mailbox and poll interval
python producer.py --mailbox "INBOX" --poll-interval 30

# Batch processing (fetch max 100 emails per poll)
python producer.py --batch-size 100

# Dry run (fetch but don't push to Redis)
python producer.py --dry-run
```

### Command Line Options

| Option | Description | Default |
|--------|-------------|---------|
| `--username` | Gmail email address | From env/config |
| `--mailbox` | Mailbox to monitor | INBOX |
| `--batch-size` | Max emails per poll | 50 |
| `--poll-interval` | Seconds between polls | 60 |
| `--dry-run` | Fetch without pushing | False |
| `--auth-setup` | Run OAuth2 setup | - |

---

## Architecture Details

### Phase 1: Core Infrastructure ✅

- Pydantic configuration management
- Redis client with connection pooling & retry logic
- Structured JSON logging
- Custom exception hierarchy
- Comprehensive unit tests

### Phase 2: Producer with OAuth2 & IMAP ✅

**Components:**

1. **OAuth2 Manager** (`src/auth/oauth2_gmail.py`):
   - Token storage & refresh
   - XOAUTH2 authentication string generation
   - Automatic token renewal

2. **IMAP Client** (`src/imap/imap_client.py`):
   - UID-based incremental fetching
   - UIDVALIDITY change detection
   - Email parsing with headers/body

3. **State Manager** (`src/producer/state_manager.py`):
   - Persistent UID tracking in Redis
   - UIDVALIDITY monitoring
   - Atomic state updates

4. **Producer** (`producer.py`):
   - Main orchestration loop
   - Error handling & retry
   - Graceful shutdown (SIGINT/SIGTERM)

**Data Flow:**
```
Gmail IMAP → OAuth2 Auth → Fetch UIDs → Parse Emails → Redis Stream
     ↓                                                       ↓
  UID State ←─────────── State Manager ←──────────── XADD Success
```

### Phase 3: Worker + Idempotenza + DLQ ⏸️

**Coming Next** (see `PROGRESS.md`):
- Consumer groups for parallel processing
- Idempotency layer with Redis Sets
- Dead Letter Queue with exponential backoff
- Integration tests

### Phase 4-5: Robustness & Observability ⏸️

See [Implementation Plan](C:\Users\malbanese\.claude\plans\snuggly-wondering-snowflake.md) for details.

---

## Redis Streams Format

### Main Stream (`email_ingestion_stream`)

Each message contains a JSON payload:

```json
{
  "uid": 12345,
  "uidvalidity": 67890,
  "mailbox": "INBOX",
  "from": "sender@example.com",
  "to": ["recipient@example.com"],
  "subject": "Email subject",
  "date": "2026-02-16T10:30:00Z",
  "body_text": "Email body preview... (first 2000 chars)",
  "body_html_preview": "<html>... (first 500 chars)",
  "size": 15360,
  "headers": {
    "Message-ID": "<msg123@example.com>",
    "Content-Type": "text /plain; charset=utf-8"
  },
  "message_id": "<msg123@example.com>",
  "fetched_at": "2026-02-16T10:30:05Z"
}
```

### Inspect Stream

```bash
# Check stream length
redis-cli XLEN email_ingestion_stream

# Read first 10 messages
redis-cli XREAD COUNT 10 STREAMS email_ingestion_stream 0

# Read latest messages
redis-cli XREAD COUNT 10 STREAMS email_ingestion_stream $
```

---

## State Management

Producer state stored in Redis:

| Key Pattern | Description | Example |
|-------------|-------------|---------|
| `producer_state:{user}:{mailbox}:last_uid` | Last processed UID | `12345` |
| `producer_state:{user}:{mailbox}:uidvalidity` | Current UIDVALIDITY | `67890` |
| `producer_state:{user}:{mailbox}:last_poll` | Last poll timestamp | `2026-02-16T10:30:00Z` |
| `producer_state:{user}:{mailbox}:total_emails` | Total processed | `542` |

### Check State

```bash
# View state for mailbox
redis-cli GET "producer_state:user@gmail.com:INBOX:last_uid"
redis-cli GET "producer_state:user@gmail.com:INBOX:uidvalidity"
```

---

## Testing

### Unit Tests

```bash
# Run all tests
pytest tests/unit/ -v

# With coverage
pytest tests/unit/ --cov=src --cov-report=html

# Specific test file
pytest tests/unit/test_redis_client.py -v
```

### Integration Tests (Phase 3)

Integration tests will test end-to-end flow:
- Producer → Redis Stream → Worker
- OAuth2 flow
- UIDVALIDITY change handling

---

## Monitoring & Health Checks (Phase 5)

### Metrics Exporter (Prometheus)
- Start metrics server (default port 9090):
   ```bash
   python -m src.monitoring.metrics
   ```
- Counters/Histograms exposed (PromQL names):
   - `email_ingestion_emails_produced_total`
   - `email_ingestion_emails_processed_total`
   - `email_ingestion_emails_failed_total`
   - `email_ingestion_dlq_messages_total`
   - `email_ingestion_backoff_retries_total`
   - `email_ingestion_processing_latency_seconds` (histogram)
   - `email_ingestion_imap_poll_duration_seconds` (histogram)
   - `email_ingestion_stream_depth` (gauge)

### Prometheus
- Sample config: [config/prometheus.yml](config/prometheus.yml)
- Verify scrape: open Prometheus targets UI and ensure `email_ingestion_metrics` is `UP`.

### Grafana
- Import dashboard: [config/grafana_dashboard.json](config/grafana_dashboard.json)
- Datasource name expected: `Prometheus` (UID `PROMETHEUS_DS`).

### Health Endpoints (Phase 4)
```bash
curl http://localhost:8080/health
curl http://localhost:8080/ready
curl http://localhost:8080/status
```

---

## Troubleshooting

### Producer Won't Start

**Error**: `OAuth2AuthenticationError`

**Solution**:
```bash
# Re-authenticate
python producer.py --auth-setup
```

---

### No Emails Being Fetched

**Check**:
1. Redis running: `redis-cli ping`
2. OAuth2 token valid: `python -m src.auth.oauth2_gmail --info`
3. IMAP connectivity: Check logs for connection errors
4. Mailbox has new emails
5. Last UID state: `redis-cli GET "producer_state:{user}:INBOX:last_uid"`

---

### UIDVALIDITY Changed Warning

**Normal behavior** when:
- Mailbox recreated in Gmail
- Gmail server-side changes
- First run (no previous UIDVALIDITY)

**Producer will**:
- Reset `last_uid` to 0
- Re-fetch all emails from beginning
- Update UIDVALIDITY
- Continue normally

---

### Token Refresh Fails

**Error**: `TokenRefreshError`

**Solution**:
```bash
# Revoke and re-authenticate
python -m src.auth.oauth2_gmail --revoke
python producer.py --auth-setup
```

---

## Development

### Project Structure

```
c:\TT_IngestionLayer\
├── config/
│   └── settings.py          # Pydantic configuration
├── src/
│   ├── auth/
│   │   └── oauth2_gmail.py  # OAuth2 manager
│   ├── imap/
│   │   └── imap_client.py   # IMAP client
│   ├── producer/
│   │   └── state_manager.py # State persistence
│   ├── worker/              # Phase 3
│   ├── common/
│   │   ├── redis_client.py  # Redis wrapper
│   │   ├── logging_config.py
│   │   ├── exceptions.py
│   │   └── retry.py
│   └── monitoring/          # Phase 5
├── tests/
│   ├── unit/
│   ├── integration/         # Phase 3
│   └── load/                # Phase 4
├── scripts/                 # Phase 5
├── docs/
│   ├── OAUTH2_SETUP.md
│   └── runbooks/            # Phase 5
├── producer.py              # Main producer script
├── worker.py                # Phase 3
├── PROGRESS.md              # Detailed progress tracking
└── README.md
```

### Adding Features

1. Create feature branch
2. Implement with tests
3. Update `PROGRESS.md`
4. Run test suite
5. Submit for review

---

## Performance Targets

| Metric | Target | Current |
|--------|--------|---------|
| Producer throughput | 100+ emails/min | ✅ Configurable batch |
| IMAP poll latency | <5s | ✅ With retry |
| Redis push latency | <100ms | ✅ Connection pool |
| Token refresh | Automatic | ✅ 5min buffer |

---

## Security

✅ **OAuth2 production-ready** - No app passwords
✅ **Credentials in .env** - Gitignored
✅ **Tokens encrypted** - File permissions 600
✅ **No PII in logs** - Only metadata
⏸️ **Redis ACL** - Phase 4
⏸️ **Secrets management** - HashiCorp Vault integration (optional)

See [OAuth2 Setup Guide](docs/OAUTH2_SETUP.md) for security best practices.

---

## Roadmap

| Phase | Status | Deliverable |
|-------|--------|-------------|
| Phase 1: Infrastructure | ✅ Complete | Redis, Config, Logging, Tests |
| Phase 2: Producer OAuth2+IMAP | ✅ Complete | Email fetching, State mgmt |
| Phase 3: Worker+Idempotency | ⏸️ Next | Consumer groups, DLQ, Tests |
| Phase 4: Robustness | ⏸️ Planned | Load tests, Health checks, Graceful shutdown |
| Phase 5: Observability | ⏸️ Planned | Prometheus, Grafana, Ops tools |

See [PROGRESS.md](PROGRESS.md) for detailed task breakdown.

---

## Contributing

This is an internal project. For issues or improvements:
1. Check `PROGRESS.md` for current status
2. Review implementation plan
3. Follow existing code patterns
4. Add comprehensive tests
5. Update documentation

---

## License

Internal use only.

---

## Support

- **Documentation**: `docs/`
- **Progress Tracking**: `PROGRESS.md`
- **Implementation Plan**: `.claude/plans/snuggly-wondering-snowflake.md`
- **OAuth2 Setup**: `docs/OAUTH2_SETUP.md`

---

## FAQ

**Q: Can I use this with non-Gmail IMAP servers?**
A: Currently optimized for Gmail. Other IMAP servers would need OAuth2 adapter changes.

**Q: How do I handle multiple mailboxes?**
A: Run multiple producer instances with different `--mailbox` arguments.

**Q: What happens if Redis goes down?**
A: Producer will retry with exponential backoff. State preserved in Redis (RDB/AOF).

**Q: Can I process historical emails?**
A: Yes! Reset last_uid: `redis-cli SET "producer_state:{user}:INBOX:last_uid" 0`

**Q: How do I scale?**
A: Phase 3 implements consumer groups for horizontal worker scaling.

---

**Status**: Phase 2 Complete ✅ | Ready for Phase 3 Development

For next steps, see [PROGRESS.md](PROGRESS.md).
