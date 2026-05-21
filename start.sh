#!/usr/bin/env bash
# AutoResearch local service manager.
#
# This project does not use OMC's interactive onboarding wizard. Instead,
# it bootstraps .onemancompany/ directly from the checked-in repo assets.
#
# Usage:
#   bash start.sh            # Rebuild runtime data and restart backend
#   bash start.sh start      # Start backend only (auto-bootstrap if needed)
#   bash start.sh stop       # Stop backend only
#   bash start.sh restart    # Rebuild runtime data and restart backend
#   bash start.sh status     # Show whether the backend is listening
#
# Backward-compatible aliases:
#   bash start.sh --start
#   bash start.sh --stop

set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DATA_DIR="$REPO_DIR/.onemancompany"
ROOT_ENV_FILE="$REPO_DIR/.env"
ROOT_ENV_EXAMPLE="$REPO_DIR/.env.example"
PYTHON="$REPO_DIR/.venv/bin/python"
LOG="/tmp/memento-research-backend.log"

info()  { printf '\033[1;36m▸ %s\033[0m\n' "$*"; }
warn()  { printf '\033[1;33m⚠ %s\033[0m\n' "$*"; }
error() { printf '\033[1;31m✖ %s\033[0m\n' "$*" >&2; exit 1; }

print_help() {
  cat <<'EOF'
Usage: bash start.sh [start|stop|restart|status|--start|--stop|--help]

Commands:
  (default)  Rebuild runtime data from this repo and restart backend
  start      Start backend only, bootstrapping .onemancompany/ if needed
  stop       Stop backend only
  restart    Rebuild runtime data from this repo and restart backend
  status     Show whether the backend is listening
  --start    Alias for start
  --stop     Alias for stop
  --help     Show this help text

Notes:
  - This repo does not use the interactive OMC onboarding wizard.
  - Runtime data is bootstrapped from ./company and ./.env.
EOF
}

ensure_uv() {
  if command -v uv >/dev/null 2>&1; then
    return
  fi
  info "Installing UV..."
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
  command -v uv >/dev/null 2>&1 || error "UV installed but not in PATH. Restart your terminal and try again."
}

ensure_venv() {
  ensure_uv
  cd "$REPO_DIR"

  if [ ! -x "$PYTHON" ]; then
    info "Creating Python virtual environment..."
    uv venv --python 3.12
  fi

  info "Installing dependencies..."
  # Pin install target to the project venv. Without --python, uv falls back to
  # an active CONDA_PREFIX when VIRTUAL_ENV is unset, installing the package
  # outside .venv and breaking the subsequent `$PYTHON -c "from onemancompany..."`.
  VIRTUAL_ENV="$REPO_DIR/.venv" uv pip install -e . -q --python "$PYTHON"
}

resolve_env_source() {
  if [ -f "$ROOT_ENV_FILE" ]; then
    printf '%s\n' "$ROOT_ENV_FILE"
    return
  fi
  if [ -f "$DATA_DIR/.env" ]; then
    printf '%s\n' "$DATA_DIR/.env"
    return
  fi
  if [ -f "$ROOT_ENV_EXAMPLE" ]; then
    error "No $ROOT_ENV_FILE found. Create it from .env.example before starting."
  fi
  error "No .env configuration found for this repo."
}

resolve_port() {
  local env_file port
  env_file="$(resolve_env_source)"
  port="$(awk -F= '/^[[:space:]]*PORT=/{gsub(/[[:space:]]/, "", $2); print $2; exit}' "$env_file" 2>/dev/null || true)"
  if [ -n "${PORT:-}" ]; then
    printf '%s\n' "$PORT"
  elif [ -n "$port" ]; then
    printf '%s\n' "$port"
  else
    printf '8000\n'
  fi
}

listener_pids() {
  local port
  port="$(resolve_port)"
  lsof -tiTCP:"$port" -sTCP:LISTEN 2>/dev/null || true
}

stop_backend() {
  local port pids
  port="$(resolve_port)"
  pids="$(listener_pids)"
  if [ -z "$pids" ]; then
    info "No backend running on :$port"
    return 0
  fi

  info "Stopping backend on :$port (PIDs: $pids)..."
  echo "$pids" | xargs kill -TERM 2>/dev/null || true

  for _ in $(seq 1 20); do
    if [ -z "$(listener_pids)" ]; then
      info "Backend stopped"
      return 0
    fi
    sleep 0.5
  done

  warn "Backend still running after SIGTERM, forcing shutdown"
  echo "$pids" | xargs kill -9 2>/dev/null || true
  sleep 1
}

status_backend() {
  local port pids
  port="$(resolve_port)"
  pids="$(listener_pids)"
  if [ -z "$pids" ]; then
    info "Backend is not running on :$port"
    return 1
  fi

  info "Backend is listening on :$port"
  lsof -nP -iTCP:"$port" -sTCP:LISTEN
}

init_data() {
  local env_source
  env_source="$(resolve_env_source)"

  info "Initializing $DATA_DIR from $REPO_DIR/company ..."
  rm -rf "$DATA_DIR"
  mkdir -p "$DATA_DIR"

  cp -r "$REPO_DIR/company" "$DATA_DIR/company"

  rm -rf "$DATA_DIR/company/business/projects"
  rm -rf "$DATA_DIR/company/business/products"
  rm -f "$DATA_DIR/company/activity_log.yaml"
  rm -f "$DATA_DIR/company/system_cron_state.yaml"
  mkdir -p "$DATA_DIR/company/business/projects"
  mkdir -p "$DATA_DIR/company/business/products"

  cp "$env_source" "$DATA_DIR/.env"

  info "Runtime data initialized."
}

start_backend() {
  local port pid
  port="$(resolve_port)"

  ensure_venv

  if [ ! -d "$DATA_DIR/company/human_resource/employees" ] || [ ! -f "$DATA_DIR/.env" ]; then
    warn ".onemancompany/ missing or incomplete — bootstrapping from repo."
    init_data
  fi

  if [ -n "$(listener_pids)" ]; then
    error "Port $port is already in use. Run 'bash start.sh restart' or 'bash start.sh stop'."
  fi

  info "Starting backend..."
  cd "$REPO_DIR"
  nohup "$PYTHON" -c "from onemancompany.main import run; run()" > "$LOG" 2>&1 &
  pid=$!
  info "Backend PID: $pid"

  for _ in $(seq 1 15); do
    if [ -n "$(listener_pids)" ]; then
      info "Backend ready at http://localhost:$port"
      return 0
    fi
    sleep 1
  done

  warn "Backend did not become ready in time. Last log lines:"
  tail -10 "$LOG" || true
  exit 1
}

hire_from_list() {
  local hire_file count port
  hire_file="$REPO_DIR/company/hire_list.json"
  port="$(resolve_port)"

  if [ ! -f "$hire_file" ]; then
    info "No hire_list.json found, skipping auto-hire."
    return
  fi

  count="$("$PYTHON" -c "import json; print(len(json.load(open('$hire_file'))))")"
  if [ "$count" -eq 0 ]; then
    info "hire_list.json is empty, skipping auto-hire."
    return
  fi

  info "Auto-hiring $count employee(s) from hire_list.json ..."
  "$PYTHON" -c "
import json, urllib.request

with open('$hire_file', encoding='utf-8') as f:
    hires = json.load(f)

for cv in hires:
    body = json.dumps({'cv': cv}).encode()
    req = urllib.request.Request(
        'http://localhost:$port/api/candidates/hire-from-cv',
        data=body,
        headers={'Content-Type': 'application/json'},
    )
    try:
        resp = urllib.request.urlopen(req, timeout=30)
        result = json.loads(resp.read())
        print(f'  ✓ {cv[\"name\"]}: {result.get(\"message\", result)}')
    except Exception as e:
        print(f'  ✗ {cv[\"name\"]}: {e}')
"
}

COMMAND="${1:-restart}"

case "$COMMAND" in
  --help|-h)
    print_help
    ;;
  stop|--stop)
    stop_backend
    ;;
  start|--start)
    start_backend
    ;;
  status)
    status_backend
    ;;
  restart|"")
    stop_backend
    init_data
    start_backend
    hire_from_list
    ;;
  *)
    error "Unknown command: $COMMAND. Run 'bash start.sh --help' for usage."
    ;;
esac
