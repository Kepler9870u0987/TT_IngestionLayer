#!/usr/bin/env bash
# ---------------------------------------------------------------
# health_check.sh – Quick health-check against the running system
# ---------------------------------------------------------------
set -euo pipefail

HOST="${1:-localhost}"
HEALTH_PORT="${2:-8080}"
METRICS_PORT="${3:-9090}"

log() { echo "[$(date -u '+%Y-%m-%dT%H:%M:%SZ')] $*"; }
ok()  { log "✓ $*"; }
fail(){ log "✗ $*"; }

EXIT_CODE=0

# ---- /health (liveness) ----
if curl -sf "http://${HOST}:${HEALTH_PORT}/health" -o /dev/null 2>/dev/null; then
    ok "Liveness: http://${HOST}:${HEALTH_PORT}/health"
else
    fail "Liveness: http://${HOST}:${HEALTH_PORT}/health"
    EXIT_CODE=1
fi

# ---- /ready (readiness) ----
HTTP_CODE=$(curl -s -o /dev/null -w '%{http_code}' "http://${HOST}:${HEALTH_PORT}/ready" 2>/dev/null || echo "000")
if [ "$HTTP_CODE" = "200" ]; then
    ok "Readiness: http://${HOST}:${HEALTH_PORT}/ready  (HTTP $HTTP_CODE)"
else
    fail "Readiness: http://${HOST}:${HEALTH_PORT}/ready  (HTTP $HTTP_CODE)"
    EXIT_CODE=1
fi

# ---- /status (full status) ----
if curl -sf "http://${HOST}:${HEALTH_PORT}/status" -o /dev/null 2>/dev/null; then
    ok "Status: http://${HOST}:${HEALTH_PORT}/status"
    echo "---"
    curl -s "http://${HOST}:${HEALTH_PORT}/status" | python3 -m json.tool 2>/dev/null || \
        curl -s "http://${HOST}:${HEALTH_PORT}/status"
    echo "---"
else
    fail "Status: http://${HOST}:${HEALTH_PORT}/status"
    EXIT_CODE=1
fi

# ---- /metrics (Prometheus) ----
if curl -sf "http://${HOST}:${METRICS_PORT}/metrics" -o /dev/null 2>/dev/null; then
    ok "Metrics: http://${HOST}:${METRICS_PORT}/metrics"
else
    fail "Metrics: http://${HOST}:${METRICS_PORT}/metrics  (may not be running)"
    # Don't fail exit code – metrics server is optional in dev
fi

# ---- Redis connectivity ----
if command -v redis-cli &>/dev/null; then
    if redis-cli -h "${REDIS_HOST:-localhost}" -p "${REDIS_PORT:-6379}" ping &>/dev/null; then
        ok "Redis: ${REDIS_HOST:-localhost}:${REDIS_PORT:-6379}"
        STREAM_LEN=$(redis-cli -h "${REDIS_HOST:-localhost}" -p "${REDIS_PORT:-6379}" XLEN email_ingestion_stream 2>/dev/null || echo "?")
        DLQ_LEN=$(redis-cli -h "${REDIS_HOST:-localhost}" -p "${REDIS_PORT:-6379}" XLEN email_ingestion_dlq 2>/dev/null || echo "?")
        log "  Stream depth: ${STREAM_LEN}  |  DLQ depth: ${DLQ_LEN}"
    else
        fail "Redis: ${REDIS_HOST:-localhost}:${REDIS_PORT:-6379}"
        EXIT_CODE=1
    fi
else
    log "  (redis-cli not on PATH – skipping Redis check)"
fi

exit $EXIT_CODE
