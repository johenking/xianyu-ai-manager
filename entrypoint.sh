#!/usr/bin/env bash
set -euo pipefail

mkdir -p /app/data /app/logs /app/backups /app/static/uploads/images

export API_HOST="${API_HOST:-0.0.0.0}"
export API_PORT="${PORT:-${API_PORT:-8080}}"

exec python Start.py
