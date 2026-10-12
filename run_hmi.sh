#!/usr/bin/env bash
# PetroTwin — one-command start/stop for the STANDALONE operator HMI (PC4).
#
#   ./run_hmi.sh start     # standalone HMI (background) + open tab
#   ./run_hmi.sh stop
#   ./run_hmi.sh restart
#   ./run_hmi.sh status
#
# This is the Phase-2.5 (distributed) HMI: a Modbus TCP master that polls the
# two real OpenPLC slaves DIRECTLY (ui/hmi_server.py) — no physics access in
# this process at all, so "spoofable" is structural, not just a label. It
# needs both OpenPLC slaves already up and running the plant programs — i.e.
# run_plant.sh on PC1 (or a manual plc.load_scenario.load_plant) first.
#
# Single-host use: run this alongside run_plant.sh on one machine (defaults
# all point at 127.0.0.1) to see the SAME contrast ui/plant_server.py's
# bundled /hmi tab shows, but from a genuinely separate, physics-blind
# process — a local rehearsal of the 4-PC split.
#
# Distributed use (PC4): `source deploy/hosts.env` first (see deploy/README.md),
# which points PETROTWIN_BPCS_HOST / PETROTWIN_SIS_HOST at PC2 / PC3's real IPs.
set -euo pipefail

cd "$(dirname "$0")"

MODULE="ui.hmi_server"
PORT="${PETROTWIN_HMI_PORT:-8093}"
URL="http://127.0.0.1:${PORT}"
LOG="/tmp/petrotwin-hmi.log"
PY=".venv/bin/python"
[ -x "$PY" ] || PY="python3"

pids() { pgrep -f "$MODULE" 2>/dev/null || true; }

open_url() {
  local url="$1" o
  for o in xdg-open gio open sensible-browser; do
    if command -v "$o" >/dev/null 2>&1; then
      if [ "$o" = "gio" ]; then nohup gio open "$url" >/dev/null 2>&1 &
      else nohup "$o" "$url" >/dev/null 2>&1 & fi
      return
    fi
  done
  echo "   (open $url manually — no browser opener found)"
}

start() {
  local p; p="$(pids)"
  if [ -n "$p" ]; then
    echo "already running (pid $p) -> $URL"; open_url "$URL"; return 0
  fi
  PYTHONPATH="$(pwd)" nohup "$PY" -m "$MODULE" >"$LOG" 2>&1 &
  local i
  for i in $(seq 1 40); do
    curl -fsS -o /dev/null "$URL" 2>/dev/null && break
    sleep 0.5
  done
  p="$(pids)"
  if [ -n "$p" ]; then
    echo "started (pid $p)"
    echo "   standalone HMI (PC4): $URL"
    echo "   (log: $LOG)"
    open_url "$URL"
  else
    echo "failed to start — see $LOG"; tail -n 30 "$LOG" 2>/dev/null || true
    echo "(usually: the PLCs aren't up/loaded yet — run run_plant.sh on PC1 first)"
    return 1
  fi
}

stop() {
  local p; p="$(pids)"
  if [ -z "$p" ]; then echo "not running"; return 0; fi
  # shellcheck disable=SC2086
  kill $p 2>/dev/null || true
  sleep 0.5
  p="$(pids)"
  # shellcheck disable=SC2086
  [ -n "$p" ] && kill -9 $p 2>/dev/null || true
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
