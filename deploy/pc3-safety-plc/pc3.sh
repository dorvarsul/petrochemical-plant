#!/usr/bin/env bash
# PetroTwin — PC3: Safety PLC (SIS), the attack TARGET. One command, NO
# docker-compose:
#
#   ./pc3.sh start      # load/build the image if needed, run the container, open firewall
#   ./pc3.sh stop       # stop the container (kept for a fast restart)
#   ./pc3.sh restart    # stop + start
#   ./pc3.sh recreate   # delete + run a fresh container (clean slate)
#   ./pc3.sh status
#   ./pc3.sh logs       # follow the OpenPLC container log
#
# Offline: set KIT=/path/to/petrotwin-kit (or PETROTWIN_KIT) and `start` loads
# the image from the USB kit's openplc-v3.tar instead of building. Online with
# no kit: it builds once from the pinned OpenPLC_v3 commit in docker-compose.yml.
# Skip firewall edits with PETROTWIN_SKIP_UFW=1. Override ports with
# PETROTWIN_SIS_MODBUS_PORT / PETROTWIN_SIS_WEB_PORT.
set -uo pipefail
cd "$(dirname "$0")"                       # deploy/pc3-safety-plc
REPO="$(cd ../.. && pwd)"
source ../common.lib.sh

LABEL="Safety PLC (SIS) [PC3]"
CONTAINER="petrotwin-sis"
MODBUS_PORT="${PETROTWIN_SIS_MODBUS_PORT:-5021}"
WEB_PORT="${PETROTWIN_SIS_WEB_PORT:-8082}"
COMPOSE_FILE="docker-compose.yml"          # read only to recover the build pin when online

openplc_dispatch "${1:-}"
