#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

if [ -f "$PROJECT_DIR/.env" ]; then
  set -a
  # shellcheck disable=SC1091
  source "$PROJECT_DIR/.env"
  set +a
fi

PYTHON="${PYTHON:-$PROJECT_DIR/.venv/bin/python}"
LOG_DIR="${LOG_DIR:-$PROJECT_DIR/logs}"
LOCK_FILE="${LOCK_FILE:-$LOG_DIR/generar_recurrentes.lock}"

mkdir -p "$LOG_DIR"
cd "$PROJECT_DIR"

flock -n "$LOCK_FILE" "$PYTHON" manage.py generar_recurrentes >> "$LOG_DIR/recurrentes.log" 2>&1
