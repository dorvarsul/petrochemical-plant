#!/usr/bin/env bash
# PetroTwin — one-command start/stop for the connected whole-plant HMI.
#
#   ./run_plant.sh start     # OpenPLC up + whole-plant HMI (background) + open tab
#   ./run_plant.sh stop      # stop the HMI (leaves the OpenPLC containers up)
#   ./run_plant.sh restart   # stop then start
#   ./run_plant.sh status    # is it running?
#
# Serves the whole connected flowsheet (T-101 -> R-301 -> V-201) on
# http://127.0.0.1:8092, driven by the REAL OpenPLC pair over Modbus: one BPCS
# slave carries control for all three units and one SIS slave carries safety.
# `start` brings the two OpenPLC containers up first; the server self-loads the
# multi-unit PLC programs. If Docker is unavailable it falls back to the
# dependency-free fake-PLC path so the view still runs.
#
# Phase 2.5 (distributed, 4-PC): on PC1, `source deploy/hosts.env` first so
# PETROTWIN_BPCS_HOST/PETROTWIN_SIS_HOST point at PC2/PC3's real IPs — this
# script then probes THOSE hosts over the network instead of looking for a
# local Docker container, so it needs no other change to run on PC1. See
# deploy/README.md / deploy/checklist.md.
set -euo pipefail

cd "$(dirname "$0")"

MODULE="ui.plant_server"
PORT="${PETROTWIN_PLANT_PORT:-8092}"
URL="http://127.0.0.1:${PORT}"
LOG="/tmp/petrotwin-plant.log"
PY=".venv/bin/python"
[ -x "$PY" ] || PY="python3"

COMPOSE="plc/docker-compose.yml"
CONTAINERS=(petrotwin-bpcs petrotwin-sis)
BPCS_HOST="${PETROTWIN_BPCS_HOST:-127.0.0.1}"
BPCS_MODBUS_PORT="${PETROTWIN_BPCS_MODBUS_PORT:-5020}"
SIS_HOST="${PETROTWIN_SIS_HOST:-127.0.0.1}"
SIS_MODBUS_PORT="${PETROTWIN_SIS_MODBUS_PORT:-5021}"

# --- can we already reach a Modbus TCP slave at host:port? ---------------
_tcp_open() { (exec 3<>"/dev/tcp/$1/$2") 2>/dev/null && exec 3>&- 3<&-; }

pids() { pgrep -f "$MODULE" 2>/dev/null || true; }

# --- open a URL in the default browser, in its own tab, silently ---------
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

# --- ensure both OpenPLC slaves are reachable; echo "ok" on success -------
# First tries the network (works for a remote PC2/PC3 in the distributed
# layout AND for already-running local containers); only falls back to
# bringing up LOCAL containers via docker-compose when both hosts are still
# at their localhost default (i.e. this is the single-host case).
ensure_openplc() {
  if _tcp_open "$BPCS_HOST" "$BPCS_MODBUS_PORT" && _tcp_open "$SIS_HOST" "$SIS_MODBUS_PORT"; then
    echo ok; return
  fi
  if [ "$BPCS_HOST" != "127.0.0.1" ] || [ "$SIS_HOST" != "127.0.0.1" ]; then
    echo >&2 "   OpenPLC: configured for $BPCS_HOST:$BPCS_MODBUS_PORT / $SIS_HOST:$SIS_MODBUS_PORT" \
              "but unreachable — bring up PC2/PC3 first (see deploy/checklist.md); running on the fake-PLC path"
    return
  fi
  if ! command -v docker >/dev/null 2>&1; then
    echo >&2 "   OpenPLC: docker not found — running on the fake-PLC path"
    return
  fi
  echo >&2 "   OpenPLC: bringing local containers up ..."
  if ! docker compose -f "$COMPOSE" up -d >/dev/null 2>&1; then
    echo >&2 "   OpenPLC: 'docker compose up -d' failed — running on the fake-PLC path"
    return
  fi
  local i
  for i in $(seq 1 20); do  # containers just started: give the ports a moment
    _tcp_open "$BPCS_HOST" "$BPCS_MODBUS_PORT" && _tcp_open "$SIS_HOST" "$SIS_MODBUS_PORT" && { echo ok; return; }
    sleep 0.5
  done
  echo >&2 "   OpenPLC: containers up but Modbus ports never answered — running on the fake-PLC path"
}

start() {
  local p; p="$(pids)"
  if [ -n "$p" ]; then
    echo "already running (pid $p) -> $URL  (HMI: $URL/hmi)"
    open_url "$URL"; open_url "$URL/hmi"; return 0
  fi
  local live_ok mode; live_ok="$(ensure_openplc)"
  if [ "$live_ok" = "ok" ]; then export PETROTWIN_LIVE=1; mode="LIVE OpenPLC"
  else unset PETROTWIN_LIVE; mode="fake PLCs"; fi
  PYTHONPATH="$(pwd)" nohup "$PY" -m "$MODULE" >"$LOG" 2>&1 &
  # wait up to ~40s for the port to answer (live mode compiles+loads the PLC
  # programs on startup, which takes a few seconds per slave)
  local i
  for i in $(seq 1 80); do
    curl -fsS -o /dev/null "$URL" 2>/dev/null && break
    sleep 0.5
  done
  p="$(pids)"
  if [ -n "$p" ]; then
    echo "started (pid $p, $mode)"
    echo "   3D ground truth : $URL"
    echo "   operator HMI     : $URL/hmi"
    echo "   (log: $LOG)"
    open_url "$URL"          # 3D ground-truth tab
    open_url "$URL/hmi"      # operator HMI tab
  else
    echo "failed to start — see $LOG"; tail -n 20 "$LOG" 2>/dev/null || true; return 1
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
