# Troubleshooting Guide

## Health Endpoints
- `/health` should return `alive`; `/ready` should return `ready`
- If `/status` is slow, check for heavy Redis latency or blocked worker threads

## Common Failures
- **Redis connection errors**: verify host/port, check firewall, ensure Redis running
- **OAuth2 refresh failures**: re-run `python producer.py --auth-setup`
- **Consumer lag**: check worker logs, increase workers or batch size, verify no hot partitions
- **DLQ growth**: inspect entries, fix processing errors, use `worker.dlq` tools to reprocess

## Metrics to Watch
- `email_ingestion_emails_failed_total` and `email_ingestion_dlq_messages_total`
- Processing latency quantiles (p90/p99)
- Stream depth gauge `email_ingestion_stream_depth`
- Backoff retries `email_ingestion_backoff_retries_total`

## Quick Checks
- Redis ping: `redis-cli PING`
- Stream info: `redis-cli XINFO STREAM email_ingestion_stream`
- Consumer groups: `redis-cli XINFO GROUPS email_ingestion_stream`
- Logs: ensure correlation IDs are present for traceability
