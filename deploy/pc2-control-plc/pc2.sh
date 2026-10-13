#!/usr/bin/env bash
# PetroTwin — PC2: Control PLC (BPCS). One command, NO docker-compose:
#
#   ./pc2.sh start      # load/build the image if needed, run the container, open firewall
#   ./pc2.sh stop       # stop the container (kept for a fast restart)
#   ./pc2.sh restart    # stop + start
#   ./pc2.sh recreate   # delete + run a fresh container (clean slate)
#   ./pc2.sh status
#   ./pc2.sh logs       # follow the OpenPLC container log
#
# Offline: set KIT=/path/to/petrotwin-kit (or PETROTWIN_KIT) and `start` loads
# the image from the USB kit's openplc-v3.tar instead of building. Online with
# no kit: it builds once from the pinned OpenPLC_v3 commit in docker-compose.yml.
# Skip firewall edits with PETROTWIN_SKIP_UFW=1. Override ports with
# PETROTWIN_BPCS_MODBUS_PORT / PETROTWIN_BPCS_WEB_PORT.
set -uo pipefail
cd "$(dirname "$0")"                       # deploy/pc2-control-plc
REPO="$(cd ../.. && pwd)"
source ../common.lib.sh

LABEL="Control PLC (BPCS) [PC2]"
CONTAINER="petrotwin-bpcs"
MODBUS_PORT="${PETROTWIN_BPCS_MODBUS_PORT:-5020}"
WEB_PORT="${PETROTWIN_BPCS_WEB_PORT:-8081}"
COMPOSE_FILE="docker-compose.yml"          # read only to recover the build pin when online

openplc_dispatch "${1:-}"
