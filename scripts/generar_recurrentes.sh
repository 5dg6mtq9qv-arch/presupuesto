#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="/home/cristian/Dev/TaskBudget"
PYTHON="$PROJECT_DIR/.venv/bin/python"
LOG_DIR="$PROJECT_DIR/logs"

export POSTGRES_PASSWORD="${POSTGRES_PASSWORD:-postgres}"

mkdir -p "$LOG_DIR"
cd "$PROJECT_DIR"

"$PYTHON" manage.py generar_recurrentes >> "$LOG_DIR/recurrentes.log" 2>&1
