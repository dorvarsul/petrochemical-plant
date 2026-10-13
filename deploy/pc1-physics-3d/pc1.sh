#!/usr/bin/env bash
# PetroTwin — PC1: physics + 3D ground truth + Modbus bridge. One command:
#
#   ./pc1.sh start      # ensure venv/deps, source hosts.env, launch the LIVE whole-plant server
#   ./pc1.sh stop
#   ./pc1.sh restart
#   ./pc1.sh status
#   ./pc1.sh logs       # follow the server log
#   ./pc1.sh setup      # just build the venv + install deps, don't start
#
# Bring up PC2 (./pc2.sh start) and PC3 (./pc3.sh start) FIRST: this serves the
# 3D ground truth (http://<PC1-IP>:8092/, unspoofable) driven by the real
# OpenPLC pair, and pushes the PLC programs to them on startup.
#
# Offline: set KIT=/path/to/petrotwin-kit (or PETROTWIN_KIT) to install deps
# from the USB wheelhouse instead of PyPI. Reads PC2/PC3 IPs from
# deploy/hosts.env (cp deploy/hosts.env.example deploy/hosts.env first; without
# it, defaults to single-host 127.0.0.1).
set -uo pipefail
cd "$(dirname "$0")"                       # deploy/pc1-physics-3d
REPO="$(cd ../.. && pwd)"
cd "$REPO"
source deploy/common.lib.sh

case "${1:-}" in
  setup)
    ensure_venv ;;
  start)
    ensure_venv; source_hosts
    echo ">> launching physics + 3D (LIVE) via run_plant.sh"
    exec ./run_plant.sh start ;;
  stop)
    exec ./run_plant.sh stop ;;
  restart)
    ensure_venv; source_hosts
    ./run_plant.sh stop || true
    exec ./run_plant.sh start ;;
  status)
    exec ./run_plant.sh status ;;
  logs)
    exec tail -n 60 -f /tmp/petrotwin-plant.log ;;
  *)
    echo "usage: $(basename "$0") {start|stop|restart|status|logs|setup}"; exit 2 ;;
esac
