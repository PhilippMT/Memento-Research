#!/usr/bin/env bash
# Reset OMC backend to a clean state using autoresearch company config.
# Usage: ./scripts/reset.sh          — reset data + restart backend
#        ./scripts/reset.sh --stop   — just stop backend
#        ./scripts/reset.sh --start  — just start backend (no reset)
set -euo pipefail

OMC_DIR="/Users/yuzhengxu/projects/OneManCompany"
AR_DIR="/Users/yuzhengxu/projects/autoresearch"
DATA_DIR="$OMC_DIR/.onemancompany"
ENV_FILE="$AR_DIR/.onemancompany/.env"
PYTHON="$OMC_DIR/.venv/bin/python"
LOG="/tmp/omc-backend.log"
PORT=8000

# ── Helpers ──

stop_backend() {
  local pids
  pids=$(lsof -ti :$PORT 2>/dev/null || true)
  if [ -n "$pids" ]; then
    echo "Stopping backend (PIDs: $pids)..."
    echo "$pids" | xargs kill -9 2>/dev/null || true
    sleep 1
  else
    echo "No backend running on :$PORT"
  fi
}

init_data() {
  echo "Initializing $DATA_DIR from $AR_DIR/company ..."

  # Wipe runtime data but keep the directory
  rm -rf "$DATA_DIR"
  mkdir -p "$DATA_DIR"

  # Copy company config (employees, SOPs, operations)
  cp -r "$AR_DIR/company" "$DATA_DIR/company"

  # Remove stale runtime artifacts from copied data
  rm -rf "$DATA_DIR/company/business/projects"
  rm -rf "$DATA_DIR/company/business/products"
  rm -f  "$DATA_DIR/company/activity_log.yaml"
  rm -f  "$DATA_DIR/company/system_cron_state.yaml"
  mkdir -p "$DATA_DIR/company/business/projects"
  mkdir -p "$DATA_DIR/company/business/products"

  # Copy .env (API keys, server config)
  cp "$ENV_FILE" "$DATA_DIR/.env"

  echo "Data initialized."
}

start_backend() {
  # Verify port is free
  if lsof -ti :$PORT >/dev/null 2>&1; then
    echo "ERROR: Port $PORT already in use"
    exit 1
  fi

  echo "Starting backend..."
  cd "$OMC_DIR"
  nohup "$PYTHON" -c "from onemancompany.main import run; run()" > "$LOG" 2>&1 &
  local pid=$!
  echo "Backend PID: $pid"

  # Wait for startup
  for i in $(seq 1 10); do
    if lsof -ti :$PORT >/dev/null 2>&1; then
      echo "Backend ready at http://localhost:$PORT"
      return 0
    fi
    sleep 1
  done

  echo "ERROR: Backend failed to start. Check $LOG"
  tail -10 "$LOG"
  exit 1
}

# ── Main ──

case "${1:-}" in
  --stop)
    stop_backend
    ;;
  --start)
    start_backend
    ;;
  *)
    stop_backend
    init_data
    start_backend
    ;;
esac
