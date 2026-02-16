#!/usr/bin/env bash
# ---------------------------------------------------------------
# start.sh – Start the Email Ingestion producer and worker
# ---------------------------------------------------------------
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
PID_DIR="${PROJECT_DIR}/pids"

mkdir -p "$PID_DIR"

# ---------- helpers ----------
log()  { echo "[$(date -u '+%Y-%m-%dT%H:%M:%SZ')] $*"; }
die()  { log "ERROR: $*"; exit 1; }

check_redis() {
    if command -v redis-cli &>/dev/null; then
        redis-cli -h "${REDIS_HOST:-localhost}" -p "${REDIS_PORT:-6379}" ping &>/dev/null \
            && return 0
    fi
    return 1
}

check_python() {
    command -v python3 &>/dev/null || command -v python &>/dev/null \
        || die "Python not found.  Activate your virtual environment."
    PYTHON=$(command -v python3 || command -v python)
}

# ---------- pre-flight ----------
check_python
log "Using Python: $PYTHON"

if [ -f "${PROJECT_DIR}/venv/bin/activate" ]; then
    # shellcheck disable=SC1091
    source "${PROJECT_DIR}/venv/bin/activate"
    log "Activated virtualenv"
fi

if ! check_redis; then
    log "WARNING: Redis not reachable at ${REDIS_HOST:-localhost}:${REDIS_PORT:-6379}"
    log "  Processes will start but may fail to connect."
fi

# ---------- start producer ----------
if [ -f "${PID_DIR}/producer.pid" ] && kill -0 "$(cat "${PID_DIR}/producer.pid")" 2>/dev/null; then
    log "Producer already running (PID $(cat "${PID_DIR}/producer.pid"))"
else
    log "Starting producer…"
    cd "$PROJECT_DIR"
    nohup "$PYTHON" producer.py "$@" > "${PROJECT_DIR}/logs/producer.log" 2>&1 &
    echo $! > "${PID_DIR}/producer.pid"
    log "Producer started (PID $!)"
fi

# ---------- start worker ----------
if [ -f "${PID_DIR}/worker.pid" ] && kill -0 "$(cat "${PID_DIR}/worker.pid")" 2>/dev/null; then
    log "Worker already running (PID $(cat "${PID_DIR}/worker.pid"))"
else
    log "Starting worker…"
    cd "$PROJECT_DIR"
    mkdir -p "${PROJECT_DIR}/logs"
    nohup "$PYTHON" worker.py > "${PROJECT_DIR}/logs/worker.log" 2>&1 &
    echo $! > "${PID_DIR}/worker.pid"
    log "Worker started (PID $!)"
fi

log "Done.  Use scripts/stop.sh to stop."
