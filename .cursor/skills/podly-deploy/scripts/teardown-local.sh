#!/usr/bin/env bash
# Stop local Podly writer (50001), Flask (5001), and Vite (5174).
set -euo pipefail

kill_listeners() {
  local port="$1"
  local pids
  pids="$(lsof -nP -iTCP:"${port}" -sTCP:LISTEN -t 2>/dev/null || true)"
  if [[ -z "${pids}" ]]; then
    echo "port ${port}: already free"
    return
  fi
  echo "port ${port}: stopping PIDs ${pids}"
  # shellcheck disable=SC2086
  kill ${pids} 2>/dev/null || true
  sleep 1
  pids="$(lsof -nP -iTCP:"${port}" -sTCP:LISTEN -t 2>/dev/null || true)"
  if [[ -n "${pids}" ]]; then
    echo "port ${port}: force-stopping PIDs ${pids}"
    # shellcheck disable=SC2086
    kill -9 ${pids} 2>/dev/null || true
  fi
}

kill_listeners 50001
kill_listeners 5001
kill_listeners 5174

# uv/python children sometimes linger without holding the port
pkill -f 'python -m app.writer' 2>/dev/null || true
pkill -f 'python src/main.py' 2>/dev/null || true

echo "local Podly stack down"
lsof -nP -iTCP:50001,5001,5174 -sTCP:LISTEN 2>/dev/null || echo "no listeners on 50001/5001/5174"
