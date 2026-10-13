#!/usr/bin/env bash
# PetroTwin — shared helpers for the per-PC deployment scripts
# (pc1.sh / pc2.sh / pc3.sh / pc4.sh and attacker/attack.sh).
#
# This file is SOURCED, never run directly. Each per-PC script `cd`s to the
# repo root (or its own dir, for the PLC PCs), sets a few role globals, then
# sources this and calls the matching helper. Keeping the logic here means the
# four scripts stay short and readable instead of copy-pasting ~150 lines.
#
# Conventions used by the helpers below:
#   REPO          absolute path to the repo root (set by the caller)
#   KIT / PETROTWIN_KIT   optional path to the offline USB kit (petrotwin-kit)
#   PETROTWIN_SKIP_UFW=1  skip the automatic firewall rules
#
# No `set` here — each executable script sets `set -uo pipefail` itself.

IMAGE="petrotwin/openplc:v3"

# ---------------------------------------------------------------------------
# Offline kit discovery
# ---------------------------------------------------------------------------
# Honour $KIT / $PETROTWIN_KIT first, then probe the usual USB/sibling spots
# (the kit is normally a sibling of the repo, e.g. ~/petrotwin + ~/petrotwin-kit,
# or /media/<user>/<stick>/petrotwin-kit). Prints one candidate root per line.
_kit_roots() {
  local k="${KIT:-${PETROTWIN_KIT:-}}"
  [ -n "$k" ] && echo "$k"
  echo "${REPO:-.}/../petrotwin-kit"
  local d
  for d in /media/*/petrotwin-kit /media/*/*/petrotwin-kit /mnt/*/petrotwin-kit; do
    [ -e "$d" ] && echo "$d"
  done
}

find_kit_file() {   # $1 = filename inside the kit, e.g. openplc-v3.tar
  local root
  while read -r root; do
    [ -n "$root" ] && [ -e "$root/$1" ] && { echo "$root/$1"; return 0; }
  done < <(_kit_roots)
  return 1
}

find_wheelhouse() {
  local root
  while read -r root; do
    [ -n "$root" ] && [ -d "$root/wheelhouse" ] && { echo "$root/wheelhouse"; return 0; }
  done < <(_kit_roots)
  return 1
}

# ---------------------------------------------------------------------------
# Docker / OpenPLC — used by PC2 (BPCS) and PC3 (SIS)
# Role globals expected: CONTAINER, MODBUS_PORT, WEB_PORT, LABEL, COMPOSE_FILE
# ---------------------------------------------------------------------------
need_docker() {
  command -v docker >/dev/null 2>&1 || {
    echo "!! docker not found — install Docker Engine first (docs/DEPLOY-OFFLINE.md §A4)" >&2; exit 1; }
  docker info >/dev/null 2>&1 || {
    echo "!! docker daemon not reachable — is it running, and are you in the 'docker' group?" >&2; exit 1; }
}

_cstate() { docker inspect -f '{{.State.Status}}' "$1" 2>/dev/null || echo absent; }

host_docker_arch() {
  case "$(uname -m)" in
    x86_64)  echo amd64 ;;
    aarch64) echo arm64 ;;
    armv7l)  echo arm ;;
    *)       uname -m ;;
  esac
}

# Make sure $IMAGE is present: load it from the offline kit if we can, else
# build it once from the pinned OpenPLC_v3 commit (read straight out of the
# sibling docker-compose.yml so the pin can never drift from the compose file).
ensure_image() {
  docker image inspect "$IMAGE" >/dev/null 2>&1 && return 0
  local tar
  if tar="$(find_kit_file openplc-v3.tar)"; then
    echo ">> offline kit found — loading image from $tar"
    docker load -i "$tar" || return 1
    return 0
  fi
  local pin
  pin="$(grep -ohE 'OpenPLC_v3\.git#[0-9a-fA-F]{7,40}' "${COMPOSE_FILE:-}" 2>/dev/null | head -1 | sed 's/.*#//')"
  [ -n "$pin" ] || {
    echo "!! no kit image at \$KIT/openplc-v3.tar and no build pin in ${COMPOSE_FILE:-<unset>}" >&2
    echo "   set KIT=/path/to/petrotwin-kit (offline), or run this online to build once." >&2
    return 1; }
  echo ">> no offline kit — building $IMAGE from pinned OpenPLC_v3 @ ${pin:0:12} (one-time, needs internet) ..."
  docker build -t "$IMAGE" "https://github.com/thiagoralves/OpenPLC_v3.git#${pin}" || return 1
}

check_arch() {
  local want have
  want="$(host_docker_arch)"
  have="$(docker image inspect "$IMAGE" --format '{{.Architecture}}' 2>/dev/null || true)"
  if [ -n "$have" ] && [ "$have" != "$want" ]; then
    echo "!! WARNING: image arch '$have' != this host '$want' — the container may not run." >&2
    echo "   the kit image is built for one arch; rebuild it on a '$want' box (docs/DEPLOY-OFFLINE.md §A2)." >&2
  fi
  return 0
}

open_firewall() {   # uses $MODBUS_PORT, $WEB_PORT; skip with PETROTWIN_SKIP_UFW=1
  [ "${PETROTWIN_SKIP_UFW:-0}" = "1" ] && return 0
  command -v ufw >/dev/null 2>&1 || return 0
  sudo ufw status 2>/dev/null | grep -qi 'Status: active' || return 0
  echo ">> ufw active — allowing ${MODBUS_PORT}/tcp (Modbus) and ${WEB_PORT}/tcp (web UI)"
  sudo ufw allow "${MODBUS_PORT}/tcp" >/dev/null 2>&1 || echo "   (manual: sudo ufw allow ${MODBUS_PORT}/tcp)"
  sudo ufw allow "${WEB_PORT}/tcp"    >/dev/null 2>&1 || echo "   (manual: sudo ufw allow ${WEB_PORT}/tcp)"
}

_tcp_open() { (exec 3<>"/dev/tcp/127.0.0.1/$1") 2>/dev/null && exec 3>&- 3<&-; }

openplc_status() {
  local st; st="$(_cstate "$CONTAINER")"
  if [ "$st" = running ]; then
    echo "$LABEL: running ($CONTAINER)"
    echo "   web UI : http://<this PC's IP>:${WEB_PORT}/   (login openplc/openplc)"
    echo "   Modbus : <this PC's IP>:${MODBUS_PORT}"
    if _tcp_open "$MODBUS_PORT"; then echo "   local Modbus port ${MODBUS_PORT}: answering"
    else echo "   local Modbus port ${MODBUS_PORT}: NOT answering yet"; fi
  else
    echo "$LABEL: $st ($CONTAINER)"
  fi
}

openplc_start() {
  need_docker
  if [ "$(_cstate "$CONTAINER")" = running ]; then
    echo "$LABEL already running."; openplc_status; return 0
  fi
  ensure_image || exit 1
  check_arch
  if [ "$(_cstate "$CONTAINER")" != absent ]; then
    echo ">> starting existing container $CONTAINER"
    docker start "$CONTAINER" >/dev/null || exit 1
  else
    echo ">> docker run $CONTAINER ($IMAGE): Modbus ${MODBUS_PORT}->502, web ${WEB_PORT}->8080"
    docker run -d --name "$CONTAINER" --restart unless-stopped \
      -p "${MODBUS_PORT}:502" -p "${WEB_PORT}:8080" "$IMAGE" >/dev/null || exit 1
  fi
  open_firewall
  local i; for i in $(seq 1 30); do _tcp_open "$MODBUS_PORT" && break; sleep 0.5; done
  openplc_status
  echo "   (the PLC programs are pushed here by PC1 when you run ./pc1.sh start)"
}

openplc_stop() {
  need_docker
  if [ "$(_cstate "$CONTAINER")" = running ]; then
    docker stop "$CONTAINER" >/dev/null && echo "$LABEL stopped (container kept; 'start' resumes it)."
  else
    echo "$LABEL not running."
  fi
}

openplc_recreate() {
  need_docker
  docker rm -f "$CONTAINER" >/dev/null 2>&1 || true
  openplc_start
}

openplc_logs() { need_docker; docker logs -f "$CONTAINER"; }

openplc_dispatch() {
  case "${1:-}" in
    start)    openplc_start ;;
    stop)     openplc_stop ;;
    restart)  openplc_stop; openplc_start ;;
    recreate) openplc_recreate ;;
    status)   need_docker; openplc_status ;;
    logs)     openplc_logs ;;
    *) echo "usage: $(basename "$0") {start|stop|restart|recreate|status|logs}"; exit 2 ;;
  esac
}

# ---------------------------------------------------------------------------
# Python layers — used by PC1 (physics+3D), PC4 (HMI) and the attacker.
# Caller must `cd "$REPO"` first (requirements.txt / .venv live at the root).
# ---------------------------------------------------------------------------
ensure_venv() {
  local py=".venv/bin/python"
  if [ -x "$py" ] && "$py" -c 'import pymodbus, serial' >/dev/null 2>&1; then
    return 0
  fi
  echo ">> setting up Python venv + deps"
  if [ ! -x "$py" ]; then
    python3 -m venv .venv || {
      echo "!! python3 -m venv failed — install python3-venv (docs/DEPLOY-OFFLINE.md §A4)" >&2; exit 1; }
  fi
  local wh
  if wh="$(find_wheelhouse)"; then
    echo "   offline: pip install --no-index --find-links $wh"
    .venv/bin/pip install --no-index --find-links "$wh" -r requirements.txt || exit 1
  else
    echo "   online: pip install -r requirements.txt"
    .venv/bin/pip install -r requirements.txt || exit 1
  fi
  .venv/bin/python -c 'import pymodbus, serial; print("   deps ok:", pymodbus.__version__)'
}

source_hosts() {
  if [ -f deploy/hosts.env ]; then
    echo ">> sourcing deploy/hosts.env"
    # shellcheck disable=SC1091
    source deploy/hosts.env
  else
    echo ">> deploy/hosts.env not found — using single-host defaults (127.0.0.1)."
    echo "   distributed 4-PC run? cp deploy/hosts.env.example deploy/hosts.env and set PC2/PC3 IPs."
  fi
}
