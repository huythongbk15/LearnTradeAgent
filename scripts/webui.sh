#!/usr/bin/env bash
# Trading Agent System — Web UI server manager (FastAPI backend)
# Usage: bash scripts/webui.sh {start|stop|restart|status|logs}
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LOG="$ROOT/.webui/server.log"
PIDFILE="$ROOT/.webui/server.pid"
mkdir -p "$ROOT/.webui"

start() {
  if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
    echo "already running (pid $(cat "$PIDFILE"))"; return 0
  fi
  cd "$ROOT"
  if [ -f .env ]; then
    set -a; . ./.env; set +a
  fi
  # Spawn qua python Popen(close_fds=True, start_new_session=True) —
  # uvicorn KHÔNG kế thừa fd của caller → lệnh gọi không bao giờ treo.
  local newpid
  newpid=$(python3 - "$LOG" <<'PY'
import subprocess, sys, os
log_path = sys.argv[1]
log = open(log_path, "ab")
devnull = open(os.devnull, "rb")
p = subprocess.Popen(
    [sys.executable, "-m", "uvicorn", "webui.backend.app:app",
     "--host", "127.0.0.1", "--port", "8000"],
    stdout=log, stderr=log, stdin=devnull,
    start_new_session=True, close_fds=True,
)
print(p.pid)
PY
)
  echo "$newpid" > "$PIDFILE"
  echo "started pid $newpid — log: $LOG"
}

stop() {
  local target="${1:-}"
  if [ -n "$target" ] && kill -0 "$target" 2>/dev/null; then
    kill "$target"; echo "stopped pid $target"; rm -f "$PIDFILE"; return 0
  fi
  if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
    kill "$(cat "$PIDFILE")"; rm -f "$PIDFILE"; echo "stopped"
  else
    echo "not running via pidfile"; rm -f "$PIDFILE"
  fi
}

status() {
  if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
    echo "running (pid $(cat "$PIDFILE"))"
    curl -s --max-time 5 http://127.0.0.1:8000/health || echo "health: no response"
  else
    echo "stopped"
  fi
}

case "${1:-}" in
  start) start ;;
  stop) stop "${2:-}" ;;
  restart) stop "${2:-}"; sleep 1; start ;;
  status) status ;;
  logs) tail -50 "$LOG" 2>/dev/null || echo "no log yet" ;;
  *) echo "usage: $0 {start|stop|restart|status|logs}"; exit 1 ;;
esac