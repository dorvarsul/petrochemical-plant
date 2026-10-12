#!/usr/bin/env bash
# PetroTwin whole-plant view — one-command start/stop.
#
#   ./run_plant.sh start     # launch the connected-plant server (background)
#   ./run_plant.sh stop      # stop it
#   ./run_plant.sh restart   # stop then start
#   ./run_plant.sh status    # is it running?
#
# Serves the whole connected flowsheet (T-101 -> R-301 -> V-201) on
# http://127.0.0.1:8092 using the dependency-free fake-PLC path (no Docker).
set -euo pipefail

cd "$(dirname "$0")"
MODULE="ui.plant_server"
PORT=8092
URL="http://127.0.0.1:${PORT}"
LOG="/tmp/petrotwin-plant.log"
PY=".venv/bin/python"
[ -x "$PY" ] || PY="python3"

pids() { pgrep -f "$MODULE" 2>/dev/null || true; }

start() {
  local p; p="$(pids)"
  if [ -n "$p" ]; then echo "already running (pid $p) -> $URL"; return 0; fi
  PYTHONPATH="$(pwd)" nohup "$PY" -m "$MODULE" >"$LOG" 2>&1 &
  sleep 1.5
  p="$(pids)"
  if [ -n "$p" ]; then echo "started (pid $p) -> $URL   (log: $LOG)"
  else echo "failed to start — see $LOG"; tail -n 20 "$LOG" 2>/dev/null || true; return 1; fi
}

stop() {
  local p; p="$(pids)"
  if [ -z "$p" ]; then echo "not running"; return 0; fi
  # shellcheck disable=SC2086
  kill $p 2>/dev/null || true
  sleep 0.5
  p="$(pids)"; if [ -n "$p" ]; then kill -9 $p 2>/dev/null || true; fi
  echo "stopped"
}

case "${1:-}" in
  start)   start ;;
  stop)    stop ;;
  restart) stop; start ;;
  status)
    p="$(pids)"
    if [ -n "$p" ]; then echo "running (pid $p) -> $URL"; else echo "stopped"; fi ;;
  *) echo "usage: $0 {start|stop|restart|status}"; exit 2 ;;
esac
