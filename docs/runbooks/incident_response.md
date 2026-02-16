# Incident Response - Pipeline Outage

## Symptoms
- Health checks failing (/ready returns not_ready)
- Producer/worker logs show connection or auth errors
- Lag in Redis stream depth increasing rapidly

## Immediate Actions
1. Check health endpoints: `curl http://localhost:8080/status`
2. Verify Redis connectivity: `redis-cli PING`
3. Inspect circuit breaker state in logs (should be CLOSED)
4. Verify OAuth2 token validity if producer is failing

## Mitigation
- Restart affected service with graceful shutdown to drain in-flight work
- If Redis unavailable, switch to backup instance and run restore script
- If IMAP throttled, increase poll interval temporarily and monitor

## Recovery Verification
- Health endpoints return `alive` and `ready`
- Stream depth stabilizes or decreases
- Error rate drops to baseline

## Postmortem Notes
- Capture root cause, time to detect, time to resolve
- Update runbooks and alerts if gaps were found
