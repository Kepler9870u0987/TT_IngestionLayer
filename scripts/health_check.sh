#!/usr/bin/env bash
set -euo pipefail

PORT="${1:-8080}"

if command -v curl >/dev/null 2>&1; then
  curl -sSf "http://localhost:${PORT}/health" | sed 's/$/\n/'
else
  echo "curl is required for this script" >&2
  exit 1
fi
