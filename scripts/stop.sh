#!/usr/bin/env bash
# ---------------------------------------------------------------
# stop.sh – Gracefully stop the Email Ingestion producer & worker
# ---------------------------------------------------------------
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
PID_DIR="${PROJECT_DIR}/pids"
TIMEOUT=${1:-30}   # seconds to wait before SIGKILL

log() { echo "[$(date -u '+%Y-%m-%dT%H:%M:%SZ')] $*"; }

stop_process() {
    local name=$1
    local pid_file="${PID_DIR}/${name}.pid"

    if [ ! -f "$pid_file" ]; then
        log "${name}: no PID file found"
        return
    fi

    local pid
    pid=$(cat "$pid_file")

    if ! kill -0 "$pid" 2>/dev/null; then
        log "${name}: process $pid not running (stale PID file)"
        rm -f "$pid_file"
        return
    fi

    log "${name}: sending SIGTERM to PID $pid…"
    kill -TERM "$pid" 2>/dev/null || true

    local waited=0
    while kill -0 "$pid" 2>/dev/null && [ "$waited" -lt "$TIMEOUT" ]; do
        sleep 1
        waited=$((waited + 1))
    done

    if kill -0 "$pid" 2>/dev/null; then
        log "${name}: still running after ${TIMEOUT}s – sending SIGKILL"
        kill -KILL "$pid" 2>/dev/null || true
    else
        log "${name}: stopped gracefully (${waited}s)"
    fi

    rm -f "$pid_file"
}

stop_process "producer"
stop_process "worker"

log "All processes stopped."
