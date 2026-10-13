#!/usr/bin/env bash
# PetroTwin — PC4: standalone operator HMI (Modbus-only, physics-blind). One
# command:
#
#   ./pc4.sh start      # ensure venv/deps, source hosts.env, launch the standalone HMI
#   ./pc4.sh stop
#   ./pc4.sh restart
#   ./pc4.sh status
#   ./pc4.sh logs       # follow the HMI server log
#   ./pc4.sh setup      # just build the venv + install deps, don't start
#
# This HMI polls the two real OpenPLC slaves DIRECTLY (ui/hmi_server.py) with
# no physics access in the process — so "spoofable" is structural. It has no
# fake fallback and refuses to start until it can reach both slaves, so bring
# up PC2 (./pc2.sh), PC3 (./pc3.sh) and PC1 (./pc1.sh) FIRST. Opens on
# http://<PC4-IP>:8093/.
#
# Offline: set KIT=/path/to/petrotwin-kit (or PETROTWIN_KIT) to install deps
# from the USB wheelhouse. Reads PC2/PC3 IPs from deploy/hosts.env.
set -uo pipefail
cd "$(dirname "$0")"                       # deploy/pc4-hmi
REPO="$(cd ../.. && pwd)"
cd "$REPO"
source deploy/common.lib.sh

case "${1:-}" in
  setup)
    ensure_venv ;;
  start)
    ensure_venv; source_hosts
    echo ">> launching standalone HMI via run_hmi.sh (needs PC1+PC2+PC3 already up)"
    exec ./run_hmi.sh start ;;
  stop)
    exec ./run_hmi.sh stop ;;
  restart)
    ensure_venv; source_hosts
    ./run_hmi.sh stop || true
    exec ./run_hmi.sh start ;;
  status)
    exec ./run_hmi.sh status ;;
  logs)
    exec tail -n 60 -f /tmp/petrotwin-hmi.log ;;
  *)
    echo "usage: $(basename "$0") {start|stop|restart|status|logs|setup}"; exit 2 ;;
esac
